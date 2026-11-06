from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Set, Tuple

import numpy as np

from .breeze import (SWAY_OFFSET_IN_PIXELS_FOR_EACH_ANIMATION_TICK, BreezePlan, bent_grass_tips, colours_of_metatiles,
                     metatile_ids_nested_in, nodding_flowers, swayed_crown)

METATILE_SIZE_IN_PIXELS = 16
STILL = (0, 0)
HORIZONTAL_SWAY_OFFSETS = tuple((offset_in_pixels, 0) for offset_in_pixels in SWAY_OFFSET_IN_PIXELS_FOR_EACH_ANIMATION_TICK)
OFFSETS_IN_PIXELS_FOR_EACH_TICK_BY_MOTION = {
    "sway": HORIZONTAL_SWAY_OFFSETS,
    "tips": HORIZONTAL_SWAY_OFFSETS,
    "nod": HORIZONTAL_SWAY_OFFSETS,
    "bop": (STILL,) * 9 + ((0, -1), (0, -1), STILL, STILL, (0, -1), (0, -1), STILL),
    "dance": (STILL, (-1, 0), (-1, -1), (-1, 0), STILL, (1, 0), (1, -1), (1, 0)) * 2,
    "roll": (STILL,) * 10 + ((1, 0), (1, 0), STILL, (-1, 0), (-1, 0), STILL),
    "wobble": (STILL,) * 12 + ((1, 0), STILL, (-1, 0), STILL),
}
BREEZE_MOTIONS = ("sway", "tips", "nod")
OBJECT_MOTIONS = ("bop", "dance", "roll", "wobble")
MOTION_CHOICES = ("off", "game") + BREEZE_MOTIONS + OBJECT_MOTIONS
SPEED_CHOICES = (0.5, 1.0, 2.0)
BREEZE_MOTION_BY_KIND = {"crown": "sway", "grass_tips": "tips", "flower": "nod"}
PROP_MOTION_BY_NAME_PREFIX = (("mushroom", "bop"),)
GROUND_COLOUR_TOLERANCE = 40


@dataclass(frozen=True)
class TileMotion:
    motion: str
    speed: float = 1.0


@dataclass(frozen=True)
class PropCells:
    metatile_rows: Tuple[Tuple[int, ...], ...]

    def offset_of(self, metatile_id: int) -> Tuple[int, int]:
        for row_index, metatile_row in enumerate(self.metatile_rows):
            if metatile_id in metatile_row:
                return metatile_row.index(metatile_id), row_index
        return 0, 0


def is_known_motion(motion: str) -> bool:
    return motion in MOTION_CHOICES


def nearest_speed(speed: float) -> float:
    closest_speed = min(SPEED_CHOICES, key=lambda speed_choice: abs(speed_choice - float(speed)))
    return closest_speed


def default_prop_motion(prop_name: str) -> str:
    matching_motions = [motion for name_prefix, motion in PROP_MOTION_BY_NAME_PREFIX if prop_name.startswith(name_prefix)]
    default_motion = matching_motions[-1] if matching_motions else ""
    return default_motion


def prop_cells_from_ark_class_table(ark_class_table: dict) -> Dict[int, PropCells]:
    prop_cells_by_metatile: Dict[int, PropCells] = {}
    for prop in ark_class_table.get("props", {}).values():
        prop_cells = PropCells(tuple(tuple(int(metatile_id) for metatile_id in row) for row in prop["cells"]))
        prop_cells_by_metatile.update(dict.fromkeys(metatile_ids_nested_in(prop["cells"]), prop_cells))
    return prop_cells_by_metatile


def default_prop_motions_from_ark_class_table(ark_class_table: dict) -> Dict[int, TileMotion]:
    prop_motions = {metatile_id: TileMotion(default_prop_motion(prop_name))
                    for prop_name, prop in ark_class_table.get("props", {}).items()
                    for metatile_id in metatile_ids_nested_in(prop["cells"]) if default_prop_motion(prop_name)}
    return prop_motions


def default_motions(breeze_plan: BreezePlan, prop_motions: Mapping[int, TileMotion], game_animated: Iterable[int]) -> Dict[int, TileMotion]:
    motions = {metatile_id: TileMotion(BREEZE_MOTION_BY_KIND[kind]) for metatile_id, kind in breeze_plan.kind_by_metatile.items()
               if kind in BREEZE_MOTION_BY_KIND and metatile_id not in breeze_plan.ground_metatiles}
    motions.update(prop_motions)
    motions.update({metatile_id: TileMotion("game") for metatile_id in game_animated})
    return motions


