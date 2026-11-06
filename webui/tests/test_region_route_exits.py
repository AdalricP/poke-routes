"""Every region route has exactly as many exits as it has network connections, all the
way from the region network through the exported spec to the generated map. A dead
end has one exit and ends in a dressed clearing with an item.

    python3 -m pytest -q webui/tests/test_region_route_exits.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeroutes.generate import generate
from pokeroutes.region import edit
from pokeroutes.spec import Exit, RouteSpec


def land_cell_far_from(world, taken, distance):
    rows, columns = np.nonzero(world.land & ~world.lake)
    for row, column in zip(rows.tolist(), columns.tolist()):
        if 10 <= column < world.W - 10 and 10 <= row < world.H - 10 and \
                all(abs(column - x) + abs(row - y) >= distance for x, y in taken):
            return column, row
    raise AssertionError("no free land")


def region_with_four_towns():
    doc = edit.generate_doc(11, geography_kind="continent")
    world = edit.doc_to_world(doc)
    taken = []
    for _ in range(4):
        cell = land_cell_far_from(world, taken, 30)
        taken.append(cell)
        doc = edit.add_town(doc, *cell)
    return edit.recompute(doc, ["routes"])


def route_spec_object(spec_dict) -> RouteSpec:
    fields = {k: v for k, v in spec_dict.items() if k in RouteSpec.__dataclass_fields__}
    fields["exits"] = [Exit(**{k: v for k, v in e.items() if k in Exit.__dataclass_fields__}) for e in spec_dict["exits"]]
    return RouteSpec(**fields)


def generated_exit_count(doc, route) -> int:
    spec = route_spec_object(edit.full_spec(doc, route["id"]))
    return len(generate(spec).exits)


@pytest.fixture(scope="module")
def hand_placed_region():
    return region_with_four_towns()


@pytest.fixture(scope="module")
def arklarna_region():
    return edit.generate_doc(1, preset="arklarna")


def test_routes_between_hand_placed_towns_have_one_exit_per_connection(hand_placed_region):
    routes = hand_placed_region["routes"]
    assert len(routes) >= 3
    for route in routes:
        connections = len(route["exits"])
        assert len(route["spec"]["exits"]) == connections, route["name"]
        assert generated_exit_count(hand_placed_region, route) == connections, route["name"]


def test_every_arklarna_route_has_one_exit_per_connection(arklarna_region):
    for route in arklarna_region["routes"]:
        connections = len(route["exits"])
        assert len(route["spec"]["exits"]) == connections, route["name"]
        assert generated_exit_count(arklarna_region, route) == connections, route["name"]


def test_dead_ends_have_one_exit_and_something_at_the_end(arklarna_region):
    dead_ends = [route for route in arklarna_region["routes"] if route["role"] == "dead_end"]
    assert dead_ends
    for route in dead_ends:
        assert len(route["exits"]) == 1
        generated = generate(route_spec_object(edit.full_spec(arklarna_region, route["id"])))
        assert len(generated.exits) == 1
        goal = next(zone for zone in generated.zones if zone.name == "goal")
        assert goal.meta.get("purpose", {}).get("goal") is True
        goal_cells = set(map(tuple, goal.cells))
        rewards = [event for event in generated.events
                   if event.kind == "item_ball" and (event.y, event.x) in goal_cells]
        assert rewards, route["name"]


def test_web_ui_keeps_a_single_exit():
    import server

    spec = server.spec_from({"name": "Spur", "exits": [{"side": "north", "at": 0.4, "to": "MAP_ROUTE2"}]})
    assert [(e.side, e.to) for e in spec.exits] == [("north", "MAP_ROUTE2")]
    assert len(server.spec_from({"name": "Blank"}).exits) == 2
