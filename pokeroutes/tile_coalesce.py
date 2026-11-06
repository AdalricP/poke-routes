from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .tile_editing import (QUADRANTS_PER_LAYER, rendered_metatile_rgb, OverlayEdit, TileEntry, blank_tile_index, decode_tile_entry,
                           encode_tile_entry, require_existing_metatile)
from .tileset import TilesetPair
from .tileset_overlay import PIXELS_PER_TILE_SIDE, InvalidOverlay

DEFAULT_MASK_BIT_ORDER = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
ORTHOGONAL_SIDES_OF_DIAGONAL = {"NE": ("N", "E"), "NW": ("N", "W"), "SE": ("S", "E"), "SW": ("S", "W")}
QUADRANT_NEIGHBOUR_DIRECTIONS = {0: ("N", "W", "NW"), 1: ("N", "E", "NE"), 2: ("S", "W", "SW"), 3: ("S", "E", "SE")}
DEFAULT_BORDER_WIDTH_IN_PIXELS = 3
OUTER_CORNER_ROUNDING_RADIUS_IN_PIXELS = 4
INNER_CORNER_ROUNDING_RADIUS_IN_PIXELS = 2
OUTLINE_DARKENING_FACTOR = 0.6
FULLY_SURROUNDED_MASK_DIRECTIONS = frozenset(DEFAULT_MASK_BIT_ORDER)


def neighbours_of_mask(mask: int, mask_bit_order: Sequence[str]) -> frozenset:
    same_class_directions = frozenset(direction for bit_position, direction in enumerate(mask_bit_order)
                                      if mask & (1 << bit_position))
    return same_class_directions


def normalised_neighbours(same_class_directions: frozenset) -> frozenset:
    kept_directions = frozenset(direction for direction in same_class_directions
                                if is_direction_kept_after_normalising(direction, same_class_directions))
    return kept_directions


def is_direction_kept_after_normalising(direction: str, same_class_directions: frozenset) -> bool:
    if direction not in ORTHOGONAL_SIDES_OF_DIAGONAL:
        return True
    both_sides_present = all(side in same_class_directions for side in ORTHOGONAL_SIDES_OF_DIAGONAL[direction])
    return both_sides_present


def mask_of_neighbours(same_class_directions: frozenset, mask_bit_order: Sequence[str]) -> int:
    mask = sum(1 << bit_position for bit_position, direction in enumerate(mask_bit_order)
               if direction in same_class_directions)
    return mask


def every_normalised_neighbour_set(mask_bit_order: Sequence[str]) -> List[frozenset]:
    distinct_sets = {normalised_neighbours(neighbours_of_mask(mask, mask_bit_order)) for mask in range(256)}
    ordered_sets = sorted(distinct_sets, key=lambda neighbour_set: mask_of_neighbours(neighbour_set, mask_bit_order))
    return ordered_sets


def quadrant_shape_kind(quadrant_position: int, same_class_directions: frozenset) -> str:
    vertical_direction, horizontal_direction, diagonal_direction = QUADRANT_NEIGHBOUR_DIRECTIONS[quadrant_position]
    has_vertical = vertical_direction in same_class_directions
    has_horizontal = horizontal_direction in same_class_directions
    has_diagonal = diagonal_direction in same_class_directions
    if has_vertical and has_horizontal:
        return "interior" if has_diagonal else "inner_corner"
    if has_horizontal:
        return "vertical_edge"
    if has_vertical:
        return "horizontal_edge"
    return "outer_corner"


def is_inside_shape(shape_kind: str, distance_from_side_x: int, distance_from_side_y: int, border_width: int) -> bool:
    rounding_radius = OUTER_CORNER_ROUNDING_RADIUS_IN_PIXELS
    shape_tests = {
        "interior": lambda: True,
        "vertical_edge": lambda: distance_from_side_y >= border_width,
        "horizontal_edge": lambda: distance_from_side_x >= border_width,
        "inner_corner": lambda: not is_inside_rounded_outer_corner(border_width - 1 - distance_from_side_x,
                                                                    border_width - 1 - distance_from_side_y,
                                                                    INNER_CORNER_ROUNDING_RADIUS_IN_PIXELS),
        "outer_corner": lambda: is_inside_rounded_outer_corner(distance_from_side_x - border_width,
                                                               distance_from_side_y - border_width, rounding_radius),
    }
    is_inside = shape_tests[shape_kind]()
    return is_inside


def is_inside_rounded_outer_corner(offset_x: int, offset_y: int, rounding_radius: int) -> bool:
    if offset_x < 0 or offset_y < 0:
        return False
    if offset_x >= rounding_radius or offset_y >= rounding_radius:
        return True
    distance_squared = (rounding_radius - offset_x - 0.5) ** 2 + (rounding_radius - offset_y - 0.5) ** 2
    is_inside = distance_squared <= rounding_radius ** 2
    return is_inside


