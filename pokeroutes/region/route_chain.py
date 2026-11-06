from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .world import CELL

Cell = Tuple[int, int]
GridCell = Tuple[int, int]

MAP_GRID_CELL_SIDE_IN_REGION_CELLS = 10
STEP_TOWARD_SIDE = {"east": (1, 0), "west": (-1, 0), "south": (0, 1), "north": (0, -1)}
SIDE_OF_STEP = {step: side for side, step in STEP_TOWARD_SIDE.items()}
OPPOSITE_SIDE = {"east": "west", "west": "east", "north": "south", "south": "north"}
ROLES_THAT_DEPEND_ON_THE_WHOLE_ROUTE = ("crossroads", "dead_end", "turn", "straight")


@dataclass(frozen=True)
class MapGrid:
    column_boundaries_in_region_cells: Tuple[int, ...]
    row_boundaries_in_region_cells: Tuple[int, ...]

    @property
    def column_count(self) -> int:
        return len(self.column_boundaries_in_region_cells) - 1

    @property
    def row_count(self) -> int:
        return len(self.row_boundaries_in_region_cells) - 1


def evenly_spaced_boundaries(start_in_region_cells: int, end_in_region_cells: int) -> Tuple[int, ...]:
    length_in_region_cells = end_in_region_cells - start_in_region_cells
    piece_count = max(1, round(length_in_region_cells / MAP_GRID_CELL_SIDE_IN_REGION_CELLS))
    boundaries = tuple(start_in_region_cells + round(piece_index * length_in_region_cells / piece_count)
                       for piece_index in range(piece_count + 1))
    return boundaries


def painted_grid_cells_of_route(route: dict) -> List[List[int]]:
    painted_grid_cells = [[int(grid_cell[0]), int(grid_cell[1])] for grid_cell in route.get("grid_cells") or []]
    return painted_grid_cells


def global_map_grid_reaching(grid_cells: Sequence[Sequence[int]]) -> MapGrid:
    side = MAP_GRID_CELL_SIDE_IN_REGION_CELLS
    column_boundaries = tuple(range(0, (max(grid_cell[0] for grid_cell in grid_cells) + 2) * side, side))
    row_boundaries = tuple(range(0, (max(grid_cell[1] for grid_cell in grid_cells) + 2) * side, side))
    global_map_grid = MapGrid(column_boundaries, row_boundaries)
    return global_map_grid


def map_grid_of_route(route: dict) -> MapGrid:
    painted_grid_cells = painted_grid_cells_of_route(route)
    if painted_grid_cells:
        painted_map_grid = global_map_grid_reaching(painted_grid_cells)
        return painted_map_grid
    x0, y0, x1, y1 = route["rect"]
    if not route.get("path"):
        single_cell_grid = MapGrid((x0, x1), (y0, y1))
        return single_cell_grid
    map_grid = MapGrid(evenly_spaced_boundaries(x0, x1), evenly_spaced_boundaries(y0, y1))
    return map_grid


def boundary_index_holding(boundaries: Sequence[int], coordinate_in_region_cells: int) -> int:
    for boundary_index in range(len(boundaries) - 1):
        if boundaries[boundary_index] <= coordinate_in_region_cells < boundaries[boundary_index + 1]:
            return boundary_index
    nearest_index = 0 if coordinate_in_region_cells < boundaries[0] else len(boundaries) - 2
    return nearest_index


def grid_cell_holding(map_grid: MapGrid, region_cell: Sequence[int]) -> GridCell:
    grid_cell = (boundary_index_holding(map_grid.column_boundaries_in_region_cells, int(region_cell[0])),
                 boundary_index_holding(map_grid.row_boundaries_in_region_cells, int(region_cell[1])))
    return grid_cell


def region_rect_of_grid_cell(map_grid: MapGrid, grid_cell: Sequence[int]) -> List[int]:
    column_index, row_index = int(grid_cell[0]), int(grid_cell[1])
    columns, rows = map_grid.column_boundaries_in_region_cells, map_grid.row_boundaries_in_region_cells
    region_rect = [columns[column_index], rows[row_index], columns[column_index + 1], rows[row_index + 1]]
    return region_rect


def are_grid_neighbours(grid_cell: Sequence[int], other_grid_cell: Sequence[int]) -> bool:
    manhattan_distance = abs(grid_cell[0] - other_grid_cell[0]) + abs(grid_cell[1] - other_grid_cell[1])
    return manhattan_distance == 1


