from __future__ import annotations

from typing import List, Tuple

import numpy as np

from scipy.ndimage import binary_dilation

from .generate import WALKABLE
from .rivers import RIVER_CELLS_KEY
from .noise import stretched_fbm

TALL_GRASS_CLASS = "t"
GRASS_CLASS = "g"
WATER_CLASS = "w"
TREE_CLASS = "f"
TALL_GRASS_GROWTH_NOISE = 0.62
TALL_GRASS_BITE_NOISE = 0.38
TALL_GRASS_CORNER_BITE_CHANCE = 0.6
POND_BITE_NOISE = 0.35
POND_MARGIN_FROM_MAP_EDGE = 3
ORTHOGONAL_STEPS = ((1, 0), (-1, 0), (0, 1), (0, -1))

Cell = Tuple[int, int]


def soften_shapes(route) -> None:
    roughen_tall_grass(route)
    round_ponds(route)
    route.snap("soft shapes")


def normalised_noise(route, scale_in_cells: float) -> np.ndarray:
    normalised = stretched_fbm(route.h, route.w, scale_in_cells, route.rng, octaves=2)
    return normalised


def orthogonal_classes(route, cell_y: int, cell_x: int) -> List[str]:
    classes = [str(route.cls[cell_y + step_y, cell_x + step_x]) if 0 <= cell_y + step_y < route.h and 0 <= cell_x + step_x < route.w else ""
               for step_y, step_x in ORTHOGONAL_STEPS]
    return classes


def is_outer_corner(neighbour_classes: List[str], own_class: str) -> bool:
    below, above, right, left = (neighbour_class == own_class for neighbour_class in neighbour_classes)
    is_corner = (below != above) and (right != left) and (below + above + right + left) == 2
    return is_corner


def roughen_tall_grass(route) -> None:
    noise = normalised_noise(route, 3.0)
    is_tall_grass = route.cls == TALL_GRASS_CLASS
    if not is_tall_grass.any():
        return
    changes = []
    for cell_y, cell_x in np.argwhere(np.isin(route.cls, [TALL_GRASS_CLASS, GRASS_CLASS])):
        cell_y, cell_x = int(cell_y), int(cell_x)
        new_class = softened_grass_class(route, noise, cell_y, cell_x)
        if new_class:
            changes.append(((cell_y, cell_x), new_class))
    for cell, new_class in changes:
        route.cls[cell] = new_class


def softened_grass_class(route, noise: np.ndarray, cell_y: int, cell_x: int) -> str:
    own_class = str(route.cls[cell_y, cell_x])
    neighbour_classes = orthogonal_classes(route, cell_y, cell_x)
    tall_neighbours = neighbour_classes.count(TALL_GRASS_CLASS)
    if own_class == GRASS_CLASS:
        grows = tall_neighbours >= 1 and noise[cell_y, cell_x] > TALL_GRASS_GROWTH_NOISE
        return TALL_GRASS_CLASS if grows else ""
    on_the_rim = tall_neighbours < 4
    if on_the_rim and noise[cell_y, cell_x] < TALL_GRASS_BITE_NOISE and GRASS_CLASS in neighbour_classes:
        return GRASS_CLASS
    if is_outer_corner(neighbour_classes, TALL_GRASS_CLASS) and route.rng.random() < TALL_GRASS_CORNER_BITE_CHANCE:
        return GRASS_CLASS
    return ""


def pond_cells(route) -> np.ndarray:
    is_pond = np.zeros(route.cls.shape, dtype=bool)
    for zone in route.zones:
        if zone.kind == "lake" and is_a_pond(zone):
            for zone_cell in zone.cells:
                is_pond[zone_cell] = True
    river_cells = route.__dict__.get(RIVER_CELLS_KEY)
    if river_cells is not None and river_cells.shape == is_pond.shape:
        is_pond &= ~binary_dilation(river_cells, np.ones((3, 3), dtype=bool))
    away_from_map_edge = np.zeros(is_pond.shape, dtype=bool)
    away_from_map_edge[POND_MARGIN_FROM_MAP_EDGE:-POND_MARGIN_FROM_MAP_EDGE, POND_MARGIN_FROM_MAP_EDGE:-POND_MARGIN_FROM_MAP_EDGE] = True
    pond_water = is_pond & (route.cls == WATER_CLASS) & away_from_map_edge
    return pond_water


def is_a_pond(zone) -> bool:
    is_pond = not any(zone.meta.get(flag) for flag in ("river", "frozen", "crater", "region_lake", "sea")) and "sea" not in zone.name
    return is_pond


def land_class_beside(route, cell_y: int, cell_x: int) -> str:
    neighbour_classes = orthogonal_classes(route, cell_y, cell_x)
    land_class = GRASS_CLASS if any(neighbour_class in WALKABLE for neighbour_class in neighbour_classes) else TREE_CLASS
    return land_class


def water_covered_by_two_by_two_blocks(is_water: np.ndarray) -> np.ndarray:
    whole_blocks = is_water[:-1, :-1] & is_water[1:, :-1] & is_water[:-1, 1:] & is_water[1:, 1:]
    covered = np.zeros_like(is_water)
    for step_y in (0, 1):
        for step_x in (0, 1):
            covered[step_y:step_y + whole_blocks.shape[0], step_x:step_x + whole_blocks.shape[1]] |= whole_blocks
    return covered


def drain_thin_pond_water(route, was_pond: np.ndarray) -> None:
    is_water = np.isin(route.cls, [WATER_CLASS, "q", "a"])
    thin_pond_water = was_pond & (route.cls == WATER_CLASS) & ~water_covered_by_two_by_two_blocks(is_water)
    for cell_y, cell_x in np.argwhere(thin_pond_water):
        route.cls[cell_y, cell_x] = land_class_beside(route, int(cell_y), int(cell_x))


def round_ponds(route) -> None:
    from .glitches import crooked_bridges

    is_pond = pond_cells(route)
    if not is_pond.any():
        return
    noise = normalised_noise(route, 2.5)
    bitten_cells = []
    for cell_y, cell_x in np.argwhere(is_pond):
        cell_y, cell_x = int(cell_y), int(cell_x)
        neighbour_classes = orthogonal_classes(route, cell_y, cell_x)
        on_the_rim = neighbour_classes.count(WATER_CLASS) < 4
        if is_outer_corner(neighbour_classes, WATER_CLASS) or (on_the_rim and noise[cell_y, cell_x] < POND_BITE_NOISE):
            bitten_cells.append((cell_y, cell_x))
    crooked_bridges_before = len(crooked_bridges(route.cls))
    for cell_y, cell_x in bitten_cells:
        class_before = str(route.cls[cell_y, cell_x])
        route.cls[cell_y, cell_x] = land_class_beside(route, cell_y, cell_x)
        if len(crooked_bridges(route.cls)) > crooked_bridges_before:
            route.cls[cell_y, cell_x] = class_before
    drain_thin_pond_water(route, is_pond)