def distances_from_outer_sides(quadrant_position: int, local_x: int, local_y: int) -> Tuple[int, int]:
    distance_x = local_x if quadrant_position % 2 == 0 else PIXELS_PER_TILE_SIDE - 1 - local_x
    distance_y = local_y if quadrant_position // 2 == 0 else PIXELS_PER_TILE_SIDE - 1 - local_y
    return distance_x, distance_y


def quadrant_pixel_roles(quadrant_position: int, shape_kind: str, border_width: int) -> np.ndarray:
    roles = np.empty((PIXELS_PER_TILE_SIDE, PIXELS_PER_TILE_SIDE), dtype="<U7")
    for local_y in range(PIXELS_PER_TILE_SIDE):
        for local_x in range(PIXELS_PER_TILE_SIDE):
            roles[local_y, local_x] = pixel_role(quadrant_position, shape_kind, border_width, local_x, local_y)
    return roles


def pixel_role(quadrant_position: int, shape_kind: str, border_width: int, local_x: int, local_y: int) -> str:
    distance_x, distance_y = distances_from_outer_sides(quadrant_position, local_x, local_y)
    if not is_inside_shape(shape_kind, distance_x, distance_y, border_width):
        return "outside"
    neighbour_offsets = ((1, 0), (-1, 0), (0, 1), (0, -1))
    touches_outside = any(not is_inside_shape(shape_kind, distance_x + offset_x, distance_y + offset_y, border_width)
                          for offset_x, offset_y in neighbour_offsets)
    role = "outline" if touches_outside else "inside"
    return role


def quantised_to_palette(rgb_pixels: np.ndarray, palette_colours: np.ndarray) -> Tuple[np.ndarray, int]:
    squared_distances = ((rgb_pixels[:, :, np.newaxis, :].astype(int)
                          - palette_colours[np.newaxis, np.newaxis, :, :].astype(int)) ** 2).sum(axis=3)
    colour_indices = squared_distances.argmin(axis=2).astype(np.uint8)
    total_error = int(squared_distances.min(axis=2).sum())
    return colour_indices, total_error


