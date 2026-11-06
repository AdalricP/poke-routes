from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Set, Tuple

import numpy as np

SWAY_OFFSET_IN_PIXELS_FOR_EACH_ANIMATION_TICK = (0, 0, 0, 0, 1, 1, 1, 1, 0, 0, 0, 0, -1, -1, -1, -1)
SWAY_OFFSETS_IN_PIXELS_THAT_NEED_FRAMES = (1, -1)
METATILE_SIZE_IN_PIXELS = 16
TILE_SIZE_IN_PIXELS = 8
ROWS_OF_EACH_TILE_THAT_ARE_GRASS_TIPS = 3
OUTLINE_LUMINANCE_CEILING = 56
SHARE_OF_PLAIN_GRASS_PIXELS_FOR_A_FLOWER_SPRITE = 0.4
TREE_CLASS_LETTERS_IN_LEARNED_MODELS = ("f",)
TALL_GRASS_CLASS_LETTERS_IN_LEARNED_MODELS = ("t",)
ARK_TABLE_KEYS_HOLDING_TREE_METATILES = ("by_context", "overlays", "bush", "base")
ARK_TABLE_KEYS_HOLDING_TALL_GRASS_METATILES = ("by_mask", "base")


@dataclass(frozen=True)
class BreezePlan:
    kind_by_metatile: Dict[int, str] = field(default_factory=dict)
    ground_metatiles: Tuple[int, ...] = ()
    plain_grass_metatile: int = -1


def metatile_ids_nested_in(table_entry) -> Iterator[int]:
    if isinstance(table_entry, bool):
        return
    if isinstance(table_entry, int):
        yield table_entry
        return
    if isinstance(table_entry, dict):
        for nested_entry in table_entry.values():
            yield from metatile_ids_nested_in(nested_entry)
        return
    if isinstance(table_entry, list):
        for nested_entry in table_entry:
            yield from metatile_ids_nested_in(nested_entry)


def metatile_ids_of_ark_class(class_entry: dict, table_keys: Tuple[str, ...]) -> Set[int]:
    metatile_ids = {metatile_id for table_key in table_keys for metatile_id in metatile_ids_nested_in(class_entry.get(table_key, {}))}
    return metatile_ids


def is_ark_tree_class(class_entry: dict) -> bool:
    is_tree_class = class_entry.get("autotile") == "tree2x2"
    return is_tree_class


def is_ark_tall_grass_class(class_entry: dict) -> bool:
    is_tall_grass_class = "tall grass" in str(class_entry.get("name", "")) or class_entry.get("encounters") == "tall grass"
    return is_tall_grass_class


def ark_flower_metatiles(ark_class_table: dict) -> Set[int]:
    flower_metatiles = {metatile_id for prop_name, prop in ark_class_table.get("props", {}).items()
                        if prop_name.startswith("flowers") for metatile_id in metatile_ids_nested_in(prop.get("cells", []))}
    return flower_metatiles


def breeze_plan_for_ark_class_table(ark_class_table: dict) -> BreezePlan:
    classes_by_letter = ark_class_table["classes"]
    kind_by_metatile: Dict[int, str] = {}
    for class_entry in classes_by_letter.values():
        if is_ark_tree_class(class_entry):
            kind_by_metatile.update(dict.fromkeys(metatile_ids_of_ark_class(class_entry, ARK_TABLE_KEYS_HOLDING_TREE_METATILES), "crown"))
        if is_ark_tall_grass_class(class_entry):
            kind_by_metatile.update(dict.fromkeys(metatile_ids_of_ark_class(class_entry, ARK_TABLE_KEYS_HOLDING_TALL_GRASS_METATILES), "grass_tips"))
    kind_by_metatile.update(dict.fromkeys(ark_flower_metatiles(ark_class_table), "flower"))
    plain_grass_metatile = classes_by_letter["g"]["base"][0]
    ground_metatiles = (plain_grass_metatile, *classes_by_letter.get("p", {}).get("base", []))
    breeze_plan = BreezePlan(kind_by_metatile, tuple(ground_metatiles), plain_grass_metatile)
    return breeze_plan


