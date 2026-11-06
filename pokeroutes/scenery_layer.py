from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np

RAISED_GROUND_CLASS = "o"
TREE_CLASS = "f"
DRAWN_CLASS_KEY = "drawn_class_by_cell"
PLACED_PROPS_KEY = "placed_props"

Cell = Tuple[int, int]


def drawn_class_by_cell(route) -> Dict[Cell, str]:
    drawn_classes = route.__dict__.setdefault(DRAWN_CLASS_KEY, {})
    return drawn_classes


def cells_under_tree_crown_reach(class_grid: np.ndarray) -> np.ndarray:
    """A tree's crown reaches up into the row above its 2-tall trunk block, drawing
    over whatever is there. A cell at (y, x) is in that reach when the two cells
    straight below it, (y + 1, x) and (y + 2, x), are both tree trunk."""
    height_in_cells = class_grid.shape[0]
    is_tree = np.asarray(class_grid) == TREE_CLASS
    reach = np.zeros(class_grid.shape, dtype=bool)
    if height_in_cells >= 3:
        reach[: height_in_cells - 2, :] = is_tree[1 : height_in_cells - 1, :] & is_tree[2:height_in_cells, :]
    return reach


def cell_is_under_tree_crown_reach(class_grid: np.ndarray, cell: Cell) -> bool:
    cell_y, cell_x = cell
    height_in_cells = class_grid.shape[0]
    if cell_y + 2 >= height_in_cells:
        return False
    under_crown = str(class_grid[cell_y + 1, cell_x]) == TREE_CLASS and str(class_grid[cell_y + 2, cell_x]) == TREE_CLASS
    return under_crown


def placed_props(route) -> List[dict]:
    props = route.__dict__.setdefault(PLACED_PROPS_KEY, [])
    return props


def draw_raised_ground_as(route, cell: Cell, drawn_letter: str) -> None:
    drawn_class_by_cell(route)[(int(cell[0]), int(cell[1]))] = drawn_letter


def place_prop(route, prop_name: str, top_left_cell: Cell, footprint_cells: List[Cell]) -> None:
    classes_under_prop = {f"{cell_y},{cell_x}": str(route.cls[cell_y, cell_x]) for cell_y, cell_x in footprint_cells}
    placed_prop = {"prop": prop_name, "y": int(top_left_cell[0]), "x": int(top_left_cell[1]), "under": classes_under_prop}
    placed_props(route).append(placed_prop)


def footprint_of_prop(prop_table: dict, prop_name: str, top_left_cell: Cell) -> List[Cell]:
    prop_rows = prop_table[prop_name]["cells"]
    footprint = [(top_left_cell[0] + row_index, top_left_cell[1] + column_index)
                 for row_index, prop_row in enumerate(prop_rows) for column_index in range(len(prop_row))]
    return footprint


def class_grid_as_drawn(route, class_grid: np.ndarray) -> np.ndarray:
    drawn_grid = np.array(class_grid, copy=True)
    for (cell_y, cell_x), drawn_letter in drawn_class_by_cell(route).items():
        if 0 <= cell_y < drawn_grid.shape[0] and 0 <= cell_x < drawn_grid.shape[1] and drawn_grid[cell_y, cell_x] == RAISED_GROUND_CLASS:
            drawn_grid[cell_y, cell_x] = drawn_letter
    return drawn_grid


def prop_still_fits(class_grid: np.ndarray, placed_prop: dict) -> bool:
    height_in_cells, width_in_cells = class_grid.shape
    for cell_key, class_under_prop in placed_prop["under"].items():
        cell_y, cell_x = (int(part) for part in cell_key.split(","))
        if not (0 <= cell_y < height_in_cells and 0 <= cell_x < width_in_cells):
            return False
        if str(class_grid[cell_y, cell_x]) != class_under_prop:
            return False
    return True


def stamp_prop(layout, prop_definition: dict, placed_prop: dict) -> List[Cell]:
    blocked_rows = prop_definition.get("blocked")
    stamped_cells = []
    for row_index, prop_row in enumerate(prop_definition["cells"]):
        for column_index, metatile_id in enumerate(prop_row):
            cell = (placed_prop["y"] + row_index, placed_prop["x"] + column_index)
            is_blocked = blocked_rows[row_index][column_index] if blocked_rows else prop_definition.get("collision", 0)
            layout.metatile[cell] = int(metatile_id)
            if is_blocked:
                layout.collision[cell] = 1
                layout.elevation[cell] = 0
            stamped_cells.append(cell)
    return stamped_cells


def stamp_placed_props(route, class_grid: np.ndarray, layout, prop_table: dict) -> Dict[Cell, int]:
    crown_reach = cells_under_tree_crown_reach(class_grid)
    stamped_metatiles: Dict[Cell, int] = {}
    for placed_prop in placed_props(route):
        prop_definition = prop_table.get(placed_prop["prop"])
        if prop_definition is None or not prop_still_fits(class_grid, placed_prop):
            continue
        footprint = footprint_of_prop(prop_table, placed_prop["prop"], (placed_prop["y"], placed_prop["x"]))
        if any(crown_reach[cell] for cell in footprint):
            continue
        for cell in stamp_prop(layout, prop_definition, placed_prop):
            stamped_metatiles[cell] = int(layout.metatile[cell])
    return stamped_metatiles


def block_raised_ground(class_grid: np.ndarray, layout) -> None:
    is_raised_ground = np.asarray(class_grid) == RAISED_GROUND_CLASS
    layout.collision[is_raised_ground] = 1
