"""Break up flat open grass with the lighter grass patches real Emerald routes use.

The autotiler fills every open grass cell with the same plain metatile, so the
ground reads as one flat sheet. This pass stamps a few rectangular patches of
the tilesets' nine-slice light grass and dirt-speckled grass (and the odd
flower clump) onto
cells that are plain grass, after tiling, without touching collision.
"""
from __future__ import annotations

import random

import numpy as np

from .layout import Layout

plain_grass_metatile_id = 1
flower_metatile_id = 4
light_grass_patch_metatile_ids_by_row_and_column = (
    (464, 465, 466),
    (472, 473, 474),
    (480, 481, 482),
)
dirt_speckled_grass_patch_metatile_ids_by_row_and_column = (
    (600, 601, 602),
    (608, 609, 610),
    (616, 617, 618),
)
nine_slice_patch_kinds = (light_grass_patch_metatile_ids_by_row_and_column,
                          dirt_speckled_grass_patch_metatile_ids_by_row_and_column)
smallest_patch_side_in_cells = 2
largest_patch_side_in_cells = 6
patch_attempts_per_hundred_plain_grass_cells = 200
flower_clumps_per_thousand_plain_grass_cells = 4
flower_clump_size_in_cells = 3


def texture_open_ground(layout: Layout, cls: np.ndarray, seed: int = 0) -> Layout:
    random_source = random.Random(seed * 7919 + 17)
    metatile_with_texture = layout.metatile.copy()
    is_plain_open_grass = (cls == "g") & (layout.metatile == plain_grass_metatile_id) & (layout.collision == 0)

    is_cell_claimed = stamp_light_grass_patches(metatile_with_texture, is_plain_open_grass, random_source)
    scatter_flower_clumps(metatile_with_texture, is_plain_open_grass & ~is_cell_claimed, random_source)

    textured_layout = Layout(metatile_with_texture, layout.collision.copy(), layout.elevation.copy())
    return textured_layout


def stamp_light_grass_patches(metatile_grid: np.ndarray, is_plain_open_grass: np.ndarray,
                              random_source: random.Random) -> np.ndarray:
    is_cell_claimed = np.zeros_like(is_plain_open_grass)
    plain_grass_rows, plain_grass_columns = np.nonzero(is_plain_open_grass)
    if len(plain_grass_rows) == 0:
        return is_cell_claimed
    attempt_count = len(plain_grass_rows) * patch_attempts_per_hundred_plain_grass_cells // 100

    for attempt_number in range(attempt_count):
        anchor_cell_index = random_source.randrange(len(plain_grass_rows))
        patch_height_in_cells = random_source.randint(smallest_patch_side_in_cells, largest_patch_side_in_cells)
        patch_width_in_cells = random_source.randint(smallest_patch_side_in_cells, largest_patch_side_in_cells)
        patch_top_row = int(plain_grass_rows[anchor_cell_index]) - random_source.randrange(patch_height_in_cells)
        patch_left_column = int(plain_grass_columns[anchor_cell_index]) - random_source.randrange(patch_width_in_cells)
        if patch_top_row < 0 or patch_left_column < 0:
            continue
        patch_rows = slice(patch_top_row, patch_top_row + patch_height_in_cells)
        patch_columns = slice(patch_left_column, patch_left_column + patch_width_in_cells)
        if not can_place_patch(is_plain_open_grass, is_cell_claimed, patch_rows, patch_columns):
            continue
        patch_kind = random_source.choice(nine_slice_patch_kinds)
        metatile_grid[patch_rows, patch_columns] = nine_slice_patch_metatiles(patch_kind, patch_height_in_cells,
                                                                              patch_width_in_cells)
        is_cell_claimed[patch_rows, patch_columns] = True

    return is_cell_claimed


