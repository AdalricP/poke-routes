from __future__ import annotations

from typing import List, Tuple

import numpy as np
from scipy.ndimage import binary_dilation, binary_opening, distance_transform_cdt, gaussian_filter, label, zoom

from .generate import WALKABLE
from .scenery_layer import RAISED_GROUND_CLASS
from .seams import SEAM_BAND_DEPTH_IN_METATILES, band_cell, edge_length_of_side, seam_bands_of_route

BUSH_CLASS = "f"
OPEN_GROUND_CLASS = "g"
PLAIN_GROUND_CLASSES = "gtpd"
EXIT_CORRIDOR_KEPT_OPEN_IN_CELLS = 6
NOTCH_FILL_PASSES = 4
RING_AROUND_A_CELL = ((-1, -1), (-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1))
ORTHOGONAL_RING_INDICES = (1, 3, 5, 7)
LEDGE_CLASSES = "sejx"
SMALLEST_TERRACE_PIECE_IN_CELLS = 20
LEDGE_TIDY_PASSES = 3
REGION_HEIGHT_WEIGHT = 0.75
WORLD_HEIGHT_DETAIL_WEIGHT = 0.25
HEIGHT_SMOOTHING_IN_CELLS = 2.0
HIGH_GROUND_SHARE_OF_INNER_RAISED_GROUND = 0.55
HIGHEST_TERRACE_LEVEL = 2
SQUARE_OF_NINE = np.ones((3, 3), dtype=bool)
LOW_SIDE_LEDGES = (((1, 0), "s"), ((-1, 0), "j"), ((0, 1), "e"), ((0, -1), "x"),
                   ((1, -1), "s"), ((1, 1), "s"), ((-1, -1), "j"), ((-1, 1), "j"))

Cell = Tuple[int, int]


def lay_stepped_terraces(route) -> None:
    frozen_cells = seam_band_cells(route)
    absorb_bushes_into_raised_ground(route, frozen_cells)
    turn_thin_raised_ground_into_open_ground(route, frozen_cells)
    raise_ground_just_inside_raised_seam_bands(route, frozen_cells)
    fill_notches_in_raised_ground(route)
    turn_thin_raised_ground_into_open_ground(route, frozen_cells)
    terrace_levels = levels_of_raised_ground(route)
    route.__dict__["_low_trees"] = trees_standing_on_low_ground(route, frozen_cells)
    route.__dict__["terrace_levels"] = terrace_levels
    ledge_cells = ledge_cells_along_level_steps(route, terrace_levels, frozen_cells)
    for (cell_y, cell_x), ledge_letter in ledge_cells:
        route.cls[cell_y, cell_x] = ledge_letter
    open_back_to_back_ledges(route, frozen_cells)
    tidy_ledge_joins(route, frozen_cells)
    route.snap("stepped terraces")


def back_to_back_ledge_cells(route) -> np.ndarray:
    cls = route.cls
    back_to_back = np.zeros(cls.shape, dtype=bool)
    north_over_south = (cls[:-1, :] == "j") & (cls[1:, :] == "s")
    back_to_back[:-1, :] |= north_over_south
    back_to_back[1:, :] |= north_over_south
    west_beside_east = (cls[:, :-1] == "x") & (cls[:, 1:] == "e")
    back_to_back[:, :-1] |= west_beside_east
    back_to_back[:, 1:] |= west_beside_east
    return back_to_back


def open_back_to_back_ledges(route, frozen_cells: np.ndarray) -> None:
    from .generate import reachable

    back_to_back = back_to_back_ledge_cells(route) & ~frozen_cells
    if not back_to_back.any():
        return
    route.cls[back_to_back] = OPEN_GROUND_CLASS
    if route.exits:
        reached = reachable(route.cls, route.exits[0][1])
        route.cls[back_to_back & ~reached] = BUSH_CLASS


def seam_band_cells(route) -> np.ndarray:
    is_band_cell = np.zeros(route.cls.shape, dtype=bool)
    for seam_band in seam_bands_of_route(route):
        side, start_along_edge = seam_band["side"], int(seam_band["start"])
        edge_length = edge_length_of_side(route, side)
        for band_index in range(len(seam_band["classes"])):
            position_along_edge = start_along_edge + band_index
            if not 0 <= position_along_edge < edge_length:
                continue
            for depth_from_edge in range(SEAM_BAND_DEPTH_IN_METATILES):
                is_band_cell[band_cell(route, side, position_along_edge, depth_from_edge)] = True
    return is_band_cell


