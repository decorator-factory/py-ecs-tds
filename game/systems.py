import itertools
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
    PlayerScoreChanged,
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


class TimeToLive(NamedTuple):
    seconds: float


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


class BoxCollider(NamedTuple):
    shape: Box


class CircleCollider(NamedTuple):
    shape: Circle


class Position(NamedTuple):
    value: Vec


class Orientation(NamedTuple):
    radians: float


class Velocity(NamedTuple):
    value: Vec


class Collisions(NamedTuple):
    contacts: list[tuple[Entity, Vec]]


class Solid(NamedTuple):
    pass


class Player(NamedTuple):
    id: int
    username: str
    weapon_cooldown: float = 0
    score: int = 0
    last_damage_source: int | None = None


class Health(NamedTuple):
    points: int
    modify_queue: list[int]


class Score(NamedTuple):
    points: int
    modify_queue: list[int]


class HealthNotification(NamedTuple):
    change: int
    new_health: int


class ScoreNotification(NamedTuple):
    change: int
    new_score: int


class Bullet(NamedTuple):
    parent: int


class Remote(NamedTuple):
    client_id: ClientId
    needs_snapshot: bool
    controls: set[Control]


class DisconnectRequest(NamedTuple):
    client_id: ClientId


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


def clear_notifications_system(
    w: World,
    health_notifications: Query[HealthNotification],
    score_notifications: Query[ScoreNotification],
    newborns: Query[Fresh],
) -> None:
    w.unapply_many((e, [HealthNotification]) for e, _ in health_notifications.all())
    w.unapply_many((e, [ScoreNotification]) for e, _ in score_notifications.all())
    w.unapply_many((e, [Fresh]) for e, _ in newborns.all())


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
    for e, player, health, [pos], [contacts] in collisions.all():
        total_push = Vec(0, 0)
        for other, push in contacts:
            if corpses.get(other):
                continue

            if solids.get(other) or players.get(other):
                total_push += push

            if b := bullets.get(other):
                shooter_id = b[0].parent
                if shooter_id != player.id:
                    health.modify_queue.append(-1)
                    w.schedule_tweak(e, Player, lambda p: p._replace(last_damage_source=shooter_id))
                    w.apply(other, [Gone()])
        w.schedule_tweak(e, Position, lambda p, dp=total_push: Position(p.value + dp))


