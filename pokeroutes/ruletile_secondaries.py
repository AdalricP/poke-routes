from __future__ import annotations

import copy
from typing import Callable, Dict, List, Optional, Set, Tuple

import numpy as np
from scipy.ndimage import distance_transform_edt, gaussian_filter, label

from .ruletile_terrain import DIAGONAL_SIDES, NEIGHBOUR_OFFSETS, components_of

WOODLAND_SECONDARY = "ark_woodland"
WOODLAND_SYMBOL = "gTileset_ArkWoodland"
FIRST_SECONDARY_METATILE_ID = 0x200
OPEN_SEA_MODE = "sea"
ROCK_LETTER = "r"
SEA_LETTER = "b"
SAND_LETTER = "d"
SEA_ROCK_LETTER = "u"
BOULDER_PROP_NAME = "boulder"
FULL_MASK = 255

Cell = Tuple[int, int]


def integers_inside(value) -> Set[int]:
    if isinstance(value, bool):
        return set()
    if isinstance(value, int):
        return {value}
    if isinstance(value, dict):
        return set().union(set(), *(integers_inside(inner_value) for inner_value in value.values()))
    if isinstance(value, list):
        return set().union(set(), *(integers_inside(inner_value) for inner_value in value))
    return set()


def uses_secondary_metatiles(entry: dict) -> bool:
    metatile_ids = integers_inside({key: inner_value for key, inner_value in entry.items()
                                    if key not in ("collision", "elevation", "phases")})
    uses_secondary = any(metatile_id >= FIRST_SECONDARY_METATILE_ID for metatile_id in metatile_ids)
    return uses_secondary


def is_primary_entry(entry: dict) -> bool:
    essential_part = {key: entry[key] for key in ("base", "cells") if key in entry}
    is_primary = not uses_secondary_metatiles(essential_part)
    return is_primary


def entry_without_secondary_parts(entry: dict) -> dict:
    stripped_entry = {key: inner_value for key, inner_value in entry.items()
                      if not uses_secondary_metatiles({key: inner_value})}
    return stripped_entry


def primary_only_entries(entries: dict) -> dict:
    kept_entries = {name: entry_without_secondary_parts(entry) for name, entry in entries.items() if is_primary_entry(entry)}
    return kept_entries


def merged_class_entry(base_entry: Optional[dict], secondary_entry: dict) -> dict:
    is_partial_entry = "autotile" not in secondary_entry and "base" not in secondary_entry
    if base_entry is None or not is_partial_entry:
        return secondary_entry
    merged_entry = dict(base_entry)
    merged_entry.update(secondary_entry)
    return merged_entry


def class_table_for_secondary(class_table: dict, secondary_name: str) -> dict:
    if secondary_name == WOODLAND_SECONDARY:
        return class_table
    secondary_block = class_table["secondary_tilesets"][secondary_name]
    table = copy.deepcopy({key: inner_value for key, inner_value in class_table.items() if key != "secondary_tilesets"})
    classes = primary_only_entries(table["classes"])
    for letter, secondary_entry in secondary_block["classes"].items():
        classes[letter] = merged_class_entry(classes.get(letter), secondary_entry)
    props = primary_only_entries(table.get("props", {}))
    props.update(secondary_block.get("props", {}))
    table["classes"], table["props"] = classes, props
    table["tileset"] = dict(table["tileset"], secondary=secondary_block["symbol"])
    table["class_of_metatile"] = {key: code for key, code in table["class_of_metatile"].items()
                                  if int(key) < FIRST_SECONDARY_METATILE_ID}
    table["class_of_metatile"].update(secondary_block.get("class_of_metatile", {}))
    return table


def secondary_names_of(class_table: dict) -> List[str]:
    names = [WOODLAND_SECONDARY] + sorted(class_table.get("secondary_tilesets", {}))
    return names


def symbol_of_secondary(class_table: dict, secondary_name: str) -> str:
    if secondary_name == WOODLAND_SECONDARY:
        return class_table.get("tileset", {}).get("secondary", WOODLAND_SYMBOL)
    symbol = class_table["secondary_tilesets"][secondary_name]["symbol"]
    return symbol


def is_open_sea_spec(spec) -> bool:
    region_block = spec.region if isinstance(getattr(spec, "region", None), dict) else {}
    is_open_sea = region_block.get("mode") == OPEN_SEA_MODE
    return is_open_sea


def secondary_for_spec(class_table: dict, spec) -> str:
    requested_name = (getattr(spec, "extra", {}) or {}).get("ark_secondary") or ""
    if requested_name in secondary_names_of(class_table):
        return requested_name
    wanted_biome = OPEN_SEA_MODE if is_open_sea_spec(spec) else getattr(spec, "biome", "")
    matching_names = [name for name, block in sorted(class_table.get("secondary_tilesets", {}).items())
                      if wanted_biome in block.get("biomes", [])]
    chosen_name = matching_names[0] if matching_names else WOODLAND_SECONDARY
    return chosen_name


