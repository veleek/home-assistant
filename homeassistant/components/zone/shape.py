"""Shapes describing the area covered by a zone."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import override

from homeassistant.const import EntityStateAttribute
from homeassistant.core import State
from homeassistant.util.location import distance

from .const import ZoneEntityStateAttribute


class ZoneShape(ABC):
    """The area covered by a zone.

    All distances are in meters.
    """

    __slots__ = ()

    @property
    @abstractmethod
    def size(self) -> float:
        """Return the size used to rank overlapping zones, smallest first."""

    @property
    @abstractmethod
    def center(self) -> tuple[float, float]:
        """Return the latitude and longitude of the center of the shape."""

    def distance_to_center(self, latitude: float, longitude: float) -> float | None:
        """Return the distance from a point to the center of the shape."""
        return distance(latitude, longitude, *self.center)

    @abstractmethod
    def distance_to_edge(self, latitude: float, longitude: float) -> float | None:
        """Return the distance from a point to the edge of the shape.

        The distance is negative when the point is inside the shape.
        """

    @abstractmethod
    def contains_shape(self, other: ZoneShape) -> bool:
        """Return if the other shape is fully inside this shape.

        A shape whose edge touches this shape's edge from the inside counts as
        contained.
        """

    def contains_point(
        self, latitude: float, longitude: float, accuracy: float = 0
    ) -> bool:
        """Return if a point is inside the shape.

        A point with an accuracy radius is inside the shape if any part of the
        accuracy circle overlaps the shape.
        """
        edge_distance = self.distance_to_edge(latitude, longitude)
        return edge_distance is not None and edge_distance < accuracy


@dataclass(frozen=True, slots=True)
class CircleZoneShape(ZoneShape):
    """A circular zone defined by a center and a radius."""

    latitude: float
    longitude: float
    radius: float

    @property
    @override
    def size(self) -> float:
        """Return the radius of the circle."""
        return self.radius

    @property
    @override
    def center(self) -> tuple[float, float]:
        """Return the latitude and longitude of the center of the circle."""
        return (self.latitude, self.longitude)

    @override
    def distance_to_edge(self, latitude: float, longitude: float) -> float | None:
        """Return the distance from a point to the edge of the circle."""
        if (center_distance := self.distance_to_center(latitude, longitude)) is None:
            return None
        return center_distance - self.radius

    @override
    def contains_shape(self, other: ZoneShape) -> bool:
        """Return if the other shape is fully inside this circle."""
        if not isinstance(other, CircleZoneShape):
            raise NotImplementedError(
                f"Containment of {type(other).__name__} is not supported"
            )
        if (
            center_distance := self.distance_to_center(other.latitude, other.longitude)
        ) is None:
            return False
        return center_distance + other.radius <= self.radius


def zone_shape_from_state(state: State) -> ZoneShape | None:
    """Return the shape of a zone from its state, or None if it has no shape."""
    attrs = state.attributes
    latitude = attrs.get(EntityStateAttribute.LATITUDE)
    longitude = attrs.get(EntityStateAttribute.LONGITUDE)
    radius = attrs.get(ZoneEntityStateAttribute.RADIUS)
    if latitude is None or longitude is None or radius is None:
        return None
    return CircleZoneShape(latitude, longitude, radius)
