from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np
from scipy import ndimage as ndi

from . import map_grid
from .geography import SEA_RING_CELLS, SMALLEST_ISLET_CELLS, label_landmasses
from .terrain import Fbm, norm01, shore_distance
from .world import World

SMALLEST_REGION_SIDE_IN_SQUARES = 3
LARGEST_REGION_SIDE_IN_REGION_CELLS = 512
GRIDS_KEPT_FROM_THE_OLD_AREA = ("lake", "river", "flow", "temp", "moist", "tiers", "biome")
OVERRIDE_GRIDS_THAT_CONTINUE_FROM_THE_EDGE = ("heat", "rain")
OVERRIDE_GRIDS_EMPTY_IN_NEW_AREA = ("spit", "no_route", "trench", "isles", "no_lake", "lake_extra", "upland")
CONTINUATION_REACH_IN_REGION_CELLS = 9.0
ISLAND_RAMP_IN_REGION_CELLS = 6.0
HEIGHT_BLEND_REACH_IN_REGION_CELLS = 4.0
SHARE_OF_OLD_LAND_FRACTION_IN_NEW_AREA = 0.85
SHARE_OF_SEA_ON_A_RINGED_BORDER = 0.9
ISLAND_NOISE_SCALE_IN_REGION_CELLS = 14.0
HILL_NOISE_SCALE_IN_REGION_CELLS = 22.0


class RegionResizeProblem(ValueError):
    pass


def checked_region_size_in_region_cells(width_in_squares: int, height_in_squares: int, region_cells_per_square: int) -> Tuple[int, int]:
    if min(width_in_squares, height_in_squares) < SMALLEST_REGION_SIDE_IN_SQUARES:
        raise RegionResizeProblem(f"a region is at least {SMALLEST_REGION_SIDE_IN_SQUARES} squares on a side, not {width_in_squares} x {height_in_squares}")
    width_in_region_cells, height_in_region_cells = width_in_squares * region_cells_per_square, height_in_squares * region_cells_per_square
    if max(width_in_region_cells, height_in_region_cells) > LARGEST_REGION_SIDE_IN_REGION_CELLS:
        largest_side_in_squares = LARGEST_REGION_SIDE_IN_REGION_CELLS // region_cells_per_square
        raise RegionResizeProblem(f"a region with this square size is at most {largest_side_in_squares} squares on a side")
    return width_in_region_cells, height_in_region_cells


def grid_resized(grid: np.ndarray, new_shape: Tuple[int, int], fill) -> np.ndarray:
    old_height, old_width = grid.shape
    kept_height, kept_width = min(old_height, new_shape[0]), min(old_width, new_shape[1])
    resized = np.full(new_shape, fill, dtype=grid.dtype)
    resized[:kept_height, :kept_width] = grid[:kept_height, :kept_width]
    return resized


def old_area_mask(old_shape: Tuple[int, int], new_shape: Tuple[int, int]) -> np.ndarray:
    mask = np.zeros(new_shape, dtype=bool)
    mask[:min(old_shape[0], new_shape[0]), :min(old_shape[1], new_shape[1])] = True
    return mask


