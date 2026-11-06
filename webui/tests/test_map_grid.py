from __future__ import annotations

import numpy as np
import pytest

from pokeroutes.region import edit, map_grid
from pokeroutes.region.world import World

GRID_WIDTH, GRID_HEIGHT = 10, 8


def flat_world() -> World:
    size = map_grid.REGION_CELLS_PER_GRID_CELL
    width, height = GRID_WIDTH * size, GRID_HEIGHT * size
    w = World(seed=1, W=width, H=height)
    w.land = np.ones((height, width), dtype=bool)
    w.height = np.full((height, width), 0.2)
    w.landmass = np.ones((height, width), dtype=np.int8)
    w.sea = ~w.land
    w.lake = np.zeros((height, width), dtype=bool)
    w.river = np.zeros((height, width), dtype=bool)
    w.biome = np.full((height, width), "m", dtype="<U1")
    w.tiers = np.zeros((height, width), dtype=np.int8)
    return w


def painted(w: World, strokes) -> dict:
    stored_map_grid = map_grid.map_grid_of_world(w)
    marks, assignment = map_grid.marks_of_map_grid(stored_map_grid), map_grid.assignment_of_map_grid(stored_map_grid)
    for stroke in strokes:
        brush, cells = stroke[0], stroke[1]
        route_key = stroke[2] if len(stroke) > 2 else None
        marks, assignment = map_grid.painted_map(marks, assignment, cells, brush, map_grid.grid_size_of_world(w), route_key)
    merged_away = map_grid.map_from_marks(w, marks, assignment)
    return merged_away


def route_named(w: World, name: str) -> dict:
    found_route = next(route for route in w.routes if route["name"] == name)
    return found_route


def test_a_straight_chain_walks_from_one_end_to_the_other():
    chains = map_grid.route_chains({(3, 1), (1, 1), (2, 1), (4, 1)})
    assert chains == [[(1, 1), (2, 1), (3, 1), (4, 1)]]


def test_a_branch_becomes_its_own_chain_and_the_straight_run_goes_through_the_junction():
    chains = map_grid.route_chains({(1, 1), (2, 1), (3, 1), (2, 2), (2, 3)})
    assert [(1, 1), (2, 1), (3, 1)] in chains
    assert [(2, 2), (2, 3)] in chains


def test_the_route_brush_never_paints_over_a_town_but_the_town_brush_and_eraser_do():
    grid_size = (GRID_WIDTH, GRID_HEIGHT)
    marks = map_grid.with_cells_painted({}, [(1, 1)], "town", grid_size)
    marks = map_grid.with_cells_painted(marks, [(1, 1), (2, 1)], "route", grid_size)
    assert marks == {(1, 1): "t", (2, 1): "r"}
    marks = map_grid.with_cells_painted(marks, [(2, 1)], "town", grid_size)
    assert marks == {(1, 1): "t", (2, 1): "t"}
    marks = map_grid.with_cells_painted(marks, [(1, 1), (99, 99)], "erase", grid_size)
    assert marks == {(2, 1): "t"}
    with pytest.raises(map_grid.MapGridProblem):
        map_grid.with_cells_painted(marks, [(1, 1)], "lava", grid_size)


def test_neighbouring_town_cells_are_one_town_even_l_shaped():
    w = flat_world()
    painted(w, [("town", [(2, 2), (3, 2), (2, 3)]), ("town", [(7, 5)])])
    assert len(w.cities) == 2
    big_town = next(town for town in w.cities if len(town.grid_cells) == 3)
    assert big_town.size == "city"
    assert big_town.rect == [16, 16, 32, 32]


def test_a_route_between_two_towns_joins_both_and_keeps_its_cells_in_path_order():
    w = flat_world()
    painted(w, [("town", [(1, 3), (8, 3)]), ("route", [(2, 3), (3, 3), (4, 3), (4, 4), (5, 4), (6, 4), (7, 4), (7, 3)])])
    assert len(w.routes) == 1
    route = w.routes[0]
    assert route["grid_cells"] in ([[2, 3], [3, 3], [4, 3], [4, 4], [5, 4], [6, 4], [7, 4], [7, 3]],
                                   [[7, 3], [7, 4], [6, 4], [5, 4], [4, 4], [4, 3], [3, 3], [2, 3]])
    assert sorted(exit_record["to"] for exit_record in route["exits"]) == ["Town 1", "Town 2"]
    assert route["goal"] is None
    assert route["rect"] == [16, 24, 64, 40]
    first_exit_cell = route["path"][0]
    assert map_grid.grid_cell_of_region_cell(first_exit_cell, map_grid.REGION_CELLS_PER_GRID_CELL) == tuple(route["grid_cells"][0])


