from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pokeroutes.classify import CLASS_NAMES  # noqa: E402
from pokeroutes.tile_editing import COMMON_BEHAVIOURS, LAYER_TYPES, TileSlotsExhausted  # noqa: E402
from pokeroutes.tile_operations import apply_tile_operation  # noqa: E402
from pokeroutes.tileset import NUM_PRIMARY_METATILES, NUM_PRIMARY_TILES  # noqa: E402
from pokeroutes.tileset_overlay import (ARK_TILESET_PAIR_NAME, MAXIMUM_SECONDARY_METATILE_COUNT,  # noqa: E402
                                        MAXIMUM_SECONDARY_TILE_COUNT, InvalidOverlay, OverlayRevisionConflict,
                                        colour_to_hex, read_overlay_for_tileset_pair, tile_pixels_to_hex,
                                        validate_overlay_against_tileset_pair, write_overlay_for_tileset_pair)

MAXIMUM_REQUEST_BODY_IN_BYTES = 8 * 1024 * 1024
ARK_TILESET_LABEL = "PokeRoutes"
TILE_EDITOR_PATH_PREFIX = "/api/tile-editor/"


def handle(request_handler, method: str, parsed_url, query, srv) -> bool:
    route_key = (method, parsed_url.path[len(TILE_EDITOR_PATH_PREFIX):])
    route_functions = {
        ("GET", "tilesets"): lambda: request_handler._json({"tilesets": openable_tilesets(srv)}),
        ("GET", "tileset"): lambda: send_tileset_model(request_handler, srv, query.get("id", [""])[0]),
        ("POST", "edit"): lambda: answer_edit_request(request_handler, srv),
        ("POST", "save"): lambda: answer_save_request(request_handler, srv),
    }
    if route_key not in route_functions:
        request_handler._json({"error": "not found"}, 404)
        return True
    try:
        route_functions[route_key]()
    except (InvalidOverlay, TileSlotsExhausted, ValueError) as rejected_request:
        request_handler._json({"error": str(rejected_request)}, 400)
    except OverlayRevisionConflict as conflict:
        request_handler._json({"error": str(conflict), "conflict": True}, 409)
    return True


def openable_tilesets(srv) -> list:
    import shell_api

    is_ark_available, _ark_error = shell_api.ark_available()
    ark_entry = [{"id": ARK_TILESET_PAIR_NAME, "label": ARK_TILESET_LABEL, "editable": True}] if is_ark_available else []
    learned_entries = [{"id": tileset_name, "label": learned_tileset_label(tileset_name), "editable": False}
                       for tileset_name in learned_tileset_names(srv)]
    tilesets = ark_entry + learned_entries
    return tilesets


def learned_tileset_names(srv) -> list:
    if not (srv.EMERALD / "data/tilesets").is_dir():
        return []
    model_names = sorted(model_path.stem for model_path in srv.MODELS.glob("*_*.json"))
    return model_names


def learned_tileset_label(tileset_name: str) -> str:
    primary_name, secondary_name = tileset_name.split("_", 1)
    label = f"Emerald {primary_name.title()} + {secondary_name.title()}"
    return label


def require_known_tileset(srv, tileset_id: str) -> dict:
    known_tilesets = {tileset["id"]: tileset for tileset in openable_tilesets(srv)}
    if tileset_id not in known_tilesets:
        raise InvalidOverlay(f"unknown tileset {tileset_id!r}")
    return known_tilesets[tileset_id]


def base_pair_and_class_table(srv, tileset_id: str):
    if tileset_id == ARK_TILESET_PAIR_NAME:
        import shell_api

        ark_tiler = shell_api.ark_tiler()
        rom_class_table = json.loads((ark_tiler.rom / "data/tilesets/metatile_classes_ark.json").read_text())
        return ark_tiler.rom_pair(), rom_class_table
    return srv.pair(tileset_id), {}


