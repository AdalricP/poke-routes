from __future__ import annotations

import numpy as np
from scipy.ndimage import binary_dilation, binary_opening, label

from .noise import fbm

DIRT_CLASS = "p"
GRASS_CLASS = "g"
BIOMES_WITH_WORN_PATHS = ("plains", "meadow", "riverland", "forest", "beach")
WORN_SHARE_OF_THE_WAY_BY_BIOME = {"plains": 0.14, "meadow": 0.18, "riverland": 0.18, "forest": 0.2, "beach": 0.15}
EXIT_MOUTH_DIRT_DEPTH_IN_CELLS = 3
WIDTH_OF_THE_WORN_STRIP_IN_CELLS = 3
SMALLEST_WORN_PATCH_IN_CELLS = 5
TWO_BY_TWO = np.ones((2, 2), dtype=bool)


def wear_paths_into_patches(route) -> None:
    if route.spec.biome not in BIOMES_WITH_WORN_PATHS:
        return
    from .stepped_terraces import seam_band_cells

    frozen_cells = seam_band_cells(route)
    worn_patches = worn_patches_along_the_way(route, frozen_cells)
    exit_mouths = dirt_at_exit_mouths(route)
    is_loose_dirt = (route.cls == DIRT_CLASS) & ~frozen_cells & ~exit_mouths & ~worn_patches
    route.cls[is_loose_dirt] = GRASS_CLASS
    route.cls[worn_patches & (route.cls == GRASS_CLASS)] = DIRT_CLASS
    route.snap("worn paths")


def the_way_through(route) -> np.ndarray:
    on_the_way = np.zeros(route.cls.shape, dtype=bool)
    for path_cell in route.critical_path:
        on_the_way[path_cell] = True
    near_the_way = binary_dilation(on_the_way, np.ones((WIDTH_OF_THE_WORN_STRIP_IN_CELLS, WIDTH_OF_THE_WORN_STRIP_IN_CELLS), dtype=bool))
    return near_the_way


def worn_patches_along_the_way(route, frozen_cells: np.ndarray) -> np.ndarray:
    raw_noise = fbm(route.h, route.w, 3.0, route.rng, octaves=2)
    walked_ground = np.isin(route.cls, [DIRT_CLASS, GRASS_CLASS]) & the_way_through(route) & ~frozen_cells
    if not walked_ground.any():
        return walked_ground
    worn_share = WORN_SHARE_OF_THE_WAY_BY_BIOME[route.spec.biome]
    threshold = np.quantile(raw_noise[walked_ground], 1.0 - worn_share)
    worn = binary_opening(walked_ground & (raw_noise >= threshold), TWO_BY_TWO)
    pieces, piece_count = label(worn)
    if piece_count == 0:
        return worn
    piece_sizes = np.bincount(pieces.ravel())
    big_enough = worn & (piece_sizes[pieces] >= SMALLEST_WORN_PATCH_IN_CELLS)
    return big_enough


def dirt_at_exit_mouths(route) -> np.ndarray:
    from .generate import inward

    mouths = np.zeros(route.cls.shape, dtype=bool)
    for map_exit, exit_cells in route.exits:
        for exit_cell in exit_cells:
            for depth in range(EXIT_MOUTH_DIRT_DEPTH_IN_CELLS):
                mouths[inward(route, map_exit, exit_cell, depth)] = True
    return mouths
