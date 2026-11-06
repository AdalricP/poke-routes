from __future__ import annotations

import numpy as np
from scipy.ndimage import binary_opening, distance_transform_cdt, label

from .noise import fbm

SAND_CLASS = "d"
GRASS_CLASS = "g"
SEA_AND_LAKE_CLASSES = "bwh"
BIOMES_WITH_SAND_ALONG_WIDE_WATER = ("plains", "meadow", "riverland", "forest")
BEACH_BAND_DEPTHS_IN_CELLS = (3, 6)
RIVER_BANK_DEPTHS_IN_CELLS = (1, 3)
SMALLEST_WATER_BODY_WITH_A_SANDY_BANK_IN_CELLS = 40
SHARE_OF_THE_BANK_THAT_IS_SANDY = 0.55
TWO_BY_TWO = np.ones((2, 2), dtype=bool)


def keep_sand_to_the_shore(route) -> None:
    if route.spec.biome == "beach":
        narrow_beach_sand_to_a_band(route)
        return
    if route.spec.biome in BIOMES_WITH_SAND_ALONG_WIDE_WATER:
        lay_sand_along_wide_water(route)


def distance_from(mask: np.ndarray) -> np.ndarray:
    if not mask.any():
        far_away = np.full(mask.shape, 10 ** 6)
        return far_away
    distance = distance_transform_cdt(~mask, metric="chessboard")
    return distance


def band_depth_per_cell(route, shallowest_in_cells: int, deepest_in_cells: int) -> np.ndarray:
    raw_noise = fbm(route.h, route.w, 6.0, route.rng, octaves=2)
    noise_range = max(1e-6, float(raw_noise.max() - raw_noise.min()))
    normalised = (raw_noise - raw_noise.min()) / noise_range
    depth = shallowest_in_cells + normalised * (deepest_in_cells - shallowest_in_cells)
    return depth


def frozen_band_cells(route) -> np.ndarray:
    from .stepped_terraces import seam_band_cells

    frozen = seam_band_cells(route)
    return frozen


def narrow_beach_sand_to_a_band(route) -> None:
    water_distance = distance_from(np.isin(route.cls, list(SEA_AND_LAKE_CLASSES)))
    beach_depth = band_depth_per_cell(route, *BEACH_BAND_DEPTHS_IN_CELLS)
    from .worn_ground import dirt_at_exit_mouths

    inland_sand = (route.cls == SAND_CLASS) & (water_distance > beach_depth) & ~frozen_band_cells(route) & ~dirt_at_exit_mouths(route)
    route.cls[inland_sand] = GRASS_CLASS
    route.snap("beach band")


def wide_water(route) -> np.ndarray:
    is_water = route.cls == "w"
    pieces, piece_count = label(is_water)
    if piece_count == 0:
        return is_water
    piece_sizes = np.bincount(pieces.ravel())
    wide = is_water & (piece_sizes[pieces] >= SMALLEST_WATER_BODY_WITH_A_SANDY_BANK_IN_CELLS)
    return wide


def lay_sand_along_wide_water(route) -> None:
    water_distance = distance_from(wide_water(route))
    bank_depth = band_depth_per_cell(route, *RIVER_BANK_DEPTHS_IN_CELLS)
    raw_noise = fbm(route.h, route.w, 5.0, route.rng, octaves=2)
    on_the_bank = (route.cls == GRASS_CLASS) & (water_distance <= bank_depth) & ~frozen_band_cells(route)
    if not on_the_bank.any():
        return
    threshold = np.quantile(raw_noise[on_the_bank], 1.0 - SHARE_OF_THE_BANK_THAT_IS_SANDY)
    sandy_bank = binary_opening(on_the_bank & (raw_noise >= threshold), TWO_BY_TWO)
    route.cls[sandy_bank] = SAND_CLASS
    route.snap("sandy banks")
