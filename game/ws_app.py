from __future__ import annotations

import time
from contextlib import (
    asynccontextmanager,
    contextmanager
)
from dataclasses import asdict
from functools import partial
from typing import (
    Iterator,
    Mapping,
    Sequence
)
from weakref import WeakKeyDictionary

import anyio
from adaptix.load_error import LoadError
from anyio.streams.memory import MemoryObjectReceiveStream as MORS
from anyio.streams.memory import MemoryObjectSendStream as MOSS
from attr import frozen
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.websockets import (
    WebSocket,
    WebSocketState
)

import game.systems as systems
from game.geometry import (
    Box,
    Circle,
    Vec
)
from game.messages import (
    BadMessage,
    ClientHello,
    ClientId,
    ClientMessage,
    PlayerJoined,
    PlayerLeft,
    ServerMessage,
    ServerWelcome,
    parse_message,
    serialize_message
)


class NetOutbox:
    def __init__(self) -> None:
        self._broadcasts: list[ServerMessage] = []
        self._singles: dict[ClientId, list[ServerMessage]] = {}

    def send_broadcast(self, message: ServerMessage) -> None:
        self._broadcasts.append(message)

    def send_single(self, client_id: ClientId, message: ServerMessage) -> None:
        self._singles.setdefault(client_id, []).append(message)

    def bundle(self) -> MessageBundle:
        return MessageBundle(self._broadcasts, self._singles)

    def reset(self):
        self._broadcasts = []
        self._singles = {}


class NetInbox:
    def __init__(self) -> None:
        self._messages: dict[ClientId, list[ClientMessage]] = {}

    def append(self, client_id: ClientId, message: ClientMessage) -> None:
        self._messages.setdefault(client_id, []).append(message)

    def pop(self, client_id: ClientId) -> Sequence[ClientMessage]:
        return self._messages.pop(client_id, ())


class PlayerQueue:
    def __init__(self) -> None:
        self._players: list[ClientId] = []

    def add(self, client_id: ClientId) -> None:
        self._players.append(client_id)

    def pop(self) -> Sequence[ClientId]:
        players = self._players
        self._players = []
        return players


class PlayerHandle:
    def __init__(self, client_id: ClientId) -> None:
        self.client_id = client_id
        self.send, self.recv = anyio.create_memory_object_stream[list[object]](10)


class GameState:
    def __init__(self) -> None:
        self._inbox = NetInbox()
        self._outbox = NetOutbox()
        self._join_queue = PlayerQueue()
        self._leave_queue = PlayerQueue()
        self._next_id = 0
        self._handles: dict[ClientId, PlayerHandle] = {}
        self._player_usernames: dict[ClientId, str] = {}

    def _next_client_id(self) -> ClientId:
        self._next_id += 1
        return ClientId(self._next_id)

    def inbox(self) -> NetInbox:
        return self._inbox

    def outbox(self) -> NetOutbox:
        return self._outbox

    def join_queue(self) -> PlayerQueue:
        return self._join_queue

    def leave_queue(self) -> PlayerQueue:
        return self._leave_queue

    def handles(self) -> Sequence[PlayerHandle]:
        return list(self._handles.values())

    def username(self, client_id: ClientId, /) -> str:
        return self._player_usernames[client_id]

    @contextmanager
    def connect_new_player(self, username: str) -> Iterator[PlayerHandle]:
        client_id = self._next_client_id()
        handle = PlayerHandle(client_id)
        self._handles[client_id] = handle
        self._join_queue.add(client_id)
        self._player_usernames[client_id] = username
        print(f"Player {client_id} connected")
        self._outbox.send_broadcast(PlayerJoined(id=client_id.value, username=username))
        try:
            yield handle
        finally:
            print(f"Player {client_id} disconnected")
            self._handles.pop(client_id)
            self._leave_queue.add(client_id)
            self._outbox.send_broadcast(PlayerLeft(id=client_id.value))


APP_GAME_STATE = WeakKeyDictionary[Starlette, GameState]()