class EdgeVariantPainter:
    def __init__(self, overlay_edit: OverlayEdit, inner_metatile_id: int, outer_metatile_id: int, border_width: int):
        self.overlay_edit = overlay_edit
        self.pair = overlay_edit.pair
        self.inner_entries = overlay_edit.current_entries(inner_metatile_id)
        self.outer_entries = overlay_edit.current_entries(outer_metatile_id)
        self.inner_rgb = rendered_metatile_rgb(self.pair, inner_metatile_id)
        self.outer_rgb = rendered_metatile_rgb(self.pair, outer_metatile_id)
        self.border_width = border_width
        self.tile_entry_by_quadrant_and_kind: Dict[Tuple[int, str], int] = {}

    def quadrant_slice(self, quadrant_position: int) -> Tuple[slice, slice]:
        origin_x = (quadrant_position % 2) * PIXELS_PER_TILE_SIDE
        origin_y = (quadrant_position // 2) * PIXELS_PER_TILE_SIDE
        rows_and_columns = (slice(origin_y, origin_y + PIXELS_PER_TILE_SIDE), slice(origin_x, origin_x + PIXELS_PER_TILE_SIDE))
        return rows_and_columns

    def candidate_palette_slots(self, quadrant_position: int) -> List[int]:
        entry_positions = (quadrant_position, quadrant_position + QUADRANTS_PER_LAYER)
        candidate_slots = sorted({decode_tile_entry(entries[entry_position]).palette_slot
                                  for entries in (self.inner_entries, self.outer_entries)
                                  for entry_position in entry_positions})
        return candidate_slots

    def composed_quadrant_rgb(self, quadrant_position: int, shape_kind: str) -> np.ndarray:
        inner_quadrant_rgb = self.inner_rgb[self.quadrant_slice(quadrant_position)].astype(float)
        outer_quadrant_rgb = self.outer_rgb[self.quadrant_slice(quadrant_position)].astype(float)
        roles = quadrant_pixel_roles(quadrant_position, shape_kind, self.border_width)[:, :, np.newaxis]
        outline_rgb = inner_quadrant_rgb.mean(axis=(0, 1)) * OUTLINE_DARKENING_FACTOR
        composed_rgb = np.where(roles == "outside", outer_quadrant_rgb, inner_quadrant_rgb)
        composed_rgb = np.where(roles == "outline", outline_rgb, composed_rgb)
        return composed_rgb

    def painted_quadrant(self, quadrant_position: int, shape_kind: str) -> Tuple[np.ndarray, int]:
        composed_rgb = self.composed_quadrant_rgb(quadrant_position, shape_kind)
        quantised_by_slot = {palette_slot: quantised_to_palette(composed_rgb, self.pair.palettes[palette_slot])
                             for palette_slot in self.candidate_palette_slots(quadrant_position)}
        best_palette_slot = min(quantised_by_slot, key=lambda palette_slot: quantised_by_slot[palette_slot][1])
        best_pixels = quantised_by_slot[best_palette_slot][0]
        return best_pixels, best_palette_slot

    def bottom_entry_for(self, quadrant_position: int, shape_kind: str, is_primary_target: bool) -> int:
        if shape_kind == "interior":
            return self.inner_entries[quadrant_position]
        cache_key = (quadrant_position, shape_kind)
        if cache_key in self.tile_entry_by_quadrant_and_kind:
            return self.tile_entry_by_quadrant_and_kind[cache_key]
        quadrant_pixels, palette_slot = self.painted_quadrant(quadrant_position, shape_kind)
        new_tile_index = self.overlay_edit.allocator.next_tile_index(is_primary_target)
        self.overlay_edit.store_tile(new_tile_index, quadrant_pixels)
        packed_entry = encode_tile_entry(TileEntry(new_tile_index, False, False, palette_slot))
        self.tile_entry_by_quadrant_and_kind[cache_key] = packed_entry
        return packed_entry

    def top_entry_for(self, quadrant_position: int, shape_kind: str, blank_top_entry: int) -> int:
        top_entry = self.inner_entries[quadrant_position + QUADRANTS_PER_LAYER] if shape_kind == "interior" else blank_top_entry
        return top_entry


def coalesce_edge_variants(base_pair: TilesetPair, overlay: dict, inner_metatile_id, outer_metatile_id,
                           class_letter: str, class_name: str = "", border_width: int = DEFAULT_BORDER_WIDTH_IN_PIXELS,
                           mask_bit_order: Optional[Sequence[str]] = None, existing_class_letters: Sequence[str] = ()):
    require_new_class_letter(class_letter, existing_class_letters, overlay)
    overlay_edit = OverlayEdit(base_pair, overlay)
    inner_id = require_existing_metatile(overlay_edit.pair, inner_metatile_id)
    outer_id = require_existing_metatile(overlay_edit.pair, outer_metatile_id)
    painter = EdgeVariantPainter(overlay_edit, inner_id, outer_id, int(border_width))
    bit_order = tuple(mask_bit_order or DEFAULT_MASK_BIT_ORDER)
    blank_top_entry = encode_tile_entry(TileEntry(blank_tile_index(overlay_edit.pair), False, False, 0))
    inner_attribute = overlay_edit.pair.attribute(inner_id)
    metatile_id_by_mask: Dict[str, int] = {}
    for same_class_directions in every_normalised_neighbour_set(bit_order):
        mask_key = str(mask_of_neighbours(same_class_directions, bit_order))
        metatile_id_by_mask[mask_key] = metatile_for_neighbour_set(
            overlay_edit, painter, same_class_directions, inner_id, blank_top_entry, inner_attribute, class_letter)
    overlay_edit.overlay["classes"][class_letter] = {
        "name": class_name or class_letter, "autotile": "blob47", "by_mask": metatile_id_by_mask,
        "joins": [class_letter], "collision": 0, "coalesced_from": {"inner": inner_id, "outer": outer_id}}
    return overlay_edit.overlay, metatile_id_by_mask


def require_new_class_letter(class_letter: str, existing_class_letters: Sequence[str], overlay: dict) -> None:
    is_one_letter = isinstance(class_letter, str) and len(class_letter) == 1 and class_letter.isalpha()
    if not is_one_letter:
        raise InvalidOverlay(f"class must be one letter, not {class_letter!r}")
    is_taken_by_rom_table = class_letter in existing_class_letters and class_letter not in overlay["classes"]
    if is_taken_by_rom_table:
        raise InvalidOverlay(f"class {class_letter!r} is already drawn by the tileset; pick another letter")


def metatile_for_neighbour_set(overlay_edit: OverlayEdit, painter: EdgeVariantPainter, same_class_directions: frozenset,
                               inner_id: int, blank_top_entry: int, inner_attribute: int, class_letter: str) -> int:
    if same_class_directions == FULLY_SURROUNDED_MASK_DIRECTIONS:
        return inner_id
    new_metatile_id = overlay_edit.next_metatile_id_including_pending()
    shape_kinds = [quadrant_shape_kind(quadrant_position, same_class_directions)
                   for quadrant_position in range(QUADRANTS_PER_LAYER)]
    bottom_entries = [painter.bottom_entry_for(quadrant_position, shape_kind, False)
                      for quadrant_position, shape_kind in enumerate(shape_kinds)]
    top_entries = [painter.top_entry_for(quadrant_position, shape_kind, blank_top_entry)
                   for quadrant_position, shape_kind in enumerate(shape_kinds)]
    overlay_edit.store_entries(new_metatile_id, bottom_entries + top_entries)
    overlay_edit.overlay["metatile_attributes"][str(new_metatile_id)] = int(inner_attribute)
    overlay_edit.overlay["custom_metatiles"][str(new_metatile_id)] = {
        "name": f"{class_letter} edge {new_metatile_id:03X}", "class": class_letter, "collision": False, "in_palette": False}
    return new_metatile_id
