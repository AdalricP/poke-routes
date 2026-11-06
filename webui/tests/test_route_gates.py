from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

from pokeroutes.export import seal_edges, story_gate_export
from pokeroutes.gates import gate_kinds_in_build_order, requested_route_gate
from pokeroutes.generate import WALKABLE, generate, reachable
from pokeroutes.region import edit
from pokeroutes.region.gates import choose_gated_entrance
from pokeroutes.spec import RouteSpec

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_SPEC_DIRECTORY = REPOSITORY_ROOT / "examples"
WEB_UI_DIRECTORY = REPOSITORY_ROOT / "webui"
OBSTACLE_EVENT_KIND_FOR_GATE_KIND = {"cut": "cut_tree", "rock_smash": "rock_smash", "strength": "strength_boulder",
                                     "story": "story_blocker"}
LEDGE_JUMP_DIRECTION_BY_CLASS = {"s": (1, 0), "e": (0, 1), "x": (0, -1)}
STEPS_IN_FOUR_DIRECTIONS = ((-1, 0), (1, 0), (0, -1), (0, 1))
GATED_EXAMPLE_CASES = [
    (example_name, gate_kind, entrance_index)
    for example_name in ("bernsteinweg", "erlenbruch", "kiefernheide")
    for gate_kind in ("cut", "rock_smash", "strength", "surf", "waterfall", "story")
    for entrance_index in (0, 1)
]


def gate_request_for_kind(gate_kind: str, entrance_index: int) -> dict:
    if gate_kind == "story":
        story_gate_request = {"after": 2, "after_beat": "the second badge", "entrance": entrance_index}
        return story_gate_request
    field_move_gate_request = {"needs": gate_kind, "entrance": entrance_index}
    return field_move_gate_request


def gated_example_route(example_name: str, gate_request: dict):
    spec = RouteSpec.load(EXAMPLE_SPEC_DIRECTORY / f"{example_name}.yaml")
    spec.gate = dict(gate_request)
    gated_route = generate(spec)
    return gated_route


def grid_with_obstacles_standing(route, passable_obstacle_kinds, can_surf: bool, can_climb_waterfalls: bool) -> np.ndarray:
    grid = np.array(route.cls, copy=True)
    for event in route.events:
        if event.kind in OBSTACLE_EVENT_KIND_FOR_GATE_KIND.values() and event.kind not in passable_obstacle_kinds:
            grid[event.y, event.x] = "f"
    if can_surf and can_climb_waterfalls:
        grid[grid == "a"] = "p"
    if can_surf:
        grid[np.isin(grid, ["w", "b"])] = "p"
    return grid


def flood_with_every_ledge_direction(grid: np.ndarray, start_cells) -> set:
    reached_cells = {cell for cell in start_cells if grid[cell] in WALKABLE}
    stack = list(reached_cells)
    while stack:
        cell_y, cell_x = stack.pop()
        for step_y, step_x in STEPS_IN_FOUR_DIRECTIONS:
            next_cell = (cell_y + step_y, cell_x + step_x)
            if not (0 <= next_cell[0] < grid.shape[0] and 0 <= next_cell[1] < grid.shape[1]):
                continue
            if LEDGE_JUMP_DIRECTION_BY_CLASS.get(grid[next_cell]) == (step_y, step_x):
                next_cell = (next_cell[0] + step_y, next_cell[1] + step_x)
                if not (0 <= next_cell[0] < grid.shape[0] and 0 <= next_cell[1] < grid.shape[1]):
                    continue
            if next_cell in reached_cells or grid[next_cell] not in WALKABLE:
                continue
            reached_cells.add(next_cell)
            stack.append(next_cell)
    return reached_cells


def entrance_and_far_exit_cells(route, entrance_index: int):
    entrance_cells = route.exits[entrance_index][1]
    far_exit_cells = [cell for position, (_, cells) in enumerate(route.exits) if position != entrance_index for cell in cells]
    return entrance_cells, far_exit_cells


def gap_side_cells(gap_cell, path_step):
    lateral_step = (abs(path_step[1]), abs(path_step[0]))
    side_cells = [(gap_cell[0] + lateral_step[0], gap_cell[1] + lateral_step[1]),
                  (gap_cell[0] - lateral_step[0], gap_cell[1] - lateral_step[1])]
    return side_cells


