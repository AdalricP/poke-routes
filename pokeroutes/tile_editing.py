from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

from .tileset import NUM_PRIMARY_METATILES, NUM_PRIMARY_TILES, NUM_PALS_TOTAL, TilesetPair
from .tileset_overlay import (COLOURS_PER_PALETTE, MAXIMUM_SECONDARY_METATILE_COUNT, MAXIMUM_SECONDARY_TILE_COUNT,
                              PIXELS_PER_TILE_SIDE, InvalidOverlay, apply_overlay_to_tileset_pair, colour_from_hex,
                              colour_to_hex, tile_pixels_to_hex)

LAYER_COUNT = 2
QUADRANTS_PER_LAYER = 4
METATILE_SIDE_IN_PIXELS = 16
TILE_INDEX_MASK = 0x3FF
HORIZONTAL_FLIP_BIT = 0x400
VERTICAL_FLIP_BIT = 0x800
PALETTE_SLOT_SHIFT = 12
BEHAVIOUR_MASK = 0xFF
LAYER_TYPE_SHIFT = 12
DEFAULT_NEW_METATILE_ATTRIBUTE = 0x1000
COMMON_BEHAVIOURS = (
    (0x00, "normal"), (0x02, "tall grass"), (0x03, "long grass"), (0x07, "short grass"), (0x06, "deep sand"),
    (0x21, "sand"), (0x24, "ash grass"), (0x10, "pond water"), (0x12, "deep water"), (0x15, "ocean water"),
    (0x17, "shallow water"), (0x13, "waterfall"), (0x16, "puddle"), (0x28, "hot springs"),
    (0x38, "jump east"), (0x39, "jump west"), (0x3A, "jump north"), (0x3B, "jump south"), (0x71, "bridge over pond"),
)
LAYER_TYPES = ((0, "normal"), (1, "covered"), (2, "split"))


class TileSlotsExhausted(Exception):
    pass


@dataclass(frozen=True)
class TileEntry:
    tile_index: int
    is_flipped_horizontally: bool
    is_flipped_vertically: bool
    palette_slot: int


def decode_tile_entry(packed_entry: int) -> TileEntry:
    decoded_entry = TileEntry(int(packed_entry) & TILE_INDEX_MASK, bool(int(packed_entry) & HORIZONTAL_FLIP_BIT),
                              bool(int(packed_entry) & VERTICAL_FLIP_BIT), (int(packed_entry) >> PALETTE_SLOT_SHIFT) & 0xF)
    return decoded_entry


def encode_tile_entry(tile_entry: TileEntry) -> int:
    packed_entry = ((tile_entry.tile_index & TILE_INDEX_MASK)
                    | (HORIZONTAL_FLIP_BIT if tile_entry.is_flipped_horizontally else 0)
                    | (VERTICAL_FLIP_BIT if tile_entry.is_flipped_vertically else 0)
                    | ((tile_entry.palette_slot & 0xF) << PALETTE_SLOT_SHIFT))
    return packed_entry


def every_metatile_id(pair: TilesetPair) -> List[int]:
    primary_count, secondary_count = pair.metatile_count()
    metatile_ids = list(range(primary_count)) + [NUM_PRIMARY_METATILES + offset for offset in range(secondary_count)]
    return metatile_ids


def metatile_exists(pair: TilesetPair, metatile_id: int) -> bool:
    exists = pair._entries(int(metatile_id)) is not None
    return exists


def metatile_entries(pair: TilesetPair, metatile_id: int) -> List[int]:
    stored_entries = pair._entries(int(metatile_id))
    if stored_entries is None:
        raise KeyError(f"no metatile {int(metatile_id)} (0x{int(metatile_id):03X}) in this tileset pair")
    entries = [int(packed_entry) for packed_entry in stored_entries]
    return entries


def is_primary_metatile(metatile_id: int) -> bool:
    is_primary = int(metatile_id) < NUM_PRIMARY_METATILES
    return is_primary


def static_tile_pixels(pair: TilesetPair, tile_index: int) -> np.ndarray:
    owning_tileset = pair.primary if tile_index < NUM_PRIMARY_TILES else pair.secondary
    local_tile_index = tile_index if tile_index < NUM_PRIMARY_TILES else tile_index - NUM_PRIMARY_TILES
    if local_tile_index >= len(owning_tileset.tiles):
        return np.zeros((PIXELS_PER_TILE_SIDE, PIXELS_PER_TILE_SIDE), dtype=np.uint8)
    tile_pixels = owning_tileset.tiles[local_tile_index].copy()
    return tile_pixels


