import itertools
from typing import (
    NamedTuple,
    Protocol,
    Sequence
)

from game.collision import (
    BboxGrouper,
    collide_shapes
)
from game.ecs import (
    Entity,
    Query,
    Resource,
    World
)
from game.geometry import (
    Box,
    Circle,
    Vec
)
from game.messages import (
    BoxIntro,
    CircleIntro,
    ClientId,
    ClientMessage,
    Control,
    InputDown,
    InputUp,
    PlayerIntro,
    PlayerPosition,
    ServerGoodbye,
    ServerMessage,
    WorldSnapshot
)


class Channel(Protocol):
    def receive(self) -> list[ClientMessage]:
        ...

    def send(self, message: ServerMessage, /) -> None:
        ...


class Outbox(Protocol):
    def send_broadcast(self, message: ServerMessage, /) -> None:
        ...

    def send_single(self, client_id: ClientId, message: ServerMessage, /) -> None:
        ...


class Inbox(Protocol):
    def pop(self, client_id: ClientId, /) -> Sequence[ClientMessage]:
        ...


# Resources

NET_INBOX = Resource(Inbox)
NET_OUTBOX = Resource(Outbox)
TIME_DELTA = Resource(float)
FRAME = Resource(int)


# Components


class InputSet(NamedTuple):
    controls: set[Control]


class BoxCollider(NamedTuple):
    shape: Box


class CircleCollider(NamedTuple):
    shape: Circle


class Position(NamedTuple):
    value: Vec


class Speed(NamedTuple):
    magnitude: float


class Velocity(NamedTuple):
    value: Vec


class Health(NamedTuple):
    points: int


class TimeToLive(NamedTuple):
    seconds: float


class Collisions(NamedTuple):
    items: list[tuple[Entity, Vec]]


class Solid(NamedTuple):
    pass


class Character(NamedTuple):
    id: int
    username: str


class Remote(NamedTuple):
    client_id: ClientId
    needs_snapshot: bool


class DisconnectRequest(NamedTuple):
    client_id: ClientId


# Systems


## Generic systems


def ttl_system(w: World, items: Query[TimeToLive]) -> None:
    for entity, [ticks] in items.all():
        if ticks <= 0:
            w.kill(entity)
        else:
            w.apply(entity, [TimeToLive(ticks - w[TIME_DELTA])])


# Movement and collisions


def detect_collisions_system(
    w: World,
    with_box: Query[Position, BoxCollider],
    with_circle: Query[Position, CircleCollider],
) -> None:
    grouper = BboxGrouper[tuple[Entity, Box | Circle]](chunk_size=128.0)

    for e, [pos], [box] in with_box.all():
        box = box.shift(pos)
        grouper.push((e, box), box)

    for e, [pos], [circle] in with_circle.all():
        circle = circle.shift(pos)
        grouper.push((e, circle), circle.bbox())

    checked: set[tuple[Entity, Entity]] = set()

    collisions: dict[Entity, list[tuple[Entity, Vec]]] = {}

    for region in grouper.regions():
        for (e1, shape1), (e2, shape2) in itertools.combinations(region, 2):
            if (e1, e2) in checked:
                continue
            else:
                checked.add((e1, e2))
            if push := collide_shapes(shape1, shape2):
                collisions.setdefault(e1, []).append((e2, push))
                collisions.setdefault(e2, []).append((e1, -push))

    for e, entries in collisions.items():
        w.apply(e, [Collisions(entries)])


def remove_collisions_system(
    w: World,
    items: Query[Collisions],
) -> None:
    w.unapply_many((e, [Collisions]) for e, _ in items.all())


def apply_player_collision_system(
    w: World,
    collisions: Query[Character, Position, Collisions],
    solids: Query[Solid],
    characters: Query[Character],
) -> None:
    for e, _char, [pos], [entries] in collisions.all():
        total_push = Vec(0, 0)
        for other, push in entries:
            if solids.get(other) or characters.get(other):
                total_push += push
        w.schedule_tweak(e, Position, lambda p, dp=total_push: Position(p.value + dp))