def mask_from_predicate(width: int, height: int, cell_x: int, cell_y: int, is_same: Callable[[int, int], bool],
                        mask_bits: List[str]) -> int:
    neighbour_is_same = {}
    for direction, (step_x, step_y) in NEIGHBOUR_OFFSETS.items():
        neighbour_x, neighbour_y = cell_x + step_x, cell_y + step_y
        is_outside = not (0 <= neighbour_x < width and 0 <= neighbour_y < height)
        neighbour_is_same[direction] = is_outside or is_same(neighbour_x, neighbour_y)
    for diagonal, (vertical_side, horizontal_side) in DIAGONAL_SIDES.items():
        if not (neighbour_is_same[vertical_side] and neighbour_is_same[horizontal_side]):
            neighbour_is_same[diagonal] = False
    mask = sum(1 << bit_index for bit_index, direction in enumerate(mask_bits) if neighbour_is_same[direction])
    return mask


BAND_EDGE_WOBBLE_AMPLITUDE_IN_METATILES = 0.6
BAND_EDGE_SMOOTHING_IN_METATILES = 1.3


def band_edge_wobble_in_metatiles(shape) -> np.ndarray:
    rows, columns = np.mgrid[:shape[0], :shape[1]].astype(float)
    wobble = BAND_EDGE_WOBBLE_AMPLITUDE_IN_METATILES * np.sin(columns * 0.43 + np.cos(rows * 0.29) * 1.7) * np.cos(rows * 0.37 - columns * 0.13)
    return wobble


def broad_land_mask(is_not_depth_water: np.ndarray) -> np.ndarray:
    """Islets, rock clusters and ridges sitting in open sea are small, isolated
    patches of "not depth water" placed well clear of the map edge; they should
    not read as their own shoreline for the depth bands, only real land does.
    Real land is a coastline, so it reaches the edge of the map; keep only the
    components that touch an edge and drop the rest so distance-to-land is
    measured from real land alone."""
    labelled, count = label(is_not_depth_water)
    if count == 0:
        return is_not_depth_water
    touches_edge = set(labelled[0, :]) | set(labelled[-1, :]) | set(labelled[:, 0]) | set(labelled[:, -1])
    touches_edge.discard(0)
    broad = is_not_depth_water & np.isin(labelled, list(touches_edge))
    return broad


def sea_band_grid(grid: List[str], sea_class: dict) -> np.ndarray:
    depth_water_letters = set(sea_class.get("depth_water", SEA_LETTER))
    is_depth_water = np.array([[letter in depth_water_letters for letter in grid_row] for grid_row in grid])
    is_real_land = broad_land_mask(~is_depth_water)
    distance_to_land = distance_transform_edt(np.pad(~is_real_land, 1, constant_values=True))[1:-1, 1:-1]
    distance_to_land = gaussian_filter(distance_to_land, BAND_EDGE_SMOOTHING_IN_METATILES) + band_edge_wobble_in_metatiles(is_depth_water.shape)
    band_starts = sea_class["band_starts_in_metatiles"]
    bands = np.zeros(is_depth_water.shape, np.int32)
    for band_index, band_start in enumerate(band_starts):
        bands[distance_to_land >= band_start] = band_index
    bands[~is_depth_water] = -1
    smoothed_bands = bands_without_single_cell_spots(bands)
    return smoothed_bands


def bands_without_single_cell_spots(bands: np.ndarray) -> np.ndarray:
    padded = np.pad(bands, 1, mode="edge")
    orthogonal_neighbours = np.stack([padded[:-2, 1:-1], padded[2:, 1:-1], padded[1:-1, :-2], padded[1:-1, 2:]])
    water_neighbours = np.where(orthogonal_neighbours < 0, bands[None], orthogonal_neighbours)
    highest_neighbour, lowest_neighbour = water_neighbours.max(0), water_neighbours.min(0)
    is_water = bands >= 0
    smoothed = np.where(is_water & (bands > highest_neighbour), highest_neighbour, bands)
    smoothed = np.where(is_water & (smoothed < lowest_neighbour), lowest_neighbour, smoothed)
    return smoothed