def apply_bullet_collision_system(
    w: World,
    collisions: Query[Bullet, Velocity, Collisions],
    solids: Query[Solid],
) -> None:
    w[TIME_DELTA]
    for e, _, [velocity], [contacts] in collisions.all():
        for other, push in contacts:
            if solids.get(other):
                if -0.6 <= velocity.alignment(push) <= 0.6:
                    # Ricochet
                    w.schedule_tweak(
                        e,
                        Velocity,
                        lambda v, push=push: Velocity((v.value + push * 100) * 0.9),
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

_FIRE_DELAY = 0.5
_SPEED = 200


def apply_inputs_system(
    w: World,
    items: Query[Player, Orientation, Position, Remote],
) -> None:
    for e, player, [angle], [pos], remote in items.all():
        direction = Vec(0, 0)
        for control in remote.controls:
            direction += _directions.get(control) or Vec(0, 0)

        if player.weapon_cooldown > 0:
            w.apply(e, [player._replace(weapon_cooldown=player.weapon_cooldown - w[TIME_DELTA])])
        elif Control.fire in remote.controls:
            _spawn_bullet(w, parent=player.id, pos=pos, angle=angle)
            w.apply(e, [player._replace(weapon_cooldown=_FIRE_DELAY)])

        w.apply(e, [Velocity(direction.normal() * _SPEED)])


## User stats


def apply_health_system(
    w: World,
    players: Query[Player, Health],
    corpses: Query[Gone],
    scores: Query[Score],
) -> None:
    player_id_to_entity = {player.id: e for e, player, _ in players.all()}

    for e, player, health in players.all():
        if not corpses.get(e):
            delta = sum(health.modify_queue)
            new_hp = health.points + delta
            if new_hp <= 0:
                # Dead
                w.apply(
                    e,
                    [
                        Health(0, []),
                        Gone(),
                    ],
                )
                if killer_id := player.last_damage_source:
                    if killer_entity := player_id_to_entity.get(killer_id):
                        if score_tuple := scores.get(killer_entity):
                            [score] = score_tuple
                            score.modify_queue.append(1)
            elif health.modify_queue:
                w.apply(
                    e, [Health(new_hp, []), HealthNotification(change=delta, new_health=new_hp)]
                )


def apply_score_system(
    w: World,
    scores: Query[Score],
) -> None:
    for e, [points, queue] in scores.all():
        if queue:
            delta = sum(queue)
            new_points = points + delta
            w.apply(
                e,
                [
                    Score(new_points, []),
                    ScoreNotification(delta, new_points),
                ],
            )


## Networking


def networking_system(
    w: World,
    remotes: Query[Remote],
    players: Query[Player, Position, Orientation, Health, Score],
    circles: Query[Solid, Position, CircleCollider],
    boxes: Query[Solid, Position, BoxCollider],
    bullets: Query[Bullet, Position],
    corpses: Query[Gone],
    newborns: Query[Fresh],
    health_notes: Query[HealthNotification],
    score_notes: Query[ScoreNotification],
) -> None:
    inbox = w[NET_INBOX]
    outbox = w[NET_OUTBOX]

    for e, [player_id, *_], [pos], [angle], [hp, *_], [score, *_] in players.all():
        if newborns.get(e):
            outbox.send_broadcast(PlayerHealthChanged(player_id, hp))

        if health_note_tuple := health_notes.get(e):
            [change] = health_note_tuple
            outbox.send_broadcast(PlayerHealthChanged(player_id, change.new_health))

        if score_note_tuple := score_notes.get(e):
            [change] = score_note_tuple
            outbox.send_broadcast(PlayerScoreChanged(player_id, change.new_score))

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

    for e, [client_id, needs_snapshot, controls] in remotes.all():
        for msg in inbox.pop(client_id):
            match msg:
                case InputDown(control):
                    controls.add(control)

                case InputUp(control):
                    controls.discard(control)

                case Rotate(radians):
                    w.apply(e, [Orientation(radians)])

        if needs_snapshot:
            if snapshot is None:
                snapshot = _compute_snapshot_message(
                    players=(
                        (pos, player, angle, health, score)
                        for _, player, [pos], angle, health, score in players.all()
                    ),
                    circles=((pos, circle) for _, _, [pos], [circle] in circles.all()),
                    boxes=((pos, box) for _, _, [pos], [box] in boxes.all()),
                )

            outbox.send_single(client_id, snapshot)
            w.apply(e, [Remote(client_id, needs_snapshot=False, controls=controls)])


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
    velocity = direction * 600
    w.spawn(
        Position(pos + direction * 21),
        Velocity(velocity),
        CircleCollider(Circle(Vec(0, 0), radius=4)),
        Bullet(parent),
        TimeToLive(0.5),
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
    spawn_point: Vec,
) -> None:
    w.spawn(
        Player(id=client_id.value, username=username, weapon_cooldown=0),
        Position(spawn_point),
        Orientation(radians=0),
        Velocity(Vec(0, 0)),
        Remote(client_id, needs_snapshot=True, controls=set()),
        CircleCollider(Circle(Vec(0, 0), radius=16)),
        Health(points=5, modify_queue=[]),
        Score(0, []),
        Fresh(),
    )


def disconnect_player(
    w: World,
    client_id: ClientId,
) -> None:
    w.spawn(DisconnectRequest(client_id))


def _compute_snapshot_message(
    players: Iterable[tuple[Vec, Player, Orientation, Health, Score]],
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
            PlayerIntro(player.id, player.username, pos.x, pos.y, angle, health, score)
            for pos, player, [angle], [health, *_], [score, *_] in players
        ],
        shapes=circle_intros + box_intros,
    )
