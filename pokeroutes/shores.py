"""Shores the player sees up close: no single ground cell sticking out into water,
no islet smaller than 2x2, and no sign standing in water."""
from __future__ import annotations

from collections import Counter
from typing import List, Tuple

import numpy as np

from .generate import WALKABLE, Route
from .surf_crossing import drop_things_left_standing_in_water
from .glitches import (WATER_CLASSES, cells_of_components, ground_spikes_into_water, has_square_of_side, is_surrounded_by_water,
                       touches_map_edge)

Cell = Tuple[int, int]

SMALLEST_ISLET_SIDE_IN_METATILES = 2
MOST_SPIKE_PASSES = 4
SEAM_RING_DEPTH_IN_METATILES = 2
FARTHEST_SIGN_MOVE_IN_METATILES = 3
STEPS = ((0, 1), (1, 0), (0, -1), (-1, 0))


def exit_cell_mask(route: Route) -> np.ndarray:
    exit_cells = np.zeros(route.cls.shape, dtype=bool)
    for _exit, cells in route.exits:
        for cell in cells:
            exit_cells[cell] = True
    return exit_cells


def is_in_seam_ring(route: Route, cell: Cell) -> bool:
    depth = SEAM_RING_DEPTH_IN_METATILES
    in_ring = cell[0] < depth or cell[1] < depth or cell[0] >= route.h - depth or cell[1] >= route.w - depth
    return in_ring


def water_class_beside(class_grid: np.ndarray, cell: Cell) -> str:
    y, x = cell
    window = class_grid[max(0, y - 1):y + 2, max(0, x - 1):x + 2].ravel()
    water_letters = [str(letter) for letter in window if str(letter) in WATER_CLASSES]
    water_class = Counter(water_letters).most_common(1)[0][0] if water_letters else "w"
    return water_class


def flood_ground_spikes(route: Route) -> int:
    exit_cells = exit_cell_mask(route)
    flooded_count = 0
    for _pass in range(MOST_SPIKE_PASSES):
        spikes = [cell for cell in ground_spikes_into_water(route.cls) if not exit_cells[cell] and not is_in_seam_ring(route, cell)]
        if not spikes:
            break
        for cell in spikes:
            route.cls[cell] = water_class_beside(route.cls, cell)
        flooded_count += len(spikes)
    return flooded_count


def islets_of(route: Route) -> List[List[Cell]]:
    land = ~np.isin(route.cls, list(WATER_CLASSES + "q"))
    islets = [cells for cells in cells_of_components(land)
              if not touches_map_edge(route.cls, cells) and is_surrounded_by_water(route.cls, cells)]
    return islets


def cells_off_every_square(cells: List[Cell]) -> List[Cell]:
    cell_set = set(cells)
    side = SMALLEST_ISLET_SIDE_IN_METATILES
    covered = {(y + dy, x + dx) for y, x in cells
               if all((y + oy, x + ox) in cell_set for oy in range(side) for ox in range(side))
               for dy in range(side) for dx in range(side)}
    loose_cells = [cell for cell in cells if cell not in covered]
    return loose_cells


def trim_islets(route: Route) -> int:
    trimmed_count = 0
    for islet_cells in islets_of(route):
        loose_cells = islet_cells if not has_square_of_side(islet_cells, SMALLEST_ISLET_SIDE_IN_METATILES) else cells_off_every_square(islet_cells)
        for cell in loose_cells:
            route.cls[cell] = water_class_beside(route.cls, cell)
        trimmed_count += len(loose_cells)
    return trimmed_count


def nearest_dry_cell(route: Route, cell: Cell, taken_cells: set) -> Cell:
    candidates = [(abs(dy) + abs(dx), (cell[0] + dy, cell[1] + dx))
                  for dy in range(-FARTHEST_SIGN_MOVE_IN_METATILES, FARTHEST_SIGN_MOVE_IN_METATILES + 1)
                  for dx in range(-FARTHEST_SIGN_MOVE_IN_METATILES, FARTHEST_SIGN_MOVE_IN_METATILES + 1)
                  if route.inside(cell[0] + dy, cell[1] + dx) and (cell[0] + dy, cell[1] + dx) not in taken_cells
                  and str(route.cls[cell[0] + dy, cell[1] + dx]) in WALKABLE and str(route.cls[cell[0] + dy, cell[1] + dx]) != "q"]
    nearest = min(candidates)[1] if candidates else None
    return nearest


def move_signs_out_of_water(route: Route) -> int:
    taken_cells = {(event.y, event.x) for event in route.events}
    kept_events, moved_count = [], 0
    for event in route.events:
        if event.kind != "sign" or str(route.cls[event.y, event.x]) not in WATER_CLASSES + "q":
            kept_events.append(event)
            continue
        dry_cell = nearest_dry_cell(route, (event.y, event.x), taken_cells)
        moved_count += 1
        if dry_cell is None:
            continue
        event.y, event.x = dry_cell
        taken_cells.add(dry_cell)
        kept_events.append(event)
    route.events = kept_events
    return moved_count


def tidy_shores(route: Route) -> None:
    flooded_count = flood_ground_spikes(route)
    trimmed_count = trim_islets(route)
    moved_sign_count = move_signs_out_of_water(route)
    drop_things_left_standing_in_water(route)
    if flooded_count or trimmed_count or moved_sign_count:
        route.log.append(f"shores: {flooded_count} ground spikes and {trimmed_count} islet cells smaller than 2x2 became water, "
                         f"{moved_sign_count} signs moved out of the water")