def grid_cells_along_path(map_grid: MapGrid, path: Sequence[Sequence[int]]) -> List[GridCell]:
    grid_cells: List[GridCell] = []
    for region_cell in path:
        grid_cell = grid_cell_holding(map_grid, region_cell)
        if grid_cell in grid_cells:
            del grid_cells[grid_cells.index(grid_cell) + 1:]
            continue
        grid_cells.append(grid_cell)
    return grid_cells


def grid_steps_from_chain(map_grid: MapGrid, chain: Sequence[GridCell], start: GridCell) -> List[GridCell]:
    came_from: Dict[GridCell, Optional[GridCell]] = {start: None}
    waiting = deque([start])
    while waiting:
        grid_cell = waiting.popleft()
        if grid_cell in chain:
            steps_back_to_start = walk_back(came_from, grid_cell)
            return steps_back_to_start
        for neighbour in grid_neighbours_inside(map_grid, grid_cell):
            if neighbour not in came_from:
                came_from[neighbour] = grid_cell
                waiting.append(neighbour)
    return [start]


def walk_back(came_from: Dict[GridCell, Optional[GridCell]], reached_chain_cell: GridCell) -> List[GridCell]:
    steps: List[GridCell] = []
    grid_cell = came_from[reached_chain_cell]
    while grid_cell is not None:
        steps.append(grid_cell)
        grid_cell = came_from[grid_cell]
    return steps


def grid_neighbours_inside(map_grid: MapGrid, grid_cell: GridCell) -> List[GridCell]:
    neighbours = [(grid_cell[0] + step_x, grid_cell[1] + step_y) for step_x, step_y in STEP_TOWARD_SIDE.values()]
    inside = [neighbour for neighbour in neighbours
              if 0 <= neighbour[0] < map_grid.column_count and 0 <= neighbour[1] < map_grid.row_count]
    return inside


def ordered_grid_cells_of_route(route: dict) -> List[List[int]]:
    painted_grid_cells = painted_grid_cells_of_route(route)
    if painted_grid_cells:
        return painted_grid_cells
    map_grid = map_grid_of_route(route)
    chain = grid_cells_along_path(map_grid, route.get("path") or []) or [(0, 0)]
    for route_exit in route.get("exits") or []:
        exit_grid_cell = grid_cell_holding(map_grid, route_exit["cell"])
        if exit_grid_cell not in chain:
            chain.extend(grid_steps_from_chain(map_grid, chain, exit_grid_cell))
    ordered_grid_cells = [[int(column_index), int(row_index)] for column_index, row_index in chain]
    return ordered_grid_cells


def chain_parent_index(ordered_grid_cells: Sequence[Sequence[int]], piece_index: int) -> Optional[int]:
    for earlier_index in range(piece_index - 1, -1, -1):
        if are_grid_neighbours(ordered_grid_cells[earlier_index], ordered_grid_cells[piece_index]):
            return earlier_index
    return None


def is_inside_rect(region_cell: Sequence[int], rect: Sequence[int]) -> bool:
    x0, y0, x1, y1 = rect
    inside = x0 <= region_cell[0] < x1 and y0 <= region_cell[1] < y1
    return inside


def side_between_rects(rect: Sequence[int], neighbour_rect: Sequence[int]) -> str:
    if rect[2] == neighbour_rect[0]:
        return "east"
    if rect[0] == neighbour_rect[2]:
        return "west"
    if rect[3] == neighbour_rect[1]:
        return "south"
    return "north"


def seam_crossing_cells(path: Sequence[Sequence[int]], rect: Sequence[int], neighbour_rect: Sequence[int]) -> Optional[Tuple[Cell, Cell]]:
    crossing = None
    for path_index in range(len(path) - 1):
        here, there = tuple(path[path_index]), tuple(path[path_index + 1])
        if is_inside_rect(here, rect) and is_inside_rect(there, neighbour_rect):
            crossing = (here, there)
        if is_inside_rect(there, rect) and is_inside_rect(here, neighbour_rect):
            crossing = (there, here)
    return crossing


def middle_crossing_cells(rect: Sequence[int], neighbour_rect: Sequence[int]) -> Tuple[Cell, Cell]:
    side = side_between_rects(rect, neighbour_rect)
    step_x, step_y = STEP_TOWARD_SIDE[side]
    if side in ("east", "west"):
        middle_row = (max(rect[1], neighbour_rect[1]) + min(rect[3], neighbour_rect[3])) // 2
        column_inside = rect[2] - 1 if side == "east" else rect[0]
        here = (column_inside, middle_row)
    else:
        middle_column = (max(rect[0], neighbour_rect[0]) + min(rect[2], neighbour_rect[2])) // 2
        row_inside = rect[3] - 1 if side == "south" else rect[1]
        here = (middle_column, row_inside)
    crossing = (here, (here[0] + step_x, here[1] + step_y))
    return crossing


