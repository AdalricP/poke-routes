from __future__ import annotations

import pytest

from pokeroutes.region import edit, map_grid

SEEDS_TO_ROUND_TRIP = (0, 1, 2, 3)
SMALLER_SQUARE_SIZE_IN_REGION_CELLS = 4
LARGER_SQUARE_SIZE_IN_REGION_CELLS = 12
STARTING_SQUARE_SIZE_IN_REGION_CELLS = 8


@pytest.fixture(scope="module")
def generated_maps_by_seed():
    maps_by_seed = {seed: edit.generate_map(edit.generate_doc(seed)) for seed in SEEDS_TO_ROUND_TRIP}
    return maps_by_seed


def towns_by_name(world):
    town_cells_by_name = {town.name: sorted(map(tuple, town.grid_cells)) for town in world.cities}
    return town_cells_by_name


def route_names_of(world):
    route_names = sorted(route["name"] for route in world.routes)
    return route_names


def sides_in_squares_of(cells):
    columns = {cell[0] for cell in cells}
    rows = {cell[1] for cell in cells}
    sides_in_squares = (max(columns) - min(columns) + 1, max(rows) - min(rows) + 1)
    return sides_in_squares


def is_filled_rectangle(cells):
    width_in_squares, height_in_squares = sides_in_squares_of(cells)
    filled = len(set(cells)) == width_in_squares * height_in_squares
    return filled


def is_one_connected_piece(cells):
    one_piece = len(map_grid.connected_groups(map(tuple, cells))) == 1
    return one_piece


def assert_towns_are_compact_blocks(world_before, world_after):
    towns_before = {town.id: town for town in world_before.cities}
    for town in world_after.cities:
        cells = [tuple(cell) for cell in town.grid_cells]
        assert is_filled_rectangle(cells), f"{town.name} is not a filled block: {cells}"
        before_cells = [tuple(cell) for cell in towns_before[town.id].grid_cells]
        before_width, before_height = sides_in_squares_of(before_cells)
        after_width, after_height = sides_in_squares_of(cells)
        if before_width == before_height:
            assert after_width == after_height, f"{town.name} went from a square to a {after_width} x {after_height} strip"


def assert_regridded_map_keeps_its_places(world_before, world_after):
    assert sorted(towns_by_name(world_after)) == sorted(towns_by_name(world_before))
    assert set(route_names_of(world_after)) <= set(route_names_of(world_before))
    for route in world_after.routes:
        assert is_one_connected_piece(route["grid_cells"]), f"{route['name']} was cut in pieces"


@pytest.mark.parametrize("seed", SEEDS_TO_ROUND_TRIP)
@pytest.mark.parametrize("other_square_size", (SMALLER_SQUARE_SIZE_IN_REGION_CELLS, LARGER_SQUARE_SIZE_IN_REGION_CELLS))
def test_a_square_size_round_trip_gives_back_the_same_towns_and_routes(generated_maps_by_seed, seed, other_square_size):
    starting_doc = generated_maps_by_seed[seed]
    starting_world = edit.doc_to_world(starting_doc)

    other_size_world = edit.doc_to_world(edit.regrid_map(starting_doc, other_square_size))
    round_trip_world = edit.doc_to_world(edit.regrid_map(edit.regrid_map(starting_doc, other_square_size), STARTING_SQUARE_SIZE_IN_REGION_CELLS))

    assert_towns_are_compact_blocks(starting_world, other_size_world)
    assert_regridded_map_keeps_its_places(starting_world, other_size_world)
    assert towns_by_name(round_trip_world) == towns_by_name(starting_world)
    assert route_names_of(round_trip_world) == route_names_of(starting_world)


@pytest.mark.parametrize("seed", SEEDS_TO_ROUND_TRIP)
def test_a_smaller_square_keeps_every_town_and_route(generated_maps_by_seed, seed):
    starting_world = edit.doc_to_world(generated_maps_by_seed[seed])

    smaller_world = edit.doc_to_world(edit.regrid_map(generated_maps_by_seed[seed], SMALLER_SQUARE_SIZE_IN_REGION_CELLS))

    assert len(smaller_world.cities) == len(starting_world.cities)
    assert route_names_of(smaller_world) == route_names_of(starting_world)


@pytest.mark.parametrize("seed", SEEDS_TO_ROUND_TRIP)
def test_painting_forgets_the_earlier_square_size_and_snaps_afresh(generated_maps_by_seed, seed):
    starting_world = edit.doc_to_world(generated_maps_by_seed[seed])
    larger_doc = edit.regrid_map(generated_maps_by_seed[seed], LARGER_SQUARE_SIZE_IN_REGION_CELLS)
    painted_doc = edit.paint_map(larger_doc, [[0, 0]], "erase")

    snapped_back_world = edit.doc_to_world(edit.regrid_map(painted_doc, STARTING_SQUARE_SIZE_IN_REGION_CELLS))

    assert "earlier_square_sizes" not in (painted_doc.get("map_grid") or {})
    assert_towns_are_compact_blocks(starting_world, snapped_back_world)
    assert_regridded_map_keeps_its_places(starting_world, snapped_back_world)


@pytest.mark.parametrize("square_size", (LARGER_SQUARE_SIZE_IN_REGION_CELLS, 16, 6))
def test_a_square_size_that_does_not_divide_the_region_grows_it_to_whole_squares(generated_maps_by_seed, square_size):
    starting_world = edit.doc_to_world(generated_maps_by_seed[0])

    regridded_world = edit.doc_to_world(edit.regrid_map(generated_maps_by_seed[0], square_size))

    assert regridded_world.W % square_size == 0 and regridded_world.H % square_size == 0
    assert starting_world.W <= regridded_world.W < starting_world.W + square_size
    assert starting_world.H <= regridded_world.H < starting_world.H + square_size
    assert list(map_grid.grid_size_of_world(regridded_world)) == [regridded_world.W // square_size, regridded_world.H // square_size]
    assert sorted(towns_by_name(regridded_world)) == sorted(towns_by_name(starting_world))
