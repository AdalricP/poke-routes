from __future__ import annotations

import json
import math
from typing import Dict, List, Optional, Sequence, Set, Tuple

from . import map_grid, network
from .map_grid import GridCell, MapGridProblem
from .world import City, World

EARLIER_SQUARE_SIZES_KEY = "earlier_square_sizes"
TOWN_BLOCK_SHIFTS_IN_SQUARES: Tuple[GridCell, ...] = ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1))


def checked_square_size(new_size_in_region_cells: int) -> int:
    if new_size_in_region_cells not in map_grid.GRID_CELL_SIZES_IN_REGION_CELLS:
        allowed_sizes = ", ".join(map(str, map_grid.GRID_CELL_SIZES_IN_REGION_CELLS))
        raise MapGridProblem(f"the map grid can be {allowed_sizes} region cells wide, not {new_size_in_region_cells}")
    return new_size_in_region_cells


def plain_json_value_of(numpy_value):
    plain_value = numpy_value.tolist() if hasattr(numpy_value, "tolist") else str(numpy_value)
    return plain_value


def plain_json_copy_of(structure):
    plain_copy = json.loads(json.dumps(structure, default=plain_json_value_of))
    return plain_copy


def map_at_current_square_size(w: World) -> dict:
    stored_map_grid = map_grid.map_grid_of_world(w)
    remembered_map = {"rows": list(stored_map_grid.get("rows") or []), "routes": plain_json_copy_of(stored_map_grid.get("routes") or []),
                      "towns": {town.id: [[cell[0], cell[1]] for cell in town.grid_cells] for town in w.cities},
                      "route_records": plain_json_copy_of(w.routes)}
    return remembered_map


def earlier_square_sizes_of(w: World) -> Dict[str, dict]:
    stored_map_grid = w.overrides.get("map_grid") or {}
    earlier_square_sizes = dict(stored_map_grid.get(EARLIER_SQUARE_SIZES_KEY) or {})
    return earlier_square_sizes


def side_in_squares_of_footprint(side_in_region_cells: float, size_in_region_cells: int) -> int:
    side_in_squares = max(1, math.floor(side_in_region_cells / size_in_region_cells + 0.5))
    return side_in_squares


def town_block_at(town: City, size_in_region_cells: int, grid_size: Tuple[int, int], block_size_in_squares: GridCell, shift_in_squares: GridCell) -> List[GridCell]:
    block_width_in_squares = min(block_size_in_squares[0], grid_size[0])
    block_height_in_squares = min(block_size_in_squares[1], grid_size[1])
    centred_left_square = math.floor(town.cx / size_in_region_cells - block_width_in_squares / 2 + 0.5) + shift_in_squares[0]
    centred_top_square = math.floor(town.cy / size_in_region_cells - block_height_in_squares / 2 + 0.5) + shift_in_squares[1]
    left_square = min(max(centred_left_square, 0), grid_size[0] - block_width_in_squares)
    top_square = min(max(centred_top_square, 0), grid_size[1] - block_height_in_squares)
    block = [(left_square + column_offset, top_square + row_offset)
             for row_offset in range(block_height_in_squares) for column_offset in range(block_width_in_squares)]
    return block


def candidate_town_blocks(town: City, size_in_region_cells: int, grid_size: Tuple[int, int]) -> List[Tuple[int, int, List[GridCell]]]:
    left, top, right, bottom = town.rect
    footprint_in_squares = (side_in_squares_of_footprint(right - left, size_in_region_cells), side_in_squares_of_footprint(bottom - top, size_in_region_cells))
    block_sizes_in_squares = [footprint_in_squares] + ([(1, 1)] if footprint_in_squares != (1, 1) else [])
    candidate_blocks = [(size_rank, shift_rank, town_block_at(town, size_in_region_cells, grid_size, block_size_in_squares, shift_in_squares))
                        for size_rank, block_size_in_squares in enumerate(block_sizes_in_squares)
                        for shift_rank, shift_in_squares in enumerate(TOWN_BLOCK_SHIFTS_IN_SQUARES)]
    return candidate_blocks