def chain_piece_names(route: dict, piece_index: int, piece_count: int) -> dict:
    if piece_count == 1:
        single_piece_names = {"name": route["name"], "id": route["id"], "map": route["map"]}
        return single_piece_names
    piece_number = piece_index + 1
    piece_names = {"name": f"{route['name']} ({piece_number}/{piece_count})", "id": f"{route['id']}_{piece_number}",
                   "map": f"{route['map']}_{piece_number}"}
    return piece_names


def seam_exits_between_pieces(route: dict, pieces: List[dict], piece_index: int, parent_index: int) -> None:
    piece, parent = pieces[piece_index], pieces[parent_index]
    crossing = seam_crossing_cells(route.get("path") or [], parent["rect"], piece["rect"])
    parent_cell, piece_cell = crossing or middle_crossing_cells(parent["rect"], piece["rect"])
    side_from_parent = side_between_rects(parent["rect"], piece["rect"])
    parent["exits"].append(dict(side=side_from_parent, cell=list(parent_cell), to=route["name"], to_map=piece["map"],
                                to_chain_map=piece["name"], link=route.get("link"), seam=True))
    piece["exits"].append(dict(side=OPPOSITE_SIDE[side_from_parent], cell=list(piece_cell), to=route["name"],
                               to_map=parent["map"], to_chain_map=parent["name"], link=route.get("link"), seam=True))


def piece_holding(pieces: Sequence[dict], region_cell: Sequence[int]) -> dict:
    holding = next((piece for piece in pieces if is_inside_rect(region_cell, piece["rect"])), pieces[0])
    return holding


def path_index_nearest(path: Sequence[Sequence[int]], region_cell: Sequence[int]) -> int:
    if not path:
        return 0
    distances = [abs(cell[0] - region_cell[0]) + abs(cell[1] - region_cell[1]) for cell in path]
    nearest_index = distances.index(min(distances))
    return nearest_index


def exits_in_path_order(piece: dict, path: Sequence[Sequence[int]]) -> List[dict]:
    ordered = sorted(piece["exits"], key=lambda piece_exit: path_index_nearest(path, piece_exit["cell"]))
    return ordered


def shape_of_exit_sides(exit_sides: Sequence[str]) -> str:
    if len(exit_sides) <= 1:
        return "dead_end"
    if len(exit_sides) > 2:
        return "crossroads"
    first_side, second_side = exit_sides
    if OPPOSITE_SIDE[first_side] == second_side:
        return "straight"
    shape = "u_turn" if first_side == second_side else "turn"
    return shape


def role_of_piece(route: dict, shape: str) -> str:
    if route.get("role") not in ROLES_THAT_DEPEND_ON_THE_WHOLE_ROUTE:
        return route.get("role") or "straight"
    if shape in ("crossroads", "dead_end"):
        return shape
    role = "turn" if shape in ("turn", "u_turn") else "straight"
    return role


def empty_piece(route: dict, map_grid: MapGrid, grid_cell: Sequence[int], piece_index: int, piece_count: int) -> dict:
    rect = region_rect_of_grid_cell(map_grid, grid_cell)
    piece = {key: value for key, value in route.items() if key not in ("exits", "path", "rect", "goal", "goal_landmark", "gate", "spec", "chain")}
    piece.update(chain_piece_names(route, piece_index, piece_count))
    piece.update(rect=rect, exits=[], path=[list(cell) for cell in route.get("path") or [] if is_inside_rect(cell, rect)])
    piece["chain"] = {"route": route["name"], "map_id": piece["id"], "index": piece_index, "count": piece_count, "grid_cell": list(grid_cell),
                      "map_section": route["map"]}
    return piece


def place_route_exits_on_pieces(route: dict, pieces: List[dict]) -> None:
    for route_exit in route.get("exits") or []:
        piece_holding(pieces, route_exit["cell"])["exits"].append(dict(route_exit))


def place_goal_on_its_piece(route: dict, pieces: List[dict]) -> None:
    if not route.get("goal"):
        return
    goal_piece = piece_holding(pieces, route["goal"])
    goal_piece["goal"] = list(route["goal"])
    goal_piece["goal_landmark"] = route.get("goal_landmark")


def place_gate_on_its_piece(route: dict, pieces: List[dict], gated_entrance_toward: str) -> None:
    if not route.get("gate"):
        return
    gated_piece = next((piece for piece in pieces if any(piece_exit.get("to") == gated_entrance_toward and not piece_exit.get("seam")
                                                         for piece_exit in piece["exits"])), pieces[0])
    gated_piece["gate"] = route["gate"]