def test_a_route_ending_on_open_ground_is_a_dead_end_that_starts_at_its_town():
    w = flat_world()
    painted(w, [("town", [(2, 2)]), ("route", [(5, 2), (4, 2), (3, 2)])])
    route = w.routes[0]
    assert route["grid_cells"] == [[3, 2], [4, 2], [5, 2]]
    assert route["role"] == "dead_end"
    assert [exit_record["side"] for exit_record in route["exits"]] == ["west"]
    assert map_grid.grid_cell_of_region_cell(route["goal"], map_grid.REGION_CELLS_PER_GRID_CELL) == (5, 2)


def test_a_route_over_water_is_a_sea_route_and_a_little_water_stays_a_crossing():
    w = flat_world()
    size = map_grid.REGION_CELLS_PER_GRID_CELL
    w.land[:, 4 * size:7 * size] = False
    painted(w, [("town", [(1, 1)]), ("route", [(column, 1) for column in range(2, 7)]), ("route", [(column, 3) for column in range(2, 5)])])
    mostly_sea = next(route for route in w.routes if [2, 1] in route["grid_cells"])
    mostly_land = next(route for route in w.routes if [2, 3] in route["grid_cells"])
    assert mostly_sea["mode"] == "sea" and mostly_sea["role"] == "sea"
    assert mostly_land["mode"] == "land"


def test_strokes_side_by_side_stay_separate_routes_and_a_stroke_from_a_route_extends_it():
    w = flat_world()
    painted(w, [("route", [(1, 1), (2, 1), (3, 1)]), ("route", [(1, 2), (2, 2), (3, 2)])])
    assert sorted(len(route["grid_cells"]) for route in w.routes) == [3, 3]
    painted(w, [("route", [(3, 1), (4, 1), (5, 1)])])
    assert sorted(len(route["grid_cells"]) for route in w.routes) == [3, 5]
    top_route = next(route for route in w.routes if [1, 1] in route["grid_cells"])
    assert top_route["grid_cells"][-1] == [5, 1] or top_route["grid_cells"][0] == [5, 1]


def test_a_tile_can_be_handed_to_another_route_and_a_cut_route_splits_in_two():
    w = flat_world()
    painted(w, [("route", [(1, 1), (2, 1), (3, 1), (4, 1), (5, 1)]), ("route", [(3, 2), (3, 3)])])
    lower = next(route for route in w.routes if [3, 3] in route["grid_cells"])
    painted(w, [("route", [(3, 1)], lower["grid_key"])])
    assert len(w.routes) == 3
    lower_now = next(route for route in w.routes if route["grid_key"] == lower["grid_key"])
    assert sorted(lower_now["grid_cells"]) == [[3, 1], [3, 2], [3, 3]]
    assert lower_now["name"] == lower["name"]
    painted(w, [("route", [(6, 6)], "new")])
    assert len(w.routes) == 4


def test_touching_routes_are_joined_and_repainting_keeps_names():
    w = flat_world()
    painted(w, [("town", [(1, 2), (8, 2)]), ("route", [(column, 2) for column in range(2, 8)]), ("route", [(4, 3), (4, 4), (4, 5)])])
    branch = next(route for route in w.routes if [4, 5] in route["grid_cells"])
    trunk = next(route for route in w.routes if [2, 2] in route["grid_cells"])
    assert [exit_record["to"] for exit_record in branch["exits"]] == [trunk["name"]]
    assert branch["name"] in [exit_record["to"] for exit_record in trunk["exits"]]
    route_named(w, trunk["name"])["spec_overrides"] = {"tall_grass": 0.7}
    painted(w, [("route", [(5, 3)], trunk["grid_key"])])
    assert route_named(w, trunk["name"])["spec_overrides"] == {"tall_grass": 0.7}
    assert [5, 3] in route_named(w, trunk["name"])["grid_cells"]


def test_merging_towns_keeps_the_bigger_name_and_reports_the_lost_one():
    w = flat_world()
    painted(w, [("town", [(2, 2), (3, 2)]), ("town", [(5, 2)])])
    merged_away = painted(w, [("town", [(2, 2), (3, 2), (5, 2), (4, 2)])])
    assert [town.name for town in w.cities] == ["Town 1"]
    assert merged_away == {"Town 2": "Town 1"}


