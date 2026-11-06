"""Where towns go on a generic region: on flat, low, dry land, drawn to sheltered
coast and to river mouths, spread out over the land, more of them on a bigger map.
Every pick is scored on the region grid; a variant number adds a little noise to
the scores, so asking again gives another sensible layout."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy import ndimage as ndi

from .world import World

FEWEST_TOWNS, MOST_TOWNS = 4, 14
LAND_CELLS_PER_TOWN = 780
SMALLEST_LANDMASS_CELLS_FOR_A_TOWN = 160
TOWN_WIDTH_IN_CELLS, TOWN_HEIGHT_IN_CELLS = 10, 9
MAP_MARGIN_IN_CELLS = 3
SMALLEST_DRY_LAND_SHARE_UNDER_A_TOWN = 0.85
UNBUILDABLE_BIOME_CODES = "MSvo~="
COAST_DISTANCE_BAND_IN_CELLS = (2, 7)
RIVER_MOUTH_REACH_IN_CELLS = 6
RIVER_REACH_IN_CELLS = 3
SCORE_FOR_FLAT_GROUND, SCORE_FOR_LOW_GROUND = 3.0, 1.5
SCORE_FOR_COAST, SCORE_FOR_RIVER_MOUTH, SCORE_FOR_RIVERSIDE = 1.5, 2.5, 1.0
SCORE_NOISE_OF_A_VARIANT = 1.2
SPACING_SHARE_OF_EVEN_SPREAD = 0.8
SMALLEST_SPACING_IN_CELLS = 18


def wanted_town_count(w: World) -> int:
    town_count = sum(towns_per_landmass(w).values())
    return town_count


def dry_land_cells_per_landmass(w: World) -> Dict[int, int]:
    dry_land = w.land & ~w.lake
    labels = np.where(dry_land, w.landmass, 0) if w.landmass is not None else ndi.label(dry_land)[0]
    counts = np.bincount(labels.ravel())
    cells_per_landmass = {int(label): int(count) for label, count in enumerate(counts) if label and count}
    return cells_per_landmass


def is_landmass_big_enough_for_a_town(dry_land_cells: int) -> bool:
    big_enough = dry_land_cells >= SMALLEST_LANDMASS_CELLS_FOR_A_TOWN
    return big_enough


def towns_per_landmass(w: World) -> Dict[int, int]:
    cells_per_landmass = {label: cells for label, cells in dry_land_cells_per_landmass(w).items()
                          if is_landmass_big_enough_for_a_town(cells)}
    total_dry_land_cells = sum(cells_per_landmass.values())
    towns_for_the_land = max(round(total_dry_land_cells / LAND_CELLS_PER_TOWN), len(cells_per_landmass))
    total_town_count = int(np.clip(towns_for_the_land, FEWEST_TOWNS, MOST_TOWNS))
    largest_first = sorted(cells_per_landmass, key=lambda label: -cells_per_landmass[label])
    quota = {label: 0 for label in largest_first}
    for label in largest_first[:total_town_count]:
        quota[label] = 1
    for _ in range(total_town_count - sum(quota.values())):
        label_with_most_land_per_town = max(largest_first, key=lambda label: cells_per_landmass[label] / (quota[label] + 1))
        quota[label_with_most_land_per_town] += 1
    towns_wanted = {label: count for label, count in quota.items() if count}
    return towns_wanted


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


def landmass_labels_under_cells(w: World) -> np.ndarray:
    labels = w.landmass if w.landmass is not None else ndi.label(w.land)[0]
    return np.asarray(labels)


def kept_towns_per_landmass(w: World, kept_centres: List[Tuple[float, float]]) -> Dict[int, int]:
    labels = landmass_labels_under_cells(w)
    kept_counts: Dict[int, int] = {}
    for centre_x, centre_y in kept_centres:
        label = int(labels[min(w.H - 1, int(centre_y)), min(w.W - 1, int(centre_x))])
        kept_counts[label] = kept_counts.get(label, 0) + 1
    return kept_counts


def best_site_on_landmass(scores: np.ndarray, on_landmass: np.ndarray) -> Optional[Tuple[int, int]]:
    scores_on_landmass = np.where(on_landmass, scores, -np.inf)
    if not np.isfinite(scores_on_landmass).any():
        return None
    best_y, best_x = np.unravel_index(int(np.argmax(scores_on_landmass)), scores.shape)
    best_site = (int(best_x), int(best_y))
    return best_site


def pick_town_sites(w: World, variant: int, kept_centres: List[Tuple[float, float]]) -> List[Tuple[int, int]]:
    """Each landmass big enough for a town gets a share of the towns by its dry land;
    a landmass's towns are picked best score first, never closer than the spacing to
    any other town, so they spread over the land and the islands."""
    towns_wanted = towns_per_landmass(w)
    scores = town_site_scores(w, variant)
    spacing = town_spacing_in_cells(w, sum(towns_wanted.values()))
    labels = landmass_labels_under_cells(w)
    rows, columns = np.mgrid[0:w.H, 0:w.W]
    for centre_x, centre_y in kept_centres:
        scores[np.hypot(columns - centre_x, rows - centre_y) < spacing] = -np.inf
    still_wanted = {label: count - kept_towns_per_landmass(w, kept_centres).get(label, 0) for label, count in towns_wanted.items()}
    sites: List[Tuple[int, int]] = []
    landmasses_with_room = set(still_wanted)
    while any(count > 0 for count in still_wanted.values()) and landmasses_with_room:
        label = max(still_wanted, key=lambda landmass_label: still_wanted[landmass_label])
        site = best_site_on_landmass(scores, labels == label)
        if site is None:
            landmasses_with_room.discard(label)
            hand_towns_to_a_landmass_with_room(still_wanted, label, landmasses_with_room)
            continue
        sites.append(site)
        still_wanted[label] -= 1
        scores[np.hypot(columns - site[0], rows - site[1]) < spacing] = -np.inf
    return sites


def hand_towns_to_a_landmass_with_room(still_wanted: Dict[int, int], full_label: int, landmasses_with_room: set) -> None:
    left_over = max(0, still_wanted[full_label])
    still_wanted[full_label] = 0
    if not left_over or not landmasses_with_room:
        return
    receiving_label = min(landmasses_with_room)
    still_wanted[receiving_label] += left_over
