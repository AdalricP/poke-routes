from __future__ import annotations

from typing import List

import numpy as np
from scipy.ndimage import gaussian_filter

from .woodland_closeness import region_block_of, surrounding_biome_rows_and_margin

MOUNTAIN_REGION_BIOME_CODES = "MS"
MOUNTAIN_SPEC_BIOMES = ("mountain", "snow", "volcanic")
BLUR_RADIUS_OF_MOUNTAIN_SHARE_IN_METATILES = 3.0
DITHER_AMPLITUDE_OF_MOUNTAIN_SHARE = 0.22
BLOCK_SIDE_IN_METATILES = 2
MOUNTAIN_SHARE_WHERE_ROCK_TAKES_OVER_WOODS = 0.62
MOUNTAIN_SHARE_WHERE_OUTCROPS_START = 0.38
MOUNTAIN_SHARE_WHERE_STONY_GROUND_STARTS = 0.5
MOUNTAIN_SHARE_BELOW_WHICH_A_MOUNTAIN_MAP_TURNS_GREEN = 0.3
ROCK_CLASS, TREE_CLASS, GRASS_CLASS = "r", "f", "g"
STONY_GROUND_CLASS = "p"
MOUNTAIN_WALL_CLASSES = "kr"
MOUNTAIN_GROUND_CLASSES = "v"
BLOCK_HASH_MULTIPLIER_ALONG_X, BLOCK_HASH_MULTIPLIER_ALONG_Y = 73856093, 19349663
BLOCK_HASH_SALT_FOR_DITHER, BLOCK_HASH_SALT_FOR_OUTCROPS = 40503, 92821


def mountain_share_per_metatile(biome_rows: List[str], margin_in_region_cells: int, cell_side_in_metatiles: int,
                                map_height_in_metatiles: int, map_width_in_metatiles: int) -> np.ndarray:
    is_mountain_region_cell = np.array([[code in MOUNTAIN_REGION_BIOME_CODES for code in row] for row in biome_rows], dtype=float)
    mountain_per_metatile = np.kron(is_mountain_region_cell, np.ones((cell_side_in_metatiles, cell_side_in_metatiles)))
    blurred_mountain_share = gaussian_filter(mountain_per_metatile, BLUR_RADIUS_OF_MOUNTAIN_SHARE_IN_METATILES, mode="nearest")
    margin_in_metatiles = margin_in_region_cells * cell_side_in_metatiles
    cropped_mountain_share = np.zeros((map_height_in_metatiles, map_width_in_metatiles))
    inside_part = blurred_mountain_share[margin_in_metatiles:margin_in_metatiles + map_height_in_metatiles,
                                         margin_in_metatiles:margin_in_metatiles + map_width_in_metatiles]
    cropped_mountain_share[:inside_part.shape[0], :inside_part.shape[1]] = inside_part
    return cropped_mountain_share


def hashed_fraction_of_world_block(world_block_x: np.ndarray, world_block_y: np.ndarray, salt: int) -> np.ndarray:
    mixed_bits = (world_block_x * BLOCK_HASH_MULTIPLIER_ALONG_X) ^ (world_block_y * BLOCK_HASH_MULTIPLIER_ALONG_Y) ^ salt
    mixed_bits = (mixed_bits * 2654435761) % 4294967296
    fraction_between_zero_and_one = (mixed_bits % 10007) / 10007.0
    return fraction_between_zero_and_one


def world_block_coordinates(region_block: dict, map_height_in_metatiles: int, map_width_in_metatiles: int) -> tuple:
    cell_side_in_metatiles = int(region_block.get("cell", 4))
    origin_x_in_region_cells, origin_y_in_region_cells = (list(region_block.get("rect") or [0, 0])[:2])
    block_rows = map_height_in_metatiles // BLOCK_SIDE_IN_METATILES
    block_columns = map_width_in_metatiles // BLOCK_SIDE_IN_METATILES
    origin_x_in_blocks = int(origin_x_in_region_cells) * cell_side_in_metatiles // BLOCK_SIDE_IN_METATILES
    origin_y_in_blocks = int(origin_y_in_region_cells) * cell_side_in_metatiles // BLOCK_SIDE_IN_METATILES
    world_block_y, world_block_x = np.mgrid[origin_y_in_blocks:origin_y_in_blocks + block_rows,
                                            origin_x_in_blocks:origin_x_in_blocks + block_columns]
    return world_block_x.astype(np.int64), world_block_y.astype(np.int64)


