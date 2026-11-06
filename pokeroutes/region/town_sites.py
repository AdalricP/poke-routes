"""Where towns go on a generic region: on flat, low, dry land, drawn to sheltered
coast and to river mouths, spread out over the land, more of them on a bigger map.
Every pick is scored on the region grid; a variant number adds a little noise to
the scores, so asking again gives another sensible layout."""
from __future__ import annotations

from typing import List, Tuple

import numpy as np
from scipy import ndimage as ndi

from .world import World

FEWEST_TOWNS, MOST_TOWNS = 4, 8
LAND_CELLS_PER_TOWN = 1500
TOWN_WIDTH_IN_CELLS, TOWN_HEIGHT_IN_CELLS = 10, 9
MAP_MARGIN_IN_CELLS = 3
SMALLEST_DRY_LAND_SHARE_UNDER_A_TOWN = 0.85
UNBUILDABLE_BIOME_CODES = "MSlvo~="
COAST_DISTANCE_BAND_IN_CELLS = (2, 7)
RIVER_MOUTH_REACH_IN_CELLS = 6
RIVER_REACH_IN_CELLS = 3
SCORE_FOR_FLAT_GROUND, SCORE_FOR_LOW_GROUND = 3.0, 1.5
SCORE_FOR_COAST, SCORE_FOR_RIVER_MOUTH, SCORE_FOR_RIVERSIDE = 1.5, 2.5, 1.0
SCORE_NOISE_OF_A_VARIANT = 1.2
SPACING_SHARE_OF_EVEN_SPREAD = 0.75
SMALLEST_SPACING_IN_CELLS = 20


def wanted_town_count(w: World) -> int:
    dry_land_cells = int((w.land & ~w.lake).sum())
    town_count = int(np.clip(round(dry_land_cells / LAND_CELLS_PER_TOWN), FEWEST_TOWNS, MOST_TOWNS))
    return town_count


def buildable_land(w: World) -> np.ndarray:
    dry_land = w.land & ~w.lake & ~np.isin(w.biome, list(UNBUILDABLE_BIOME_CODES))
    return dry_land


def town_footprint_fits(w: World) -> np.ndarray:
    dry_land_share = ndi.uniform_filter(buildable_land(w).astype(float), size=(TOWN_HEIGHT_IN_CELLS, TOWN_WIDTH_IN_CELLS), mode="constant")
    fits = dry_land_share >= SMALLEST_DRY_LAND_SHARE_UNDER_A_TOWN
    margin_y = MAP_MARGIN_IN_CELLS + TOWN_HEIGHT_IN_CELLS // 2 + 1
    margin_x = MAP_MARGIN_IN_CELLS + TOWN_WIDTH_IN_CELLS // 2 + 1
    fits[:margin_y, :] = fits[-margin_y:, :] = False
    fits[:, :margin_x] = fits[:, -margin_x:] = False
    return fits


def flatness_score(w: World) -> np.ndarray:
    gradient_y, gradient_x = np.gradient(np.where(w.land, w.height, 0.0))
    slope = ndi.uniform_filter(np.hypot(gradient_x, gradient_y), size=5)
    flatness = 1.0 - slope / max(1e-9, float(np.quantile(slope[w.land], 0.95)) if w.land.any() else 1.0)
    clipped_flatness = np.clip(flatness, 0.0, 1.0)
    return clipped_flatness


def lowness_score(w: World) -> np.ndarray:
    highest_land = float(w.height[w.land].max()) if w.land.any() else 1.0
    lowness = 1.0 - np.clip(w.height / max(1e-9, highest_land), 0.0, 1.0)
    return lowness


def coast_score(w: World) -> np.ndarray:
    distance_to_sea = ndi.distance_transform_edt(w.land)
    nearest, farthest = COAST_DISTANCE_BAND_IN_CELLS
    near_the_coast = ((distance_to_sea >= nearest) & (distance_to_sea <= farthest)).astype(float)
    return near_the_coast


def river_mouth_score(w: World) -> np.ndarray:
    sea_next_door = ndi.binary_dilation(~w.land, iterations=1)
    mouths = np.asarray(w.river, bool) & sea_next_door
    if not mouths.any():
        return np.zeros(w.land.shape)
    distance_to_mouth = ndi.distance_transform_edt(~mouths)
    near_a_mouth = np.clip(1.0 - distance_to_mouth / RIVER_MOUTH_REACH_IN_CELLS, 0.0, 1.0)
    return near_a_mouth


def riverside_score(w: World) -> np.ndarray:
    river = np.asarray(w.river, bool)
    if not river.any():
        return np.zeros(w.land.shape)
    distance_to_river = ndi.distance_transform_edt(~river)
    beside_a_river = ((distance_to_river >= 1) & (distance_to_river <= RIVER_REACH_IN_CELLS)).astype(float)
    return beside_a_river


def town_site_scores(w: World, variant: int) -> np.ndarray:
    scores = (SCORE_FOR_FLAT_GROUND * flatness_score(w) + SCORE_FOR_LOW_GROUND * lowness_score(w)
              + SCORE_FOR_COAST * coast_score(w) + SCORE_FOR_RIVER_MOUTH * river_mouth_score(w)
              + SCORE_FOR_RIVERSIDE * riverside_score(w))
    rng = np.random.default_rng((int(w.seed), int(variant), 17))
    noise = ndi.gaussian_filter(rng.random(scores.shape), 3.0, mode="nearest")
    noise = (noise - noise.mean()) / max(1e-9, float(noise.std()))
    scores = scores + SCORE_NOISE_OF_A_VARIANT * noise
    scores = np.where(town_footprint_fits(w), scores, -np.inf)
    return scores


def town_spacing_in_cells(w: World, town_count: int) -> float:
    dry_land_cells = float((w.land & ~w.lake).sum())
    even_spread = (dry_land_cells / max(1, town_count)) ** 0.5
    spacing = max(SMALLEST_SPACING_IN_CELLS, SPACING_SHARE_OF_EVEN_SPREAD * even_spread)
    return spacing


def pick_town_sites(w: World, variant: int, kept_centres: List[Tuple[float, float]]) -> List[Tuple[int, int]]:
    town_count = wanted_town_count(w)
    scores = town_site_scores(w, variant)
    spacing = town_spacing_in_cells(w, town_count)
    rows, columns = np.mgrid[0:w.H, 0:w.W]
    for centre_x, centre_y in kept_centres:
        scores[np.hypot(columns - centre_x, rows - centre_y) < spacing] = -np.inf
    sites: List[Tuple[int, int]] = []
    while len(sites) + len(kept_centres) < town_count and np.isfinite(scores).any():
        best_y, best_x = np.unravel_index(int(np.argmax(scores)), scores.shape)
        sites.append((int(best_x), int(best_y)))
        scores[np.hypot(columns - best_x, rows - best_y) < spacing] = -np.inf
    return sites
