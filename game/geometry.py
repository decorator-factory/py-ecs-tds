from __future__ import annotations

from dataclasses import dataclass
from math import sqrt


@dataclass(frozen=True, slots=True)
class Vec:
    x: float
    y: float

    def __add__(self, other: Vec) -> Vec:
        return Vec(self.x + other.x, self.y + other.y)

    def __sub__(self, other: Vec) -> Vec:
        return Vec(self.x - other.x, self.y - other.y)

    def __mul__(self, other: float) -> Vec:
        return Vec(self.x * other, self.y * other)

    def __neg__(self) -> Vec:
        return Vec(-self.x, -self.y)

    def length_squared(self) -> float:
        return self.x**2 + self.y**2

    def length(self) -> float:
        return sqrt(self.x**2 + self.y**2)

    def normal(self) -> Vec:
        if self.x == self.y == 0:
            return self
        return self * (1 / sqrt(self.x**2 + self.y**2))

    def __str__(self) -> str:
        return f"<{self.x:.2f}; {self.y:.2f}>"


class Box:
    __slots__ = ("_tl", "_br")
    __match_args__ = ("tl", "br")

    def __init__(self, p1: Vec, p2: Vec, /) -> None:
        self._tl = Vec(min(p1.x, p2.x), min(p1.y, p2.y))
        self._br = Vec(max(p1.x, p2.x), max(p1.y, p2.y))

    @property
    def tl(self) -> Vec:
        """Top-left corner"""
        return self._tl

    @property
    def br(self) -> Vec:
        """Bottom-right corner"""
        return self._br

    @property
    def tr(self) -> Vec:
        """Top-right corner"""
        return Vec(self._br.x, self._tl.y)

    @property
    def bl(self) -> Vec:
        """Bottom-left corner"""
        return Vec(self._tl.x, self._br.y)

    def corners(self) -> tuple[Vec, Vec, Vec, Vec]:
        return (self._tl, self._br, self.tr, self.bl)

    def center(self) -> Vec:
        return (self._tl + self._br) * 0.5

    def size(self) -> Vec:
        return Vec(self.width(), self.height())

    def width(self) -> float:
        return self._br.x - self._tl.x

    def height(self) -> float:
        return self._br.y - self._tl.y

    def shift(self, vec: Vec) -> Box:
        return Box(self._tl + vec, self._br + vec)

    def contains(self, vec: Vec) -> bool:
        return self._tl.x <= vec.x <= self._br.x and self._tl.y <= vec.y <= self._br.y

    def __repr__(self) -> str:
        return f"Box({self._tl!r}, {self._br!r})"

    def __str__(self) -> str:
        return f"Box({self._tl}, {self._br})"


@dataclass(frozen=True)
class Circle:
    center: Vec
    radius: float

    def bbox(self) -> Box:
        delta = Vec(self.radius / 2, self.radius / 2)
        return Box(self.center - delta, self.center + delta)

    def shift(self, vec: Vec) -> Circle:
        return Circle(self.center + vec, self.radius)

    def __post_init__(self) -> None:
        assert self.radius >= 0.0