def does_block_touch_taken_cells(block: Sequence[GridCell], taken_cells: Set[GridCell]) -> bool:
    reach_of_block = set(block) | {neighbour for cell in block for neighbour in map_grid.neighbours_of(cell)}
    touches = bool(reach_of_block & taken_cells)
    return touches


def does_block_overlap_town_footprint(block: Sequence[GridCell], town: City, size_in_region_cells: int) -> bool:
    block_left, block_top, block_right, block_bottom = map_grid.region_rect_of_grid_cells(block, size_in_region_cells)
    town_left, town_top, town_right, town_bottom = town.rect
    overlaps = block_left < town_right and town_left < block_right and block_top < town_bottom and town_top < block_bottom
    return overlaps


def preference_of_town_block(candidate_block: Tuple[int, int, List[GridCell]], town: City, size_in_region_cells: int,
                             route_path_cells: Set[GridCell]) -> Tuple[int, bool, int, int]:
    size_rank, shift_rank, block = candidate_block
    leaves_town_footprint = not does_block_overlap_town_footprint(block, town, size_in_region_cells)
    covered_route_cell_count = len(set(block) & route_path_cells)
    preference = (size_rank, leaves_town_footprint, covered_route_cell_count, shift_rank)
    return preference


def best_free_town_block(town: City, size_in_region_cells: int, grid_size: Tuple[int, int], taken_cells: Set[GridCell],
                         route_path_cells: Set[GridCell]) -> List[GridCell]:
    candidate_blocks = candidate_town_blocks(town, size_in_region_cells, grid_size)
    free_blocks = [candidate for candidate in candidate_blocks if not does_block_touch_taken_cells(candidate[2], taken_cells)]
    if not free_blocks:
        return candidate_blocks[0][2]
    best_block = min(free_blocks, key=lambda candidate: preference_of_town_block(candidate, town, size_in_region_cells, route_path_cells))[2]
    return best_block


def footprint_area_in_region_cells(town: City) -> int:
    left, top, right, bottom = town.rect
    area_in_region_cells = (right - left) * (bottom - top)
    return area_in_region_cells


def snapped_town_blocks(towns: Sequence[City], size_in_region_cells: int, grid_size: Tuple[int, int],
                        route_path_cells: Set[GridCell]) -> Dict[str, List[GridCell]]:
    towns_largest_first = sorted(towns, key=lambda town: (-footprint_area_in_region_cells(town), town.id))
    taken_cells: Set[GridCell] = set()
    block_of_town_id: Dict[str, List[GridCell]] = {}
    for town in towns_largest_first:
        block = best_free_town_block(town, size_in_region_cells, grid_size, taken_cells, route_path_cells)
        block_of_town_id[town.id] = block
        taken_cells.update(block)
    return block_of_town_id


def path_cells_of_route(route: dict, size_in_region_cells: int, grid_size: Tuple[int, int], town_cells: Set[GridCell]) -> List[GridCell]:
    path_cells = [cell for cell in map_grid.grid_cells_along_region_path(route.get("path") or [], size_in_region_cells)
                  if map_grid.is_inside_grid(cell, grid_size) and cell not in town_cells]
    return path_cells


def cell_beyond_exit(exit_record: dict, size_in_region_cells: int) -> GridCell:
    step_x, step_y = network.DELTA[exit_record["side"]]
    beyond_cell = map_grid.grid_cell_of_region_cell((exit_record["cell"][0] + step_x, exit_record["cell"][1] + step_y), size_in_region_cells)
    return beyond_cell


def is_cell_beside_any(cell: GridCell, cells: Set[GridCell]) -> bool:
    beside = any(neighbour in cells for neighbour in map_grid.neighbours_of(cell))
    return beside