def can_place_patch(is_plain_open_grass: np.ndarray, is_cell_claimed: np.ndarray,
                    patch_rows: slice, patch_columns: slice) -> bool:
    patch_area_is_plain_grass = bool(is_plain_open_grass[patch_rows, patch_columns].all())
    patch_area_fits_on_grid = is_plain_open_grass[patch_rows, patch_columns].shape == (
        patch_rows.stop - patch_rows.start, patch_columns.stop - patch_columns.start)
    margin_rows = slice(max(0, patch_rows.start - 1), patch_rows.stop + 1)
    margin_columns = slice(max(0, patch_columns.start - 1), patch_columns.stop + 1)
    margin_is_unclaimed = not is_cell_claimed[margin_rows, margin_columns].any()
    can_place = patch_area_fits_on_grid and patch_area_is_plain_grass and margin_is_unclaimed
    return can_place


def nine_slice_patch_metatiles(patch_kind: tuple, patch_height_in_cells: int,
                               patch_width_in_cells: int) -> np.ndarray:
    nine_slice_row_for_each_patch_row = [nine_slice_index(row_number, patch_height_in_cells)
                                         for row_number in range(patch_height_in_cells)]
    nine_slice_column_for_each_patch_column = [nine_slice_index(column_number, patch_width_in_cells)
                                               for column_number in range(patch_width_in_cells)]
    patch_metatiles = np.array([[patch_kind[slice_row][slice_column]
                                 for slice_column in nine_slice_column_for_each_patch_column]
                                for slice_row in nine_slice_row_for_each_patch_row], dtype=np.uint16)
    return patch_metatiles


def nine_slice_index(position_in_cells: int, side_length_in_cells: int) -> int:
    if position_in_cells == 0:
        return 0
    if position_in_cells == side_length_in_cells - 1:
        return 2
    return 1


def scatter_flower_clumps(metatile_grid: np.ndarray, is_free_plain_grass: np.ndarray,
                          random_source: random.Random) -> None:
    free_rows, free_columns = np.nonzero(is_free_plain_grass)
    if len(free_rows) == 0:
        return
    clump_count = len(free_rows) * flower_clumps_per_thousand_plain_grass_cells // 1000

    for clump_number in range(clump_count):
        seed_cell_index = random_source.randrange(len(free_rows))
        plant_single_flower_clump(metatile_grid, is_free_plain_grass, int(free_rows[seed_cell_index]),
                                  int(free_columns[seed_cell_index]), random_source)


def plant_single_flower_clump(metatile_grid: np.ndarray, is_free_plain_grass: np.ndarray, start_row: int,
                              start_column: int, random_source: random.Random) -> None:
    grid_height_in_cells, grid_width_in_cells = metatile_grid.shape
    current_row, current_column = start_row, start_column

    for flower_number in range(flower_clump_size_in_cells):
        if is_free_plain_grass[current_row, current_column]:
            metatile_grid[current_row, current_column] = flower_metatile_id
        row_step, column_step = random_source.choice(((0, 1), (1, 0), (0, -1), (-1, 0)))
        current_row = min(max(current_row + row_step, 0), grid_height_in_cells - 1)
        current_column = min(max(current_column + column_step, 0), grid_width_in_cells - 1)


def tile_with_false_walls(route, tile, cls: np.ndarray = None) -> Layout:
    """Tile ``cls`` (default: the route's grid) with every false-wall cell drawn as
    forest, then clear those cells' collision: trees you can walk straight through,
    the way hidden paths work in the real games. ``tile`` maps a class grid to a Layout.
    False walls are recorded by places.place_hidden in route.false_walls (cell lists)."""
    walls = [list(map(tuple, w)) for w in route.__dict__.get("false_walls") or []]
    cls = np.array(route.cls if cls is None else cls, copy=True)
    for w in walls:
        for c in w:
            cls[c] = "f"
    layout = tile(cls)
    every = {c for w in walls for c in w}
    h, w_ = cls.shape
    for w in walls:
        # walk at the height of the open ground the gap joins
        near = [int(layout.elevation[y + dy, x + dx]) for (y, x) in w for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1))
                if 0 <= y + dy < h and 0 <= x + dx < w_ and (y + dy, x + dx) not in every
                and layout.collision[y + dy, x + dx] == 0]
        el = max(set(near), key=near.count) if near else 3
        for c in w:
            layout.collision[c] = 0
            layout.elevation[c] = el
    return layout
