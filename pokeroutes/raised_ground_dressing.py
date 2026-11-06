from __future__ import annotations

from typing import List, Tuple

import numpy as np

from .noise import fbm
from .scenery_layer import RAISED_GROUND_CLASS, draw_raised_ground_as, drawn_class_by_cell
from .woodland_closeness import woodland_closeness

TREE_CLASS = "f"
SEAM_BAND_PLACEHOLDER_CLASS = "O"
FENCE_CLASS = "c"
TALL_GRASS_CLASS = "t"
ROW_LEDGES = "sj"
COLUMN_LEDGES = "ex"
WALKABLE_GROUND_FOR_FENCE_CHECK = "gptdzq"
CLASSES_A_CROWN_MAY_HANG_OVER = "of"
TALL_GRASS_SHARE_OF_RAISED_GROUND = 0.45
TREE_CHANCE_ON_RAISED_GROUND = 0.02
EXTRA_TREE_CHANCE_BESIDE_WOODLAND = 0.5
EXTRA_TREE_CHANCE_BESIDE_WOODLAND_BY_OPEN_BIOME = {"plains": 0.1, "meadow": 0.14, "beach": 0.1}
TALL_GRASS_SWEEP_SCALE_IN_CELLS = 9.0
BUSH_CLUSTER_CHANCE_PER_CELL = 0.006
EXTRA_BUSH_CLUSTER_CHANCE_BESIDE_WOODLAND = 0.03
BUSH_CLUSTER_SIZES = (2, 5)
FENCE_CHANCE_PER_LONG_LEDGE_RUN = 0.35
SHORTEST_LEDGE_RUN_FOR_A_FENCE = 6
FENCE_LENGTHS = (3, 5)
STEPS_TO_ORTHOGONAL_NEIGHBOURS = ((1, 0), (-1, 0), (0, 1), (0, -1))
HIGH_SIDE_STEP_BY_LEDGE = {"s": (-1, 0), "j": (1, 0), "e": (0, -1), "x": (0, 1)}

Cell = Tuple[int, int]


def dress_raised_ground(route) -> None:
    if not (route.cls == RAISED_GROUND_CLASS).any():
        return
    from .stepped_terraces import seam_band_cells

    closeness = woodland_closeness(route)
    band_cells = seam_band_cells(route)
    route.cls[band_cells & (route.cls == RAISED_GROUND_CLASS)] = SEAM_BAND_PLACEHOLDER_CLASS
    plant_trees_on_raised_ground(route, closeness)
    plant_bush_clusters_on_raised_ground(route, closeness)
    run_fences_behind_ledges(route)
    sweep_tall_grass_over_raised_ground(route)
    route.cls[route.cls == SEAM_BAND_PLACEHOLDER_CLASS] = RAISED_GROUND_CLASS
    route.snap("raised ground dressing")


def is_inside(route, cell_y: int, cell_x: int) -> bool:
    inside = 0 <= cell_y < route.h and 0 <= cell_x < route.w
    return inside


def crown_has_room(route, block_y: int, block_x: int) -> bool:
    if block_y == 0:
        return True
    above_classes = {str(route.cls[block_y - 1, column]) for column in (block_x, block_x + 1)}
    has_room = above_classes <= set(CLASSES_A_CROWN_MAY_HANG_OVER)
    return has_room


def plant_trees_on_raised_ground(route, closeness: np.ndarray) -> None:
    extra_tree_chance = EXTRA_TREE_CHANCE_BESIDE_WOODLAND_BY_OPEN_BIOME.get(route.spec.biome, EXTRA_TREE_CHANCE_BESIDE_WOODLAND)
    for block_y in range(0, route.h - 1, 2):
        for block_x in range(0, route.w - 1, 2):
            block = (slice(block_y, block_y + 2), slice(block_x, block_x + 2))
            if not (route.cls[block] == RAISED_GROUND_CLASS).all() or not crown_has_room(route, block_y, block_x):
                continue
            tree_chance = TREE_CHANCE_ON_RAISED_GROUND + extra_tree_chance * float(closeness[block].mean())
            if route.rng.random() < tree_chance:
                route.cls[block] = TREE_CLASS


def grow_cluster(route, start_cell: Cell, cell_count: int, can_hold) -> List[Cell]:
    cluster = [start_cell]
    for _attempt in range(cell_count * 4):
        if len(cluster) >= cell_count:
            break
        from_y, from_x = cluster[int(route.rng.integers(0, len(cluster)))]
        step_y, step_x = STEPS_TO_ORTHOGONAL_NEIGHBOURS[int(route.rng.integers(0, 4))]
        next_cell = (from_y + step_y, from_x + step_x)
        if next_cell not in cluster and is_inside(route, *next_cell) and can_hold(next_cell):
            cluster.append(next_cell)
    return cluster