async def client_ws_handler(ws: WebSocket) -> None:
    await ws.accept()

    game_state = APP_GAME_STATE[ws.app]

    while True:
        message = parse_message(await ws.receive_json())
        print(message)
        if isinstance(message, ClientHello):
            username = message.username
            break

    async def _send_updates_to_player() -> None:
        async with handle.recv:
            async for bundle in handle.recv:
                for msg in bundle:
                    if ws.client_state == WebSocketState.DISCONNECTED:
                        return
                    await ws.send_json(msg)

    async def _read_inputs_from_player() -> None:
        async for json in ws.iter_json():
            try:
                msg = parse_message(json)
            except LoadError as exc:
                error_msg = BadMessage(asdict(exc))
                await ws.send_json(serialize_message(error_msg))
            else:
                game_state.inbox().append(handle.client_id, msg)

    with game_state.connect_new_player(username) as handle:
        game_state.outbox().send_single(
            handle.client_id, ServerWelcome(client_id=handle.client_id.value)
        )
        async with anyio.create_task_group() as tg:
            tg.start_soon(_send_updates_to_player)
            tg.start_soon(_read_inputs_from_player)


class Ticker:
    def __init__(self, fps: float) -> None:
        self._target_duration = 1 / fps
        self._last = time.monotonic()

    async def tick(self) -> None:
        to_sleep = self._target_duration - (time.monotonic() - self._last)
        await anyio.sleep(max(0, to_sleep))
        self._last = time.monotonic()


def _init_buildings(w: systems.World) -> None:
    systems.add_solid_box(w, Box(Vec(50, 200), Vec(90, 400)))
    systems.add_solid_box(w, Box(Vec(50, 270), Vec(150, 330)))
    systems.add_solid_circle(w, Circle(Vec(500, 300), 80))


async def game_loop(
    fps: float,
    send: MOSS[MessageBundle],
    state: GameState,
) -> None:
    async with send:
        world = systems.World()
        world[systems.NET_INBOX] = state.inbox()
        world[systems.NET_OUTBOX] = state.outbox()
        world.add_systems(
            # Movement and collisions
            systems.movement_system,
            systems.detect_collisions_system,
            systems.apply_player_collision_system,
            systems.remove_collisions_system,
            # Handling input
            systems.apply_inputs_system,
            # Networking
            systems.networking_system,
            systems.disconnect_players_system,
            # Diagnostics
            systems.debug_system,
        )
        _init_buildings(world)
        world.commit()

        ticker = Ticker(fps)

        world[systems.FRAME] = 0

        last_simulation = time.monotonic()
        while True:
            for client_id in state.join_queue().pop():
                systems.connect_new_player(world, client_id, state.username(client_id))

            for client_id in state.leave_queue().pop():
                systems.disconnect_player(world, client_id)

            world.commit()

            t = time.monotonic()
            since_last_simulation = t - last_simulation
            world[systems.TIME_DELTA] = since_last_simulation
            world.step()
            last_simulation = time.monotonic()

            bundle = state.outbox().bundle()
            state.outbox().reset()
            await send.send(bundle)

            await ticker.tick()
            world[systems.FRAME] += 1


@frozen
class MessageBundle:
    broadcast: Sequence[ServerMessage]
    single: Mapping[ClientId, list[ServerMessage]]


@asynccontextmanager
async def lifespan(app: Starlette):
    async with anyio.create_task_group() as tg:
        game_state = GameState()
        APP_GAME_STATE[app] = game_state

        send, recv = anyio.create_memory_object_stream[MessageBundle]()
        tg.start_soon(
            lambda: game_loop(
                fps=100,
                send=send,
                state=game_state,
            )
        )
        tg.start_soon(partial(push_messages_to_clients, app, recv))
        print("Yielding...")
        yield
        print("Closing...")
        tg.cancel_scope.cancel()


async def push_messages_to_clients(
    app: Starlette,
    recv: MORS[MessageBundle],
) -> None:
    game_state = APP_GAME_STATE[app]

    async with recv:
        async for bundle in recv:
            async with anyio.create_task_group() as tg:
                broad_serialized = list(map(serialize_message, bundle.broadcast))
                for handle in game_state.handles():
                    local_serialized = list(
                        map(serialize_message, bundle.single.get(handle.client_id, ()))
                    )
                    tg.start_soon(partial(handle.send.send, local_serialized + broad_serialized))


def create_app():
    return Starlette(routes=[WebSocketRoute("/ws", client_ws_handler)], lifespan=lifespan)