@pytest.mark.parametrize("example_name,gate_kind,entrance_index", GATED_EXAMPLE_CASES)
def test_gate_blocks_the_main_path_until_the_move_is_allowed(example_name, gate_kind, entrance_index):
    gated_route = gated_example_route(example_name, gate_request_for_kind(gate_kind, entrance_index))
    gate_record = gated_route.gates[0]
    entrance_cells, far_exit_cells = entrance_and_far_exit_cells(gated_route, entrance_index)
    every_obstacle_kind = set(OBSTACLE_EVENT_KIND_FOR_GATE_KIND.values())
    obstacle_kind = OBSTACLE_EVENT_KIND_FOR_GATE_KIND.get(gate_kind)

    assert gate_record["built"], gate_record["note"]
    without_the_move = grid_with_obstacles_standing(
        gated_route, every_obstacle_kind - {obstacle_kind}, can_surf=gate_kind != "surf", can_climb_waterfalls=gate_kind not in ("surf", "waterfall"))
    reached_without_the_move = flood_with_every_ledge_direction(without_the_move, entrance_cells)
    assert not reached_without_the_move & set(far_exit_cells)

    with_every_move = grid_with_obstacles_standing(gated_route, every_obstacle_kind, can_surf=True, can_climb_waterfalls=True)
    reached_with_the_move = reachable(with_every_move, entrance_cells)
    assert any(reached_with_the_move[cell] for cell in far_exit_cells)


@pytest.mark.parametrize("gate_kind", ["cut", "rock_smash", "strength", "story"])
def test_gap_obstacle_stands_in_a_one_wide_gap(gate_kind):
    gated_route = gated_example_route("bernsteinweg", gate_request_for_kind(gate_kind, 0))
    gate_record = gated_route.gates[0]
    gap_cell = tuple(gate_record["cell"])
    cell_after_gap = tuple(gate_record["cells"][1])
    path_step = (cell_after_gap[0] - gap_cell[0], cell_after_gap[1] - gap_cell[1])
    gate_events = [event for event in gated_route.events if event.data.get("route_gate")]

    assert [(event.kind, event.y, event.x) for event in gate_events] == [(OBSTACLE_EVENT_KIND_FOR_GATE_KIND[gate_kind], *gap_cell)]
    assert all(gated_route.cls[cell] not in WALKABLE for cell in gap_side_cells(gap_cell, path_step))


def test_strength_boulder_can_be_pushed_out_of_the_way():
    gated_route = gated_example_route("erlenbruch", gate_request_for_kind("strength", 0))

    assert gated_route.gates[0]["pushes"] >= 1


def test_water_gates_lay_water_across_the_path():
    surf_route = gated_example_route("bernsteinweg", gate_request_for_kind("surf", 1))
    waterfall_route = gated_example_route("bernsteinweg", gate_request_for_kind("waterfall", 0))
    surf_classes = {surf_route.cls[tuple(cell)] for cell in surf_route.gates[0]["cells"]}
    waterfall_classes = {waterfall_route.cls[tuple(cell)] for cell in waterfall_route.gates[0]["cells"]}

    assert surf_classes == {"w"}
    assert waterfall_classes == {"w", "a"}


@pytest.mark.parametrize("gate_kind", ["surf", "waterfall"])
def test_sealing_the_edges_keeps_the_far_side_of_a_water_gate(gate_kind):
    gated_route = gated_example_route("bernsteinweg", gate_request_for_kind(gate_kind, 0))
    walkable_cells_before_sealing = int(np.isin(gated_route.cls, list(WALKABLE)).sum())
    seal_edges(gated_route)
    entrance_cells, far_exit_cells = entrance_and_far_exit_cells(gated_route, 0)
    with_every_move = grid_with_obstacles_standing(gated_route, set(OBSTACLE_EVENT_KIND_FOR_GATE_KIND.values()),
                                                   can_surf=True, can_climb_waterfalls=True)

    assert int(np.isin(gated_route.cls, list(WALKABLE)).sum()) > walkable_cells_before_sealing // 2
    assert any(reachable(with_every_move, entrance_cells)[cell] for cell in far_exit_cells)


def test_dive_gate_is_reported_as_not_supported_and_changes_nothing():
    open_route = generate(RouteSpec.load(EXAMPLE_SPEC_DIRECTORY / "bernsteinweg.yaml"))
    dive_route = gated_example_route("bernsteinweg", {"needs": "dive"})

    assert dive_route.gates[0]["built"] is False
    assert dive_route.gates[0]["supported"] is False
    assert "not supported yet" in dive_route.gates[0]["note"]
    assert np.array_equal(open_route.cls, dive_route.cls)


def test_needs_and_story_gate_both_get_built():
    gated_route = gated_example_route("erlenbruch", {"needs": "cut", "after": 0, "after_beat": "the first badge"})
    gate_event_kinds = sorted(event.kind for event in gated_route.events if event.data.get("route_gate"))

    assert [gate_record["built"] for gate_record in gated_route.gates] == [True, True]
    assert gate_event_kinds == ["cut_tree", "story_blocker"]


def test_gate_request_is_read_from_the_region_block_too():
    spec = RouteSpec.load(EXAMPLE_SPEC_DIRECTORY / "bernsteinweg.yaml")
    spec.region = {"gate": {"needs": "Rock Smash", "after": 1}}

    assert gate_kinds_in_build_order(requested_route_gate(spec)) == ["story", "rock_smash"]