def bridging_cells_beyond_exits(route: dict, path_cells: Set[GridCell], size_in_region_cells: int, grid_size: Tuple[int, int],
                                town_cells: Set[GridCell]) -> Set[GridCell]:
    beyond_cells = {cell_beyond_exit(exit_record, size_in_region_cells) for exit_record in route.get("exits") or []}
    bridging_cells = {cell for cell in beyond_cells if map_grid.is_inside_grid(cell, grid_size) and cell not in town_cells
                      and cell not in path_cells and is_cell_beside_any(cell, path_cells)}
    return bridging_cells


def claimed_cells_of_each_route(routes: Sequence[dict], size_in_region_cells: int, grid_size: Tuple[int, int],
                                town_cells: Set[GridCell]) -> Tuple[Dict[str, Set[GridCell]], Dict[str, Set[GridCell]]]:
    claims: Dict[str, Set[GridCell]] = {}
    path_claims: Dict[str, Set[GridCell]] = {}
    for route in routes:
        path_cells = set(path_cells_of_route(route, size_in_region_cells, grid_size, town_cells))
        bridging_cells = bridging_cells_beyond_exits(route, path_cells, size_in_region_cells, grid_size, town_cells)
        path_claims.setdefault(route["grid_key"], set()).update(path_cells)
        claims.setdefault(route["grid_key"], set()).update(path_cells | bridging_cells)
    return claims, path_claims


def does_route_need_cell_to_stay_whole(cell: GridCell, route_cells: Set[GridCell]) -> bool:
    piece_count_without_cell = len(map_grid.connected_groups(route_cells - {cell}))
    needs_cell = piece_count_without_cell > 1
    return needs_cell


def owner_of_shared_cell(cell: GridCell, claimant_keys: Sequence[str], claims: Dict[str, Set[GridCell]],
                         path_claims: Dict[str, Set[GridCell]]) -> str:
    keys_that_need_the_cell = [key for key in claimant_keys if does_route_need_cell_to_stay_whole(cell, claims[key])]
    keys_left_with_nothing_else = [key for key in claimant_keys if claims[key] == {cell}]
    keys_whose_path_crosses_the_cell = [key for key in claimant_keys if cell in path_claims[key]]
    owner_key = (keys_that_need_the_cell or keys_left_with_nothing_else or keys_whose_path_crosses_the_cell or list(claimant_keys))[0]
    return owner_key


def claims_with_shared_cells_settled(claims: Dict[str, Set[GridCell]], path_claims: Dict[str, Set[GridCell]]) -> Dict[str, Set[GridCell]]:
    settled_claims = {key: set(cells) for key, cells in claims.items()}
    ordered_keys = sorted(settled_claims, key=map_grid.route_key_order)
    all_claimed_cells = sorted({cell for cells in settled_claims.values() for cell in cells}, key=map_grid.scan_order)
    for cell in all_claimed_cells:
        claimant_keys = [key for key in ordered_keys if cell in settled_claims[key]]
        if len(claimant_keys) < 2:
            continue
        owner_key = owner_of_shared_cell(cell, claimant_keys, settled_claims, path_claims)
        for losing_key in claimant_keys:
            if losing_key != owner_key:
                settled_claims[losing_key].discard(cell)
    return settled_claims


def largest_piece_of(cells: Set[GridCell]) -> List[GridCell]:
    pieces = sorted(map_grid.connected_groups(cells), key=lambda piece: (-len(piece), map_grid.scan_order(piece[0])))
    largest_piece = pieces[0] if pieces else []
    return largest_piece


def assignment_of_whole_routes(claims: Dict[str, Set[GridCell]]) -> Dict[GridCell, str]:
    assignment = {cell: key for key, cells in claims.items() for cell in largest_piece_of(cells)}
    return assignment


def marks_of_towns_and_routes(town_cells: Set[GridCell], assignment: Dict[GridCell, str]) -> Dict[GridCell, str]:
    marks = {cell: map_grid.ROUTE_MARK for cell in assignment}
    marks.update({cell: map_grid.TOWN_MARK for cell in town_cells})
    return marks