def sea_depth_metatile(grid: List[str], bands: np.ndarray, cell_x: int, cell_y: int, sea_class: dict,
                       mask_bits: List[str]) -> int:
    height, width = bands.shape
    own_band = int(max(bands[cell_y, cell_x], 0))

    def is_same(neighbour_x: int, neighbour_y: int) -> bool:
        neighbour_band = bands[neighbour_y, neighbour_x]
        return neighbour_band < 0 or neighbour_band >= own_band

    mask = mask_from_predicate(width, height, cell_x, cell_y, is_same, mask_bits)
    metatile_id = sea_class["bands"][own_band][str(mask)]
    return metatile_id


def sand_metatile(grid: List[str], cell_x: int, cell_y: int, sand_class: dict, mask_bits: List[str]) -> int:
    height, width = len(grid), len(grid[0])
    water_letters = set(sand_class.get("water", ""))
    joining_letters = set(sand_class.get("joins", SAND_LETTER))
    has_water_neighbour = any(0 <= cell_x + step_x < width and 0 <= cell_y + step_y < height
                              and grid[cell_y + step_y][cell_x + step_x] in water_letters
                              for step_x, step_y in NEIGHBOUR_OFFSETS.values())
    if has_water_neighbour and "water_by_mask" in sand_class:
        mask = mask_from_predicate(width, height, cell_x, cell_y, lambda x, y: grid[y][x] not in water_letters, mask_bits)
        return sand_class["water_by_mask"][str(mask)]
    mask = mask_from_predicate(width, height, cell_x, cell_y, lambda x, y: grid[y][x] in joining_letters, mask_bits)
    metatile_id = sand_class["by_mask"][str(mask)]
    return metatile_id


def is_letter_at(grid: List[str], cell_x: int, cell_y: int, letters: str) -> bool:
    is_inside = 0 <= cell_y < len(grid) and 0 <= cell_x < len(grid[0])
    found = is_inside and grid[cell_y][cell_x] in letters
    return found


def touches_letters(grid: List[str], component: Set[Cell], letters: str) -> bool:
    touches = any(is_letter_at(grid, cell_x + step_x, cell_y + step_y, letters)
                  for cell_x, cell_y in component for step_x, step_y in NEIGHBOUR_OFFSETS.values())
    return touches


def tile_rock_ridges_in_sea(grid: List[str], metatile_ids: np.ndarray, rock_class: dict, sea_letters: str,
                            mask_bits: List[str]) -> None:
    ridge_by_mask = rock_class.get("in_sea_by_mask")
    if not ridge_by_mask:
        return
    height, width = metatile_ids.shape
    for component in components_of(grid, ROCK_LETTER):
        if not touches_letters(grid, component, sea_letters):
            continue
        for cell_x, cell_y in component:
            mask = mask_from_predicate(width, height, cell_x, cell_y, lambda x, y: grid[y][x] == ROCK_LETTER, mask_bits)
            metatile_ids[cell_y, cell_x] = ridge_by_mask[str(mask)]


def is_whole_rock_block(grid: List[str], left: int, top: int, claimed: Set[Cell]) -> bool:
    block_cells = [(left + step_x, top + step_y) for step_y in (0, 1) for step_x in (0, 1)]
    is_whole = all(is_letter_at(grid, cell_x, cell_y, SEA_ROCK_LETTER) and (cell_x, cell_y) not in claimed
                   for cell_x, cell_y in block_cells)
    return is_whole


def place_rock_block(metatile_ids: np.ndarray, block_rows, left: int, top: int, claimed: Set[Cell]) -> None:
    for step_y in (0, 1):
        for step_x in (0, 1):
            metatile_ids[top + step_y, left + step_x] = block_rows[step_y][step_x]
            claimed.add((left + step_x, top + step_y))


def tile_sea_rocks(grid: List[str], metatile_ids: np.ndarray, blocked: np.ndarray, rock_class: dict,
                   sea_letters: str, land_boulder_id: Optional[int]) -> None:
    blocks = rock_class.get("blocks_2x2", [])
    singles = rock_class.get("single", rock_class.get("base", []))
    claimed: Set[Cell] = set()
    height, width = metatile_ids.shape
    for cell_y in range(height):
        for cell_x in range(width):
            if grid[cell_y][cell_x] != SEA_ROCK_LETTER or (cell_x, cell_y) in claimed:
                continue
            blocked[cell_y, cell_x] = True
            is_in_water = touches_letters(grid, {(cell_x, cell_y)}, sea_letters)
            if blocks and is_in_water and is_whole_rock_block(grid, cell_x, cell_y, claimed):
                block_rows = blocks[(cell_x * 7 + cell_y * 13) % len(blocks)]
                place_rock_block(metatile_ids, block_rows, cell_x, cell_y, claimed)
                blocked[cell_y:cell_y + 2, cell_x:cell_x + 2] = True
                continue
            land_or_single_id = singles[(cell_x + cell_y) % len(singles)] if is_in_water or land_boulder_id is None else land_boulder_id
            metatile_ids[cell_y, cell_x] = land_or_single_id
            claimed.add((cell_x, cell_y))


