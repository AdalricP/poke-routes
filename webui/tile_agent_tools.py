from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

PREVIEW_SCALE = 8
SAMPLE_BLOB_HEIGHT_IN_CELLS = 9
SAMPLE_BLOB_WIDTH_IN_CELLS = 12
STRING_SCHEMA, INTEGER_SCHEMA, OBJECT_SCHEMA = {"type": "string"}, {"type": "integer"}, {"type": "object"}
OPERATION_HELP = (
    "operation.kind is one of: write_pixels {metatile_id, layer: bottom|top, pixels: [[x, y, colour 0-15], ...] or "
    "rows: 16 strings of 16 hex digits, edit_shared_tiles?}; transform {metatile_id, transform: flip_horizontal|"
    "flip_vertical|rotate_clockwise|rotate_counterclockwise, layers?}; quadrant_palette {metatile_id, layer, quadrant 0-3, "
    "palette_slot 0-12}; palette_colours {palette_slot, colours: {index: \"#rrggbb\"}}; create {copy_from?, name?, class?, "
    "collision?}; properties {metatile_id, properties: {name, class, collision, in_palette, behaviour, layer_type}}; "
    "forget_properties {metatile_id}; coalesce {inner, outer, class, name?, border_width?}."
)


class ArkTileContext:
    def __init__(self, refresh_generator_modules: Callable):
        refresh_generator_modules()
        from pokeroutes.ruletile import ArkTiler
        from pokeroutes.tileset_overlay import ARK_TILESET_PAIR_NAME, read_overlay_for_tileset_pair

        self.tiler = ArkTiler.load()
        self.base_pair = self.tiler.rom_pair()
        self.rom_class_table = json.loads((self.tiler.rom / "data/tilesets/metatile_classes_ark.json").read_text())
        self.overlay = read_overlay_for_tileset_pair(ARK_TILESET_PAIR_NAME)

    def overlaid_pair(self, overlay: dict = None):
        from pokeroutes.tileset_overlay import apply_overlay_to_tileset_pair

        pair_with_overlay = apply_overlay_to_tileset_pair(self.base_pair, overlay or self.overlay)
        return pair_with_overlay


def preview_path(output_directory: Path, file_stem: str) -> Path:
    output_directory.mkdir(parents=True, exist_ok=True)
    file_path = output_directory / f"{file_stem}.png"
    return file_path


def save_metatile_preview(pair, metatile_id: int, output_directory: Path) -> str:
    from pokeroutes.tile_editing import metatile_preview_image

    file_path = preview_path(output_directory, f"metatile_{metatile_id:03x}")
    metatile_preview_image(pair, metatile_id, PREVIEW_SCALE).save(file_path)
    return str(file_path)


