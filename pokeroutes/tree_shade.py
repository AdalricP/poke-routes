from typing import Dict, List, Mapping, Tuple

import numpy as np

from .spec import TREE_SHADES

SHADE_CHOICES = TREE_SHADES
FLOOR_COLOURS_BY_SHADE = {
    "green": ((57, 123, 82), (90, 148, 99), (33, 115, 66)),
    "yellow": ((115, 99, 41), (156, 140, 82), (90, 82, 66)),
    "red": ((107, 45, 38), (143, 74, 60), (82, 36, 33)),
}
LEAF_SHADE_BY_TREE_LETTER = {"f": "green", "F": "yellow", "R": "red"}
LEAF_COLOURS_BY_TREE_LETTER = {
    "f": ["#5f9e45", "#7fb85a", "#467f34", "#9ccc6e"],
    "F": ["#e9a63a", "#d8692c", "#c4822b", "#f0c05a"],
    "R": ["#b9381f", "#9c2f1c", "#d0503a", "#e06a4a"],
}
MEADOW_SHADE = "meadow"
BIOMES_WITH_MEADOW_SHADE = ("meadow", "plains")
SHADE_WHEN_NOTHING_IS_SET = "leaves"


def resolved_shade(route_choice: str, tileset_choice: str, biome: str) -> str:
    if route_choice in SHADE_CHOICES and route_choice != "auto":
        return route_choice
    if tileset_choice in SHADE_CHOICES and tileset_choice != "auto":
        return tileset_choice
    biome_shade = MEADOW_SHADE if biome in BIOMES_WITH_MEADOW_SHADE else SHADE_WHEN_NOTHING_IS_SET
    return biome_shade


def shade_for_tree_letter(shade: str, tree_letter: str) -> str:
    if shade == "leaves":
        return LEAF_SHADE_BY_TREE_LETTER.get(tree_letter, "green")
    return shade


def grass_ramp(plain_grass_pixels: np.ndarray) -> List[Tuple[int, int, int]]:
    colours, counts = np.unique(plain_grass_pixels.reshape(-1, 3), axis=0, return_counts=True)
    ramp = [tuple(int(channel) for channel in colours[index]) for index in np.argsort(-counts)]
    return ramp


def shade_colour_for_grass_pixel(grass_pixel: Tuple[int, int, int], ramp: List[Tuple[int, int, int]], shade: str) -> Tuple[int, int, int]:
    if shade == MEADOW_SHADE:
        return grass_pixel
    floor_colours = FLOOR_COLOURS_BY_SHADE[shade]
    rank = ramp.index(grass_pixel) if grass_pixel in ramp else 0
    shade_colour = floor_colours[min(rank, len(floor_colours) - 1)]
    return shade_colour


def floor_mask_from_rows(mask_rows: List[int]) -> np.ndarray:
    floor_mask = np.array([[bool(mask_row >> column & 1) for column in range(16)] for mask_row in mask_rows])
    return floor_mask


def shaded_metatile_pixels(metatile_pixels: np.ndarray, mask_rows: List[int], plain_grass_pixels: np.ndarray, shade: str) -> np.ndarray:
    ramp = grass_ramp(plain_grass_pixels)
    floor_mask = floor_mask_from_rows(mask_rows)
    shaded_pixels = metatile_pixels.copy()
    for row, column in zip(*np.nonzero(floor_mask)):
        grass_pixel = tuple(int(channel) for channel in plain_grass_pixels[row, column])
        shaded_pixels[row, column] = shade_colour_for_grass_pixel(grass_pixel, ramp, shade)
    return shaded_pixels


def floor_plan_for_shade(ark_class_table: dict, shade: str) -> Dict[int, Tuple[List[int], str]]:
    floor_plan: Dict[int, Tuple[List[int], str]] = {}
    for tree_letter, class_entry in ark_class_table.get("classes", {}).items():
        letter_shade = shade_for_tree_letter(shade, tree_letter)
        if letter_shade == class_entry.get("floor_shade"):
            continue
        floor_plan.update({int(metatile_id): (mask_rows, letter_shade) for metatile_id, mask_rows in class_entry.get("floor_masks", {}).items()})
    return floor_plan


class ShadedTilesetPair:
    def __init__(self, tileset_pair, floor_plan: Mapping[int, Tuple[List[int], str]], plain_grass_metatile: int):
        self.tileset_pair = tileset_pair
        self.floor_plan = floor_plan
        self.plain_grass_pixels = tileset_pair.render_metatile(plain_grass_metatile)

    def render_metatile(self, metatile_id: int, frame: int = 0) -> np.ndarray:
        metatile_pixels = self.tileset_pair.render_metatile(metatile_id, frame=frame)
        if metatile_id not in self.floor_plan:
            return metatile_pixels
        mask_rows, shade = self.floor_plan[metatile_id]
        shaded_pixels = shaded_metatile_pixels(metatile_pixels, mask_rows, self.plain_grass_pixels, shade)
        return shaded_pixels

    def __getattr__(self, attribute_name):
        return getattr(self.tileset_pair, attribute_name)
