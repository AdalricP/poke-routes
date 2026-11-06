import json
import threading
from collections import OrderedDict, deque
from dataclasses import replace
from typing import List, Optional, Tuple

import numpy as np

from .scenery_layer import DRAWN_CLASS_KEY, PLACED_PROPS_KEY
from .whole_route_spine import mask_of_squares, square_rects_of_spec

Cell = Tuple[int, int]
VOID_CLASS = "f"
WHOLE_ROUTES_KEPT_IN_MEMORY = 6
WALKABLE_CLASSES_FOR_EXIT_OPENINGS = set("gptdhyvzqnwbD")
OPENING_ROAD_CLASS = "g"
SLICED_DICT_KEYS_KEPT_AS_THEY_ARE = ("terrace_levels",)
EDGES_OPEN_TO_THE_SAME_ROUTE_KEY = "edges_open_to_the_same_route"

whole_routes_by_spec_text: "OrderedDict[str, object]" = OrderedDict()
whole_routes_lock = threading.Lock()


VOID_BORDER_DEPTH_IN_METATILES = 2
SAME_ROUTE_EDGE_KEPT_AS_CUT_IN_METATILES = 3


def void_and_its_border_mask(route) -> np.ndarray:
    from scipy.ndimage import binary_dilation

    square_rects = square_rects_of_spec(route.spec)
    if len(square_rects) < 2:
        no_void = np.zeros(route.cls.shape, dtype=bool)
        return no_void
    is_void = ~mask_of_squares(square_rects, route.h, route.w)
    border_structure = np.ones((2 * VOID_BORDER_DEPTH_IN_METATILES + 1, 2 * VOID_BORDER_DEPTH_IN_METATILES + 1), dtype=bool)
    is_void_or_border = binary_dilation(is_void, structure=border_structure)
    return is_void_or_border


def stage_whole_route_void(route) -> None:
    is_void_or_border = void_and_its_border_mask(route)
    if not is_void_or_border.any():
        return
    route.cls[is_void_or_border] = VOID_CLASS
    if route.protect is not None:
        route.protect[is_void_or_border] = False
    route.snap("whole route shape")


def whole_route_block_of_chain_spec(spec) -> Optional[dict]:
    region_block = spec.region if isinstance(getattr(spec, "region", None), dict) else {}
    whole_route_block = region_block.get("whole_route_map")
    if not isinstance(whole_route_block, dict) or not whole_route_block.get("spec"):
        return None
    return whole_route_block


def whole_route_generated_once(whole_spec_dict: dict):
    from .generate import generate
    from .region.seam_check import route_spec_object_from_dict

    spec_text = json.dumps(whole_spec_dict, sort_keys=True, default=str)
    with whole_routes_lock:
        if spec_text in whole_routes_by_spec_text:
            whole_routes_by_spec_text.move_to_end(spec_text)
            return whole_routes_by_spec_text[spec_text]
        whole_route = generate(route_spec_object_from_dict(whole_spec_dict))
        whole_routes_by_spec_text[spec_text] = whole_route
        while len(whole_routes_by_spec_text) > WHOLE_ROUTES_KEPT_IN_MEMORY:
            whole_routes_by_spec_text.popitem(last=False)
    return whole_route


def is_cell_inside_square(cell: Cell, square: Tuple[int, int, int, int]) -> bool:
    left, top, right, bottom = square
    is_inside = top <= cell[0] < bottom and left <= cell[1] < right
    return is_inside


def shifted_cell(cell, square) -> Cell:
    moved_cell = (int(cell[0]) - square[1], int(cell[1]) - square[0])
    return moved_cell


def sliced_zones(whole_route, square):
    from .generate import Zone

    zones = [Zone(zone.kind, [shifted_cell(cell, square) for cell in zone.cells if is_cell_inside_square(cell, square)], zone.name, dict(zone.meta))
             for zone in whole_route.zones]
    zones_with_cells = [zone for zone in zones if zone.cells]
    return zones_with_cells


def sliced_events(whole_route, square):
    from .generate import Event

    events = [Event(event.kind, event.y - square[1], event.x - square[0], dict(event.data))
              for event in whole_route.events if is_cell_inside_square((event.y, event.x), square)]
    return events


def sliced_props(props: List[dict], square) -> List[dict]:
    kept_props = [dict(prop, y=int(prop["y"]) - square[1], x=int(prop["x"]) - square[0])
                  for prop in props if is_cell_inside_square((int(prop["y"]), int(prop["x"])), square)]
    return kept_props


