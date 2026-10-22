"""Tests for zone shapes."""

import pytest

from homeassistant.components.zone import (
    CircleZoneShape,
    ZoneShape,
    zone_shape_from_state,
)
from homeassistant.core import State

CENTER = (32.880586, -117.237564)
# Roughly 111 meters north of CENTER.
NORTH_111M = (32.881586, -117.237564)
# Distance to the antipode cannot be calculated.
ANTIPODE = (-32.880586, 62.762436)

CIRCLE = CircleZoneShape(*CENTER, 250)


def test_circle_size_and_center() -> None:
    """Test the size and center of a circle."""
    assert CIRCLE.size == 250
    assert CIRCLE.center == CENTER


@pytest.mark.parametrize(
    ("point", "expected"),
    [
        pytest.param(CENTER, -250, id="center"),
        pytest.param(NORTH_111M, pytest.approx(111 - 250, abs=1), id="inside"),
    ],
)
def test_circle_distance_to_edge(point: tuple[float, float], expected: float) -> None:
    """Test the signed distance from a point to the edge of a circle."""
    assert CIRCLE.distance_to_edge(*point) == expected


@pytest.mark.parametrize(
    ("radius", "accuracy", "expected"),
    [
        pytest.param(200, 0, True, id="inside"),
        pytest.param(100, 0, False, id="outside"),
        pytest.param(100, 20, True, id="outside_within_accuracy"),
        pytest.param(100, 5, False, id="outside_beyond_accuracy"),
    ],
)
def test_circle_contains_point(radius: float, accuracy: float, expected: bool) -> None:
    """Test whether a circle contains a point."""
    circle = CircleZoneShape(*CENTER, radius)
    assert circle.contains_point(*NORTH_111M, accuracy) is expected


@pytest.mark.parametrize(
    ("inner", "expected"),
    [
        pytest.param(CircleZoneShape(*CENTER, 100), True, id="concentric_smaller"),
        pytest.param(CircleZoneShape(*CENTER, 250), True, id="equal"),
        pytest.param(CircleZoneShape(*CENTER, 300), False, id="concentric_larger"),
        pytest.param(CircleZoneShape(*NORTH_111M, 130), True, id="offset_inside"),
        pytest.param(CircleZoneShape(*NORTH_111M, 150), False, id="offset_overlap"),
    ],
)
def test_circle_contains_shape(inner: CircleZoneShape, expected: bool) -> None:
    """Test whether a circle fully contains another circle."""
    assert CIRCLE.contains_shape(inner) is expected


def test_circle_antipode() -> None:
    """Test a point where the distance cannot be calculated."""
    assert CIRCLE.distance_to_edge(*ANTIPODE) is None
    assert CIRCLE.contains_point(*ANTIPODE) is False
    assert CIRCLE.contains_shape(CircleZoneShape(*ANTIPODE, 1)) is False


def test_circle_contains_unsupported_shape() -> None:
    """Test containment of an unsupported shape raises."""

    class OtherShape(ZoneShape):
        size = 1
        center = CENTER

        def distance_to_edge(self, latitude: float, longitude: float) -> float:
            return 0

        def contains_shape(self, other: ZoneShape) -> bool:
            return False

    with pytest.raises(NotImplementedError):
        CIRCLE.contains_shape(OtherShape())


def test_zone_shape_from_state() -> None:
    """Test building a shape from zone state attributes."""
    state = State(
        "zone.test",
        "0",
        {"latitude": CENTER[0], "longitude": CENTER[1], "radius": 250},
    )
    assert zone_shape_from_state(state) == CIRCLE


@pytest.mark.parametrize(
    "attributes",
    [
        pytest.param({"longitude": CENTER[1], "radius": 250}, id="no_latitude"),
        pytest.param({"latitude": CENTER[0], "radius": 250}, id="no_longitude"),
        pytest.param({"latitude": CENTER[0], "longitude": CENTER[1]}, id="no_radius"),
    ],
)
def test_zone_shape_from_state_missing_attributes(attributes: dict) -> None:
    """Test a zone state without shape attributes has no shape."""
    assert zone_shape_from_state(State("zone.test", "0", attributes)) is None
