from dataclasses import dataclass

from game.ecs import (
    Query,
    World
)


def test_ecs_smoke():
    world = World()

    @dataclass(frozen=True)
    class Baz:
        fizz: str
        buzz: tuple[int, ...]

    calls = []

    @world.add_systems
    def system1(w: World, query: Query[int, str, Baz]) -> None:
        for e, foo, bar, baz in query.all():
            calls.append(("system1", e, foo, bar, baz))

    @world.add_systems
    def system2(w: World, query: Query[int, str]) -> None:
        for e, foo, bar in query.all():
            calls.append(("system2", e, foo, bar))

    @world.add_systems
    def system3(w: World, query: Query[Baz]) -> None:
        for e, baz in query.all():
            calls.append(("system3", e, baz))

    e1 = world.spawn()
    e2 = world.spawn()
    e3 = world.spawn()
    e4 = world.spawn()
    baz1 = Baz("fizz1", (1, 2, 3))
    baz2 = Baz("fizz2", (4, 5))

    world.apply(e1, [10, "a", baz1])
    world.apply(e2, [20])
    world.apply_many([(e2, [baz2]), (e3, [30, "c"]), (e2, ["b"])])
    world.apply(e4, [40])

    world.kill(e4)

    world.commit()
    world.step()

    assert set(calls) == {
        ("system1", e1, 10, "a", baz1),
        ("system1", e2, 20, "b", baz2),
        ("system2", e1, 10, "a"),
        ("system2", e2, 20, "b"),
        ("system2", e3, 30, "c"),
        ("system3", e1, baz1),
        ("system3", e2, baz2),
    }


def test_ecs_exception_handling():
    errors = []
    world = World(on_error=errors.append)

    ok_calls = []

    @world.add_systems
    def system1(w: World, query: Query[int]) -> None:
        for _e, foo in query.all():
            with w.catch():
                _ = 1 / foo
                ok_calls.append(("system1", foo))

    @world.add_systems
    def system2(w: World, query: Query[int, str]) -> None:
        for _e, foo, bar in query.all():
            with w.catch():
                _ = 1 / (2 - abs(foo))
                ok_calls.append(("system2", foo, bar))

    @world.add_systems
    def system3(w: World) -> None:
        raise OSError

    e1 = world.spawn()
    e2 = world.spawn()
    e3 = world.spawn()
    e4 = world.spawn()

    world.apply(e1, [3, "a"])
    world.apply_many(
        [
            (e2, [2, "b"]),
            (e3, [0, "c"]),
        ]
    )
    world.apply(e4, [-2, "d"])

    world.commit()
    world.step()

    assert len(errors) == 4
    assert isinstance(errors[0], ZeroDivisionError)
    assert isinstance(errors[1], ZeroDivisionError)
    assert isinstance(errors[2], ZeroDivisionError)
    assert isinstance(errors[3], OSError)

    assert set(ok_calls) == {
        ("system1", 3),
        ("system1", 2),
        ("system1", -2),
        ("system2", 3, "a"),
        ("system2", 0, "c"),
    }


def test_unapply_simple():
    calls = []

    world = World()

    @world.add_systems
    def system1(w: World, query: Query[int]) -> None:
        for e, number in query.all():
            calls.append(("system1", e, number))

    e1 = world.spawn(10)
    e2 = world.spawn(20)
    e3 = world.spawn(30)
    e4 = world.spawn(40)
    e5 = world.spawn(50)

    world.commit()

    world.unapply(e2, [int])
    world.unapply(e5, [int])

    world.commit()

    world.step()

    assert set(calls) == {
        ("system1", e1, 10),
        ("system1", e3, 30),
        ("system1", e4, 40),
    }


def test_unapply_complex():
    calls = []

    world = World()

    @world.add_systems
    def system1(w: World, query: Query[int, str]) -> None:
        for e, number, string in query.all():
            calls.append(("system1", e, number, string))

    @world.add_systems
    def system2(w: World, query: Query[int]) -> None:
        for e, number in query.all():
            calls.append(("system2", e, number))

    @world.add_systems
    def system3(w: World, query: Query[str]) -> None:
        for e, string in query.all():
            calls.append(("system3", e, string))

    @world.add_systems
    def system4(w: World, query: Query[bool]) -> None:
        for e, value in query.all():
            calls.append(("system4", e, value))

    e1 = world.spawn(100, "foo", True)
    e2 = world.spawn(200, True)
    e3 = world.spawn(300, "bar", True)
    e4 = world.spawn(400, "baz", True)
    e5 = world.spawn(500, "fizz", True)
    e6 = world.spawn(600, "buzz", True)

    world.commit()

    world.unapply(e3, [int, str])
    world.unapply_many([(e4, [int]), (e5, [str, bool])])

    world.commit()
    world.step()

    assert set(calls) == {
        ("system1", e1, 100, "foo"),
        ("system1", e6, 600, "buzz"),
        ("system2", e1, 100),
        ("system2", e2, 200),
        ("system2", e5, 500),
        ("system2", e6, 600),
        ("system3", e1, "foo"),
        ("system3", e4, "baz"),
        ("system3", e6, "buzz"),
        ("system4", e1, True),
        ("system4", e2, True),
        ("system4", e3, True),
        ("system4", e4, True),
        ("system4", e6, True),
    }