def most_common_metatile_in_uniform_context(learned_model: dict, class_letter: str) -> int:
    counts_by_metatile = learned_model.get("ctx9", {}).get(class_letter * 9, {})
    if not counts_by_metatile:
        return -1
    most_common_metatile = int(max(counts_by_metatile, key=counts_by_metatile.get))
    return most_common_metatile


def breeze_kind_for_learned_class(class_letter: str) -> str:
    if class_letter in TREE_CLASS_LETTERS_IN_LEARNED_MODELS:
        return "crown"
    if class_letter in TALL_GRASS_CLASS_LETTERS_IN_LEARNED_MODELS:
        return "grass_tips"
    return ""


def breeze_plan_for_learned_model(learned_model: dict) -> BreezePlan:
    kind_by_metatile = {int(metatile_id): breeze_kind_for_learned_class(class_letter)
                        for metatile_id, class_letter in learned_model.get("classes", {}).items()}
    kind_by_metatile = {metatile_id: kind for metatile_id, kind in kind_by_metatile.items() if kind}
    plain_grass_metatile = most_common_metatile_in_uniform_context(learned_model, "g")
    plain_path_metatile = most_common_metatile_in_uniform_context(learned_model, "p")
    ground_metatiles = tuple(metatile_id for metatile_id in (plain_grass_metatile, plain_path_metatile) if metatile_id >= 0)
    breeze_plan = BreezePlan(kind_by_metatile, ground_metatiles, plain_grass_metatile)
    return breeze_plan


def colours_of_metatiles(metatile_pixels: List[np.ndarray]) -> Set[Tuple[int, int, int]]:
    colours = {tuple(int(channel) for channel in pixel) for pixels in metatile_pixels for pixel in pixels.reshape(-1, 3)}
    return colours


def mask_of_pixels_with_colours(metatile_pixels: np.ndarray, colours: Set[Tuple[int, int, int]]) -> np.ndarray:
    flat_pixels = metatile_pixels.reshape(-1, 3)
    is_listed_colour = np.array([tuple(int(channel) for channel in pixel) in colours for pixel in flat_pixels])
    pixel_mask = is_listed_colour.reshape(metatile_pixels.shape[:2])
    return pixel_mask


def mask_of_outline_pixels(metatile_pixels: np.ndarray) -> np.ndarray:
    luminance = metatile_pixels.astype(np.int32) @ np.array([299, 587, 114]) // 1000
    outline_mask = luminance < OUTLINE_LUMINANCE_CEILING
    return outline_mask


def mask_of_inner_columns() -> np.ndarray:
    inner_column_mask = np.zeros((METATILE_SIZE_IN_PIXELS, METATILE_SIZE_IN_PIXELS), dtype=bool)
    inner_column_mask[:, 1:METATILE_SIZE_IN_PIXELS - 1] = True
    return inner_column_mask


def mask_of_grass_tip_rows() -> np.ndarray:
    row_numbers = np.arange(METATILE_SIZE_IN_PIXELS)
    is_tip_row = (row_numbers % TILE_SIZE_IN_PIXELS) < ROWS_OF_EACH_TILE_THAT_ARE_GRASS_TIPS
    tip_row_mask = np.repeat(is_tip_row[:, None], METATILE_SIZE_IN_PIXELS, axis=1)
    return tip_row_mask


def slide_pixels_inside_mask(metatile_pixels: np.ndarray, moving_mask: np.ndarray, offset_in_pixels: int) -> np.ndarray:
    source_pixels = np.roll(metatile_pixels, offset_in_pixels, axis=1)
    source_is_moving = np.roll(moving_mask, offset_in_pixels, axis=1)
    pixels_taking_source = moving_mask & source_is_moving & mask_of_inner_columns()
    slid_pixels = np.where(pixels_taking_source[:, :, None], source_pixels, metatile_pixels)
    return slid_pixels


def crown_moving_mask(metatile_pixels: np.ndarray, ground_colours: Set[Tuple[int, int, int]]) -> np.ndarray:
    is_ground = mask_of_pixels_with_colours(metatile_pixels, ground_colours)
    foliage_mask = ~is_ground & ~mask_of_outline_pixels(metatile_pixels)
    return foliage_mask


