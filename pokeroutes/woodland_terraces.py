from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
from scipy.ndimage import binary_dilation

from .generate import WALKABLE
from .noise import fbm
from .scenery_layer import RAISED_GROUND_CLASS

TREE_CLASS = "f"
PLATEAU_GROUND_CLASSES = "gt"
BIOMES_WITH_WOODLAND_TERRACES = ("forest", "riverland", "meadow", "plains", "beach")
FRINGE_SHARE_OF_WOODLAND_EDGE_BY_BIOME = {"forest": 0.4, "riverland": 0.35, "meadow": 0.35, "plains": 0.3, "beach": 0.3}
SHORTEST_BUSH_ROW = 3
PLATEAU_SIZES_IN_CELLS = ((6, 7), (5, 7), (6, 6), (5, 6), (5, 5))
PLATEAU_TRIES = 40
PLATEAUS_PER_THOUSAND_CELLS = 1.2
KEEP_PLATEAUS_FROM_EXITS_IN_CELLS = 5

Cell = Tuple[int, int]


def raise_woodland_terraces(route) -> None:
    if route.spec.biome not in BIOMES_WITH_WOODLAND_TERRACES:
        return
    from .open_land import cells_that_need_their_trees
    from .stepped_terraces import seam_band_cells

    frozen_cells = seam_band_cells(route)
    keep_trees = cells_that_need_their_trees(route, frozen_cells)
    raise_bush_rows(route, keep_trees)
    raise_woodland_fringes(route, keep_trees)
    raise_small_plateaus_in_clearings(route)
    route.snap("woodland terraces")


def whole_tree_blocks(route) -> np.ndarray:
    is_tree = route.cls == TREE_CLASS
    in_whole_tree = np.zeros(is_tree.shape, dtype=bool)
    for block_y in range(0, route.h - 1, 2):
        for block_x in range(0, route.w - 1, 2):
            block = (slice(block_y, block_y + 2), slice(block_x, block_x + 2))
            in_whole_tree[block] = bool(is_tree[block].all())
    return in_whole_tree


def straight_runs(mask: np.ndarray, along_rows: bool) -> List[List[Cell]]:
    runs: List[List[Cell]] = []
    outer_count, inner_count = mask.shape if along_rows else mask.shape[::-1]
    for outer_index in range(outer_count):
        current_run: List[Cell] = []
        for inner_index in range(inner_count + 1):
            cell = (outer_index, inner_index) if along_rows else (inner_index, outer_index)
            if inner_index < inner_count and mask[cell]:
                current_run.append(cell)
                continue
            if len(current_run) >= SHORTEST_BUSH_ROW:
                runs.append(current_run)
            current_run = []
    return runs


def raise_bush_rows(route, keep_trees: np.ndarray) -> None:
    lone_bushes = (route.cls == TREE_CLASS) & ~whole_tree_blocks(route) & ~keep_trees
    for bush_run in straight_runs(lone_bushes, along_rows=True) + straight_runs(lone_bushes, along_rows=False):
        for bush_cell in bush_run:
            route.cls[bush_cell] = RAISED_GROUND_CLASS


def block_faces_open_ground(route, is_walkable: np.ndarray, block_y: int, block_x: int) -> bool:
    ring = is_walkable[max(0, block_y - 1):block_y + 3, max(0, block_x - 1):block_x + 3]
    faces_open_ground = bool(ring.any())
    return faces_open_ground


def raise_woodland_fringes(route, keep_trees: np.ndarray) -> None:
    is_walkable = np.isin(route.cls, list(WALKABLE))
    in_whole_tree = whole_tree_blocks(route)
    stretch_noise = fbm(route.h, route.w, 7.0, route.rng, octaves=2)
    fringe_blocks = [(block_y, block_x) for block_y in range(0, route.h - 1, 2) for block_x in range(0, route.w - 1, 2)
                     if in_whole_tree[block_y, block_x] and not keep_trees[block_y:block_y + 2, block_x:block_x + 2].any()
                     and block_faces_open_ground(route, is_walkable, block_y, block_x)]
    if not fringe_blocks:
        return
    fringe_noise = np.array([stretch_noise[block_y, block_x] for block_y, block_x in fringe_blocks])
    share = FRINGE_SHARE_OF_WOODLAND_EDGE_BY_BIOME[route.spec.biome]
    threshold = np.quantile(fringe_noise, 1.0 - share)
    chosen_blocks = {block for block, block_noise in zip(fringe_blocks, fringe_noise) if block_noise >= threshold}
    for block_y, block_x in chosen_blocks:
        if has_chosen_neighbour_block(chosen_blocks, block_y, block_x):
            route.cls[block_y:block_y + 2, block_x:block_x + 2] = RAISED_GROUND_CLASS


