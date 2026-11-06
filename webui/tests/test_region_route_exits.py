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


DEAD_END_LENGTH_IN_CELLS = 18


def is_open_dry_ground(doc, world, cell) -> bool:
    x, y = cell
    if not (3 <= x < world.W - 3 and 3 <= y < world.H - 3) or not world.land[y, x] or world.lake[y, x]:
        return False
    rects = [town["rect"] for town in doc["cities"]] + [route["rect"] for route in doc["routes"]]
    return not any(x0 - 1 <= x < x1 + 1 and y0 - 1 <= y < y1 + 1 for x0, y0, x1, y1 in rects)


def dead_end_line_from_a_town(doc):
    world = edit.doc_to_world(doc)
    for town in doc["cities"]:
        x0, y0, x1, y1 = town["rect"]
        centre = ((x0 + x1) // 2, (y0 + y1) // 2)
        for step_x, step_y in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            edge_distance = (x1 - x0) // 2 + 2 if step_x else (y1 - y0) // 2 + 2
            line = [(centre[0] + step_x * distance, centre[1] + step_y * distance)
                    for distance in range(edge_distance, edge_distance + DEAD_END_LENGTH_IN_CELLS)]
            if all(is_open_dry_ground(doc, world, cell) for cell in line):
                return [list(centre), list(line[-1])]
    raise AssertionError("no open ground for a dead end next to any town")


@pytest.fixture(scope="module")
def generated_region():
    doc = edit.generate_map(edit.generate_doc(1))
    return edit.draw_route(doc, dead_end_line_from_a_town(doc))


def test_routes_between_hand_placed_towns_have_one_exit_per_connection(hand_placed_region):
    routes = hand_placed_region["routes"]
    assert len(routes) >= 3
    for route in routes:
        connections = len(route["exits"])
        assert len(route["spec"]["exits"]) == connections, route["name"]
        assert generated_exit_count(hand_placed_region, route) == connections, route["name"]


def test_every_generated_map_route_has_one_exit_per_connection(generated_region):
    for route in generated_region["routes"]:
        connections = len(route["exits"])
        assert len(route["spec"]["exits"]) == connections, route["name"]
        assert generated_exit_count(generated_region, route) == connections, route["name"]


def test_dead_ends_have_one_exit_and_something_at_the_end(generated_region):
    dead_ends = [route for route in generated_region["routes"] if route["role"] == "dead_end"]
    assert dead_ends
    for route in dead_ends:
        assert len(route["exits"]) == 1
        generated = generate(route_spec_object(edit.full_spec(generated_region, route["id"])))
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