def flipped_as_entry_shows_it(tile_pixels: np.ndarray, tile_entry: TileEntry) -> np.ndarray:
    shown_pixels = tile_pixels
    if tile_entry.is_flipped_horizontally:
        shown_pixels = shown_pixels[:, ::-1]
    if tile_entry.is_flipped_vertically:
        shown_pixels = shown_pixels[::-1, :]
    shown_pixels_copy = np.ascontiguousarray(shown_pixels)
    return shown_pixels_copy


def tile_reference_counts(pair: TilesetPair) -> Dict[int, int]:
    reference_counts: Dict[int, int] = {}
    for metatile_id in every_metatile_id(pair):
        for packed_entry in metatile_entries(pair, metatile_id):
            tile_index = int(packed_entry) & TILE_INDEX_MASK
            reference_counts[tile_index] = reference_counts.get(tile_index, 0) + 1
    return reference_counts


def quadrant_origin_in_pixels(quadrant_position: int) -> Tuple[int, int]:
    origin_x_and_y = ((quadrant_position % 2) * PIXELS_PER_TILE_SIDE, (quadrant_position // 2) * PIXELS_PER_TILE_SIDE)
    return origin_x_and_y


def layer_pixels_as_displayed(pair: TilesetPair, metatile_id: int, layer_index: int) -> np.ndarray:
    entries = metatile_entries(pair, metatile_id)
    layer_pixels = np.zeros((METATILE_SIDE_IN_PIXELS, METATILE_SIDE_IN_PIXELS), dtype=np.uint8)
    for quadrant_position in range(QUADRANTS_PER_LAYER):
        tile_entry = decode_tile_entry(entries[layer_index * QUADRANTS_PER_LAYER + quadrant_position])
        origin_x, origin_y = quadrant_origin_in_pixels(quadrant_position)
        shown_pixels = flipped_as_entry_shows_it(static_tile_pixels(pair, tile_entry.tile_index), tile_entry)
        layer_pixels[origin_y:origin_y + PIXELS_PER_TILE_SIDE, origin_x:origin_x + PIXELS_PER_TILE_SIDE] = shown_pixels
    return layer_pixels


def layer_palette_slots(pair: TilesetPair, metatile_id: int, layer_index: int) -> List[int]:
    entries = metatile_entries(pair, metatile_id)
    palette_slots = [decode_tile_entry(entries[layer_index * QUADRANTS_PER_LAYER + quadrant_position]).palette_slot
                     for quadrant_position in range(QUADRANTS_PER_LAYER)]
    return palette_slots


def describe_metatile(pair: TilesetPair, overlay: dict, metatile_id: int) -> dict:
    entries = metatile_entries(pair, metatile_id)
    reference_counts = tile_reference_counts(pair)
    attribute_value = pair.attribute(metatile_id)
    layers = [describe_one_layer(pair, entries, layer_index, reference_counts) for layer_index in range(LAYER_COUNT)]
    used_palette_slots = sorted({quadrant["palette_slot"] for layer in layers for quadrant in layer["quadrants"]})
    description = {
        "metatile_id": int(metatile_id), "metatile_id_hex": "0x%03X" % int(metatile_id),
        "layers": layers, "behaviour": attribute_value & BEHAVIOUR_MASK,
        "layer_type": attribute_value >> LAYER_TYPE_SHIFT,
        "custom": overlay["custom_metatiles"].get(str(int(metatile_id))),
        "palettes": {str(palette_slot): [colour_to_hex(colour) for colour in pair.palettes[palette_slot]]
                     for palette_slot in used_palette_slots},
    }
    return description


def describe_one_layer(pair: TilesetPair, entries: List[int], layer_index: int, reference_counts: Dict[int, int]) -> dict:
    quadrants = []
    for quadrant_position in range(QUADRANTS_PER_LAYER):
        tile_entry = decode_tile_entry(entries[layer_index * QUADRANTS_PER_LAYER + quadrant_position])
        shown_pixels = flipped_as_entry_shows_it(static_tile_pixels(pair, tile_entry.tile_index), tile_entry)
        quadrants.append({"tile_index": tile_entry.tile_index, "palette_slot": tile_entry.palette_slot,
                          "flip_horizontal": tile_entry.is_flipped_horizontally,
                          "flip_vertical": tile_entry.is_flipped_vertically,
                          "shared_by_entries": reference_counts.get(tile_entry.tile_index, 0),
                          "rows_as_shown": ["".join("%x" % colour for colour in row) for row in shown_pixels]})
    layer_description = {"layer": "bottom" if layer_index == 0 else "top", "quadrants": quadrants}
    return layer_description


class TileAllocator:
    def __init__(self, pair: TilesetPair):
        self.pair = pair
        self.reference_counts = tile_reference_counts(pair)
        self.handed_out_tile_indices: set = set()

    def is_unused_blank_tile(self, tile_index: int) -> bool:
        is_unused_blank = (tile_index != 0 and tile_index not in self.handed_out_tile_indices
                           and self.reference_counts.get(tile_index, 0) == 0
                           and not static_tile_pixels(self.pair, tile_index).any())
        return is_unused_blank

    def next_tile_index(self, for_primary_metatile: bool) -> int:
        candidate_indices = self.candidate_tile_indices(for_primary_metatile)
        for candidate_index in candidate_indices:
            if candidate_index in self.handed_out_tile_indices:
                continue
            self.handed_out_tile_indices.add(candidate_index)
            return candidate_index
        sheet_name = "primary" if for_primary_metatile else "secondary"
        raise TileSlotsExhausted(f"no free 8x8 tile slot left for a {sheet_name} metatile")

    def candidate_tile_indices(self, for_primary_metatile: bool) -> Iterable[int]:
        appended_secondary_start = NUM_PRIMARY_TILES + len(self.pair.secondary.tiles)
        appended_secondary_indices = range(appended_secondary_start, NUM_PRIMARY_TILES + MAXIMUM_SECONDARY_TILE_COUNT)
        blank_secondary_indices = (tile_index for tile_index in range(NUM_PRIMARY_TILES, appended_secondary_start)
                                   if self.is_unused_blank_tile(tile_index))
        blank_primary_indices = (tile_index for tile_index in range(1, NUM_PRIMARY_TILES)
                                 if self.is_unused_blank_tile(tile_index))
        if for_primary_metatile:
            return blank_primary_indices
        return _chain(appended_secondary_indices, blank_secondary_indices, blank_primary_indices)


def _chain(*index_sources: Iterable[int]) -> Iterable[int]:
    for index_source in index_sources:
        yield from index_source


class OverlayEdit:
    def __init__(self, base_pair: TilesetPair, overlay: dict):
        self.base_pair = base_pair
        self.overlay = copy.deepcopy(overlay)
        self.pair = apply_overlay_to_tileset_pair(base_pair, self.overlay)
        self.allocator = TileAllocator(self.pair)
        self.pending_tiles: Dict[int, np.ndarray] = {}

    def current_tile_pixels(self, tile_index: int) -> np.ndarray:
        if tile_index in self.pending_tiles:
            return self.pending_tiles[tile_index].copy()
        tile_pixels = static_tile_pixels(self.pair, tile_index)
        return tile_pixels

    def current_entries(self, metatile_id: int) -> List[int]:
        stored_entries = self.overlay["metatiles"].get(str(int(metatile_id)))
        if stored_entries is not None:
            return list(stored_entries)
        entries = metatile_entries(self.pair, metatile_id)
        return entries

    def store_entries(self, metatile_id: int, entries: List[int]) -> None:
        self.overlay["metatiles"][str(int(metatile_id))] = [int(packed_entry) for packed_entry in entries]

    def next_metatile_id_including_pending(self) -> int:
        stored_secondary_ids = [int(metatile_id_text) for metatile_id_text in self.overlay["metatiles"]
                                if int(metatile_id_text) >= NUM_PRIMARY_METATILES]
        first_free_after_pair = next_new_metatile_id(self.pair)
        first_free_metatile_id = max([first_free_after_pair] + [metatile_id + 1 for metatile_id in stored_secondary_ids])
        if first_free_metatile_id >= NUM_PRIMARY_METATILES + MAXIMUM_SECONDARY_METATILE_COUNT:
            raise TileSlotsExhausted("the secondary tileset already has 512 metatiles")
        return first_free_metatile_id

    def store_tile(self, tile_index: int, tile_pixels: np.ndarray) -> None:
        self.pending_tiles[tile_index] = tile_pixels.copy()
        self.overlay["tiles"][str(int(tile_index))] = tile_pixels_to_hex(tile_pixels)

    def is_tile_private_to_one_entry(self, tile_index: int) -> bool:
        is_private = self.allocator.reference_counts.get(tile_index, 0) <= 1 and tile_index != 0
        return is_private or tile_index in self.allocator.handed_out_tile_indices

    def replace_quadrant(self, metatile_id: int, entry_position: int, shown_pixels: np.ndarray,
                         palette_slot: Optional[int] = None, edit_shared_tiles: bool = False) -> None:
        entries = self.current_entries(metatile_id)
        tile_entry = decode_tile_entry(entries[entry_position])
        wanted_palette_slot = tile_entry.palette_slot if palette_slot is None else int(palette_slot)
        current_shown_pixels = flipped_as_entry_shows_it(self.current_tile_pixels(tile_entry.tile_index), tile_entry)
        if np.array_equal(current_shown_pixels, shown_pixels) and wanted_palette_slot == tile_entry.palette_slot:
            return
        target_tile_index = tile_entry.tile_index
        pixels_change = not np.array_equal(current_shown_pixels, shown_pixels)
        if pixels_change and not edit_shared_tiles and not self.is_tile_private_to_one_entry(target_tile_index):
            target_tile_index = self.allocator.next_tile_index(is_primary_metatile(metatile_id))
        if pixels_change:
            self.store_tile(target_tile_index, flipped_as_entry_shows_it(np.asarray(shown_pixels, dtype=np.uint8), tile_entry))
        entries[entry_position] = encode_tile_entry(TileEntry(target_tile_index, tile_entry.is_flipped_horizontally,
                                                              tile_entry.is_flipped_vertically, wanted_palette_slot))
        self.store_entries(metatile_id, entries)

    def shown_layer_pixels(self, metatile_id: int, layer_index: int) -> np.ndarray:
        entries = self.current_entries(metatile_id)
        layer_pixels = np.zeros((METATILE_SIDE_IN_PIXELS, METATILE_SIDE_IN_PIXELS), dtype=np.uint8)
        for quadrant_position in range(QUADRANTS_PER_LAYER):
            tile_entry = decode_tile_entry(entries[layer_index * QUADRANTS_PER_LAYER + quadrant_position])
            origin_x, origin_y = quadrant_origin_in_pixels(quadrant_position)
            layer_pixels[origin_y:origin_y + PIXELS_PER_TILE_SIDE, origin_x:origin_x + PIXELS_PER_TILE_SIDE] = \
                flipped_as_entry_shows_it(self.current_tile_pixels(tile_entry.tile_index), tile_entry)
        return layer_pixels

    def layer_palette_slots(self, metatile_id: int, layer_index: int) -> List[int]:
        entries = self.current_entries(metatile_id)
        palette_slots = [decode_tile_entry(entries[layer_index * QUADRANTS_PER_LAYER + quadrant_position]).palette_slot
                         for quadrant_position in range(QUADRANTS_PER_LAYER)]
        return palette_slots

    def replace_layer(self, metatile_id: int, layer_index: int, layer_pixels: np.ndarray,
                      palette_slots: Optional[Sequence[int]] = None, edit_shared_tiles: bool = False) -> None:
        layer_pixels_before_edit = self.shown_layer_pixels(metatile_id, layer_index)
        palette_slots_before_edit = self.layer_palette_slots(metatile_id, layer_index)
        for quadrant_position in range(QUADRANTS_PER_LAYER):
            origin_x, origin_y = quadrant_origin_in_pixels(quadrant_position)
            quadrant_window = (slice(origin_y, origin_y + PIXELS_PER_TILE_SIDE), slice(origin_x, origin_x + PIXELS_PER_TILE_SIDE))
            quadrant_pixels = layer_pixels[quadrant_window]
            wanted_palette_slot = None if palette_slots is None else palette_slots[quadrant_position]
            is_unchanged = (np.array_equal(quadrant_pixels, layer_pixels_before_edit[quadrant_window])
                            and wanted_palette_slot in (None, palette_slots_before_edit[quadrant_position]))
            if is_unchanged:
                continue
            self.replace_quadrant(metatile_id, layer_index * QUADRANTS_PER_LAYER + quadrant_position,
                                  np.ascontiguousarray(quadrant_pixels), wanted_palette_slot, edit_shared_tiles)


LAYER_INDEX_BY_NAME = {"bottom": 0, 0: 0, "0": 0, "top": 1, 1: 1, "1": 1}


def require_layer_index(layer_name_or_index) -> int:
    if layer_name_or_index not in LAYER_INDEX_BY_NAME:
        raise InvalidOverlay(f"layer must be bottom or top, not {layer_name_or_index!r}")
    layer_index = LAYER_INDEX_BY_NAME[layer_name_or_index]
    return layer_index


def require_existing_metatile(pair: TilesetPair, metatile_id) -> int:
    checked_metatile_id = int(metatile_id)
    if not metatile_exists(pair, checked_metatile_id):
        raise InvalidOverlay(f"no metatile {checked_metatile_id} (0x{checked_metatile_id:03X}) in this tileset pair")
    return checked_metatile_id


def write_layer_pixels(base_pair: TilesetPair, overlay: dict, metatile_id, layer, pixel_writes: Sequence[Sequence[int]],
                       edit_shared_tiles: bool = False) -> dict:
    overlay_edit = OverlayEdit(base_pair, overlay)
    checked_metatile_id = require_existing_metatile(overlay_edit.pair, metatile_id)
    layer_index = require_layer_index(layer)
    layer_pixels = overlay_edit.shown_layer_pixels(checked_metatile_id, layer_index)
    for pixel_write in pixel_writes:
        pixel_x, pixel_y, colour_index = (int(component) for component in pixel_write)
        require_pixel_write_in_range(pixel_x, pixel_y, colour_index)
        layer_pixels[pixel_y, pixel_x] = colour_index
    overlay_edit.replace_layer(checked_metatile_id, layer_index, layer_pixels, edit_shared_tiles=edit_shared_tiles)
    return overlay_edit.overlay


def require_pixel_write_in_range(pixel_x: int, pixel_y: int, colour_index: int) -> None:
    is_inside = 0 <= pixel_x < METATILE_SIDE_IN_PIXELS and 0 <= pixel_y < METATILE_SIDE_IN_PIXELS
    if not is_inside or not 0 <= colour_index < COLOURS_PER_PALETTE:
        raise InvalidOverlay(f"pixel ({pixel_x}, {pixel_y}) colour {colour_index} is outside 16x16 / 0-15")


def write_layer_rows(base_pair: TilesetPair, overlay: dict, metatile_id, layer, rows_of_hex_digits: Sequence[str],
                     edit_shared_tiles: bool = False) -> dict:
    is_square = len(rows_of_hex_digits) == METATILE_SIDE_IN_PIXELS and all(
        len(row) == METATILE_SIDE_IN_PIXELS for row in rows_of_hex_digits)
    if not is_square:
        raise InvalidOverlay("rows must be 16 strings of 16 hex digits (one colour index per pixel)")
    pixel_writes = [(pixel_x, pixel_y, int(hex_digit, 16))
                    for pixel_y, row in enumerate(rows_of_hex_digits) for pixel_x, hex_digit in enumerate(row)]
    updated_overlay = write_layer_pixels(base_pair, overlay, metatile_id, layer, pixel_writes, edit_shared_tiles)
    return updated_overlay


LAYER_TRANSFORM_FUNCTIONS = {
    "flip_horizontal": lambda grid: grid[:, ::-1],
    "flip_vertical": lambda grid: grid[::-1, :],
    "rotate_clockwise": lambda grid: np.rot90(grid, -1),
    "rotate_counterclockwise": lambda grid: np.rot90(grid, 1),
}


def transformed_layer(layer_pixels: np.ndarray, palette_slots: List[int], transform_name: str):
    if transform_name not in LAYER_TRANSFORM_FUNCTIONS:
        raise InvalidOverlay(f"transform must be one of {', '.join(LAYER_TRANSFORM_FUNCTIONS)}")
    transform_function = LAYER_TRANSFORM_FUNCTIONS[transform_name]
    transformed_pixels = np.ascontiguousarray(transform_function(layer_pixels))
    transformed_palette_slots = np.asarray(transform_function(np.array(palette_slots).reshape(2, 2))).ravel().tolist()
    return transformed_pixels, transformed_palette_slots


def transform_layers(base_pair: TilesetPair, overlay: dict, metatile_id, layers: Sequence, transform_name: str) -> dict:
    overlay_edit = OverlayEdit(base_pair, overlay)
    checked_metatile_id = require_existing_metatile(overlay_edit.pair, metatile_id)
    for layer in layers:
        layer_index = require_layer_index(layer)
        entries = overlay_edit.current_entries(checked_metatile_id)
        palette_slots = [decode_tile_entry(entries[layer_index * QUADRANTS_PER_LAYER + quadrant_position]).palette_slot
                         for quadrant_position in range(QUADRANTS_PER_LAYER)]
        new_pixels, new_palette_slots = transformed_layer(
            overlay_edit.shown_layer_pixels(checked_metatile_id, layer_index), palette_slots, transform_name)
        overlay_edit.replace_layer(checked_metatile_id, layer_index, new_pixels, new_palette_slots)
    return overlay_edit.overlay


def set_quadrant_palette(base_pair: TilesetPair, overlay: dict, metatile_id, layer, quadrant_position: int,
                         palette_slot: int) -> dict:
    overlay_edit = OverlayEdit(base_pair, overlay)
    checked_metatile_id = require_existing_metatile(overlay_edit.pair, metatile_id)
    if not 0 <= int(palette_slot) < NUM_PALS_TOTAL or not 0 <= int(quadrant_position) < QUADRANTS_PER_LAYER:
        raise InvalidOverlay(f"palette slot must be 0-{NUM_PALS_TOTAL - 1} and quadrant 0-3")
    entry_position = require_layer_index(layer) * QUADRANTS_PER_LAYER + int(quadrant_position)
    entries = overlay_edit.current_entries(checked_metatile_id)
    tile_entry = decode_tile_entry(entries[entry_position])
    entries[entry_position] = encode_tile_entry(TileEntry(tile_entry.tile_index, tile_entry.is_flipped_horizontally,
                                                          tile_entry.is_flipped_vertically, int(palette_slot)))
    overlay_edit.store_entries(checked_metatile_id, entries)
    return overlay_edit.overlay


def set_palette_colours(base_pair: TilesetPair, overlay: dict, palette_slot: int, colours_by_index: Dict) -> dict:
    if not 0 <= int(palette_slot) < NUM_PALS_TOTAL:
        raise InvalidOverlay(f"palette slot must be 0-{NUM_PALS_TOTAL - 1}")
    updated_overlay = copy.deepcopy(overlay)
    overlaid_pair = apply_overlay_to_tileset_pair(base_pair, overlay)
    palette_hexes = [colour_to_hex(colour) for colour in overlaid_pair.palettes[int(palette_slot)]]
    for colour_index_text, colour_hex in colours_by_index.items():
        colour_index = int(colour_index_text)
        if not 0 <= colour_index < COLOURS_PER_PALETTE:
            raise InvalidOverlay(f"colour index must be 0-{COLOURS_PER_PALETTE - 1}")
        palette_hexes[colour_index] = colour_to_hex(gba_colour(colour_from_hex(colour_hex)))
    updated_overlay["palettes"][str(int(palette_slot))] = palette_hexes
    return updated_overlay


def gba_colour(red_green_blue) -> tuple:
    five_bit_colour = tuple((int(channel) >> 3) << 3 for channel in red_green_blue)
    return five_bit_colour


def next_new_metatile_id(pair: TilesetPair) -> int:
    _primary_count, secondary_count = pair.metatile_count()
    if secondary_count >= MAXIMUM_SECONDARY_METATILE_COUNT:
        raise TileSlotsExhausted("the secondary tileset already has 512 metatiles")
    new_metatile_id = NUM_PRIMARY_METATILES + secondary_count
    return new_metatile_id


def blank_tile_index(pair: TilesetPair) -> int:
    for tile_index in range(NUM_PRIMARY_TILES):
        if not static_tile_pixels(pair, tile_index).any():
            return tile_index
    raise TileSlotsExhausted("the primary sheet has no blank tile to start a new metatile from")


def create_metatile(base_pair: TilesetPair, overlay: dict, copy_from_metatile_id=None, name: str = "",
                    class_letter: str = "g", has_collision: bool = False) -> Tuple[dict, int]:
    overlay_edit = OverlayEdit(base_pair, overlay)
    new_metatile_id = overlay_edit.next_metatile_id_including_pending()
    if copy_from_metatile_id is None:
        blank_entry = encode_tile_entry(TileEntry(blank_tile_index(overlay_edit.pair), False, False, 0))
        new_entries, new_attribute = [blank_entry] * (LAYER_COUNT * QUADRANTS_PER_LAYER), DEFAULT_NEW_METATILE_ATTRIBUTE
    else:
        source_metatile_id = require_existing_metatile(overlay_edit.pair, copy_from_metatile_id)
        new_entries = metatile_entries(overlay_edit.pair, source_metatile_id)
        new_attribute = overlay_edit.pair.attribute(source_metatile_id)
    overlay_edit.store_entries(new_metatile_id, new_entries)
    overlay_edit.overlay["metatile_attributes"][str(new_metatile_id)] = int(new_attribute)
    overlay_edit.overlay["custom_metatiles"][str(new_metatile_id)] = {
        "name": name or "tile %03X" % new_metatile_id, "class": class_letter or "g", "collision": bool(has_collision)}
    return overlay_edit.overlay, new_metatile_id


def set_metatile_properties(base_pair: TilesetPair, overlay: dict, metatile_id, properties: dict) -> dict:
    overlay_edit = OverlayEdit(base_pair, overlay)
    checked_metatile_id = require_existing_metatile(overlay_edit.pair, metatile_id)
    attribute_value = overlay_edit.pair.attribute(checked_metatile_id)
    if "behaviour" in properties:
        attribute_value = (attribute_value & ~BEHAVIOUR_MASK) | (int(properties["behaviour"]) & BEHAVIOUR_MASK)
    if "layer_type" in properties:
        attribute_value = (attribute_value & 0x0FFF) | ((int(properties["layer_type"]) & 0xF) << LAYER_TYPE_SHIFT)
    if attribute_value != overlay_edit.pair.attribute(checked_metatile_id):
        overlay_edit.overlay["metatile_attributes"][str(checked_metatile_id)] = int(attribute_value)
    custom_keys = ("name", "class", "collision", "in_palette")
    if any(custom_key in properties for custom_key in custom_keys):
        stored_custom = overlay_edit.overlay["custom_metatiles"].setdefault(
            str(checked_metatile_id), {"name": "tile %03X" % checked_metatile_id, "class": "g", "collision": False})
        stored_custom.update({custom_key: properties[custom_key] for custom_key in custom_keys if custom_key in properties})
    return overlay_edit.overlay


def remove_custom_metatile_properties(overlay: dict, metatile_id) -> dict:
    updated_overlay = copy.deepcopy(overlay)
    updated_overlay["custom_metatiles"].pop(str(int(metatile_id)), None)
    return updated_overlay


def rendered_metatile_rgb(pair: TilesetPair, metatile_id: int) -> np.ndarray:
    pair._cache.clear()
    metatile_rgb = pair.render_metatile(int(metatile_id))
    return metatile_rgb


def metatile_preview_image(pair: TilesetPair, metatile_id: int, scale: int = 8, tiled_three_by_three: bool = True):
    metatile_rgb = rendered_metatile_rgb(pair, metatile_id)
    single_image = Image.fromarray(metatile_rgb).resize((16 * scale, 16 * scale), Image.NEAREST)
    if not tiled_three_by_three:
        return single_image
    gap_in_pixels = 8
    tiled_rgb = np.tile(metatile_rgb, (3, 3, 1))
    tiled_image = Image.fromarray(tiled_rgb).resize((48 * scale // 2, 48 * scale // 2), Image.NEAREST)
    preview_image = Image.new("RGB", (single_image.width + gap_in_pixels + tiled_image.width,
                                      max(single_image.height, tiled_image.height)), (40, 40, 40))
    preview_image.paste(single_image, (0, 0))
    preview_image.paste(tiled_image, (single_image.width + gap_in_pixels, 0))
    return preview_image
