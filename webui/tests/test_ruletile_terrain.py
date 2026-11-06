import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pokeroutes.ruletile_terrain import (bridge_runs_east_west, components_of, grid_with_ledge_grounds,
                                         is_sideways_waterfall, ledge_ground, shore_mask,
                                         sideways_flow_direction, tile_ledges_over_ground)

MASK_BITS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def test_bridge_across_an_east_west_river_runs_north_south():
    grid = ["ggpgg",
            "wwqww",
            "wwqww",
            "ggpgg"]
    bridge_component = components_of(grid, "q")[0]

    runs_east_west = bridge_runs_east_west(grid, bridge_component)

    assert not runs_east_west


def test_bridge_across_a_north_south_river_runs_east_west():
    grid = ["gwwg",
            "pqqp",
            "gwwg"]
    bridge_component = components_of(grid, "q")[0]

    runs_east_west = bridge_runs_east_west(grid, bridge_component)

    assert runs_east_west


def test_fall_between_west_and_east_water_is_sideways_and_flows_to_the_bigger_pool():
    grid = ["rrrrrrr",
            "wwaawww",
            "wwaawww",
            "rrrrrrr"]
    fall_component = components_of(grid, "a")[0]

    is_sideways = is_sideways_waterfall(grid, fall_component, "w")
    flow_direction = sideways_flow_direction(grid, fall_component, "w")

    assert is_sideways
    assert flow_direction == "east"


def test_fall_along_a_river_bank_stays_vertical():
    grid = ["ggggggg",
            "waaaaaw",
            "wwwwwww"]
    fall_component = components_of(grid, "a")[0]

    is_sideways = is_sideways_waterfall(grid, fall_component, "w")

    assert not is_sideways


def test_shore_mask_counts_a_diagonal_only_as_an_inner_corner():
    north_and_north_east = shore_mask({"N", "NE"}, MASK_BITS)
    only_north_east = shore_mask({"NE"}, MASK_BITS)

    assert north_and_north_east == 1
    assert only_north_east == 2


def test_south_ledge_takes_the_ground_of_its_high_side_and_paths_join_through_it():
    grid = ["gpppg",
            "sssss",
            "gpppg"]
    middle_ledge_id, over_path_id = 10, 20
    ledge_class = {"parts": {"mid": middle_ledge_id, "left": 11, "right": 12, "single": 13},
                   "over_ground": {"p": {"255": over_path_id, "31": 21, "241": 22}}}
    metatile_ids = np.full((3, 5), middle_ledge_id)

    ground_letter = ledge_ground(grid, 2, 1)
    ground_by_cell = tile_ledges_over_ground(grid, metatile_ids, {"s": ledge_class})
    seen_through_ledges = grid_with_ledge_grounds(grid, ground_by_cell)

    assert ground_letter == "p"
    assert metatile_ids[1].tolist() == [middle_ledge_id, 21, over_path_id, 22, middle_ledge_id]
    assert seen_through_ledges[1] == "sppps"