def shifted_classes_under_prop(placed_prop: dict, square) -> dict:
    shifted_classes = {}
    for cell_key, class_letter in (placed_prop.get("under") or {}).items():
        row_text, column_text = cell_key.split(",")
        moved_row, moved_column = shifted_cell((int(row_text), int(column_text)), square)
        shifted_classes[f"{moved_row},{moved_column}"] = class_letter
    return shifted_classes


def sliced_placed_props(whole_route, square) -> List[dict]:
    placed_props = [dict(placed_prop, y=int(placed_prop["y"]) - square[1], x=int(placed_prop["x"]) - square[0],
                         under=shifted_classes_under_prop(placed_prop, square))
                    for placed_prop in whole_route.__dict__.get(PLACED_PROPS_KEY) or []
                    if is_cell_inside_square((int(placed_prop["y"]), int(placed_prop["x"])), square)]
    return placed_props


def sliced_drawn_classes(whole_route, square) -> dict:
    drawn_classes = {shifted_cell(cell, square): drawn_letter
                     for cell, drawn_letter in (whole_route.__dict__.get(DRAWN_CLASS_KEY) or {}).items()
                     if is_cell_inside_square(cell, square)}
    return drawn_classes


def grid_slice(grid: Optional[np.ndarray], square):
    if grid is None:
        return None
    left, top, right, bottom = square
    sliced_grid = grid[top:bottom, left:right].copy()
    return sliced_grid


def sliced_snapshots(whole_route, square):
    snapshots = [(stage_name, grid_slice(class_grid, square)) for stage_name, class_grid in whole_route.snapshots]
    return snapshots


def exits_of_chain_map(route) -> list:
    from .generate import exit_cells

    exits = [(spec_exit, exit_cells(route, spec_exit)[1]) for spec_exit in route.spec.exits]
    return exits


def cells_from_edge_inward(route, spec_exit, edge_cell: Cell, depth_in_metatiles: int) -> List[Cell]:
    from .generate import inward

    inward_cells = [inward(route, spec_exit, edge_cell, depth) for depth in range(depth_in_metatiles)]
    return inward_cells


def walk_to_nearest_walkable(route, start_cell: Cell) -> List[Cell]:
    came_from = {start_cell: None}
    frontier = deque([start_cell])
    while frontier:
        cell = frontier.popleft()
        if cell != start_cell and route.cls[cell] in WALKABLE_CLASSES_FOR_EXIT_OPENINGS:
            walked_cells = [cell]
            while came_from[walked_cells[-1]] is not None:
                walked_cells.append(came_from[walked_cells[-1]])
            return walked_cells
        for row_step, column_step in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            next_cell = (cell[0] + row_step, cell[1] + column_step)
            if next_cell in came_from or not (1 <= next_cell[0] < route.h - 1 and 1 <= next_cell[1] < route.w - 1):
                continue
            came_from[next_cell] = cell
            frontier.append(next_cell)
    return []


