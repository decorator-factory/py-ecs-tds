from __future__ import annotations

import contextlib
import inspect
import traceback
import typing
from collections.abc import (
    Callable,
    Iterable,
    Iterator
)
from dataclasses import dataclass
from typing import (
    Any,
    Generic,
    TypeVar,
    TypeVarTuple
)
from weakref import WeakKeyDictionary


_CompKey = tuple[type, ...]
SystemFunction = Callable[..., None]


@dataclass(frozen=True)
class _System:
    queries: tuple[Query, ...]
    handler: SystemFunction


Ts = TypeVarTuple("Ts")
T = TypeVar("T")


class Query(Generic[*Ts]):
    def __init__(self, world: World, key: _CompKey) -> None:
        self._world = world
        self._key = key

    def __getitem__(self, entity: Entity) -> tuple[*Ts]:
        cs = self._world._entity_to_components[entity]
        return tuple([cs[ct] for ct in self._key])  # type: ignore

    def get(self, entity: Entity) -> tuple[*Ts] | None:
        if entity not in self._world._key_to_entities[self._key]:
            return None
        return self[entity]

    def all(self) -> Iterator[tuple[Entity, *Ts]]:
        return self._world._query_all(self._key)  # type: ignore


class Resource(Generic[T]):
    def __init__(self, key: type[T], /) -> None:
        self._key = key
        self._map: WeakKeyDictionary[World, T] = WeakKeyDictionary()

    def store(self, world: World, value: T) -> None:
        self._map[world] = value

    def get(self, world: World) -> T | None:
        return self._map.get(world)

    def __getitem__(self, world: World) -> T:
        return self._map[world]


@dataclass(frozen=True)
class Entity:
    num: int


class World:
    def __init__(
        self,
        *,
        on_error: Callable[[Exception], None] = traceback.print_exception,
    ) -> None:
        # I made a bunch of micro-optimizations and haven't actually benchmarked them 8)
        self._systems: list[_System] = []

        self._type_to_keys: dict[type, set[_CompKey]] = {}
        self._key_to_entities: dict[_CompKey, set[Entity]] = {}
        self._entity_to_keys: dict[Entity, set[_CompKey]] = {}
        self._entity_to_components: dict[Entity, dict[type, object]] = {}

        self._on_error = on_error
        self._next_number = 0
        self._frozen = False

        self._components_to_add: list[tuple[Entity, Iterable[object]]] = []
        self._components_to_delete: list[tuple[Entity, Iterable[type]]] = []
        self._entities_to_delete: list[Entity] = []
        self._tweaks: list[tuple[Entity, type, Callable[[Any], Any]]] = []

    def __setitem__(self, key: Resource[T], value: T) -> None:
        key.store(self, value)

    def __getitem__(self, key: Resource[T]) -> T:
        return key[self]

    def step(self) -> None:
        self._frozen = True

        for system in self._systems:
            try:
                system.handler(self, *system.queries)
            except Exception as exc:
                self._on_error(exc)

        self.commit()

    def commit(self) -> None:
        for e, component_types in self._components_to_delete:
            self.do_delete_components(e, component_types)

        for e, components in self._components_to_add:
            self.do_add_components(e, components)

        for e in self._entities_to_delete:
            self.do_delete_entity(e)

        self._components_to_add.clear()
        self._components_to_delete.clear()
        self._entities_to_delete.clear()

        for e, ct, tweak in self._tweaks:
            if cs := self._entity_to_components.get(e):
                if ct in cs:
                    cs[ct] = tweak(cs[ct])
        self._tweaks.clear()

    def _register_query(self, key: _CompKey) -> None:
        for ct in key:
            self._type_to_keys.setdefault(ct, set()).add(key)

        self._key_to_entities.setdefault(key, set())

    def _query_all(self, key: _CompKey) -> Iterator[tuple[Any, ...]]:
        entities = self._key_to_entities[key]

        for entity in entities:
            cs = self._entity_to_components[entity]
            yield (entity, *[cs[ct] for ct in key])

    def add_systems(self, *fns: SystemFunction) -> None:
        if self._frozen:
            raise RuntimeError("Cannot add systems after the world has already started")

        for fn in fns:
            system = self._parse_system(fn)
            for query in system.queries:
                self._register_query(query._key)
            self._systems.append(system)

    def spawn(self, *components: object) -> Entity:
        self._frozen = True
        self._next_number += 1
        entity = Entity(self._next_number)
        self._entity_to_components[entity] = {}
        self._entity_to_keys[entity] = set()
        if components:
            self.apply(entity, components)
        return entity

    def spawn_many(self, component_tuples: Iterable[Iterable[object]]) -> list[Entity]:
        return [self.spawn(*x) for x in component_tuples]

    def apply(self, entity: Entity, components: Iterable[object], /) -> None:
        self._components_to_add.append((entity, components))

    def schedule_tweak(
        self, entity: Entity, component_type: type[T], callback: Callable[[T], T]
    ) -> None:
        self._tweaks.append((entity, component_type, callback))

    def apply_many(self, values: Iterable[tuple[Entity, Iterable[object]]], /) -> None:
        self._components_to_add.extend(values)

    def unapply(self, entity: Entity, component_types: Iterable[type], /) -> None:
        self._components_to_delete.append((entity, component_types))

    def unapply_many(self, values: Iterable[tuple[Entity, Iterable[type]]], /) -> None:
        self._components_to_delete.extend(values)

    def kill(self, entity: Entity) -> None:
        self._entities_to_delete.append(entity)

    def kill_many(self, entities: Iterable[Entity]) -> None:
        self._entities_to_delete.extend(entities)

    def do_add_components(self, entity: Entity, components: Iterable[object]) -> None:
        self._frozen = True
        cs = self._entity_to_components[entity]
        for c in components:
            cs[type(c)] = c

        all_types = cs.keys()
        indices = self._entity_to_keys[entity]
        for key, es in self._key_to_entities.items():
            if all_types >= set(key):
                es.add(entity)
                indices.add(key)

    def do_delete_components(self, entity: Entity, component_types: Iterable[type]) -> None:
        cs = self._entity_to_components[entity]
        for ct in component_types:
            cs.pop(ct, None)

        all_types = cs.keys()
        keys = self._entity_to_keys[entity]
        keys_to_drop = {key for key in keys if not (all_types) >= set(key)}
        keys.difference_update(keys_to_drop)
        for key in keys_to_drop:
            self._key_to_entities[key].discard(entity)

    def do_delete_entity(self, entity: Entity) -> None:
        for idx in self._entity_to_keys[entity]:
            self._key_to_entities[idx].discard(entity)
        self._entity_to_keys.pop(entity, None)
        self._entity_to_components.pop(entity, None)

    @contextlib.contextmanager
    def catch(self) -> Iterator[None]:
        try:
            yield
        except Exception as exc:
            self._on_error(exc)

    def _parse_system(self, fn: SystemFunction) -> _System:
        sig = inspect.signature(fn)
        params = list(sig.parameters.values())

        if not params:
            raise TypeError("System function must accept at least one parameter: `world: World`")

        anns = inspect.get_annotations(fn)

        if anns.get(params[0].name) != World:
            raise TypeError("Expected first parameter to have annotation of `World`")

        queries = []

        for param in params[1:]:
            if param.name not in anns:
                raise TypeError(f"Parameter {param.name!r} does not have an annotation")
            ann = anns[param.name]

            origin = typing.get_origin(ann)
            if origin != Query:
                raise TypeError(f"Parameter {param.name!r} can only have a `Query[...]` annotation")

            queries.append(Query(self, typing.get_args(ann)))

        return _System(tuple(queries), fn)