def raised_ground_mask(route) -> np.ndarray:
    is_raised = route.cls == RAISED_GROUND_CLASS
    return is_raised


def opened_with_map_edge_raised(mask: np.ndarray) -> np.ndarray:
    padded = np.pad(mask, 2, constant_values=True)
    opened = binary_opening(padded, SQUARE_OF_NINE)[2:-2, 2:-2]
    return opened


def lone_bushes(route) -> np.ndarray:
    is_tree = route.cls == BUSH_CLASS
    in_whole_tree = np.zeros(is_tree.shape, dtype=bool)
    for block_y in range(0, route.h - 1, 2):
        for block_x in range(0, route.w - 1, 2):
            block = (slice(block_y, block_y + 2), slice(block_x, block_x + 2))
            in_whole_tree[block] = bool(is_tree[block].all())
    bushes = is_tree & ~in_whole_tree
    return bushes


def absorb_bushes_into_raised_ground(route, frozen_cells: np.ndarray) -> None:
    bushes = lone_bushes(route) & ~frozen_cells
    is_raised = raised_ground_mask(route)
    touching_raised = bushes & binary_dilation(is_raised, np.ones((3, 3), dtype=bool))
    thick_with_bushes = opened_with_map_edge_raised(is_raised | touching_raised | frozen_cells)
    route.cls[touching_raised & thick_with_bushes] = RAISED_GROUND_CLASS


def turn_thin_raised_ground_into_open_ground(route, frozen_cells: np.ndarray) -> None:
    from .export import SEAL_DEPTH

    is_raised = raised_ground_mask(route)
    thick_raised = opened_with_map_edge_raised(is_raised | frozen_cells | (route.cls == BUSH_CLASS))
    thin_raised = is_raised & ~thick_raised & ~frozen_cells
    in_edge_ring = distance_to_map_edge(route) < SEAL_DEPTH
    route.cls[thin_raised & in_edge_ring] = BUSH_CLASS
    route.cls[thin_raised & ~in_edge_ring] = OPEN_GROUND_CLASS
    if not route.exits:
        return
    from .generate import reachable

    reached = reachable(route.cls, route.exits[0][1])
    route.cls[thin_raised & ~in_edge_ring & ~reached] = BUSH_CLASS


def distance_to_map_edge(route) -> np.ndarray:
    rows = np.arange(route.h)[:, None]
    columns = np.arange(route.w)[None, :]
    distance = np.minimum(np.minimum(rows, route.h - 1 - rows), np.minimum(columns, route.w - 1 - columns))
    return distance


def cells_that_must_stay_open(route) -> np.ndarray:
    from .generate import inward

    must_stay_open = np.zeros(route.cls.shape, dtype=bool)
    for map_exit, exit_cells in route.exits:
        for exit_cell in exit_cells:
            for depth in range(EXIT_CORRIDOR_KEPT_OPEN_IN_CELLS):
                must_stay_open[inward(route, map_exit, exit_cell, depth)] = True
    for path_cell in route.critical_path:
        must_stay_open[path_cell] = True
    for event in route.events:
        must_stay_open[event.y, event.x] = True
    return must_stay_open


def walkable_runs_around(is_walkable: np.ndarray, cell_y: int, cell_x: int) -> List[List[int]]:
    height_in_cells, width_in_cells = is_walkable.shape
    ring_walkable = [0 <= cell_y + step_y < height_in_cells and 0 <= cell_x + step_x < width_in_cells
                     and bool(is_walkable[cell_y + step_y, cell_x + step_x]) for step_y, step_x in RING_AROUND_A_CELL]
    if all(ring_walkable):
        whole_ring = [list(range(len(RING_AROUND_A_CELL)))]
        return whole_ring
    first_closed_index = ring_walkable.index(False)
    runs: List[List[int]] = []
    for offset in range(1, len(RING_AROUND_A_CELL) + 1):
        ring_index = (first_closed_index + offset) % len(RING_AROUND_A_CELL)
        if not ring_walkable[ring_index]:
            continue
        previous_index = (ring_index - 1) % len(RING_AROUND_A_CELL)
        if ring_walkable[previous_index] and runs:
            runs[-1].append(ring_index)
        else:
            runs.append([ring_index])
    return runs


def is_safe_to_block(is_walkable: np.ndarray, cell_y: int, cell_x: int) -> bool:
    runs_touching_the_cell = [run for run in walkable_runs_around(is_walkable, cell_y, cell_x)
                              if any(ring_index in ORTHOGONAL_RING_INDICES for ring_index in run)]
    safe = len(runs_touching_the_cell) <= 1
    return safe


