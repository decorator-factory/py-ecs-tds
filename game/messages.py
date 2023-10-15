from enum import Enum
from typing import (
    Literal,
    Union
)

from adaptix import Retort
from adaptix.load_error import MsgError
from attr import frozen


@frozen
class ClientId:
    value: int


# Client messages


class Control(Enum):
    left = "left"
    right = "right"
    up = "up"
    down = "down"
    fire = "fire"


@frozen
class ClientHello:
    username: str


@frozen
class InputDown:
    control: Control


@frozen
class InputUp:
    control: Control


@frozen
class Rotate:
    radians: float


ClientMessage = Union[
    ClientHello,
    InputDown,
    InputUp,
    Rotate,
]

CLIENT_MESSAGES: dict[str, type[ClientMessage]] = {
    "hello": ClientHello,
    "input_down": InputDown,
    "input_up": InputUp,
    "rotate": Rotate,
}


# Server messages


@frozen
class ServerWelcome:
    client_id: int


@frozen
class ServerGoodbye:
    pass


@frozen
class PlayerJoined:
    id: int
    username: str


@frozen
class PlayerLeft:
    id: int


@frozen
class PlayerPosition:
    id: int
    x: float
    y: float
    angle: float


@frozen
class BulletPosition:
    id: int
    x: float
    y: float


@frozen
class BulletGone:
    id: int


@frozen
class PlayerDied:
    id: int


@frozen
class PlayerIntro:
    id: int
    username: str
    x: float
    y: float
    angle: float


@frozen
class BoxIntro:
    x: float
    y: float
    width: float
    height: float
    kind: Literal["box"] = "box"


@frozen
class CircleIntro:
    x: float
    y: float
    radius: float
    kind: Literal["circle"] = "circle"


@frozen
class WorldSnapshot:
    players: list[PlayerIntro]
    shapes: list[BoxIntro | CircleIntro]


@frozen
class BadMessage:
    error: object


ServerMessage = Union[
    ServerWelcome,
    ServerGoodbye,
    PlayerJoined,
    PlayerLeft,
    PlayerPosition,
    PlayerDied,
    BulletPosition,
    BulletGone,
    WorldSnapshot,
    BadMessage,
]


SERVER_MESSAGES: dict[type[ServerMessage], str] = {
    ServerWelcome: "welcome",
    ServerGoodbye: "goodbye",
    PlayerJoined: "player_joined",
    PlayerLeft: "player_left",
    PlayerPosition: "player_position",
    PlayerDied: "player_died",
    BulletPosition: "bullet_position",
    BulletGone: "bullet_gone",
    WorldSnapshot: "world_snapshot",
    BadMessage: "bad_message",
}

###


retort = Retort()


def serialize_message(message: ServerMessage) -> object:
    kind = SERVER_MESSAGES[type(message)]
    return {
        "type": kind,
        **retort.dump(message, type(message)),
    }


def parse_message(message: object) -> ClientMessage:
    match message:
        case {"type": str(kind), **rest}:
            if message_class := CLIENT_MESSAGES.get(kind):
                ok = retort.load(rest, message_class)
                return ok
            else:
                raise MsgError(f"Unknown message kind {kind!r}")
    raise MsgError("Invalid message structure")
