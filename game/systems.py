import itertools
import random
from typing import (
    Iterable,
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
    BulletGone,
    BulletPosition,
    CircleIntro,
    ClientId,
    ClientMessage,
    Control,
    InputDown,
    InputUp,
    PlayerDied,
    PlayerHealthChanged,
    PlayerIntro,
    PlayerPosition,
    Rotate,
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


class Orientation(NamedTuple):
    radians: float


class Speed(NamedTuple):
    magnitude: float


class Velocity(NamedTuple):
    value: Vec


class TimeToLive(NamedTuple):
    seconds: float


class Collisions(NamedTuple):
    contacts: list[tuple[Entity, Vec]]


class Solid(NamedTuple):
    pass


class Player(NamedTuple):
    id: int
    username: str


class Health(NamedTuple):
    points: int
    modify_queue: list[int]


class HealthNotification(NamedTuple):
    change: int
    new_points: int


class Weapon(NamedTuple):
    current_cooldown: float
    delay: float


class Bullet(NamedTuple):
    parent: int


class Remote(NamedTuple):
    client_id: ClientId
    needs_snapshot: bool


class DisconnectRequest(NamedTuple):
    client_id: ClientId


class Fresh(NamedTuple):
    """
    Component for entities that were just created. This is useful when you
    want to broadcast some initial state of the entity.
    """


class Gone(NamedTuple):
    """
    Component for things that are about to be deleted.

    We need this for things like bullets which should 'die' in two phases:
    1. mark them for death, so that on the next frame we can send a `BulletGone`
       message to all the players
    2. on the next frame the bullet shouldn't be able to kill anyone else!
    """


# Systems


## Generic systems


def ttl_system(w: World, items: Query[TimeToLive]) -> None:
    for entity, [ticks] in items.all():
        if ticks <= 0:
            w.unapply(entity, [TimeToLive])
            w.apply(entity, [Gone()])
        else:
            w.apply(entity, [TimeToLive(ticks - w[TIME_DELTA])])


def remove_gone_system(w: World, corpses: Query[Gone]) -> None:
    for entity, _ in corpses.all():
        w.kill(entity)


def remove_fresh_system(w: World, newborns: Query[Fresh]) -> None:
    for entity, _ in newborns.all():
        w.unapply(entity, [Fresh])


# Movement and collisions


def detect_collisions_system(
    w: World,
    with_box: Query[Position, BoxCollider],
    with_circle: Query[Position, CircleCollider],
) -> None:
    grouper = BboxGrouper[tuple[Entity, Box | Circle]](chunk_size=64.0)

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
    collisions: Query[Player, Health, Position, Collisions],
    solids: Query[Solid],
    players: Query[Player],
    bullets: Query[Bullet],
    corpses: Query[Gone],
) -> None:
    for e, [player_id, *_], health, [pos], [contacts] in collisions.all():
        total_push = Vec(0, 0)
        for other, push in contacts:
            if corpses.get(other):
                continue

            if solids.get(other) or players.get(other):
                total_push += push

            if b := bullets.get(other):
                if b[0].parent != player_id:
                    health.modify_queue.append(-1)
                    w.apply(other, [Gone()])
        w.schedule_tweak(e, Position, lambda p, dp=total_push: Position(p.value + dp))


def apply_bullet_collision_system(
    w: World,
    collisions: Query[Bullet, Velocity, Collisions],
    solids: Query[Solid],
) -> None:
    delta = w[TIME_DELTA]
    for e, _, [velocity], [contacts] in collisions.all():
        for other, push in contacts:
            if solids.get(other):
                if -0.5 <= velocity.alignment(push) <= 0.5:
                    # Ricochet
                    w.schedule_tweak(
                        e,
                        Velocity,
                        lambda v, push=push: Velocity((v.value + push / delta) * 0.9),
                    )
                    w.schedule_tweak(e, Position, lambda p, push=push: Position(p.value + push * 3))
                else:
                    w.apply(e, [Gone()])


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
    items: Query[Player, Orientation, Weapon, Position, InputSet, Speed],
) -> None:
    for e, [player_id, *_], [angle], [cooldown, delay], [pos], [controls], [speed] in items.all():
        direction = Vec(0, 0)
        for control in controls:
            direction += _directions.get(control) or Vec(0, 0)

        if cooldown > 0:
            w.apply(e, [Weapon(current_cooldown=cooldown - w[TIME_DELTA], delay=delay)])
        elif Control.fire in controls:
            _spawn_bullet(w, parent=player_id, pos=pos, angle=angle)
            w.apply(e, [Weapon(current_cooldown=delay, delay=delay)])

        w.apply(e, [Velocity(direction.normal() * speed)])


## User health


def apply_health_system(
    w: World,
    players: Query[Player, Health],
    corpses: Query[Gone],
) -> None:
    for e, [player_id, *_], health in players.all():
        if not corpses.get(e):
            delta = sum(health.modify_queue)
            new_hp = health.points + delta
            if new_hp <= 0:
                w.apply(e, [Health(0, [])])
                w.apply(e, [Gone()])
            elif health.modify_queue:
                w.apply(e, [Health(new_hp, [])])
                w.apply(e, [HealthNotification(change=delta, new_points=new_hp)])