def tile_tools(refresh_generator_modules: Callable, output_directory: Path) -> dict:
    def tile_sheet(arguments):
        context = ArkTileContext(refresh_generator_modules)
        which_sheet = arguments.get("which", "secondary")
        file_path = preview_path(output_directory, f"sheet_{which_sheet}")
        context.overlaid_pair().atlas(which_sheet, cols=16, scale=2).save(file_path)
        palette_tiles = {metatile_id_text: custom_properties for metatile_id_text, custom_properties
                         in context.overlay["custom_metatiles"].items() if custom_properties.get("in_palette", True)}
        sheet_result = {"image": str(file_path), "custom_tiles_in_paint_palette": palette_tiles,
                        "coalesced_classes": {class_letter: class_entry.get("name", class_letter)
                                              for class_letter, class_entry in context.overlay["classes"].items()}}
        return sheet_result

    def tile_read(arguments):
        from pokeroutes.tile_editing import describe_metatile

        context = ArkTileContext(refresh_generator_modules)
        pair = context.overlaid_pair()
        metatile_id = int(arguments["metatile_id"])
        description = describe_metatile(pair, context.overlay, metatile_id)
        description["preview"] = save_metatile_preview(pair, metatile_id, output_directory)
        return description

    def tile_edit(arguments):
        from pokeroutes.tile_operations import apply_tile_operation
        from pokeroutes.tileset_overlay import ARK_TILESET_PAIR_NAME, write_overlay_for_tileset_pair

        context = ArkTileContext(refresh_generator_modules)
        operation = arguments.get("operation") or {}
        updated_overlay, outcome = apply_tile_operation(context.base_pair, context.overlay, operation, context.rom_class_table)
        stored_overlay = write_overlay_for_tileset_pair(ARK_TILESET_PAIR_NAME, updated_overlay, context.overlay["revision"])
        pair = context.overlaid_pair(stored_overlay)
        shown_metatile_id = outcome.get("metatile_id", operation.get("metatile_id", operation.get("inner")))
        edit_result = {"saved_revision": stored_overlay["revision"], **outcome}
        if shown_metatile_id is not None:
            edit_result["preview"] = save_metatile_preview(pair, int(shown_metatile_id), output_directory)
        if operation.get("kind") == "coalesce":
            edit_result["sample"] = save_coalesced_sample(context, stored_overlay, operation["class"], output_directory)
        return edit_result

    def tile_coalesce(arguments):
        operation = {"kind": "coalesce", **{key: arguments[key] for key in ("inner", "outer", "class", "name", "border_width")
                                             if key in arguments}}
        coalesce_result = tile_edit({"operation": operation})
        return coalesce_result

    tools = {
        "tile_sheet": (tile_sheet, "Labelled sheet (PNG) of every PokeRoutes/Ark metatile with its hex id, including the "
                       "tile editor's overlay (tilesets/overrides/ark), plus the custom tiles and coalesced classes. Use it "
                       "to find metatile ids before tile_read / tile_edit.",
                       {"which": {**STRING_SCHEMA, "enum": ["primary", "secondary"]}}),
        "tile_read": (tile_read, "One Ark metatile: both layers' 8x8 tiles as hex rows (colour index per pixel, as shown, "
                      "flips applied), palette slots, how many metatiles share each 8x8 tile, behaviour, layer type, its "
                      "custom class, the colours of its palettes and a preview PNG (8x, plus tiled 3x3) to Read.",
                      {"metatile_id": INTEGER_SCHEMA}),
        "tile_edit": (tile_edit, "Change Ark tiles and SAVE the change to the overlay at once (never the ROM tree); the "
                      "routes, atlas and Porymap export pick it up on the next generate. Shared 8x8 tiles are copied "
                      "before drawing unless edit_shared_tiles. " + OPERATION_HELP,
                      {"operation": OBJECT_SCHEMA}),
        "tile_coalesce": (tile_coalesce, "Make the 47 edge and corner variants of a new terrain from a base tile (inner) "
                          "blending into a surrounding tile (outer, e.g. grass 0x000), register them as a blob class with "
                          "that letter (e.g. d for sand) so the Ark tiler and the paint palette draw it, and save. Returns "
                          "the mask table and a sample blob PNG to Read.",
                          {"inner": INTEGER_SCHEMA, "outer": INTEGER_SCHEMA, "class": STRING_SCHEMA, "name": STRING_SCHEMA,
                           "border_width": INTEGER_SCHEMA}),
    }
    return tools


def sample_class_grid(class_letter: str):
    import numpy as np

    sample_grid = np.full((SAMPLE_BLOB_HEIGHT_IN_CELLS, SAMPLE_BLOB_WIDTH_IN_CELLS), "g", dtype="<U1")
    sample_grid[1:7, 2:10] = class_letter
    sample_grid[3:5, 5:7] = "g"
    sample_grid[6:8, 7:9] = class_letter
    sample_grid[0, 4] = class_letter
    return sample_grid


def save_coalesced_sample(context: ArkTileContext, stored_overlay: dict, class_letter: str, output_directory: Path) -> str:
    import numpy as np
    from PIL import Image
    from pokeroutes.ruletile import ArkTiler

    tiler_with_overlay = ArkTiler(context.rom_class_table, context.tiler.rom, stored_overlay)
    pair = tiler_with_overlay.pair()
    metatile_grid = tiler_with_overlay.tile(sample_class_grid(class_letter)).metatile
    rows = [np.concatenate([pair.render_metatile(int(metatile_id)) for metatile_id in metatile_row], axis=1)
            for metatile_row in metatile_grid]
    sample_image = Image.fromarray(np.concatenate(rows, axis=0))
    file_path = preview_path(output_directory, f"coalesced_{class_letter}")
    sample_image.resize((sample_image.width * 3, sample_image.height * 3), Image.NEAREST).save(file_path)
    return str(file_path)