def settle_piece_shapes(route: dict, pieces: List[dict]) -> None:
    for piece in pieces:
        piece["exits"] = exits_in_path_order(piece, route.get("path") or [])
        piece["shape"] = shape_of_exit_sides([piece_exit["side"] for piece_exit in piece["exits"]])
        piece["role"] = role_of_piece(route, piece["shape"]) if len(pieces) > 1 else route.get("role")
        piece["shape"] = piece["shape"] if len(pieces) > 1 else route.get("shape")


def chain_maps_of_route(route: dict, gated_entrance_toward: str = "") -> List[dict]:
    map_grid = map_grid_of_route(route)
    ordered_grid_cells = ordered_grid_cells_of_route(route)
    pieces = [empty_piece(route, map_grid, grid_cell, piece_index, len(ordered_grid_cells))
              for piece_index, grid_cell in enumerate(ordered_grid_cells)]
    place_route_exits_on_pieces(route, pieces)
    for piece_index in range(1, len(pieces)):
        parent_index = chain_parent_index(ordered_grid_cells, piece_index)
        if parent_index is not None:
            seam_exits_between_pieces(route, pieces, piece_index, parent_index)
    place_goal_on_its_piece(route, pieces)
    place_gate_on_its_piece(route, pieces, gated_entrance_toward)
    settle_piece_shapes(route, pieces)
    return pieces


def chain_map_holding(chain_maps: Sequence[dict], region_cell: Sequence[int]) -> Optional[dict]:
    holding = next((chain_map for chain_map in chain_maps if is_inside_rect(region_cell, chain_map["rect"])), None)
    return holding


def point_exit_at_neighbouring_chain_map(route_exit: dict, chain_maps: Sequence[dict]) -> None:
    if route_exit.get("seam"):
        return
    step_x, step_y = STEP_TOWARD_SIDE[route_exit["side"]]
    target_cell = (route_exit["cell"][0] + step_x, route_exit["cell"][1] + step_y)
    target_chain_map = chain_map_holding(chain_maps, target_cell)
    if target_chain_map is None:
        return
    route_exit["to_map"] = target_chain_map["map"]
    route_exit["to_chain_map"] = target_chain_map["name"]


def connection_offset_in_metatiles(side: str, rect: Sequence[int], neighbour_rect: Sequence[int]) -> int:
    along_axis = 1 if side in ("east", "west") else 0
    offset_in_metatiles = (int(neighbour_rect[along_axis]) - int(rect[along_axis])) * CELL
    return offset_in_metatiles


def rect_of_exit_target(route_exit: dict, chain_maps: Sequence[dict], towns: Sequence) -> Optional[List[int]]:
    step_x, step_y = STEP_TOWARD_SIDE[route_exit["side"]]
    target_cell = (route_exit["cell"][0] + step_x, route_exit["cell"][1] + step_y)
    target_chain_map = chain_map_holding(chain_maps, target_cell)
    if target_chain_map is not None:
        return list(target_chain_map["rect"])
    target_town = next((town for town in towns if is_inside_rect(target_cell, town.rect)), None)
    target_rect = list(target_town.rect) if target_town is not None else None
    return target_rect


def set_connection_offsets(chain_maps: Sequence[dict], towns: Sequence) -> None:
    for chain_map in chain_maps:
        for route_exit in chain_map["exits"]:
            target_rect = rect_of_exit_target(route_exit, chain_maps, towns)
            if target_rect is not None:
                route_exit["offset"] = connection_offset_in_metatiles(route_exit["side"], chain_map["rect"], target_rect)


def gated_entrance_toward_of(world, route: dict) -> str:
    if not route.get("gate"):
        return ""
    from .gates import gate_block_for_route_spec

    gate_block = gate_block_for_route_spec(world, route, route.get("exits") or [])
    gated_entrance_toward = gate_block.get("entrance_toward") or ""
    return gated_entrance_toward


def chain_maps_of_world(world) -> List[dict]:
    chain_maps = [chain_map for route in world.routes
                  for chain_map in chain_maps_of_route(route, gated_entrance_toward_of(world, route))]
    for chain_map in chain_maps:
        for route_exit in chain_map["exits"]:
            point_exit_at_neighbouring_chain_map(route_exit, chain_maps)
    set_connection_offsets(chain_maps, world.cities)
    return chain_maps


def chain_summary(chain_maps: Sequence[dict], route_name: str) -> List[dict]:
    summary = [{"name": chain_map["name"], "id": chain_map["id"], "map": chain_map["map"], "rect": chain_map["rect"],
                "grid_cell": chain_map["chain"]["grid_cell"], "exits": chain_map["exits"]}
               for chain_map in chain_maps if chain_map["chain"]["route"] == route_name]
    return summary
