from __future__ import annotations

from typing import List, Optional

import numpy as np
from scipy.ndimage import gaussian_filter

WOODED_REGION_BIOME_CODES = "fFgB"
BLUR_RADIUS_IN_METATILES = 5.0
WOODLAND_SHARE_WITHOUT_REGION_BY_SPEC_BIOME = {"forest": 0.9, "riverland": 0.5, "meadow": 0.35, "plains": 0.08,
                                               "beach": 0.15, "desert": 0.0, "mountain": 0.2, "snow": 0.2, "volcanic": 0.0}


def region_block_of(route) -> dict:
    region_block = route.spec.region if isinstance(getattr(route.spec, "region", None), dict) else {}
    return region_block


def surrounding_biome_rows_and_margin(region_block: dict) -> tuple:
    biome_block = region_block.get("biome") or {}
    surroundings = biome_block.get("surroundings") or {}
    if surroundings.get("grid"):
        surrounding_rows_and_margin = (list(surroundings["grid"]), int(surroundings.get("margin", 0)))
        return surrounding_rows_and_margin
    own_rows_and_no_margin = (list(biome_block.get("grid") or []), 0)
    return own_rows_and_no_margin


def wooded_share_per_metatile(biome_rows: List[str], margin_in_region_cells: int, cell_side_in_metatiles: int,
                              map_height_in_metatiles: int, map_width_in_metatiles: int) -> np.ndarray:
    is_wooded_region_cell = np.array([[code in WOODED_REGION_BIOME_CODES for code in row] for row in biome_rows], dtype=float)
    wooded_per_metatile = np.kron(is_wooded_region_cell, np.ones((cell_side_in_metatiles, cell_side_in_metatiles)))
    blurred = gaussian_filter(wooded_per_metatile, BLUR_RADIUS_IN_METATILES, mode="nearest")
    margin_in_metatiles = margin_in_region_cells * cell_side_in_metatiles
    cropped = blurred[margin_in_metatiles:margin_in_metatiles + map_height_in_metatiles,
                      margin_in_metatiles:margin_in_metatiles + map_width_in_metatiles]
    if cropped.shape != (map_height_in_metatiles, map_width_in_metatiles):
        padded = np.zeros((map_height_in_metatiles, map_width_in_metatiles))
        padded[:cropped.shape[0], :cropped.shape[1]] = cropped
        return padded
    return cropped


def woodland_closeness(route) -> np.ndarray:
    cached_closeness: Optional[np.ndarray] = route.__dict__.get("_woodland_closeness")
    if cached_closeness is not None and cached_closeness.shape == route.cls.shape:
        return cached_closeness
    region_block = region_block_of(route)
    biome_rows, margin_in_region_cells = surrounding_biome_rows_and_margin(region_block)
    if not biome_rows:
        fallback_share = WOODLAND_SHARE_WITHOUT_REGION_BY_SPEC_BIOME.get(route.spec.biome, 0.5)
        closeness = np.full(route.cls.shape, fallback_share)
    else:
        closeness = wooded_share_per_metatile(biome_rows, margin_in_region_cells, int(region_block.get("cell", 4)), route.h, route.w)
    route.__dict__["_woodland_closeness"] = closeness
    return closeness
