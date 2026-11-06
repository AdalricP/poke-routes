"""Tree masses on the 2x2 grid. GBA trees are 2x2 metatiles on even rows and
columns; a tree cell whose 2x2 block is half ground can only be drawn as a bush,
which is how rows of bushes end up stacked on top of tree rows. Each half-tree
block is settled one way or the other: filled with trees when its ground is
nobody's path, cleared to ground when that only widens the lane beside it, left
alone otherwise. Nothing walkable that was reachable gets cut off."""
from __future__ import annotations

from typing import List, Set, Tuple

import numpy as np

from .generate import DIRS4, WALKABLE, Route, reachable

Cell = Tuple[int, int]

TREE_CLASS = "f"
GROUND_CLASSES_A_TREE_MAY_GROW_OVER = "gt"
LARGEST_REVERT_ROUNDS = 6


def settle_half_tree_blocks(route: Route) -> None:
    from .stages import ground_class

    if not (route.cls == TREE_CLASS).any():
        return
    kept_open_cells = cells_that_must_stay_open(route)
    original_classes = route.cls.copy()
    reach_before = reachable(route.cls, route.exits[0][1])
    filled_blocks, cleared_blocks = [], []
    for block_cells in half_tree_blocks(original_classes):
        if can_fill_block_with_trees(route, original_classes, block_cells, kept_open_cells):
            filled_blocks.append(block_cells)
        elif can_clear_block_to_ground(route, original_classes, block_cells):
            cleared_blocks.append(block_cells)
    for block_cells in filled_blocks:
        for cell in block_cells:
            route.cls[cell] = TREE_CLASS
    for block_cells in cleared_blocks:
        for cell in block_cells:
            if original_classes[cell] == TREE_CLASS:
                route.cls[cell] = ground_class(route)
    reverted_count = revert_fills_that_cut_something_off(route, original_classes, filled_blocks, reach_before)
    route.log.append(f"tree blocks: {len(filled_blocks) - reverted_count} half-tree blocks filled, "
                     f"{len(cleared_blocks)} cleared to ground, {reverted_count} left as they were")


def cells_that_must_stay_open(route: Route) -> Set[Cell]:
    critical_path_cells = set(route.critical_path)
    exit_cells = {cell for _exit, cells in route.exits for cell in cells}
    event_cells = {(event.y, event.x) for event in route.events}
    prop_cells = {(int(prop["y"]), int(prop["x"])) for prop in route.props if "y" in prop and "x" in prop}
    kept_open_cells = critical_path_cells | exit_cells | event_cells | prop_cells
    return kept_open_cells


def half_tree_blocks(class_grid: np.ndarray) -> List[List[Cell]]:
    height, width = class_grid.shape
    blocks = []
    for block_top in range(0, height - 1, 2):
        for block_left in range(0, width - 1, 2):
            block_cells = [(block_top + row, block_left + column) for row in (0, 1) for column in (0, 1)]
            tree_count = sum(1 for cell in block_cells if class_grid[cell] == TREE_CLASS)
            if 0 < tree_count < 4:
                blocks.append(block_cells)
    return blocks


def can_fill_block_with_trees(route: Route, original_classes: np.ndarray, block_cells: List[Cell],
                              kept_open_cells: Set[Cell]) -> bool:
    ground_cells = [cell for cell in block_cells if original_classes[cell] != TREE_CLASS]
    is_fillable = all(original_classes[cell] in GROUND_CLASSES_A_TREE_MAY_GROW_OVER and not route.protect[cell]
                      and cell not in kept_open_cells for cell in ground_cells)
    return is_fillable


def can_clear_block_to_ground(route: Route, original_classes: np.ndarray, block_cells: List[Cell]) -> bool:
    tree_cells = [cell for cell in block_cells if original_classes[cell] == TREE_CLASS]
    if len(tree_cells) > 2:
        return False
    block_cell_set = set(block_cells)
    opens_onto_other_ground = any(opens_onto_ground_outside_block(route, original_classes, cell, block_cell_set)
                                  for cell in tree_cells)
    is_clearable = not opens_onto_other_ground
    return is_clearable


def opens_onto_ground_outside_block(route: Route, original_classes: np.ndarray, tree_cell: Cell, block_cell_set: Set[Cell]) -> bool:
    for step_y, step_x in DIRS4:
        neighbour = (tree_cell[0] + step_y, tree_cell[1] + step_x)
        if neighbour in block_cell_set or not route.inside(*neighbour):
            continue
        if str(original_classes[neighbour]) in WALKABLE:
            return True
    return False


def revert_fills_that_cut_something_off(route: Route, original_classes: np.ndarray, filled_blocks: List[List[Cell]],
                                        reach_before: np.ndarray) -> int:
    reverted_blocks: Set[int] = set()
    for _round in range(LARGEST_REVERT_ROUNDS):
        lost = reach_before & ~reachable(route.cls, route.exits[0][1]) & (route.cls != TREE_CLASS)
        if not lost.any():
            break
        for block_index, block_cells in enumerate(filled_blocks):
            if block_index in reverted_blocks or not block_touches_mask(block_cells, lost):
                continue
            for cell in block_cells:
                route.cls[cell] = original_classes[cell]
            reverted_blocks.add(block_index)
    return len(reverted_blocks)


def block_touches_mask(block_cells: List[Cell], mask: np.ndarray) -> bool:
    height, width = mask.shape
    touches = any(mask[cell[0] + step_y, cell[1] + step_x]
                  for cell in block_cells for step_y in (-1, 0, 1) for step_x in (-1, 0, 1)
                  if 0 <= cell[0] + step_y < height and 0 <= cell[1] + step_x < width)
    return touches