def nearest_old_cell_and_distance(old_area: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    distance, (nearest_rows, nearest_columns) = ndi.distance_transform_edt(~old_area, return_indices=True)
    return distance, nearest_rows, nearest_columns


def is_border_ringed_by_sea(land: np.ndarray) -> bool:
    border_cells = np.concatenate([land[0, :], land[-1, :], land[:, 0], land[:, -1]])
    ringed = float((~border_cells).mean()) >= SHARE_OF_SEA_ON_A_RINGED_BORDER
    return ringed


def noise_field(rng: np.random.Generator, shape: Tuple[int, int], scale_in_region_cells: float) -> np.ndarray:
    rows, columns = np.mgrid[0:shape[0], 0:shape[1]].astype(float)
    field = norm01(Fbm(rng, 5)(columns / scale_in_region_cells, rows / scale_in_region_cells)) - 0.5
    return field


def island_threshold(island_noise: np.ndarray, far_new_area: np.ndarray, old_land_fraction: float) -> float:
    if not far_new_area.any():
        return 1.0
    wanted_land_fraction = min(0.9, max(0.0, old_land_fraction * SHARE_OF_OLD_LAND_FRACTION_IN_NEW_AREA))
    threshold = float(np.quantile(island_noise[far_new_area], 1 - wanted_land_fraction)) if wanted_land_fraction > 0 else 1.0
    return threshold


def outer_ring_mask(shape: Tuple[int, int]) -> np.ndarray:
    ring = np.zeros(shape, dtype=bool)
    ring[:SEA_RING_CELLS, :] = ring[-SEA_RING_CELLS:, :] = True
    ring[:, :SEA_RING_CELLS] = ring[:, -SEA_RING_CELLS:] = True
    return ring


def new_area_land(old_land: np.ndarray, old_area: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    distance, nearest_rows, nearest_columns = nearest_old_cell_and_distance(old_area)
    edge_was_land = old_land[nearest_rows, nearest_columns]
    continuation = np.where(edge_was_land, 0.35, -0.35) * np.exp(-distance / CONTINUATION_REACH_IN_REGION_CELLS)
    island_noise = noise_field(rng, old_area.shape, ISLAND_NOISE_SCALE_IN_REGION_CELLS)
    far_new_area = ~old_area & (distance >= ISLAND_RAMP_IN_REGION_CELLS)
    threshold = island_threshold(island_noise, far_new_area, float(old_land[old_area].mean()))
    islands = (island_noise - threshold) * (1 - np.exp(-distance / ISLAND_RAMP_IN_REGION_CELLS))
    land_field = continuation + islands
    land = np.where(old_area, old_land, land_field > 0)
    if is_border_ringed_by_sea(old_land[old_area].reshape(old_area_shape(old_area))):
        land &= ~(outer_ring_mask(old_area.shape) & ~old_area)
    tidied_land = without_new_specks(land, old_area)
    return tidied_land


def old_area_shape(old_area: np.ndarray) -> Tuple[int, int]:
    shape = (int(old_area.any(axis=1).sum()), int(old_area.any(axis=0).sum()))
    return shape


def without_new_specks(land: np.ndarray, old_area: np.ndarray) -> np.ndarray:
    labels, count = ndi.label(land)
    if count == 0:
        return land
    sizes = ndi.sum(land, labels, range(1, count + 1))
    touches_old_area = ndi.maximum(old_area, labels, range(1, count + 1))
    speck_labels = [label_index + 1 for label_index in range(count) if sizes[label_index] < SMALLEST_ISLET_CELLS and not touches_old_area[label_index]]
    tidied = land & ~np.isin(labels, speck_labels)
    return tidied


def generated_heights(land: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    shore = shore_distance(land)
    sea_distance = ndi.distance_transform_edt(~land)
    hills = noise_field(rng, land.shape, HILL_NOISE_SCALE_IN_REGION_CELLS) + 0.5
    rise = 1 - np.exp(-shore / 7.0)
    land_height = np.maximum(0.06 + 0.24 * rise + (hills - 0.5) * 0.34 * rise, 0.012)
    sea_height = -(0.04 + 0.42 * (1 - np.exp(-sea_distance / 7.0))) - (hills - 0.5) * 0.05
    heights = np.where(land, land_height, sea_height)
    return heights


def new_area_heights(old_height: np.ndarray, old_area: np.ndarray, land: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    distance, nearest_rows, nearest_columns = nearest_old_cell_and_distance(old_area)
    edge_height = old_height[nearest_rows, nearest_columns]
    fresh_height = generated_heights(land, rng)
    same_kind_as_edge = (edge_height >= 0) == land
    blend = np.where(same_kind_as_edge, np.exp(-distance / HEIGHT_BLEND_REACH_IN_REGION_CELLS), 0.0)
    blended = blend * edge_height + (1 - blend) * fresh_height
    kind_kept = np.where(land, np.maximum(blended, 0.012), np.minimum(blended, -0.02))
    heights = np.where(old_area, old_height, kind_kept)
    return heights


def continued_from_edge(grid: np.ndarray, old_area: np.ndarray) -> np.ndarray:
    _distance, nearest_rows, nearest_columns = nearest_old_cell_and_distance(old_area)
    continued = grid[nearest_rows, nearest_columns]
    return continued


def moisture_noise_for_new_area(old_noise: np.ndarray, old_area: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    distance, _nearest_rows, _nearest_columns = nearest_old_cell_and_distance(old_area)
    edge_noise = continued_from_edge(old_noise, old_area)
    fresh_noise = noise_field(rng, old_area.shape, 12.0)
    blend = np.exp(-distance / HEIGHT_BLEND_REACH_IN_REGION_CELLS)
    noise = np.where(old_area, old_noise, blend * edge_noise + (1 - blend) * fresh_noise)
    return noise


def resized_override_grids(w: World, new_shape: Tuple[int, int], old_area: np.ndarray, rng: np.random.Generator) -> Dict[str, np.ndarray]:
    resized = {}
    for grid_name in OVERRIDE_GRIDS_EMPTY_IN_NEW_AREA:
        grid = np.broadcast_to(np.asarray(w.overrides.get(grid_name, 0)), (w.H, w.W))
        resized[grid_name] = grid_resized(np.array(grid), new_shape, 0)
    for grid_name in OVERRIDE_GRIDS_THAT_CONTINUE_FROM_THE_EDGE:
        grid = np.broadcast_to(np.asarray(w.overrides.get(grid_name, 0), dtype=float), (w.H, w.W))
        resized[grid_name] = continued_from_edge(grid_resized(np.array(grid), new_shape, 0.0), old_area)
    old_noise = np.asarray(w.overrides.get("moist_noise", np.zeros((w.H, w.W))), dtype=float)
    resized["moist_noise"] = moisture_noise_for_new_area(grid_resized(old_noise, new_shape, 0.0), old_area, rng)
    return resized


def resized_edit_grids(w: World, new_shape: Tuple[int, int]) -> Dict[str, np.ndarray]:
    edit_grids = {grid_name: grid_resized(np.asarray(grid), new_shape, 0) for grid_name, grid in (w.overrides.get("_edit") or {}).items()}
    return edit_grids


def resized_legend_biomes(w: World, new_shape: Tuple[int, int]) -> List[Tuple[str, np.ndarray]]:
    legend_biomes = [(code, grid_resized(np.asarray(mask, bool), new_shape, False)) for code, mask in w.overrides.get("biome", [])]
    return legend_biomes


def world_grids_kept_in_old_area(w: World, new_shape: Tuple[int, int]) -> Dict[str, np.ndarray]:
    fills = {"lake": False, "river": False, "flow": 0.0, "temp": 0.0, "moist": 0.0, "tiers": 0, "biome": "~"}
    kept = {grid_name: grid_resized(np.asarray(getattr(w, grid_name)), new_shape, fills[grid_name]) for grid_name in GRIDS_KEPT_FROM_THE_OLD_AREA}
    return kept


def with_terrain_recomputed_for_new_area(w: World, kept_grids: Dict[str, np.ndarray], old_area: np.ndarray) -> None:
    from .edit import stage_biomes, stage_climate, stage_water

    for stage in (stage_water, stage_climate, stage_biomes):
        stage(w)
    for grid_name, kept_grid in kept_grids.items():
        setattr(w, grid_name, np.where(old_area, kept_grid, getattr(w, grid_name)))
    w.sea = ~w.land


def resize_grids_of_world(w: World, new_width: int, new_height: int) -> np.ndarray:
    new_shape = (new_height, new_width)
    old_area = old_area_mask((w.H, w.W), new_shape)
    rng = np.random.default_rng((int(w.seed), new_width, new_height))
    grown = not old_area.all()
    kept_grids = world_grids_kept_in_old_area(w, new_shape)
    old_land = grid_resized(np.asarray(w.land, bool), new_shape, False)
    old_height = grid_resized(np.asarray(w.height, float), new_shape, -0.4)
    override_grids = resized_override_grids(w, new_shape, old_area, rng)
    w.land = new_area_land(old_land, old_area, rng) if grown else old_land
    w.height = new_area_heights(old_height, old_area, w.land, rng) if grown else old_height
    w.lagoon = grid_resized(np.asarray(w.lagoon if w.lagoon is not None else np.zeros((w.H, w.W), bool)), new_shape, False)
    w.overrides.update(override_grids)
    w.overrides["_edit"] = resized_edit_grids(w, new_shape)
    w.overrides["biome"] = resized_legend_biomes(w, new_shape)
    w.W, w.H = new_width, new_height
    w.landmass = label_landmasses(w.land).astype(np.int8)
    for grid_name, kept_grid in kept_grids.items():
        setattr(w, grid_name, kept_grid)
    with_terrain_recomputed_for_new_area(w, kept_grids, old_area)
    return old_area


def map_grid_cropped_to(stored_map_grid: dict, grid_size: Tuple[int, int], region_cells_per_square: int) -> dict:
    marks = {cell: mark for cell, mark in map_grid.marks_of_map_grid(stored_map_grid).items() if map_grid.is_inside_grid(cell, grid_size)}
    assignment = {cell: key for cell, key in map_grid.assignment_of_map_grid(stored_map_grid).items() if cell in marks}
    cropped = map_grid.map_grid_of_marks(marks, grid_size, region_cells_per_square, assignment)
    return cropped


def landmarks_inside(w: World) -> list:
    inside = [landmark for landmark in w.landmarks if 0 <= landmark.x < w.W and 0 <= landmark.y < w.H]
    return inside


def resize_region(w: World, width_in_squares: int, height_in_squares: int) -> Dict[str, str]:
    region_cells_per_square = map_grid.region_cells_per_grid_cell(w)
    new_width, new_height = checked_region_size_in_region_cells(width_in_squares, height_in_squares, region_cells_per_square)
    stored_map_grid = map_grid.map_grid_of_world(w)
    resize_grids_of_world(w, new_width, new_height)
    w.landmarks = landmarks_inside(w)
    cropped_map_grid = map_grid_cropped_to(stored_map_grid, (width_in_squares, height_in_squares), region_cells_per_square)
    w.overrides["map_grid"] = cropped_map_grid
    merged_away = map_grid.map_from_map_grid(w, cropped_map_grid)
    return merged_away