def dithered_mountain_share_per_block(route) -> np.ndarray:
    region_block = region_block_of(route)
    biome_rows, margin_in_region_cells = surrounding_biome_rows_and_margin(region_block)
    block_rows, block_columns = route.h // BLOCK_SIDE_IN_METATILES, route.w // BLOCK_SIDE_IN_METATILES
    if not biome_rows:
        return np.zeros((block_rows, block_columns))
    share_per_metatile = mountain_share_per_metatile(biome_rows, margin_in_region_cells, int(region_block.get("cell", 4)), route.h, route.w)
    share_per_block = share_per_metatile[:block_rows * BLOCK_SIDE_IN_METATILES, :block_columns * BLOCK_SIDE_IN_METATILES].reshape(
        block_rows, BLOCK_SIDE_IN_METATILES, block_columns, BLOCK_SIDE_IN_METATILES).mean(axis=(1, 3))
    world_block_x, world_block_y = world_block_coordinates(region_block, route.h, route.w)
    dither = (hashed_fraction_of_world_block(world_block_x, world_block_y, BLOCK_HASH_SALT_FOR_DITHER) - 0.5) * 2 * DITHER_AMPLITUDE_OF_MOUNTAIN_SHARE
    dithered_share = share_per_block + dither * np.minimum(1.0, 4 * share_per_block * (1 - share_per_block) + 0.25)
    return dithered_share


def outcrop_chance_per_block(route) -> np.ndarray:
    world_block_x, world_block_y = world_block_coordinates(region_block_of(route), route.h, route.w)
    outcrop_chance = hashed_fraction_of_world_block(world_block_x, world_block_y, BLOCK_HASH_SALT_FOR_OUTCROPS)
    return outcrop_chance


def block_slices(block_row: int, block_column: int) -> tuple:
    row_slice = slice(block_row * BLOCK_SIDE_IN_METATILES, (block_row + 1) * BLOCK_SIDE_IN_METATILES)
    column_slice = slice(block_column * BLOCK_SIDE_IN_METATILES, (block_column + 1) * BLOCK_SIDE_IN_METATILES)
    return row_slice, column_slice


def is_block_all_of(class_block: np.ndarray, classes: str) -> bool:
    is_all_of_classes = bool(np.isin(class_block, list(classes)).all())
    return is_all_of_classes


def should_woods_become_rock(mountain_share: float, outcrop_chance: float) -> bool:
    if mountain_share >= MOUNTAIN_SHARE_WHERE_ROCK_TAKES_OVER_WOODS:
        return True
    if mountain_share < MOUNTAIN_SHARE_WHERE_OUTCROPS_START:
        return False
    span = MOUNTAIN_SHARE_WHERE_ROCK_TAKES_OVER_WOODS - MOUNTAIN_SHARE_WHERE_OUTCROPS_START
    is_outcrop = outcrop_chance < (mountain_share - MOUNTAIN_SHARE_WHERE_OUTCROPS_START) / span
    return is_outcrop


def roughen_block_toward_mountain(class_grid: np.ndarray, block_row: int, block_column: int,
                                  mountain_share: float, outcrop_chance: float) -> None:
    row_slice, column_slice = block_slices(block_row, block_column)
    class_block = class_grid[row_slice, column_slice]
    if is_block_all_of(class_block, TREE_CLASS + "o") and should_woods_become_rock(mountain_share, outcrop_chance):
        class_block[:, :] = ROCK_CLASS
        return
    if mountain_share >= MOUNTAIN_SHARE_WHERE_STONY_GROUND_STARTS and outcrop_chance < mountain_share:
        class_block[class_block == GRASS_CLASS] = STONY_GROUND_CLASS


def soften_block_toward_green(class_grid: np.ndarray, block_row: int, block_column: int, mountain_share: float) -> None:
    if mountain_share >= MOUNTAIN_SHARE_BELOW_WHICH_A_MOUNTAIN_MAP_TURNS_GREEN:
        return
    row_slice, column_slice = block_slices(block_row, block_column)
    class_block = class_grid[row_slice, column_slice]
    if is_block_all_of(class_block, MOUNTAIN_WALL_CLASSES):
        class_block[:, :] = TREE_CLASS
        return
    class_block[np.isin(class_block, list(MOUNTAIN_GROUND_CLASSES))] = GRASS_CLASS


def blend_biomes_per_region_cell(route) -> None:
    if not (region_block_of(route).get("biome") or {}).get("grid"):
        return
    mountain_share_per_block = dithered_mountain_share_per_block(route)
    outcrop_chance = outcrop_chance_per_block(route)
    is_mountain_map = route.spec.biome in MOUNTAIN_SPEC_BIOMES
    block_rows, block_columns = mountain_share_per_block.shape
    for block_row in range(block_rows):
        for block_column in range(block_columns):
            mountain_share = float(mountain_share_per_block[block_row, block_column])
            if is_mountain_map:
                soften_block_toward_green(route.cls, block_row, block_column, mountain_share)
                continue
            roughen_block_toward_mountain(route.cls, block_row, block_column, mountain_share,
                                          float(outcrop_chance[block_row, block_column]))