def first_free_route_key(taken_keys: Set[str]) -> str:
    route_number = 1
    while f"r{route_number}" in taken_keys:
        route_number += 1
    free_key = f"r{route_number}"
    return free_key


def routes_with_grid_keys(routes: Sequence[dict]) -> List[dict]:
    taken_keys = {route["grid_key"] for route in routes if route.get("grid_key")}
    for route in routes:
        if route.get("grid_key"):
            continue
        route["grid_key"] = first_free_route_key(taken_keys)
        taken_keys.add(route["grid_key"])
    keyed_routes = list(routes)
    return keyed_routes


def snapped_marks_and_assignment(w: World, new_size_in_region_cells: int, grid_size: Tuple[int, int]) -> Tuple[Dict[GridCell, str], Dict[GridCell, str]]:
    keyed_routes = routes_with_grid_keys(w.routes)
    route_path_cells = {cell for route in keyed_routes for cell in path_cells_of_route(route, new_size_in_region_cells, grid_size, set())}
    block_of_town_id = snapped_town_blocks(w.cities, new_size_in_region_cells, grid_size, route_path_cells)
    for town in w.cities:
        town.grid_cells = [[cell[0], cell[1]] for cell in block_of_town_id[town.id]]
    town_cells = {cell for block in block_of_town_id.values() for cell in block}
    claims, path_claims = claimed_cells_of_each_route(keyed_routes, new_size_in_region_cells, grid_size, town_cells)
    assignment = assignment_of_whole_routes(claims_with_shared_cells_settled(claims, path_claims))
    marks = marks_of_towns_and_routes(town_cells, assignment)
    return marks, assignment


def route_records_with_remembered_ones(current_routes: Sequence[dict], remembered_routes: Sequence[dict]) -> List[dict]:
    current_keys = {route.get("grid_key") for route in current_routes}
    returning_routes = [route for route in remembered_routes if route.get("grid_key") not in current_keys]
    merged_routes = list(current_routes) + returning_routes
    return merged_routes


def remembered_marks_and_assignment(w: World, remembered_map: dict, grid_size: Tuple[int, int]) -> Tuple[Dict[GridCell, str], Dict[GridCell, str]]:
    remembered_town_cells = remembered_map.get("towns") or {}
    for town in w.cities:
        town.grid_cells = remembered_town_cells.get(town.id) or town.grid_cells
    w.routes = route_records_with_remembered_ones(w.routes, remembered_map.get("route_records") or [])
    marks = {cell: mark for cell, mark in map_grid.marks_of_map_grid(remembered_map).items() if map_grid.is_inside_grid(cell, grid_size)}
    assignment = {cell: key for cell, key in map_grid.assignment_of_map_grid(remembered_map).items() if marks.get(cell) == map_grid.ROUTE_MARK}
    return marks, assignment


def regrid_world(w: World, new_size_in_region_cells: int) -> None:
    new_size_in_region_cells = checked_square_size(new_size_in_region_cells)
    old_size_in_region_cells = map_grid.region_cells_per_grid_cell(w)
    earlier_square_sizes = earlier_square_sizes_of(w)
    earlier_square_sizes[str(old_size_in_region_cells)] = map_at_current_square_size(w)
    remembered_map: Optional[dict] = earlier_square_sizes.pop(str(new_size_in_region_cells), None)
    grid_size = (w.W // new_size_in_region_cells, w.H // new_size_in_region_cells)
    w.overrides["map_grid"] = {"cell": new_size_in_region_cells, "size": list(grid_size), "rows": []}
    if remembered_map is not None:
        marks, assignment = remembered_marks_and_assignment(w, remembered_map, grid_size)
    else:
        marks, assignment = snapped_marks_and_assignment(w, new_size_in_region_cells, grid_size)
    map_grid.map_from_marks(w, marks, assignment)
    w.overrides["map_grid"][EARLIER_SQUARE_SIZES_KEY] = earlier_square_sizes