def open_exit_into_slice(route, spec_exit, exit_cells_on_edge: List[Cell]) -> None:
    if getattr(spec_exit, "offset", None) is None and not spec_exit.to:
        return
    if any(route.cls[cell] in WALKABLE_CLASSES_FOR_EXIT_OPENINGS for cell in exit_cells_on_edge):
        return
    for edge_cell in exit_cells_on_edge:
        for cell in cells_from_edge_inward(route, spec_exit, edge_cell, 2):
            route.cls[cell] = OPENING_ROAD_CLASS
    middle_cell = exit_cells_on_edge[len(exit_cells_on_edge) // 2]
    for cell in walk_to_nearest_walkable(route, cells_from_edge_inward(route, spec_exit, middle_cell, 2)[-1]):
        if route.cls[cell] not in WALKABLE_CLASSES_FOR_EXIT_OPENINGS:
            route.cls[cell] = OPENING_ROAD_CLASS


def is_seam_to_this_route(spec_exit, chain_block: dict) -> bool:
    is_same_route = bool(chain_block) and spec_exit.to == chain_block.get("route")
    return is_same_route


def open_exits_to_other_maps(route) -> None:
    chain_block = route.spec.region.get("chain") or {}
    for spec_exit, exit_cells_on_edge in route.exits:
        if is_seam_to_this_route(spec_exit, chain_block):
            continue
        open_exit_into_slice(route, spec_exit, exit_cells_on_edge)


def stamp_between_route_seams(route) -> None:
    from .seams import stage_seams

    stage_seams(route)


def shared_edge_of_squares(square, neighbour_square) -> Optional[dict]:
    left, top, right, bottom = square
    neighbour_left, neighbour_top, neighbour_right, neighbour_bottom = neighbour_square
    overlap_across_rows = (max(top, neighbour_top), min(bottom, neighbour_bottom))
    overlap_across_columns = (max(left, neighbour_left), min(right, neighbour_right))
    sides_by_touch = [("east", right == neighbour_left, overlap_across_rows, top), ("west", left == neighbour_right, overlap_across_rows, top),
                      ("south", bottom == neighbour_top, overlap_across_columns, left), ("north", top == neighbour_bottom, overlap_across_columns, left)]
    for side, is_touching, (overlap_start, overlap_end), own_start in sides_by_touch:
        if is_touching and overlap_end > overlap_start:
            shared_edge = {"side": side, "start": overlap_start - own_start, "length": overlap_end - overlap_start}
            return shared_edge
    return None


def edges_open_to_the_same_route(square, all_squares) -> List[dict]:
    open_edges = [shared_edge for shared_edge in (shared_edge_of_squares(square, other_square)
                                                  for other_square in all_squares if tuple(other_square) != tuple(square))
                  if shared_edge is not None]
    return open_edges


def cells_of_open_edge(route, open_edge: dict, depth_in_metatiles: int) -> List[Cell]:
    side, start, length = open_edge["side"], int(open_edge["start"]), int(open_edge["length"])
    positions_along_edge = range(start, start + length)
    depths = range(depth_in_metatiles)
    cells_by_side = {"north": [(depth, position) for position in positions_along_edge for depth in depths],
                     "south": [(route.h - 1 - depth, position) for position in positions_along_edge for depth in depths],
                     "west": [(position, depth) for position in positions_along_edge for depth in depths],
                     "east": [(position, route.w - 1 - depth) for position in positions_along_edge for depth in depths]}
    open_cells = [cell for cell in cells_by_side[side] if 0 <= cell[0] < route.h and 0 <= cell[1] < route.w]
    return open_cells


def tidy_ledges_of_slice(route) -> None:
    from .stepped_terraces import seam_band_cells, tidy_ledge_joins

    frozen_cells = seam_band_cells(route)
    for open_edge in route.__dict__[EDGES_OPEN_TO_THE_SAME_ROUTE_KEY]:
        for cell in cells_of_open_edge(route, open_edge, SAME_ROUTE_EDGE_KEPT_AS_CUT_IN_METATILES):
            frozen_cells[cell] = True
    tidy_ledge_joins(route, frozen_cells)


def slice_of_whole_route(whole_route, chain_spec, square, all_squares):
    from .generate import Route, density
    from .walkability_guard import run_keeping_walkability

    route = Route(chain_spec, grid_slice(whole_route.cls, square), np.random.default_rng(chain_spec.seed))
    route.zones = sliced_zones(whole_route, square)
    route.events = sliced_events(whole_route, square)
    route.props = sliced_props(whole_route.props, square)
    route.protect = grid_slice(whole_route.protect, square)
    route.snapshots = sliced_snapshots(whole_route, square)
    route.log = [f"cut from the whole route ({whole_route.w}x{whole_route.h}) at {list(square)}"] + list(whole_route.log)
    route.critical_path = [shifted_cell(cell, square) for cell in whole_route.critical_path if is_cell_inside_square(cell, square)]
    route.elevation = grid_slice(whole_route.elevation, square)
    route.gates = [dict(gate) for gate in whole_route.gates]
    route.__dict__[PLACED_PROPS_KEY] = sliced_placed_props(whole_route, square)
    route.__dict__[DRAWN_CLASS_KEY] = sliced_drawn_classes(whole_route, square)
    for kept_key in SLICED_DICT_KEYS_KEPT_AS_THEY_ARE:
        if kept_key in whole_route.__dict__:
            route.__dict__[kept_key] = whole_route.__dict__[kept_key]
    route.__dict__[EDGES_OPEN_TO_THE_SAME_ROUTE_KEY] = edges_open_to_the_same_route(square, all_squares)
    route.exits = exits_of_chain_map(route)
    open_exits_to_other_maps(route)
    stamp_between_route_seams(route)
    run_keeping_walkability(route, "ledge ends after the cut", tidy_ledges_of_slice)
    route.snap("cut from the whole route")
    route.stats = density(route)
    return route


def chain_map_cut_from_whole_route(chain_spec):
    whole_route_block = whole_route_block_of_chain_spec(chain_spec)
    whole_route = whole_route_generated_once(whole_route_block["spec"])
    square = tuple(int(edge) for edge in whole_route_block["square"])
    chain_spec_with_its_size = replace(chain_spec, size=[square[2] - square[0], square[3] - square[1]])
    all_squares = [tuple(int(edge) for edge in other_square) for other_square in whole_route_block["spec"]["region"]["whole_route"]["squares"]]
    sliced_route = slice_of_whole_route(whole_route, chain_spec_with_its_size, square, all_squares)
    return sliced_route