def test_paint_map_through_the_edit_api_derives_specs_with_grid_cells():
    doc = edit.generate_doc(3)
    world = edit.doc_to_world(doc)
    size = map_grid.REGION_CELLS_PER_GRID_CELL
    dry = [(column, row) for row in range(1, world.H // size - 1) for column in range(1, world.W // size - 5)
           if all(world.land[row * size:(row + 1) * size, (column + step) * size:(column + step + 1) * size].all() for step in range(5))]
    column, row = dry[0]
    doc = edit.paint_map(doc, [[column, row], [column + 4, row]], "town")
    doc = edit.paint_map(doc, [[column + step, row] for step in range(1, 4)], "route")
    assert len(doc["cities"]) == 2 and len(doc["routes"]) == 1
    route = doc["routes"][0]
    assert route["grid_cells"] == [[column + step, row] for step in range(1, 4)] or \
        route["grid_cells"] == [[column + step, row] for step in range(3, 0, -1)]
    assert "error" not in route["spec"] and len(route["spec"]["exits"]) == 2
    assert edit.full_spec(doc, route["id"])["region"]["grid_cells"] == route["grid_cells"]
    assert doc["map_grid"]["rows"][row][column:column + 5] == "trrrt"
    erased = edit.paint_map(doc, [[column + 2, row]], "erase")
    assert len(erased["routes"]) == 2


def test_generate_map_paints_towns_and_routes_on_the_grid():
    doc = edit.generate_map(edit.generate_doc(1))
    marks = map_grid.marks_of_map_grid(doc["map_grid"])
    assert len(doc["cities"]) >= 4 and doc["routes"]
    for town in doc["cities"]:
        assert town["grid_cells"] and all(marks[tuple(cell)] == "t" for cell in town["grid_cells"])
    for route in doc["routes"]:
        cells = [tuple(cell) for cell in route["grid_cells"]]
        assert cells and all(marks[cell] == "r" for cell in cells)
        assert all(abs(cell[0] - following[0]) + abs(cell[1] - following[1]) == 1 for cell, following in zip(cells, cells[1:]))
        assert "error" not in route["spec"]


def test_loading_a_document_from_before_the_grid_snaps_towns_and_routes_onto_it(tmp_path):
    doc = edit.generate_map(edit.generate_doc(1))
    doc.pop("map_grid")
    for town in doc["cities"]:
        town.pop("grid_cells")
    for route in doc["routes"]:
        route.pop("grid_cells")
    edit.save(doc, tmp_path / "old")
    loaded = edit.load(tmp_path / "old" / "region.json")
    assert loaded["map_grid"]["rows"]
    assert all(town["grid_cells"] for town in loaded["cities"])
    assert len(loaded["cities"]) == len(doc["cities"])


def test_changing_the_grid_size_snaps_towns_and_routes_and_keeps_their_names():
    doc = edit.generate_map(edit.generate_doc(1))
    names_before = sorted(town["name"] for town in doc["cities"])
    finer = edit.regrid_map(doc, 4)
    assert finer["map_grid"]["cell"] == 4
    assert finer["map_grid"]["size"] == [finer["size"][0] // 4, finer["size"][1] // 4]
    assert sorted(town["name"] for town in finer["cities"]) == names_before
    assert all(len(town["grid_cells"]) == 4 for town in finer["cities"])
    assert finer["routes"] and all("error" not in route["spec"] for route in finer["routes"])
    for route in finer["routes"]:
        cells = [tuple(cell) for cell in route["grid_cells"]]
        assert all(abs(cell[0] - following[0]) + abs(cell[1] - following[1]) == 1 for cell, following in zip(cells, cells[1:]))
    painted = edit.paint_map(finer, [[1, 1]], "town")
    assert painted["map_grid"]["cell"] == 4
    with pytest.raises(map_grid.MapGridProblem):
        edit.regrid_map(doc, 5)


def test_a_finer_grid_keeps_every_route_whole_and_joined():
    doc = edit.generate_map(edit.generate_doc(3))
    finer = edit.regrid_map(doc, 4)
    assert len(finer["routes"]) == len(doc["routes"])
    for route in finer["routes"]:
        assert len(map_grid.connected_groups(tuple(cell) for cell in route["grid_cells"])) == 1
    exits_before = {(route["name"], exit_record["to"]) for route in doc["routes"] for exit_record in route["exits"]}
    exits_after = {(route["name"], exit_record["to"]) for route in finer["routes"] for exit_record in route["exits"]}
    assert exits_before <= exits_after