def plant_bush_clusters_on_raised_ground(route, closeness: np.ndarray) -> None:
    def is_open_raised(cell: Cell) -> bool:
        return route.cls[cell] == RAISED_GROUND_CLASS

    for cell_y, cell_x in np.argwhere(route.cls == RAISED_GROUND_CLASS):
        cluster_chance = BUSH_CLUSTER_CHANCE_PER_CELL + EXTRA_BUSH_CLUSTER_CHANCE_BESIDE_WOODLAND * float(closeness[cell_y, cell_x])
        if route.cls[cell_y, cell_x] != RAISED_GROUND_CLASS or route.rng.random() >= cluster_chance:
            continue
        cluster_size = int(route.rng.integers(BUSH_CLUSTER_SIZES[0], BUSH_CLUSTER_SIZES[1] + 1))
        for bush_cell in grow_cluster(route, (int(cell_y), int(cell_x)), cluster_size, is_open_raised):
            route.cls[bush_cell] = TREE_CLASS


def ledge_runs(route, ledge_letters: str, along_rows: bool) -> List[List[Cell]]:
    runs: List[List[Cell]] = []
    outer_count, inner_count = (route.h, route.w) if along_rows else (route.w, route.h)
    for outer_index in range(outer_count):
        current_run: List[Cell] = []
        for inner_index in range(inner_count + 1):
            cell = (outer_index, inner_index) if along_rows else (inner_index, outer_index)
            is_ledge = inner_index < inner_count and str(route.cls[cell]) in ledge_letters
            if is_ledge:
                current_run.append(cell)
                continue
            if current_run:
                runs.append(current_run)
            current_run = []
    return runs



def fence_cell_is_clear(route, cell: Cell) -> bool:
    if not is_inside(route, *cell) or route.cls[cell] != RAISED_GROUND_CLASS:
        return False
    touches_walkable = any(is_inside(route, cell[0] + step_y, cell[1] + step_x)
                           and str(route.cls[cell[0] + step_y, cell[1] + step_x]) in WALKABLE_GROUND_FOR_FENCE_CHECK
                           for step_y, step_x in STEPS_TO_ORTHOGONAL_NEIGHBOURS)
    is_clear = not touches_walkable
    return is_clear


def fence_behind_run(route, ledge_run: List[Cell]) -> None:
    ledge_letter = str(route.cls[ledge_run[0]])
    high_step_y, high_step_x = HIGH_SIDE_STEP_BY_LEDGE[ledge_letter]
    fence_length = int(route.rng.integers(FENCE_LENGTHS[0], FENCE_LENGTHS[1] + 1))
    start_index = int(route.rng.integers(1, max(2, len(ledge_run) - fence_length)))
    fence_cells = [(ledge_cell[0] + high_step_y, ledge_cell[1] + high_step_x)
                   for ledge_cell in ledge_run[start_index:start_index + fence_length]]
    if len(fence_cells) >= FENCE_LENGTHS[0] and all(fence_cell_is_clear(route, cell) for cell in fence_cells):
        for fence_cell in fence_cells:
            route.cls[fence_cell] = FENCE_CLASS


def run_fences_behind_ledges(route) -> None:
    every_run = ledge_runs(route, ROW_LEDGES, along_rows=True) + ledge_runs(route, COLUMN_LEDGES, along_rows=False)
    for ledge_run in every_run:
        same_letter = len({str(route.cls[cell]) for cell in ledge_run}) == 1
        if same_letter and len(ledge_run) >= SHORTEST_LEDGE_RUN_FOR_A_FENCE and route.rng.random() < FENCE_CHANCE_PER_LONG_LEDGE_RUN:
            fence_behind_run(route, ledge_run)


def sweep_tall_grass_over_raised_ground(route) -> None:
    is_raised = route.cls == RAISED_GROUND_CLASS
    if not is_raised.any():
        return
    sweep_noise = fbm(route.h, route.w, TALL_GRASS_SWEEP_SCALE_IN_CELLS, route.rng, octaves=3)
    threshold = np.quantile(sweep_noise[is_raised], 1.0 - TALL_GRASS_SHARE_OF_RAISED_GROUND)
    for cell_y, cell_x in np.argwhere(is_raised & (sweep_noise >= threshold)):
        draw_raised_ground_as(route, (int(cell_y), int(cell_x)), TALL_GRASS_CLASS)


def is_drawn_as_tall_grass(route, cell: Cell) -> bool:
    drawn_as_tall_grass = drawn_class_by_cell(route).get(cell) == TALL_GRASS_CLASS
    return drawn_as_tall_grass
