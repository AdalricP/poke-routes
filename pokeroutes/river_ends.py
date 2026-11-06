from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np

Cell = Tuple[int, int]

RIVER_WATER_CLASSES = "w"
BRIDGE_CLASS = "q"
WALKABLE_CLASSES_BESIDE_A_RIVER_END = "gptdyvz"
WIDEST_RIVER_IN_METATILES = 4
SHORTEST_RIVER_RUN_BEHIND_AN_END_IN_METATILES = 5
STEPS = ((0, 1), (0, -1), (1, 0), (-1, 0))


def is_inside(class_grid: np.ndarray, y: int, x: int) -> bool:
    is_inside_grid = 0 <= y < class_grid.shape[0] and 0 <= x < class_grid.shape[1]
    return is_inside_grid


def is_river_water(class_grid: np.ndarray, y: int, x: int) -> bool:
    is_water = is_inside(class_grid, y, x) and str(class_grid[y, x]) in RIVER_WATER_CLASSES
    return is_water


def water_run_length(class_grid: np.ndarray, start: Cell, step: Cell, longest_counted: int) -> int:
    run_length = 0
    y, x = start
    while run_length < longest_counted and is_river_water(class_grid, y, x):
        run_length += 1
        y, x = y + step[0], x + step[1]
    reaches_map_edge = run_length < longest_counted and not is_inside(class_grid, y, x)
    counted_length = longest_counted if reaches_map_edge else run_length
    return counted_length


def river_width_across(class_grid: np.ndarray, cell: Cell, step: Cell) -> int:
    across_step = (step[1], step[0])
    backwards_step = (-across_step[0], -across_step[1])
    longest_counted = WIDEST_RIVER_IN_METATILES + 2
    width_in_metatiles = (water_run_length(class_grid, cell, across_step, longest_counted)
                          + water_run_length(class_grid, cell, backwards_step, longest_counted) - 1)
    return width_in_metatiles


def capped_river_cell(class_grid: np.ndarray, land_cell: Cell, step: Cell) -> Optional[Cell]:
    water_cell = (land_cell[0] + step[0], land_cell[1] + step[1])
    if not is_river_water(class_grid, *water_cell):
        return None
    runs_on_behind_the_end = water_run_length(class_grid, water_cell, step, SHORTEST_RIVER_RUN_BEHIND_AN_END_IN_METATILES) \
        >= SHORTEST_RIVER_RUN_BEHIND_AN_END_IN_METATILES
    is_narrow = river_width_across(class_grid, water_cell, step) <= WIDEST_RIVER_IN_METATILES
    if not (runs_on_behind_the_end and is_narrow and is_capped_across(class_grid, water_cell, step)):
        return None
    return water_cell


def is_capped_across(class_grid: np.ndarray, water_cell: Cell, step: Cell) -> bool:
    cells_behind_the_cap = [(cell[0] - step[0], cell[1] - step[1]) for cell in water_cells_across(class_grid, water_cell, step)]
    is_capped = not any(is_inside(class_grid, *cell) and str(class_grid[cell]) in RIVER_WATER_CLASSES + BRIDGE_CLASS
                        for cell in cells_behind_the_cap)
    return is_capped


def is_end_of_a_river(class_grid: np.ndarray, river_mask: np.ndarray, land_cell: Cell, step: Cell) -> bool:
    water_cell = capped_river_cell(class_grid, land_cell, step)
    is_river_end = water_cell is not None and bool(river_mask[water_cell])
    return is_river_end


def walkable_river_end_steps(class_grid: np.ndarray, river_mask: np.ndarray) -> List[Tuple[Cell, Cell]]:
    land_cells = zip(*np.nonzero(np.isin(class_grid, list(WALKABLE_CLASSES_BESIDE_A_RIVER_END))))
    end_steps = [((int(y), int(x)), step) for y, x in land_cells for step in STEPS
                 if is_end_of_a_river(class_grid, river_mask, (int(y), int(x)), step)]
    return end_steps


def band_water_mask(route) -> np.ndarray:
    from scipy.ndimage import label

    from .seams import SEAM_BAND_DEPTH_IN_METATILES, band_cell, seam_bands_of_route

    band_water = np.zeros(route.cls.shape, dtype=bool)
    for seam_band in seam_bands_of_route(route):
        for band_index, band_class in enumerate(seam_band["classes"]):
            position_along_edge = int(seam_band["start"]) + band_index
            if band_class not in RIVER_WATER_CLASSES:
                continue
            for depth_from_edge in range(SEAM_BAND_DEPTH_IN_METATILES):
                cell = band_cell(route, seam_band["side"], position_along_edge, depth_from_edge)
                if is_inside(route.cls, *cell):
                    band_water[cell] = True
    labelled, _count = label(np.isin(route.cls, list(RIVER_WATER_CLASSES)))
    band_labels = np.unique(labelled[band_water & (labelled > 0)])
    water_joined_to_bands = np.isin(labelled, band_labels) & (labelled > 0)
    return water_joined_to_bands


