from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np
from scipy.ndimage import label

from .generate import WALKABLE
from .ruletile import LEDGE_LETTERS, ledge_arm_key

SHORTEST_CLEAN_LEDGE_RUN_IN_CELLS = 3
ARM_STEPS = ((-1, 0), (0, 1), (1, 0), (0, -1))
ROW_LEDGE_LETTERS = "sj"

Cell = Tuple[int, int]


def arms_of_ledge_cell(grid: List[str], cell_y: int, cell_x: int) -> List[int]:
    arm_key = ledge_arm_key(grid, cell_x, cell_y)
    arms = [arm_index for arm_index, arm in enumerate(arm_key) if arm != "."]
    return arms


def is_open_ground_at(cls: np.ndarray, cell_y: int, cell_x: int) -> bool:
    height_in_cells, width_in_cells = cls.shape
    inside = 0 <= cell_y < height_in_cells and 0 <= cell_x < width_in_cells
    open_ground = inside and str(cls[cell_y, cell_x]) in WALKABLE
    return open_ground


def ends_in_the_open(cls: np.ndarray, cell_y: int, cell_x: int, arm_index: int) -> bool:
    step_y, step_x = ARM_STEPS[(arm_index + 2) % 4]
    ledge_letter = str(cls[cell_y, cell_x])
    along_row = ledge_letter in ROW_LEDGE_LETTERS
    runs_on = (step_y == 0) == along_row
    open_end = runs_on and is_open_ground_at(cls, cell_y + step_y, cell_x + step_x)
    return open_end


def broken_ledge_joins(cls: np.ndarray) -> Dict[str, List[Cell]]:
    grid = ["".join(str(letter) for letter in row) for row in cls]
    is_ledge = np.isin(cls, list(LEDGE_LETTERS))
    pieces, _piece_count = label(is_ledge)
    piece_sizes = np.bincount(pieces.ravel())
    broken: Dict[str, List[Cell]] = {"lone ledge": [], "short ledge run": [], "ledge ending in the open": [], "tangled ledge join": []}
    for cell_y, cell_x in np.argwhere(is_ledge):
        cell = (int(cell_y), int(cell_x))
        arms = arms_of_ledge_cell(grid, *cell)
        if not arms:
            broken["lone ledge"].append(cell)
        elif piece_sizes[pieces[cell]] < SHORTEST_CLEAN_LEDGE_RUN_IN_CELLS:
            broken["short ledge run"].append(cell)
        elif len(arms) == 1 and ends_in_the_open(cls, *cell, arms[0]):
            broken["ledge ending in the open"].append(cell)
        elif len(arms) >= 3:
            broken["tangled ledge join"].append(cell)
    return broken


def broken_ledge_join_count(cls: np.ndarray) -> int:
    count = sum(len(cells) for cells in broken_ledge_joins(cls).values())
    return count