def raised_neighbour_count(route, cell_y: int, cell_x: int) -> int:
    count = sum(1 for step_y, step_x in ((1, 0), (-1, 0), (0, 1), (0, -1))
                if not (0 <= cell_y + step_y < route.h and 0 <= cell_x + step_x < route.w)
                or route.cls[cell_y + step_y, cell_x + step_x] == RAISED_GROUND_CLASS)
    return count


def fill_notches_in_raised_ground(route) -> None:
    must_stay_open = cells_that_must_stay_open(route)
    for _pass in range(NOTCH_FILL_PASSES):
        is_walkable = np.isin(route.cls, list(WALKABLE))
        candidates = np.argwhere(np.isin(route.cls, list(PLAIN_GROUND_CLASSES)) & ~must_stay_open)
        filled_count = 0
        for cell_y, cell_x in candidates:
            cell_y, cell_x = int(cell_y), int(cell_x)
            if raised_neighbour_count(route, cell_y, cell_x) < 3 or not is_safe_to_block(is_walkable, cell_y, cell_x):
                continue
            route.cls[cell_y, cell_x] = RAISED_GROUND_CLASS
            is_walkable[cell_y, cell_x] = False
            filled_count += 1
        if filled_count == 0:
            return


def raise_ground_just_inside_raised_seam_bands(route, frozen_cells: np.ndarray) -> None:
    from .export import SEAL_DEPTH

    must_stay_open = cells_that_must_stay_open(route)
    is_walkable = np.isin(route.cls, list(WALKABLE))
    is_plain_ground = np.isin(route.cls, list(PLAIN_GROUND_CLASSES))
    raised_band = frozen_cells & (route.cls == RAISED_GROUND_CLASS)
    beside_raised_band = binary_dilation(raised_band, np.ones((3, 3), dtype=bool)) & (distance_to_map_edge(route) == SEAL_DEPTH)
    for cell_y, cell_x in np.argwhere(beside_raised_band & is_plain_ground & ~must_stay_open):
        cell_y, cell_x = int(cell_y), int(cell_x)
        if is_safe_to_block(is_walkable, cell_y, cell_x):
            route.cls[cell_y, cell_x] = RAISED_GROUND_CLASS
            is_walkable[cell_y, cell_x] = False


def distance_into_raised_ground(is_raised: np.ndarray) -> np.ndarray:
    padded = np.pad(is_raised, 1, constant_values=True)
    distance = distance_transform_cdt(padded, metric="chessboard")[1:-1, 1:-1]
    return distance


def without_small_pieces(mask: np.ndarray) -> np.ndarray:
    labels, piece_count = label(mask)
    if piece_count == 0:
        return mask
    piece_sizes = np.bincount(labels.ravel())
    kept = mask & (piece_sizes[labels] >= SMALLEST_TERRACE_PIECE_IN_CELLS)
    return kept


def region_fine_heights(route) -> np.ndarray:
    region_block = route.spec.region if isinstance(getattr(route.spec, "region", None), dict) else {}
    fine_rows = (region_block.get("elevation") or {}).get("fine") if region_block else None
    if not fine_rows:
        return np.zeros((route.h, route.w))
    region_cell_size = int(region_block.get("cell", 4) or 4)
    fine_grid = np.array([[int(digit) for digit in row] for row in fine_rows], dtype=float) / 9.0
    padded = np.pad(fine_grid, 1, mode="edge")
    upsampled = zoom(padded, region_cell_size, order=1)
    offset = region_cell_size - region_cell_size // 2
    cropped = upsampled[offset:offset + route.h, offset:offset + route.w]
    heights = np.pad(cropped, ((0, route.h - cropped.shape[0]), (0, route.w - cropped.shape[1])), mode="edge")
    return heights


def geography_heights(route) -> np.ndarray:
    from .height import origin, world_height

    origin_x, origin_y = origin(route.spec)
    world_xs = origin_x + np.arange(route.w)[None, :] + 0.5
    world_ys = origin_y + np.arange(route.h)[:, None] + 0.5
    world_detail = world_height(world_xs, world_ys)
    heights = REGION_HEIGHT_WEIGHT * region_fine_heights(route) + WORLD_HEIGHT_DETAIL_WEIGHT * world_detail
    smoothed = gaussian_filter(heights, HEIGHT_SMOOTHING_IN_CELLS, mode="nearest")
    return smoothed