def swayed_crown(metatile_pixels: np.ndarray, offset_in_pixels: int, ground_colours: Set[Tuple[int, int, int]]) -> np.ndarray:
    swayed_pixels = slide_pixels_inside_mask(metatile_pixels, crown_moving_mask(metatile_pixels, ground_colours), offset_in_pixels)
    return swayed_pixels


def bent_grass_tips(metatile_pixels: np.ndarray, offset_in_pixels: int) -> np.ndarray:
    bent_pixels = slide_pixels_inside_mask(metatile_pixels, mask_of_grass_tip_rows(), offset_in_pixels)
    return bent_pixels


def nodding_flowers(metatile_pixels: np.ndarray, offset_in_pixels: int, plain_grass_pixels: np.ndarray) -> np.ndarray:
    is_flower = np.any(metatile_pixels != plain_grass_pixels, axis=2)
    if is_flower.mean() > 1 - SHARE_OF_PLAIN_GRASS_PIXELS_FOR_A_FLOWER_SPRITE:
        return bent_grass_tips(metatile_pixels, offset_in_pixels)
    flowers_lifted_off = np.where(is_flower[:, :, None], plain_grass_pixels, metatile_pixels)
    moved_flower_mask = np.roll(is_flower, offset_in_pixels, axis=1) & mask_of_inner_columns()
    moved_flower_pixels = np.roll(metatile_pixels, offset_in_pixels, axis=1)
    nodded_pixels = np.where(moved_flower_mask[:, :, None], moved_flower_pixels, flowers_lifted_off)
    return nodded_pixels


def breeze_frame(metatile_pixels: np.ndarray, kind: str, offset_in_pixels: int,
                 ground_colours: Set[Tuple[int, int, int]], plain_grass_pixels: np.ndarray) -> np.ndarray:
    if kind == "crown":
        return swayed_crown(metatile_pixels, offset_in_pixels, ground_colours)
    if kind == "flower":
        return nodding_flowers(metatile_pixels, offset_in_pixels, plain_grass_pixels)
    return bent_grass_tips(metatile_pixels, offset_in_pixels)


def breeze_frames_for_metatile(metatile_pixels: np.ndarray, kind: str, ground_colours: Set[Tuple[int, int, int]],
                               plain_grass_pixels: np.ndarray) -> Dict[int, np.ndarray]:
    frames_by_offset = {offset_in_pixels: breeze_frame(metatile_pixels, kind, offset_in_pixels, ground_colours, plain_grass_pixels)
                        for offset_in_pixels in SWAY_OFFSETS_IN_PIXELS_THAT_NEED_FRAMES}
    if all(np.array_equal(frame_pixels, metatile_pixels) for frame_pixels in frames_by_offset.values()):
        return {}
    return frames_by_offset


def breeze_frames_for_tileset_pair(tileset_pair, breeze_plan: BreezePlan, metatiles_that_already_animate) -> Dict[int, Dict[int, np.ndarray]]:
    if breeze_plan.plain_grass_metatile < 0:
        return {}
    ground_colours = colours_of_metatiles([tileset_pair.render_metatile(metatile_id) for metatile_id in breeze_plan.ground_metatiles])
    plain_grass_pixels = tileset_pair.render_metatile(breeze_plan.plain_grass_metatile)
    frames_by_metatile = {metatile_id: breeze_frames_for_metatile(tileset_pair.render_metatile(metatile_id), kind, ground_colours, plain_grass_pixels)
                          for metatile_id, kind in sorted(breeze_plan.kind_by_metatile.items())
                          if metatile_id not in metatiles_that_already_animate and metatile_id not in breeze_plan.ground_metatiles}
    swaying_frames_by_metatile = {metatile_id: frames for metatile_id, frames in frames_by_metatile.items() if frames}
    return swaying_frames_by_metatile


def animation_sequence_for_swaying_metatile(base_atlas_cell: int, atlas_cell_by_offset: Dict[int, int]) -> List[int]:
    atlas_cells_for_each_tick = [atlas_cell_by_offset.get(offset_in_pixels, base_atlas_cell)
                                 for offset_in_pixels in SWAY_OFFSET_IN_PIXELS_FOR_EACH_ANIMATION_TICK]
    atlas_cells_after_the_first_tick = atlas_cells_for_each_tick[1:]
    return atlas_cells_after_the_first_tick