def has_chosen_neighbour_block(chosen_blocks: set, block_y: int, block_x: int) -> bool:
    neighbour_blocks = ((block_y + 2, block_x), (block_y - 2, block_x), (block_y, block_x + 2), (block_y, block_x - 2))
    has_neighbour = any(neighbour_block in chosen_blocks for neighbour_block in neighbour_blocks)
    return has_neighbour


def cells_kept_off_plateaus(route) -> np.ndarray:
    kept = np.zeros(route.cls.shape, dtype=bool)
    for path_cell in route.critical_path:
        kept[path_cell] = True
    for event in route.events:
        kept[event.y, event.x] = True
    exit_mouths = np.zeros(route.cls.shape, dtype=bool)
    for _exit, exit_cells in route.exits:
        for exit_cell in exit_cells:
            exit_mouths[exit_cell] = True
    reach = 2 * KEEP_PLATEAUS_FROM_EXITS_IN_CELLS + 1
    kept |= binary_dilation(exit_mouths, np.ones((reach, reach), dtype=bool))
    for zone_cell in route.zone_cells("special", "hidden", "trail"):
        kept[zone_cell] = True
    return kept


def plateau_cells(top: int, left: int, height_in_cells: int, width_in_cells: int) -> List[Cell]:
    cells = [(top + row, left + column) for row in range(height_in_cells) for column in range(width_in_cells)]
    return cells


def plateau_fits(route, is_plateau_ground: np.ndarray, kept_off: np.ndarray, top: int, left: int,
                 height_in_cells: int, width_in_cells: int) -> bool:
    if top < 3 or left < 3 or top + height_in_cells + 3 > route.h or left + width_in_cells + 3 > route.w:
        return False
    with_ring = (slice(top - 1, top + height_in_cells + 1), slice(left - 1, left + width_in_cells + 1))
    fits = bool(is_plateau_ground[with_ring].all()) and not kept_off[with_ring].any()
    return fits


def find_plateau_spot(route, is_plateau_ground: np.ndarray, kept_off: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
    for _try in range(PLATEAU_TRIES):
        height_in_cells, width_in_cells = PLATEAU_SIZES_IN_CELLS[int(route.rng.integers(0, len(PLATEAU_SIZES_IN_CELLS)))]
        if route.rng.random() < 0.5:
            height_in_cells, width_in_cells = width_in_cells, height_in_cells
        top = int(route.rng.integers(0, max(1, route.h - height_in_cells)))
        left = int(route.rng.integers(0, max(1, route.w - width_in_cells)))
        if plateau_fits(route, is_plateau_ground, kept_off, top, left, height_in_cells, width_in_cells):
            spot = (top, left, height_in_cells, width_in_cells)
            return spot
    return None


def raise_small_plateaus_in_clearings(route) -> None:
    kept_off = cells_kept_off_plateaus(route)
    plateau_count = int(PLATEAUS_PER_THOUSAND_CELLS * route.h * route.w / 1000.0 * (0.5 + route.rng.random()))
    for _plateau_number in range(plateau_count):
        is_plateau_ground = np.isin(route.cls, list(PLATEAU_GROUND_CLASSES))
        spot = find_plateau_spot(route, is_plateau_ground, kept_off)
        if spot is None:
            return
        for plateau_cell in plateau_cells(*spot):
            route.cls[plateau_cell] = RAISED_GROUND_CLASS
        top, left, height_in_cells, width_in_cells = spot
        kept_off[max(0, top - 3):top + height_in_cells + 3, max(0, left - 3):left + width_in_cells + 3] = True