def levels_of_raised_ground(route) -> np.ndarray:
    is_raised = raised_ground_mask(route)
    levels = is_raised.astype(np.int8)
    heights = geography_heights(route)
    route.__dict__["geography_heights"] = heights
    previous_level_mask = is_raised
    for level in range(2, HIGHEST_TERRACE_LEVEL + 1):
        inner = inner_part_of(previous_level_mask)
        if not inner.any():
            break
        contour_height = float(np.quantile(heights[inner], 1.0 - HIGH_GROUND_SHARE_OF_INNER_RAISED_GROUND))
        reaches_level = (heights >= contour_height) & inner
        reaches_level = without_small_pieces(opened_with_map_edge_raised(reaches_level) & previous_level_mask)
        reaches_level &= inner
        levels[reaches_level] = level
        previous_level_mask = reaches_level
    return levels


def inner_part_of(mask: np.ndarray) -> np.ndarray:
    padded = np.pad(mask, 1, constant_values=True)
    inner = distance_transform_cdt(padded, metric="chessboard")[1:-1, 1:-1] >= 3
    return inner


def trees_standing_on_low_ground(route, frozen_cells: np.ndarray) -> np.ndarray:
    is_walkable = np.isin(route.cls, list(WALKABLE))
    near_walkable = binary_dilation(is_walkable, np.ones((5, 5), dtype=bool))
    low_trees = (route.cls == BUSH_CLASS) & near_walkable & ~frozen_cells
    return low_trees


def is_lower_open_ground(route, terrace_levels: np.ndarray, own_level: int, cell_y: int, cell_x: int, is_below: bool) -> bool:
    if not (0 <= cell_y < route.h and 0 <= cell_x < route.w):
        return False
    neighbour_class = str(route.cls[cell_y, cell_x])
    is_low_tree = bool(route.__dict__["_low_trees"][cell_y, cell_x]) and not is_below
    is_open_ground = neighbour_class in WALKABLE or neighbour_class == RAISED_GROUND_CLASS or is_low_tree
    is_lower = terrace_levels[cell_y, cell_x] < own_level
    return is_open_ground and is_lower


def ledge_letter_of(route, terrace_levels: np.ndarray, cell_y: int, cell_x: int) -> str:
    own_level = int(terrace_levels[cell_y, cell_x])
    for (step_y, step_x), ledge_letter in LOW_SIDE_LEDGES:
        if is_lower_open_ground(route, terrace_levels, own_level, cell_y + step_y, cell_x + step_x, is_below=step_y > 0):
            return ledge_letter
    return ""


def ledge_cells_along_level_steps(route, terrace_levels: np.ndarray, frozen_cells: np.ndarray) -> List[tuple]:
    raised_cells = np.argwhere((terrace_levels > 0) & ~frozen_cells & (route.cls == RAISED_GROUND_CLASS))
    ledge_cells = [((int(cell_y), int(cell_x)), ledge_letter) for cell_y, cell_x in raised_cells
                   for ledge_letter in [ledge_letter_of(route, terrace_levels, int(cell_y), int(cell_x))] if ledge_letter]
    return ledge_cells


def tidy_ledge_joins(route, frozen_cells: np.ndarray) -> None:
    from .ledge_joins import ARM_STEPS, arms_of_ledge_cell, broken_ledge_joins

    must_stay_open = cells_that_must_stay_open(route)
    for _pass in range(LEDGE_TIDY_PASSES):
        broken = broken_ledge_joins(route.cls)
        changed = False
        for cell in broken["lone ledge"] + broken["short ledge run"]:
            if not frozen_cells[cell]:
                route.cls[cell] = BUSH_CLASS
                changed = True
        grid = ["".join(str(letter) for letter in row) for row in route.cls]
        is_walkable = np.isin(route.cls, list(WALKABLE))
        for cell_y, cell_x in broken["ledge ending in the open"]:
            arms = arms_of_ledge_cell(grid, cell_y, cell_x)
            if len(arms) != 1:
                continue
            step_y, step_x = ARM_STEPS[(arms[0] + 2) % 4]
            cap_y, cap_x = cell_y + step_y, cell_x + step_x
            if frozen_cells[cap_y, cap_x] or must_stay_open[cap_y, cap_x] or not is_safe_to_block(is_walkable, cap_y, cap_x):
                continue
            route.cls[cap_y, cap_x] = BUSH_CLASS
            is_walkable[cap_y, cap_x] = False
            changed = True
        if not changed:
            return
