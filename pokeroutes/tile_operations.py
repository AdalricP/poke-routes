from __future__ import annotations

from typing import Sequence, Tuple

from .tile_coalesce import DEFAULT_BORDER_WIDTH_IN_PIXELS, coalesce_edge_variants
from .tile_editing import (create_metatile, remove_custom_metatile_properties, set_metatile_properties,
                           set_palette_colours, set_quadrant_palette, transform_layers, write_layer_pixels,
                           write_layer_rows)
from .tileset import TilesetPair
from .tileset_overlay import InvalidOverlay, normalise_overlay


def apply_write_pixels(base_pair, overlay, operation, _class_table) -> Tuple[dict, dict]:
    if "rows" in operation:
        updated_overlay = write_layer_rows(base_pair, overlay, operation["metatile_id"], operation.get("layer", "bottom"),
                                           operation["rows"], bool(operation.get("edit_shared_tiles")))
        return updated_overlay, {}
    updated_overlay = write_layer_pixels(base_pair, overlay, operation["metatile_id"], operation.get("layer", "bottom"),
                                         operation.get("pixels") or [], bool(operation.get("edit_shared_tiles")))
    return updated_overlay, {}


def apply_transform(base_pair, overlay, operation, _class_table) -> Tuple[dict, dict]:
    updated_overlay = transform_layers(base_pair, overlay, operation["metatile_id"],
                                       operation.get("layers") or ["bottom", "top"], operation["transform"])
    return updated_overlay, {}


def apply_quadrant_palette(base_pair, overlay, operation, _class_table) -> Tuple[dict, dict]:
    updated_overlay = set_quadrant_palette(base_pair, overlay, operation["metatile_id"], operation.get("layer", "bottom"),
                                           int(operation["quadrant"]), int(operation["palette_slot"]))
    return updated_overlay, {}


def apply_palette_colours(base_pair, overlay, operation, _class_table) -> Tuple[dict, dict]:
    updated_overlay = set_palette_colours(base_pair, overlay, int(operation["palette_slot"]), operation["colours"])
    return updated_overlay, {}


def apply_create(base_pair, overlay, operation, _class_table) -> Tuple[dict, dict]:
    updated_overlay, new_metatile_id = create_metatile(
        base_pair, overlay, operation.get("copy_from"), operation.get("name", ""), operation.get("class", "g"),
        bool(operation.get("collision")))
    return updated_overlay, {"metatile_id": new_metatile_id}


def apply_properties(base_pair, overlay, operation, _class_table) -> Tuple[dict, dict]:
    updated_overlay = set_metatile_properties(base_pair, overlay, operation["metatile_id"], operation.get("properties") or {})
    return updated_overlay, {}


def apply_forget_properties(_base_pair, overlay, operation, _class_table) -> Tuple[dict, dict]:
    updated_overlay = remove_custom_metatile_properties(overlay, operation["metatile_id"])
    return updated_overlay, {}


def apply_coalesce(base_pair, overlay, operation, class_table) -> Tuple[dict, dict]:
    rom_class_letters: Sequence[str] = list((class_table or {}).get("classes", {}))
    updated_overlay, metatile_id_by_mask = coalesce_edge_variants(
        base_pair, overlay, operation["inner"], operation["outer"], operation["class"], operation.get("name", ""),
        int(operation.get("border_width", DEFAULT_BORDER_WIDTH_IN_PIXELS)),
        (class_table or {}).get("mask_bits"), rom_class_letters)
    return updated_overlay, {"by_mask": metatile_id_by_mask}


TILE_OPERATIONS = {
    "write_pixels": apply_write_pixels,
    "transform": apply_transform,
    "quadrant_palette": apply_quadrant_palette,
    "palette_colours": apply_palette_colours,
    "create": apply_create,
    "properties": apply_properties,
    "forget_properties": apply_forget_properties,
    "coalesce": apply_coalesce,
}


def apply_tile_operation(base_pair: TilesetPair, overlay: dict, operation: dict, class_table: dict = None):
    if not isinstance(operation, dict) or operation.get("kind") not in TILE_OPERATIONS:
        raise InvalidOverlay(f"operation kind must be one of {', '.join(TILE_OPERATIONS)}")
    operation_function = TILE_OPERATIONS[operation["kind"]]
    try:
        updated_overlay, operation_outcome = operation_function(base_pair, normalise_overlay(overlay), operation, class_table)
    except KeyError as missing_field:
        raise InvalidOverlay(f"{operation['kind']}: missing or unknown {missing_field}") from missing_field
    return updated_overlay, operation_outcome
