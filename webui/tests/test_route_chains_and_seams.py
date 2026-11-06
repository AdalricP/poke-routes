from __future__ import annotations

import numpy as np
import pytest

from pokeroutes.region import edit, map_grid
from pokeroutes.region.route_chain import chain_maps_of_route, ordered_grid_cells_of_route
from pokeroutes.region.seam_check import generate_maps_of_routes, seam_checks_of_maps, describe_seam_checks
from pokeroutes.seams import seam_mismatches

STAIRCASE_ROUTE_CELLS = [(1, 0), (1, 1), (2, 1)]
STAIRCASE_TOWN_CELLS = [(0, 0), (2, 2)]


def l_shaped_route(path_cells, rect):
    route = dict(name="Route 1", id="Route1", map="MAP_ROUTE1", rect=rect, role="turn", shape="turn", tags=[], mode="land",
                 link="A-B", path=[list(cell) for cell in path_cells], biome_code="m", kind_link="main",
                 exits=[dict(side="west", cell=list(path_cells[0]), to="Town A", to_map="MAP_TOWN_A", link="A-B"),
                        dict(side="south", cell=list(path_cells[-1]), to="Town B", to_map="MAP_TOWN_B", link="A-B")])
    return route


def l_path():
    across = [(x, 5) for x in range(0, 12)]
    down = [(11, y) for y in range(6, 16)]
    return across + down