def movement_system(
    w: World,
    movables: Query[Position, Velocity],
) -> None:
    delta = w[TIME_DELTA]

    w.apply_many((e, [Position(pos + vel * delta)]) for e, [pos], [vel] in movables.all())


## User input

_directions = {
    Control.left: Vec(-1, 0),
    Control.right: Vec(1, 0),
    Control.down: Vec(0, 1),
    Control.up: Vec(0, -1),
}


def apply_inputs_system(
    w: World,
    items: Query[InputSet, Speed],
) -> None:
    for e, [controls], [speed] in items.all():
        direction = Vec(0, 0)
        for control in controls:
            direction += _directions.get(control) or Vec(0, 0)
        w.apply(e, [Velocity(direction.normal() * speed)])


## Networking


def networking_system(
    w: World,
    remotes: Query[Remote, InputSet],
    players: Query[Character, Position],
    circles: Query[Solid, Position, CircleCollider],
    boxes: Query[Solid, Position, BoxCollider],
) -> None:
    inbox = w[NET_INBOX]
    outbox = w[NET_OUTBOX]

    if w[FRAME] % 4 == 0:  # JANKY HACK
        for _e, [player_id, *_], [pos] in players.all():
            outbox.send_broadcast(PlayerPosition(id=player_id, x=pos.x, y=pos.y))

    snapshot: ServerMessage | None = None

    for e, [client_id, needs_snapshot], [inputs] in remotes.all():
        for msg in inbox.pop(client_id):
            match msg:
                case InputDown(control):
                    inputs.add(control)

                case InputUp(control):
                    inputs.discard(control)

        if needs_snapshot:
            if snapshot is None:
                snapshot = _compute_snapshot_message(players, circles, boxes)

            outbox.send_single(client_id, snapshot)
            w.apply(e, [Remote(client_id, needs_snapshot=False)])


def disconnect_players_system(
    w: World,
    disconnects: Query[DisconnectRequest],
    players: Query[Remote],
) -> None:
    to_disconnect: set[ClientId] = set()
    for e, [client_id] in disconnects.all():
        w.kill(e)
        to_disconnect.add(client_id)

    outbox = w[NET_OUTBOX]
    for e, [client_id, *_] in players.all():
        if client_id in to_disconnect:
            outbox.send_single(client_id, ServerGoodbye())
            w.kill(e)


## Diagnostics


def debug_system(w: World) -> None:
    if w[FRAME] % 1000 == 0:
        print("Delta, ms:", round(w[TIME_DELTA] * 1000, 2))


# Utilities/shared logic


def add_solid_box(
    w: World,
    box: Box,
) -> None:
    w.spawn(
        Position(box.tl),
        BoxCollider(Box(Vec(0, 0), box.size())),
        Solid(),
    )


def add_solid_circle(
    w: World,
    circle: Circle,
) -> None:
    w.spawn(
        Position(circle.center),
        CircleCollider(circle.shift(-circle.center)),
        Solid(),
    )


def connect_new_player(
    w: World,
    client_id: ClientId,
    username: str,
) -> None:
    w.spawn(
        Character(id=client_id.value, username=username),
        Position(Vec(200, 200)),
        Velocity(Vec(0, 0)),
        Remote(client_id, needs_snapshot=True),
        InputSet(set()),
        CircleCollider(Circle(Vec(0, 0), radius=20)),
        Speed(200),
    )


def disconnect_player(
    w: World,
    client_id: ClientId,
) -> None:
    w.spawn(DisconnectRequest(client_id))


def _compute_snapshot_message(
    players: Query[Character, Position],
    circles: Query[Solid, Position, CircleCollider],
    boxes: Query[Solid, Position, BoxCollider],
) -> ServerMessage:
    circle_intros = [
        CircleIntro(pos.x, pos.y, circle.radius) for e, _, [pos], [circle] in circles.all()
    ]
    box_intros = [
        BoxIntro((pos + box.tl).x, (pos + box.tl).y, box.width(), box.height())
        for e, _, [pos], [box] in boxes.all()
    ]

    return WorldSnapshot(
        players=[
            PlayerIntro(player_id, username, pos.x, pos.y)
            for e, [player_id, username], [pos] in players.all()
        ],
        shapes=circle_intros + box_intros,
    )