def metatiles_moving_together(metatile_id: int, prop_cells_by_metatile: Mapping[int, PropCells]) -> List[int]:
    prop_cells = prop_cells_by_metatile.get(metatile_id)
    if prop_cells is None:
        return [metatile_id]
    together = list(metatile_ids_nested_in([list(row) for row in prop_cells.metatile_rows]))
    return together


def motions_with_overrides(motions: Mapping[int, TileMotion], overrides: Mapping[int, TileMotion],
                           prop_cells_by_metatile: Mapping[int, PropCells], game_animated: Set[int]) -> Dict[int, TileMotion]:
    combined = dict(motions)
    for metatile_id, override in overrides.items():
        for moving_metatile in metatiles_moving_together(metatile_id, prop_cells_by_metatile):
            combined[moving_metatile] = override_allowed_for(override, moving_metatile in game_animated)
    return combined


def override_allowed_for(override: TileMotion, is_game_animated: bool) -> TileMotion:
    if is_game_animated and override.motion not in ("off", "game"):
        return TileMotion("game", override.speed)
    if not is_game_animated and override.motion == "game":
        return TileMotion("off", override.speed)
    return override


def moving_offsets(motion: str) -> List[Tuple[int, int]]:
    offsets = sorted(set(OFFSETS_IN_PIXELS_FOR_EACH_TICK_BY_MOTION.get(motion, ())) - {STILL})
    return offsets


def breeze_frame_for_motion(metatile_pixels: np.ndarray, motion: str, offset_in_pixels: int,
                            ground_colours: Set[Tuple[int, int, int]], plain_grass_pixels: np.ndarray) -> np.ndarray:
    if motion == "sway":
        return swayed_crown(metatile_pixels, offset_in_pixels, ground_colours)
    if motion == "nod":
        return nodding_flowers(metatile_pixels, offset_in_pixels, plain_grass_pixels)
    return bent_grass_tips(metatile_pixels, offset_in_pixels)


def breeze_frames(tileset_pair, metatile_id: int, motion: str, ground_colours, plain_grass_pixels) -> Dict[Tuple[int, int], np.ndarray]:
    metatile_pixels = tileset_pair.render_metatile(metatile_id)
    frames = {offset: breeze_frame_for_motion(metatile_pixels, motion, offset[0], ground_colours, plain_grass_pixels)
              for offset in moving_offsets(motion)}
    if all(np.array_equal(frame_pixels, metatile_pixels) for frame_pixels in frames.values()):
        return {}
    return frames


def composed_prop_pixels(tileset_pair, metatile_rows: Tuple[Tuple[int, ...], ...]) -> np.ndarray:
    composed_rows = [np.concatenate([tileset_pair.render_metatile(metatile_id) for metatile_id in row], axis=1) for row in metatile_rows]
    composed = np.concatenate(composed_rows, axis=0)
    return composed


