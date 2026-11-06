from __future__ import annotations

import numpy as np
import pytest

from pokeroutes.region import edit, map_grid


@pytest.fixture(scope="module")
def region_with_a_map():
    doc = edit.generate_map(edit.generate_doc(3))
    return doc


def places_of(world):
    places = ({town.name: sorted(map(tuple, town.grid_cells)) for town in world.cities},
              {route["name"]: sorted(map(tuple, route["grid_cells"])) for route in world.routes})
    return places


def test_growing_keeps_every_place_and_the_old_ground(region_with_a_map):
    old_world = edit.doc_to_world(region_with_a_map)

    grown_world = edit.doc_to_world(edit.resize_region(region_with_a_map, 30, 20))

    assert (grown_world.W, grown_world.H) == (30 * 8, 20 * 8)
    assert places_of(grown_world) == places_of(old_world)
    assert np.array_equal(grown_world.height[:old_world.H, :old_world.W], old_world.height)
    assert np.array_equal(grown_world.biome[:old_world.H, :old_world.W], old_world.biome)
    assert np.array_equal(grown_world.river[:old_world.H, :old_world.W], old_world.river)


def test_growing_adds_some_land_in_the_new_area(region_with_a_map):
    old_world = edit.doc_to_world(region_with_a_map)

    grown_world = edit.doc_to_world(edit.resize_region(region_with_a_map, 30, 20))
    new_area_land = grown_world.land.copy()
    new_area_land[:old_world.H, :old_world.W] = False

    assert new_area_land.any()
    assert set(np.unique(grown_world.biome[new_area_land])) - set("~=o")


def test_shrinking_drops_the_places_outside_and_keeps_the_rest(region_with_a_map):
    old_world = edit.doc_to_world(region_with_a_map)
    width_in_squares, height_in_squares = 12, 9

    cropped_world = edit.doc_to_world(edit.resize_region(region_with_a_map, width_in_squares, height_in_squares))
    old_towns_inside = {town.name for town in old_world.cities
                        if all(cell[0] < width_in_squares and cell[1] < height_in_squares for cell in town.grid_cells)}

    assert (cropped_world.W, cropped_world.H) == (96, 72)
    assert old_towns_inside <= {town.name for town in cropped_world.cities}
    assert len(cropped_world.cities) < len(old_world.cities)
    assert all(map_grid.is_inside_grid(tuple(cell), (width_in_squares, height_in_squares))
               for route in cropped_world.routes for cell in route["grid_cells"])


def test_resizing_follows_the_square_size(region_with_a_map):
    regridded = edit.regrid_map(region_with_a_map, 12)

    resized_world = edit.doc_to_world(edit.resize_region(regridded, 10, 7))

    assert (resized_world.W, resized_world.H) == (120, 84)
    assert map_grid.grid_size_of_world(resized_world) == (10, 7)


def test_a_region_smaller_than_three_squares_is_refused(region_with_a_map):
    with pytest.raises(edit.RegionEditRefused):
        edit.resize_region(region_with_a_map, 2, 9)