def send_tileset_model(request_handler, srv, tileset_id: str) -> None:
    tileset_entry = require_known_tileset(srv, tileset_id)
    base_pair, rom_class_table = base_pair_and_class_table(srv, tileset_id)
    overlay = read_overlay_for_tileset_pair(tileset_id) if tileset_entry["editable"] else None
    tileset_model = {**tileset_entry, "base": base_model(base_pair), "overlay": overlay,
                     "class_options": class_options(rom_class_table), "behaviours": named_pairs(COMMON_BEHAVIOURS),
                     "layer_types": named_pairs(LAYER_TYPES),
                     "limits": {"primary_tiles": NUM_PRIMARY_TILES, "secondary_tiles": MAXIMUM_SECONDARY_TILE_COUNT,
                                "primary_metatiles": NUM_PRIMARY_METATILES,
                                "secondary_metatiles": MAXIMUM_SECONDARY_METATILE_COUNT}}
    request_handler._json(tileset_model)


def base_model(base_pair) -> dict:
    model = {
        "primary_tiles": [tile_pixels_to_hex(tile_pixels) for tile_pixels in base_pair.primary.tiles],
        "secondary_tiles": [tile_pixels_to_hex(tile_pixels) for tile_pixels in base_pair.secondary.tiles],
        "palettes": [[colour_to_hex(colour) for colour in palette] for palette in base_pair.palettes],
        "primary_metatiles": base_pair.primary.metatiles.astype(int).tolist(),
        "secondary_metatiles": base_pair.secondary.metatiles.astype(int).tolist(),
        "primary_attributes": base_pair.primary.attributes.astype(int).tolist(),
        "secondary_attributes": base_pair.secondary.attributes.astype(int).tolist(),
    }
    return model


def class_options(rom_class_table: dict) -> list:
    table_classes = rom_class_table.get("classes", {})
    class_letters = list(CLASS_NAMES) + [letter for letter in table_classes if letter not in CLASS_NAMES]
    options = [{"code": letter, "name": (table_classes.get(letter) or {}).get("name") or CLASS_NAMES.get(letter, letter)}
               for letter in class_letters]
    return options


def named_pairs(value_name_pairs) -> list:
    named = [{"value": pair_value, "name": pair_name} for pair_value, pair_name in value_name_pairs]
    return named


def read_json_body(request_handler) -> dict:
    body_length_in_bytes = int(request_handler.headers.get("Content-Length") or 0)
    if body_length_in_bytes > MAXIMUM_REQUEST_BODY_IN_BYTES:
        raise InvalidOverlay("request too large")
    request_body = json.loads(request_handler.rfile.read(body_length_in_bytes) or b"{}")
    if not isinstance(request_body, dict):
        raise InvalidOverlay("the request must be a JSON object")
    return request_body


def require_editable_tileset(srv, tileset_id: str) -> None:
    if not require_known_tileset(srv, tileset_id)["editable"]:
        raise InvalidOverlay(f"{tileset_id} is read-only")


def answer_edit_request(request_handler, srv) -> None:
    request_body = read_json_body(request_handler)
    tileset_id = request_body.get("id", "")
    require_editable_tileset(srv, tileset_id)
    base_pair, rom_class_table = base_pair_and_class_table(srv, tileset_id)
    updated_overlay, operation_outcome = apply_tile_operation(base_pair, request_body.get("overlay") or {},
                                                              request_body.get("operation"), rom_class_table)
    request_handler._json({"overlay": updated_overlay, **operation_outcome})


def answer_save_request(request_handler, srv) -> None:
    request_body = read_json_body(request_handler)
    tileset_id = request_body.get("id", "")
    require_editable_tileset(srv, tileset_id)
    base_pair, _rom_class_table = base_pair_and_class_table(srv, tileset_id)
    checked_overlay = validate_overlay_against_tileset_pair(base_pair, request_body.get("overlay") or {})
    stored_overlay = write_overlay_for_tileset_pair(tileset_id, checked_overlay, request_body.get("revision"))
    request_handler._json({"overlay": stored_overlay, "revision": stored_overlay["revision"]})