def test_story_gate_exports_an_object_with_a_flag_and_a_script_note():
    gated_route = gated_example_route("bernsteinweg", gate_request_for_kind("story", 0))
    story_blocker = next(event for event in gated_route.events if event.kind == "story_blocker")
    story_gate_object, story_gate_flag, story_gate_script = story_gate_export("Bernsteinweg", "BERNSTEINWEG", story_blocker)

    assert story_gate_object["flag"] == story_gate_flag == "FLAG_ARK_BERNSTEINWEG_STORY_GATE_OPEN"
    assert (story_gate_object["x"], story_gate_object["y"]) == (story_blocker.x, story_blocker.y)
    assert "moves after the second badge" in story_gate_script


def region_route(name: str, *neighbour_names: str, gate=None) -> dict:
    route = {"name": name, "mode": "land", "exits": [{"to": neighbour_name} for neighbour_name in neighbour_names]}
    if gate:
        route["gate"] = gate
    return route


def test_entrance_faces_the_side_you_reach_first_in_the_story():
    routes = [region_route("Route 1", "Alpha", "Beta"),
              region_route("Route 2", "Beta", "Gamma", gate={"needs": "cut"}),
              region_route("Route 3", "Alpha", "Gamma", gate={"after": 1})]
    story_beats = [{"name": "Start", "at": "Alpha", "unlocks": ""}, {"name": "Badge", "at": "Beta", "unlocks": "cut"}]
    gated_route = routes[1]

    gated_entrance = choose_gated_entrance(routes, story_beats, "Alpha", gated_route, gated_route["exits"])

    assert gated_entrance["entrance"] == 0
    assert gated_entrance["entrance_toward"] == "Beta"
    assert "new game" in gated_entrance["entrance_rule"]


def test_entrance_falls_back_to_the_side_closest_to_the_first_town():
    routes = [region_route("Route 1", "Alpha", "Beta"),
              region_route("Route 2", "Gamma", "Beta", gate={"needs": "surf"}),
              region_route("Route 4", "Gamma", "Delta", gate={"needs": "dive"})]

    gated_entrance = choose_gated_entrance(routes, [], "Alpha", routes[1], routes[1]["exits"])

    assert gated_entrance["entrance"] == 1
    assert gated_entrance["entrance_toward"] == "Beta"


@pytest.fixture(scope="module")
def gated_region_document():
    region_document = edit.generate_doc(3, "arklarna")
    region_document["story"] = [{"name": "Leave home", "at": "Pilsau", "unlocks": ""},
                                {"name": "Strength from the old man", "at": "Kaup", "unlocks": "strength"}]
    gated_route = next(route for route in region_document["routes"] if route["name"] == "Route 2")
    gated_route["gate"] = {"needs": "strength", "after": 1}
    gated_route["pinned"] = True
    return region_document


def test_full_spec_carries_the_gate_and_its_entrance_into_the_region_block(gated_region_document):
    gated_route = next(route for route in gated_region_document["routes"] if route["name"] == "Route 2")
    full_spec = edit.full_spec(gated_region_document, gated_route["id"])
    gate_block = full_spec["gate"]
    entrance_exit = full_spec["exits"][gate_block["entrance"]]
    region_exit_toward_entrance = next(exit_record for exit_record in gated_route["exits"] if exit_record["to"] == gate_block["entrance_toward"])

    assert gate_block["needs"] == "strength"
    assert gate_block["after_beat"] == "Strength from the old man"
    assert gate_block["entrance_toward"] == "Tvangsburg"
    assert entrance_exit["to"] == (region_exit_toward_entrance["to_map"] or "")
    assert full_spec["region"]["gate"] == gate_block


def test_recomputing_routes_keeps_the_gate(gated_region_document):
    recomputed_document = edit.recompute(gated_region_document, ["routes"])
    recomputed_route = next(route for route in recomputed_document["routes"] if route["name"] == "Route 2")

    assert recomputed_route["gate"] == {"needs": "strength", "after": 1}
    assert recomputed_route["spec"]["gate"]["entrance_toward"] == "Tvangsburg"


def test_scene_generate_path_builds_the_gate_from_the_region_block(gated_region_document):
    sys.path.insert(0, str(WEB_UI_DIRECTORY))
    import server

    gated_route = next(route for route in gated_region_document["routes"] if route["name"] == "Route 2")
    full_spec = edit.full_spec(gated_region_document, gated_route["id"])
    bench_parameters = {"name": full_spec["name"], "seed": full_spec["seed"], "biome": full_spec["biome"],
                        "width": full_spec["size"][0], "height": full_spec["size"][1],
                        "exits": full_spec["exits"], "region": full_spec["region"]}
    scene_route = generate(server.spec_from(bench_parameters))

    assert [gate_record["kind"] for gate_record in scene_route.gates] == ["story", "strength"]
    assert all(gate_record["built"] for gate_record in scene_route.gates)
    assert {gate_record["toward"] for gate_record in scene_route.gates} == {"Tvangsburg"}
