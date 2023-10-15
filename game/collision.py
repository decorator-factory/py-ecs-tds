from math import (
    ceil,
    floor
)
from typing import (
    Generic,
    Iterable,
    TypeVar
)

from game.geometry import (
    Box,
    Circle,
    Vec
)


_T = TypeVar("_T")


def collide_shapes(s1: Box | Circle, s2: Box | Circle) -> Vec | None:
    if isinstance(s1, Circle):
        if isinstance(s2, Circle):
            return collide_circles(s1, s2)
        else:
            push = collide_box_circle(s2, s1)
            if push is None:
                return None
            else:
                return -push
    else:
        if isinstance(s2, Box):
            return collide_boxes(s1, s2)
        else:
            return collide_box_circle(s1, s2)


class BboxGrouper(Generic[_T]):
    def __init__(self, *, chunk_size: float) -> None:
        self._chunk_size = chunk_size
        self._regions: dict[tuple[int, int], list[_T]] = {}

    def push(self, item: _T, bbox: Box) -> None:
        xstart = floor(bbox.tl.x / self._chunk_size)
        xend = ceil(bbox.br.x / self._chunk_size)

        ystart = floor(bbox.tl.y / self._chunk_size)
        yend = ceil(bbox.br.y / self._chunk_size)

        for i in range(xstart, xend + 1):
            for j in range(ystart, yend + 1):
                self._regions.setdefault((i, j), []).append(item)

    def regions(self) -> Iterable[list[_T]]:
        return self._regions.values()


def collide_circles(c1: Circle, c2: Circle) -> Vec | None:
    delta = c1.center - c2.center
    target_length = c1.radius + c2.radius
    actual_length = delta.length()
    if actual_length > target_length:
        return None
    return delta.normal() * (target_length - actual_length) * 0.75


def collide_box_circle(box: Box, circle: Circle) -> Vec | None:
    total = Vec(0, 0)

    dy = Vec(0, circle.radius)
    if Box(box.tl - dy, box.br + dy).contains(circle.center):
        if circle.center.y < box.center().y:
            delta = circle.radius - (box.tl.y - circle.center.y)
            total += Vec(0, delta)
        else:
            delta = circle.radius - (circle.center.y - box.br.y)
            total += Vec(0, -delta)

    dx = Vec(circle.radius, 0)
    if Box(box.tl - dx, box.br + dx).contains(circle.center):
        if circle.center.x < box.center().x:
            delta = circle.radius - (box.tl.x - circle.center.x)
            total += Vec(delta, 0)
        else:
            delta = circle.radius - (circle.center.x - box.br.x)
            total += Vec(-delta, 0)

    point = Circle(circle.center, 0)
    for corner in box.corners():
        if collide_circles(point, Circle(corner, circle.radius)):
            push = corner - circle.center
            total += push.normal() * (circle.radius - push.length())

    if total.length_squared() > 0:
        return total * 0.75
    else:
        return None


def collide_boxes(_b1: Box, _b2: Box) -> Vec | None:
    return None  # Not used for now, save some damn CPU cycles
