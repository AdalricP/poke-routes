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
from pokeroutes.region import edit, map_grid
from pokeroutes.spec import Exit, RouteSpec


def is_dry_grid_cell(world, cell) -> bool:
    size = map_grid.REGION_CELLS_PER_GRID_CELL
    column, row = cell
    block = world.land[row * size:(row + 1) * size, column * size:(column + 1) * size] & \
        ~world.lake[row * size:(row + 1) * size, column * size:(column + 1) * size]
    return bool(block.size) and bool(block.all())


def dry_run_of_grid_cells(world, length):
    grid_width, grid_height = map_grid.grid_size_of_world(world)
    for row in range(1, grid_height - 1):
        for column in range(1, grid_width - length):
            run = [(column + step, row) for step in range(length)]
            if all(is_dry_grid_cell(world, cell) for cell in run):
                return run
    raise AssertionError("no dry run of grid cells")


def region_with_hand_painted_towns_and_routes():
    doc = edit.generate_doc(11, geography_kind="continent")
    run = dry_run_of_grid_cells(edit.doc_to_world(doc), 7)
    doc = edit.paint_map(doc, [run[0], run[3], run[6]], "town")
    doc = edit.paint_map(doc, run[1:3] + run[4:6], "route")
    return doc


def route_spec_object(spec_dict) -> RouteSpec:
    fields = {k: v for k, v in spec_dict.items() if k in RouteSpec.__dataclass_fields__}
    fields["exits"] = [Exit(**{k: v for k, v in e.items() if k in Exit.__dataclass_fields__}) for e in spec_dict["exits"]]
    return RouteSpec(**fields)


def generated_exit_count(doc, route) -> int:
    spec = route_spec_object(edit.full_spec(doc, route["id"]))
    return len(generate(spec).exits)


@pytest.fixture(scope="module")
def hand_placed_region():
    return region_with_hand_painted_towns_and_routes()


DEAD_END_LENGTH_IN_GRID_CELLS = 2


def is_open_dry_grid_cell(world, marks, cell) -> bool:
    grid_width, grid_height = map_grid.grid_size_of_world(world)
    inside = 0 < cell[0] < grid_width - 1 and 0 < cell[1] < grid_height - 1
    return inside and is_dry_grid_cell(world, cell) and not any(neighbour in marks for neighbour in map_grid.neighbours_of(cell) + [cell])


def dead_end_from_a_town(doc):
    world = edit.doc_to_world(doc)
    marks = map_grid.marks_of_map_grid(doc["map_grid"])
    for town in doc["cities"]:
        for column, row in town["grid_cells"]:
            for step_x, step_y in map_grid.STEPS:
                line = [(column + step_x * distance, row + step_y * distance) for distance in range(1, DEAD_END_LENGTH_IN_GRID_CELLS + 1)]
                if all(is_open_dry_grid_cell(world, {k: v for k, v in marks.items() if k != (column, row)}, cell) for cell in line[1:]) and \
                        is_dry_grid_cell(world, line[0]) and line[0] not in marks:
                    return [list(cell) for cell in line]
    raise AssertionError("no open ground for a dead end next to any town")


@pytest.fixture(scope="module")
def generated_region():
    doc = edit.generate_map(edit.generate_doc(1))
    return edit.paint_map(doc, dead_end_from_a_town(doc), "route")


def test_routes_between_hand_placed_towns_have_one_exit_per_connection(hand_placed_region):
    routes = hand_placed_region["routes"]
    assert len(routes) >= 2
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