def land_boulder_metatile(class_table: dict) -> Optional[int]:
    boulder_cells = class_table.get("props", {}).get(BOULDER_PROP_NAME, {}).get("cells")
    if not boulder_cells:
        return None
    boulder_id = boulder_cells[0][0]
    return boulder_id


def surf_letters_of(classes: Dict[str, dict]) -> Set[str]:
    surf_letters = {letter for letter, class_entry in classes.items() if class_entry.get("surf")} | {"w", "a"}
    return surf_letters


def blob_metatile_with_fill_variants(class_entry: dict, mask: int, cell_x: int, cell_y: int) -> int:
    fill_variants = class_entry.get("fill_variants")
    if mask == FULL_MASK and fill_variants:
        variant_id = fill_variants[(cell_x * 7 + cell_y * 13 + (cell_x * cell_y) % 5) % len(fill_variants)]
        return variant_id
    metatile_id = class_entry["by_mask"][str(mask)]
    return metatile_id


DEEP_INTERIOR_RADIUS_IN_METATILES = 2


def cliff_face_metatile(grid: List[str], cell_x: int, cell_y: int, face_class: dict) -> int:
    own_letter = grid[cell_y][cell_x]
    joining_letters = face_class.get("joins", own_letter)
    is_upper = is_letter_at(grid, cell_x, cell_y + 1, own_letter)
    continues_west = is_letter_at(grid, cell_x - 1, cell_y, joining_letters)
    continues_east = is_letter_at(grid, cell_x + 1, cell_y, joining_letters)
    column_name = {(True, True): "mid", (False, True): "left", (True, False): "right", (False, False): "single"}[
        (continues_west, continues_east)]
    part_name = f"{'upper' if is_upper else 'lower'}_{column_name}"
    metatile_id = face_class["parts"].get(part_name, face_class["base"][0])
    return metatile_id


def is_deep_interior(grid: List[str], cell_x: int, cell_y: int, joining_letters: str) -> bool:
    height, width = len(grid), len(grid[0])
    radius = DEEP_INTERIOR_RADIUS_IN_METATILES
    is_deep = all(not (0 <= cell_x + step_x < width and 0 <= cell_y + step_y < height)
                  or grid[cell_y + step_y][cell_x + step_x] in joining_letters
                  for step_y in range(-radius, radius + 1) for step_x in range(-radius, radius + 1))
    return is_deep


def blob_cell_metatile(grid: List[str], class_entry: dict, mask: int, cell_x: int, cell_y: int) -> int:
    has_deep_interior = "deep_interior" in class_entry and mask == FULL_MASK
    if has_deep_interior and is_deep_interior(grid, cell_x, cell_y, class_entry.get("joins", grid[cell_y][cell_x])):
        return class_entry["deep_interior"]
    metatile_id = blob_metatile_with_fill_variants(class_entry, mask, cell_x, cell_y)
    return metatile_id


def smooth_position_noise(shape) -> np.ndarray:
    rows, columns = np.mgrid[:shape[0], :shape[1]].astype(float)
    noise = (np.sin(columns * 0.31 + rows * 0.17) + np.sin(rows * 0.27 - columns * 0.11 + 1.3)
             + np.sin((columns + rows) * 0.19 + 2.1)) / 3.0
    return noise


def dune_grid(grid: List[str], sand_letter: str, noise_threshold: float) -> np.ndarray:
    is_sand = np.array([[letter == sand_letter for letter in grid_row] for grid_row in grid])
    padded = np.pad(is_sand, 1, constant_values=True)
    height, width = is_sand.shape
    all_neighbours_sand = np.ones(is_sand.shape, bool)
    for step_y in (-1, 0, 1):
        for step_x in (-1, 0, 1):
            all_neighbours_sand &= padded[1 + step_y:1 + step_y + height, 1 + step_x:1 + step_x + width]
    dunes = all_neighbours_sand & (smooth_position_noise(is_sand.shape) > noise_threshold)
    return dunes


def sand_with_dunes_metatile(dunes: np.ndarray, cell_x: int, cell_y: int, sand_class: dict, mask_bits: List[str]) -> int:
    if not dunes[cell_y, cell_x]:
        flat_variants = sand_class["base"]
        return flat_variants[(cell_x * 7 + cell_y * 13 + (cell_x * cell_y) % 5) % len(flat_variants)]
    height, width = dunes.shape
    mask = mask_from_predicate(width, height, cell_x, cell_y, lambda x, y: bool(dunes[y, x]), mask_bits)
    metatile_id = sand_class["dune_by_mask"][str(mask)]
    return metatile_id