def dry_origin_for_staircase(world):
    size = map_grid.REGION_CELLS_PER_GRID_CELL
    is_dry = lambda column, row: world.land[row * size:(row + 1) * size, column * size:(column + 1) * size].all()
    origins = [(column, row) for row in range(1, world.H // size - 3) for column in range(1, world.W // size - 3)
               if all(is_dry(column + step_x, row + step_y) for step_x in range(3) for step_y in range(3))]
    return origins[0]


@pytest.fixture(scope="module")
def region_with_drawn_staircase():
    doc = edit.generate_doc(1)
    column, row = dry_origin_for_staircase(edit.doc_to_world(doc))
    doc = edit.paint_map(doc, [[column + x, row + y] for x, y in STAIRCASE_TOWN_CELLS], "town")
    doc = edit.paint_map(doc, [[column + x, row + y] for x, y in STAIRCASE_ROUTE_CELLS], "route")
    doc["added"] = [route["name"] for route in doc["routes"]]
    return doc


def test_an_l_shaped_route_becomes_three_maps_along_its_path():
    route = l_shaped_route(l_path(), [0, 0, 16, 16])

    ordered_grid_cells = ordered_grid_cells_of_route(route, map_grid.REGION_CELLS_PER_GRID_CELL)

    assert ordered_grid_cells == [[0, 0], [1, 0], [1, 1]]


def test_painted_grid_cells_win_over_the_derived_ones():
    route = dict(l_shaped_route(l_path(), [0, 0, 16, 16]), grid_cells=[[3, 4], [3, 5]])

    assert ordered_grid_cells_of_route(route, map_grid.REGION_CELLS_PER_GRID_CELL) == [[3, 4], [3, 5]]


def test_consecutive_chain_maps_meet_at_the_same_crossing():
    pieces = chain_maps_of_route(l_shaped_route(l_path(), [0, 0, 16, 16]), map_grid.REGION_CELLS_PER_GRID_CELL)

    seam_exits = [(piece["name"], piece_exit) for piece in pieces for piece_exit in piece["exits"] if piece_exit.get("seam")]

    assert len(pieces) == 3 and len(seam_exits) == 4
    for piece_name, piece_exit in seam_exits:
        partner = next(piece for piece in pieces if piece["name"] == piece_exit["to_chain_map"])
        step = {"east": (1, 0), "west": (-1, 0), "south": (0, 1), "north": (0, -1)}[piece_exit["side"]]
        across = [piece_exit["cell"][0] + step[0], piece_exit["cell"][1] + step[1]]
        assert any(back["cell"] == across and back["to_chain_map"] == piece_name for back in partner["exits"])


def test_the_first_and_last_maps_keep_the_routes_own_exits():
    pieces = chain_maps_of_route(l_shaped_route(l_path(), [0, 0, 16, 16]), map_grid.REGION_CELLS_PER_GRID_CELL)

    assert pieces[0]["exits"][0]["to"] == "Town A"
    assert pieces[-1]["exits"][-1]["to"] == "Town B"


def test_seam_mismatches_lists_each_differing_tile():
    left_map = np.full((4, 4), "f")
    right_map = np.full((4, 4), "f")
    right_map[2, 0] = "w"

    mismatches = seam_mismatches(left_map, "east", 0, right_map, 0, 4)

    assert [(mismatch.position_along_edge_in_metatiles, mismatch.class_on_this_side, mismatch.class_on_other_side)
            for mismatch in mismatches] == [(2, "f", "w")]


def test_a_drawn_staircase_route_is_a_chain_with_matching_seams(region_with_drawn_staircase):
    world = edit.doc_to_world(region_with_drawn_staircase)
    drawn_route_names = region_with_drawn_staircase["added"]

    generated_maps = generate_maps_of_routes(world, drawn_route_names)
    seam_checks = seam_checks_of_maps(generated_maps)

    assert len(generated_maps) >= 3
    assert seam_checks and all(seam_check.matches for seam_check in seam_checks), describe_seam_checks(seam_checks)


def test_neighbouring_routes_share_their_edge_tile_for_tile():
    doc = edit.generate_map(edit.generate_doc(6))
    world = edit.doc_to_world(doc)
    route_names = [route["name"] for route in world.routes]

    seam_checks = seam_checks_of_maps(generate_maps_of_routes(world, route_names))
    seams_between_different_routes = [seam_check for seam_check in seam_checks
                                      if seam_check.first_map_name.split(" (")[0] != seam_check.second_map_name.split(" (")[0]]

    assert seams_between_different_routes
    assert all(seam_check.matches for seam_check in seams_between_different_routes), describe_seam_checks(seams_between_different_routes)


def test_chain_maps_export_porymap_connections_with_world_offsets(region_with_drawn_staircase):
    from pokeroutes.region.export import chain_map_specs

    world = edit.doc_to_world(region_with_drawn_staircase)
    specs = {spec["id"]: spec for spec in chain_map_specs(world)}
    first_piece = next(spec for spec in specs.values() if spec["name"].endswith("(1/3)"))

    seam_exit = next(spec_exit for spec_exit in first_piece["exits"] if spec_exit["to"].startswith(first_piece["region"]["chain"]["map_section"]))
    neighbour = next(spec for spec in specs.values() if "MAP_" + spec["id"].upper() == seam_exit["to"])
    along_axis = 1 if seam_exit["side"] in ("east", "west") else 0

    assert seam_exit["offset"] == (neighbour["region"]["rect"][along_axis] - first_piece["region"]["rect"][along_axis]) * 4


def t_shaped_route_with_its_branch_appended():
    main_cells = [[0, 0], [1, 0], [2, 0]]
    branch_cells = [[1, 2], [1, 1]]
    route = dict(l_shaped_route(l_path(), [0, 0, 24, 24]), grid_cells=main_cells + branch_cells, path=[])
    return route


def test_a_branch_appended_after_the_longest_run_joins_the_map_beside_it():
    pieces = chain_maps_of_route(t_shaped_route_with_its_branch_appended(), map_grid.REGION_CELLS_PER_GRID_CELL)

    seam_partners = {(tuple(piece["chain"]["grid_cell"]), piece_exit["to_chain_map"]) for piece in pieces
                     for piece_exit in piece["exits"] if piece_exit.get("seam")}
    name_of_cell = {tuple(piece["chain"]["grid_cell"]): piece["name"] for piece in pieces}

    assert ((1, 1), name_of_cell[(1, 0)]) in seam_partners
    assert ((1, 0), name_of_cell[(1, 1)]) in seam_partners
    assert ((1, 2), name_of_cell[(1, 1)]) in seam_partners
    assert len(seam_partners) == 8


@pytest.mark.parametrize("region_cells_per_square", [4, 12])
def test_chain_maps_are_one_square_of_the_regions_grid(region_cells_per_square):
    pieces = chain_maps_of_route(t_shaped_route_with_its_branch_appended(), region_cells_per_square)

    first_two_rects = [piece["rect"] for piece in pieces][:2]

    assert first_two_rects == [[0, 0, region_cells_per_square, region_cells_per_square],
                               [region_cells_per_square, 0, 2 * region_cells_per_square, region_cells_per_square]]


def test_the_walk_through_a_branching_route_only_steps_between_neighbours():
    cells = [(0, 0), (1, 0), (2, 0), (1, 2), (1, 1)]

    walk = map_grid.walk_through_route_cells(cells)

    assert all(abs(walk[index][0] - walk[index + 1][0]) + abs(walk[index][1] - walk[index + 1][1]) == 1 for index in range(len(walk) - 1))
    assert walk[0] == (0, 0) and walk[-1] == (1, 1) and set(walk) == set(cells)


def test_the_largest_square_fits_the_gba_map_buffer():
    from pokeroutes.export import MAX_MAP_DATA_SIZE
    from pokeroutes.region.world import CELL

    side_in_metatiles = max(map_grid.GRID_CELL_SIZES_IN_REGION_CELLS) * CELL

    assert (side_in_metatiles + 15) * (side_in_metatiles + 14) <= MAX_MAP_DATA_SIZE
