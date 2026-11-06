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
    marks = {}
    for brush, cells in strokes:
        marks = map_grid.with_cells_painted(marks, cells, brush, map_grid.grid_size_of_world(w))
    merged_away = map_grid.map_from_marks(w, marks)
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
    assert map_grid.grid_cell_of_region_cell(first_exit_cell) == tuple(route["grid_cells"][0])


def test_a_route_ending_on_open_ground_is_a_dead_end_that_starts_at_its_town():
    w = flat_world()
    painted(w, [("town", [(2, 2)]), ("route", [(5, 2), (4, 2), (3, 2)])])
    route = w.routes[0]
    assert route["grid_cells"] == [[3, 2], [4, 2], [5, 2]]
    assert route["role"] == "dead_end"
    assert [exit_record["side"] for exit_record in route["exits"]] == ["west"]
    assert map_grid.grid_cell_of_region_cell(route["goal"]) == (5, 2)


def test_a_long_water_stretch_becomes_a_sea_route_and_a_short_one_stays_a_crossing():
    w = flat_world()
    size = map_grid.REGION_CELLS_PER_GRID_CELL
    w.land[:, 4 * size:6 * size] = False
    w.land[:, 8 * size:9 * size] = False
    painted(w, [("town", [(1, 1)]), ("route", [(column, 1) for column in range(2, 10)])])
    modes = [(route["mode"], len(route["grid_cells"])) for route in w.routes]
    assert sorted(modes) == [("land", 2), ("land", 4), ("sea", 2)]
    land_route = next(route for route in w.routes if route["mode"] == "land" and [6, 1] in route["grid_cells"])
    assert [8, 1] in land_route["grid_cells"]
    sea_route = next(route for route in w.routes if route["mode"] == "sea")
    assert sea_route["role"] == "sea" and len(sea_route["exits"]) == 2


def test_touching_routes_are_joined_and_repainting_keeps_names():
    w = flat_world()
    painted(w, [("town", [(1, 2), (8, 2)]), ("route", [(column, 2) for column in range(2, 8)]), ("route", [(4, 3), (4, 4), (4, 5)])])
    branch = next(route for route in w.routes if [4, 5] in route["grid_cells"])
    trunk = next(route for route in w.routes if [2, 2] in route["grid_cells"])
    assert [exit_record["to"] for exit_record in branch["exits"]] == [trunk["name"]]
    assert trunk["name"] in [exit_record["to"] for exit_record in branch["exits"]]
    route_named(w, trunk["name"])["spec_overrides"] = {"tall_grass": 0.7}
    marks = map_grid.marks_of_map_grid(w.overrides["map_grid"])
    marks = map_grid.with_cells_painted(marks, [(5, 3)], "route", (GRID_WIDTH, GRID_HEIGHT))
    map_grid.map_from_marks(w, marks)
    assert route_named(w, trunk["name"])["spec_overrides"] == {"tall_grass": 0.7}


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