def clear_health_change_system(
    w: World,
    changes: Query[HealthNotification],
) -> None:
    w.unapply_many((e, [HealthNotification]) for e, _ in changes.all())


## Networking


def networking_system(
    w: World,
    remotes: Query[Remote, InputSet],
    players: Query[Player, Position, Orientation, Health],
    circles: Query[Solid, Position, CircleCollider],
    boxes: Query[Solid, Position, BoxCollider],
    bullets: Query[Bullet, Position],
    corpses: Query[Gone],
    newborns: Query[Fresh],
    health_notes: Query[HealthNotification],
) -> None:
    inbox = w[NET_INBOX]
    outbox = w[NET_OUTBOX]

    for e, [player_id, *_], [pos], [angle], [hp, *_] in players.all():
        if newborns.get(e):
            outbox.send_broadcast(PlayerHealthChanged(player_id, hp))

        if note_tuple := health_notes.get(e):
            [change] = note_tuple
            outbox.send_broadcast(PlayerHealthChanged(player_id, change.new_points))

        if corpses.get(e):
            outbox.send_broadcast(PlayerDied(player_id))
        elif w[FRAME] % 2 == 0:  # JANKY HACK
            outbox.send_broadcast(PlayerPosition(id=player_id, x=pos.x, y=pos.y, angle=angle))

    for e, _, [pos] in bullets.all():
        if corpses.get(e):
            outbox.send_broadcast(BulletGone(e.num))
        elif w[FRAME] % 2 == 0:  # JANKY HACK
            outbox.send_broadcast(BulletPosition(e.num, pos.x, pos.y))

    snapshot: ServerMessage | None = None

    for e, [client_id, needs_snapshot], [inputs] in remotes.all():
        for msg in inbox.pop(client_id):
            match msg:
                case InputDown(control):
                    inputs.add(control)

                case InputUp(control):
                    inputs.discard(control)

                case Rotate(radians):
                    w.apply(e, [Orientation(radians)])

        if needs_snapshot:
            if snapshot is None:
                snapshot = _compute_snapshot_message(
                    players=(
                        (pos, player, angle, health)
                        for _, player, [pos], angle, health in players.all()
                    ),
                    circles=((pos, circle) for _, _, [pos], [circle] in circles.all()),
                    boxes=((pos, box) for _, _, [pos], [box] in boxes.all()),
                )

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

    if not to_disconnect:
        return

    outbox = w[NET_OUTBOX]
    for e, [client_id, *_] in players.all():
        if client_id in to_disconnect:
            outbox.send_single(client_id, ServerGoodbye())
            w.kill(e)


# Utilities/shared logic


def _spawn_bullet(w: World, parent: int, pos: Vec, angle: float) -> None:
    direction = Vec.from_angle(angle)
    velocity = direction * 500
    w.spawn(
        Position(pos + direction * 21),
        Velocity(velocity),
        CircleCollider(Circle(Vec(0, 0), radius=4)),
        Bullet(parent),
        TimeToLive(1.0),
        Fresh(),
    )


def add_solid_box(
    w: World,
    box: Box,
) -> None:
    w.spawn(
        Position(box.tl),
        BoxCollider(Box(Vec(0, 0), box.size())),
        Solid(),
        Fresh(),
    )


def add_solid_circle(
    w: World,
    circle: Circle,
) -> None:
    w.spawn(
        Position(circle.center),
        CircleCollider(circle.shift(-circle.center)),
        Solid(),
        Fresh(),
    )


def connect_new_player(
    w: World,
    client_id: ClientId,
    username: str,
) -> None:
    w.spawn(
        Player(id=client_id.value, username=username),
        Weapon(current_cooldown=0, delay=0.5),
        Position(Vec(200 + random.random() * 30, 200 + random.random() * 30)),
        Orientation(radians=0),
        Velocity(Vec(0, 0)),
        Remote(client_id, needs_snapshot=True),
        InputSet(set()),
        CircleCollider(Circle(Vec(0, 0), radius=20)),
        Health(points=5, modify_queue=[]),
        Speed(200),
        Fresh(),
    )


def disconnect_player(
    w: World,
    client_id: ClientId,
) -> None:
    w.spawn(DisconnectRequest(client_id))


def _compute_snapshot_message(
    players: Iterable[tuple[Vec, Player, Orientation, Health]],
    circles: Iterable[tuple[Vec, Circle]],
    boxes: Iterable[tuple[Vec, Box]],
) -> ServerMessage:
    circle_intros = [CircleIntro(pos.x, pos.y, circle.radius) for pos, circle in circles]
    box_intros = [
        BoxIntro((pos + box.tl).x, (pos + box.tl).y, box.width(), box.height())
        for pos, box in boxes
    ]

    return WorldSnapshot(
        players=[
            PlayerIntro(player.id, player.username, pos.x, pos.y, angle, health)
            for pos, player, [angle], [health, *_] in players
        ],
        shapes=circle_intros + box_intros,
    )