def river_water_mask(route) -> np.ndarray:
    from .rivers import RIVER_CELLS_KEY

    remembered_rivers = route.__dict__.get(RIVER_CELLS_KEY)
    remembered_rivers = remembered_rivers if remembered_rivers is not None else np.zeros(route.cls.shape, dtype=bool)
    rivers = remembered_rivers | band_water_mask(route)
    return rivers


def walkable_river_ends(route) -> List[Cell]:
    class_grid = np.asarray(route.cls).astype(str)
    open_ends = [land_cell for land_cell, _step in walkable_river_end_steps(class_grid, river_water_mask(route))]
    return open_ends


ROCK_CLASS = "r"
ROCK_CAP_DEPTH_IN_METATILES = 2
SEAM_RING_DEPTH_IN_METATILES = 2
MOST_CAPPING_PASSES = 4
SURF_WATER_MARGIN_IN_METATILES = 2


def is_in_seam_ring(class_grid: np.ndarray, cell: Cell) -> bool:
    height, width = class_grid.shape
    depth = SEAM_RING_DEPTH_IN_METATILES
    in_ring = cell[0] < depth or cell[1] < depth or cell[0] >= height - depth or cell[1] >= width - depth
    return in_ring


def water_cells_across(class_grid: np.ndarray, cell: Cell, step: Cell) -> List[Cell]:
    across_cells = [cell]
    for across in ((step[1], step[0]), (-step[1], -step[0])):
        next_cell = (cell[0] + across[0], cell[1] + across[1])
        while is_river_water(class_grid, *next_cell):
            across_cells.append(next_cell)
            next_cell = (next_cell[0] + across[0], next_cell[1] + across[1])
    return across_cells


def rock_cap_cells(class_grid: np.ndarray, land_cell: Cell, step: Cell, cap_depth: int) -> List[Cell]:
    cap_cells = []
    for depth in range(1, cap_depth + 1):
        cell = (land_cell[0] + step[0] * depth, land_cell[1] + step[1] * depth)
        if not is_river_water(class_grid, *cell):
            break
        across_cells = water_cells_across(class_grid, cell, step)
        if len(across_cells) > WIDEST_RIVER_IN_METATILES:
            break
        cap_cells += across_cells
    cap_cells_off_the_seams = [cell for cell in cap_cells if not is_in_seam_ring(class_grid, cell)]
    return cap_cells_off_the_seams


def surf_water_mask(route) -> np.ndarray:
    from scipy.ndimage import binary_dilation

    from .region_water import SURF_CROSSING_KEY

    surf_water = np.zeros(route.cls.shape, dtype=bool)
    for crossing_mask in (route.__dict__.get(SURF_CROSSING_KEY) or {}).values():
        surf_water |= crossing_mask
    surf_water_with_margin = binary_dilation(surf_water, iterations=SURF_WATER_MARGIN_IN_METATILES) if surf_water.any() else surf_water
    return surf_water_with_margin


def cap_river_end(route, cap_cells: List[Cell]) -> int:
    from .glitches import crooked_bridges

    crooked_before = len(crooked_bridges(route.cls))
    classes_before = [str(route.cls[cell]) for cell in cap_cells]
    for cell in cap_cells:
        route.cls[cell] = ROCK_CLASS
    if len(crooked_bridges(route.cls)) <= crooked_before:
        return len(cap_cells)
    for cell, class_before in zip(cap_cells, classes_before):
        route.cls[cell] = class_before
    return 0


def cap_river_end_as_deep_as_bridges_allow(route, land_cell: Cell, step: Cell, surf_water: np.ndarray) -> int:
    for cap_depth in range(ROCK_CAP_DEPTH_IN_METATILES, 0, -1):
        cap_cells = [cell for cell in rock_cap_cells(route.cls, land_cell, step, cap_depth) if not surf_water[cell]]
        if not cap_cells or not all(is_river_water(route.cls, *cell) for cell in cap_cells):
            continue
        capped_count = cap_river_end(route, cap_cells)
        if capped_count:
            return capped_count
    return 0


def block_open_river_ends(route) -> int:
    rocked_count = 0
    surf_water = surf_water_mask(route)
    rivers = river_water_mask(route)
    for _pass in range(MOST_CAPPING_PASSES):
        end_steps = walkable_river_end_steps(route.cls, rivers)
        if not end_steps:
            break
        capped_count = sum(cap_river_end_as_deep_as_bridges_allow(route, land_cell, step, surf_water) for land_cell, step in end_steps)
        rocked_count += capped_count
        if not capped_count:
            break
    if rocked_count:
        route.log.append(f"river ends: {rocked_count} water cells at river ends the player could walk up to became rocks")
    return rocked_count