def tiled_ground_pixels(plain_grass_pixels: np.ndarray, height_in_pixels: int, width_in_pixels: int) -> np.ndarray:
    repeats = (height_in_pixels // METATILE_SIZE_IN_PIXELS, width_in_pixels // METATILE_SIZE_IN_PIXELS, 1)
    tiled_ground = np.tile(plain_grass_pixels, repeats)
    return tiled_ground


def object_pixel_mask(prop_pixels: np.ndarray, ground_pixels: np.ndarray) -> np.ndarray:
    colour_difference = np.abs(prop_pixels.astype(np.int32) - ground_pixels.astype(np.int32)).sum(axis=2)
    is_object_pixel = colour_difference > GROUND_COLOUR_TOLERANCE
    return is_object_pixel


def shifted_object(prop_pixels: np.ndarray, ground_pixels: np.ndarray, offset: Tuple[int, int]) -> np.ndarray:
    offset_x, offset_y = offset
    is_object_pixel = object_pixel_mask(prop_pixels, ground_pixels)
    shifted_pixels = np.where(is_object_pixel[:, :, None], ground_pixels, prop_pixels)
    source_rows, source_columns = np.nonzero(is_object_pixel)
    target_rows, target_columns = source_rows + offset_y, source_columns + offset_x
    is_inside = (target_rows >= 0) & (target_rows < prop_pixels.shape[0]) & (target_columns >= 0) & (target_columns < prop_pixels.shape[1])
    shifted_pixels[target_rows[is_inside], target_columns[is_inside]] = prop_pixels[source_rows[is_inside], source_columns[is_inside]]
    return shifted_pixels


def split_into_metatiles(composed_pixels: np.ndarray, metatile_rows: Tuple[Tuple[int, ...], ...]) -> Dict[int, np.ndarray]:
    cell_pixels = {metatile_id: composed_pixels[row_index * METATILE_SIZE_IN_PIXELS:(row_index + 1) * METATILE_SIZE_IN_PIXELS,
                                                column_index * METATILE_SIZE_IN_PIXELS:(column_index + 1) * METATILE_SIZE_IN_PIXELS]
                   for row_index, row in enumerate(metatile_rows) for column_index, metatile_id in enumerate(row)}
    return cell_pixels


def object_frames(tileset_pair, metatile_rows: Tuple[Tuple[int, ...], ...], motion: str,
                  plain_grass_pixels: np.ndarray) -> Dict[int, Dict[Tuple[int, int], np.ndarray]]:
    prop_pixels = composed_prop_pixels(tileset_pair, metatile_rows)
    ground_pixels = tiled_ground_pixels(plain_grass_pixels, prop_pixels.shape[0], prop_pixels.shape[1])
    frames_by_metatile: Dict[int, Dict[Tuple[int, int], np.ndarray]] = {}
    for offset in moving_offsets(motion):
        for metatile_id, pixels in split_into_metatiles(shifted_object(prop_pixels, ground_pixels, offset), metatile_rows).items():
            frames_by_metatile.setdefault(metatile_id, {})[offset] = pixels
    return frames_by_metatile


def motion_frames_for_tileset_pair(tileset_pair, motions: Mapping[int, TileMotion], prop_cells_by_metatile: Mapping[int, PropCells],
                                   breeze_plan: BreezePlan) -> Dict[int, Dict[Tuple[int, int], np.ndarray]]:
    if breeze_plan.plain_grass_metatile < 0:
        return {}
    ground_colours = colours_of_metatiles([tileset_pair.render_metatile(metatile_id) for metatile_id in breeze_plan.ground_metatiles])
    plain_grass_pixels = tileset_pair.render_metatile(breeze_plan.plain_grass_metatile)
    frames_by_metatile: Dict[int, Dict[Tuple[int, int], np.ndarray]] = {}
    for metatile_id, tile_motion in sorted(motions.items()):
        if metatile_id in frames_by_metatile:
            continue
        if tile_motion.motion in BREEZE_MOTIONS:
            frames_by_metatile[metatile_id] = breeze_frames(tileset_pair, metatile_id, tile_motion.motion, ground_colours, plain_grass_pixels)
        if tile_motion.motion in OBJECT_MOTIONS:
            prop_cells = prop_cells_by_metatile.get(metatile_id, PropCells(((metatile_id,),)))
            frames_by_metatile.update(object_frames(tileset_pair, prop_cells.metatile_rows, tile_motion.motion, plain_grass_pixels))
    moving_frames_by_metatile = {metatile_id: frames for metatile_id, frames in frames_by_metatile.items() if frames}
    return moving_frames_by_metatile


def atlas_sequence_for_motion(base_atlas_cell: int, atlas_cell_by_offset: Mapping[Tuple[int, int], int], motion: str) -> List[int]:
    atlas_cells_for_each_tick = [atlas_cell_by_offset.get(offset, base_atlas_cell) for offset in OFFSETS_IN_PIXELS_FOR_EACH_TICK_BY_MOTION[motion]]
    atlas_cells_after_the_first_tick = atlas_cells_for_each_tick[1:]
    return atlas_cells_after_the_first_tick


def motion_meta_for_metatile(metatile_id: int, tile_motion: TileMotion, prop_cells_by_metatile: Mapping[int, PropCells]) -> dict:
    motion_meta: dict = {"motion": tile_motion.motion, "speed": tile_motion.speed}
    if tile_motion.motion in OBJECT_MOTIONS:
        prop_cells = prop_cells_by_metatile.get(metatile_id)
        motion_meta["origin"] = list(prop_cells.offset_of(metatile_id)) if prop_cells else [0, 0]
    return motion_meta
