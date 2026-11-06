"""Per-tileset looks the owner sets by hand in the Scene preview: which motion each tile plays
(and how fast), and the shade drawn under trees when a route leaves it on auto.

    GET  /api/tile-looks?tileset=ark|general_fortree...
         -> {tileset, version, motions, speeds, shades, tree_shade, overrides: {metatile: {motion, speed}},
             defaults: {metatile: {motion, speed}}, together: {metatile: [metatiles that move with it]}}
    POST /api/tile-looks {tileset, metatiles: [ids], motion, speed}   set (motion "default" clears)
    POST /api/tile-looks {tileset, tree_shade}                        the tileset's under-tree shade

Stored in webui/tile-looks.json (POKEROUTES_TILE_LOOKS overrides the path); local, not in git.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from pokeroutes.idle_motion import (MOTION_CHOICES, SPEED_CHOICES, TileMotion, is_known_motion, metatiles_moving_together,
                                    nearest_speed)
from pokeroutes.tree_shade import SHADE_CHOICES

HERE = Path(__file__).resolve().parent
TILE_LOOKS_PATH = Path(os.environ.get("POKEROUTES_TILE_LOOKS") or HERE / "tile-looks.json")
CLEARING_MOTION = "default"
LARGEST_METATILE_ID = 0x3FF
LARGEST_METATILE_COUNT_PER_CHANGE = 256

_lock = threading.Lock()
_state = {"version": int(time.time()), "looks": None}


def read_looks_file(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        stored_looks = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise RuntimeError(f"tile looks file {path} is unreadable: {error}") from error
    return stored_looks if isinstance(stored_looks, dict) else {}


def write_looks_file(path: Path, looks: dict) -> None:
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(looks, indent=1, sort_keys=True))
    temporary_path.replace(path)


def all_looks() -> dict:
    with _lock:
        if _state["looks"] is None:
            _state["looks"] = read_looks_file(TILE_LOOKS_PATH)
        return _state["looks"]


def version() -> int:
    return _state["version"]


def looks_of_tileset(tileset_key: str) -> dict:
    tileset_looks = all_looks().get(tileset_key, {})
    return tileset_looks


def motion_overrides(tileset_key: str) -> dict:
    stored_motions = looks_of_tileset(tileset_key).get("motion", {})
    overrides = {int(metatile_id): TileMotion(setting["motion"], nearest_speed(setting.get("speed", 1)))
                 for metatile_id, setting in stored_motions.items() if is_known_motion(setting.get("motion", ""))}
    return overrides


def tree_shade_of_tileset(tileset_key: str) -> str:
    tileset_shade = looks_of_tileset(tileset_key).get("tree_shade", "green")
    return tileset_shade


def changed_looks(tileset_key: str, change) -> dict:
    with _lock:
        looks = dict(_state["looks"] if _state["looks"] is not None else read_looks_file(TILE_LOOKS_PATH))
        tileset_looks = dict(looks.get(tileset_key, {}))
        change(tileset_looks)
        looks[tileset_key] = tileset_looks
        write_looks_file(TILE_LOOKS_PATH, looks)
        _state["looks"] = looks
        _state["version"] += 1
        return tileset_looks


def set_motion(tileset_key: str, metatile_ids: list, motion: str, speed: float, prop_cells_by_metatile) -> dict:
    moving_metatiles = {moving for metatile_id in metatile_ids for moving in metatiles_moving_together(metatile_id, prop_cells_by_metatile)}

    def apply_motion(tileset_looks: dict) -> None:
        stored_motions = dict(tileset_looks.get("motion", {}))
        for metatile_id in moving_metatiles:
            if motion == CLEARING_MOTION:
                stored_motions.pop(str(metatile_id), None)
                continue
            stored_motions[str(metatile_id)] = {"motion": motion, "speed": nearest_speed(speed)}
        tileset_looks["motion"] = stored_motions

    return changed_looks(tileset_key, apply_motion)


def set_tree_shade(tileset_key: str, tree_shade: str) -> dict:
    def apply_tree_shade(tileset_looks: dict) -> None:
        tileset_looks["tree_shade"] = tree_shade

    return changed_looks(tileset_key, apply_tree_shade)


# ------------------------------------------------------------------ http

def motion_json(tile_motion: TileMotion) -> dict:
    return {"motion": tile_motion.motion, "speed": tile_motion.speed}


def looks_response(tileset_key: str, srv) -> dict:
    defaults, prop_cells_by_metatile = srv.default_tile_motions(tileset_key)
    together = {str(metatile_id): metatiles_moving_together(metatile_id, prop_cells_by_metatile) for metatile_id in prop_cells_by_metatile}
    response = {"tileset": tileset_key, "version": version(), "motions": list(MOTION_CHOICES) + [CLEARING_MOTION],
                "speeds": list(SPEED_CHOICES), "shades": list(SHADE_CHOICES), "tree_shade": tree_shade_of_tileset(tileset_key),
                "overrides": {str(metatile_id): motion_json(tile_motion) for metatile_id, tile_motion in motion_overrides(tileset_key).items()},
                "defaults": {str(metatile_id): motion_json(tile_motion) for metatile_id, tile_motion in defaults.items()},
                "together": together}
    return response


def change_problem(body: dict, srv) -> str:
    if not srv.is_known_tile_looks_key(str(body.get("tileset", ""))):
        return "unknown tileset"
    if "tree_shade" in body:
        return "" if body["tree_shade"] in SHADE_CHOICES else f"tree_shade must be one of {', '.join(SHADE_CHOICES)}"
    metatile_ids = body.get("metatiles")
    if not isinstance(metatile_ids, list) or not metatile_ids or len(metatile_ids) > LARGEST_METATILE_COUNT_PER_CHANGE:
        return "expected metatiles: a non-empty list of metatile ids"
    if not all(isinstance(metatile_id, int) and 0 <= metatile_id <= LARGEST_METATILE_ID for metatile_id in metatile_ids):
        return "metatile ids are integers from 0 to 1023"
    if body.get("motion") not in MOTION_CHOICES + (CLEARING_MOTION,):
        return f"motion must be one of {', '.join(MOTION_CHOICES + (CLEARING_MOTION,))}"
    if not isinstance(body.get("speed", 1), (int, float)):
        return "speed must be a number"
    return ""


def apply_change(body: dict, srv) -> None:
    tileset_key = str(body["tileset"])
    if "tree_shade" in body:
        set_tree_shade(tileset_key, body["tree_shade"])
        return
    _defaults, prop_cells_by_metatile = srv.default_tile_motions(tileset_key)
    set_motion(tileset_key, body["metatiles"], body["motion"], float(body.get("speed", 1)), prop_cells_by_metatile)


def read_body(h) -> dict:
    length = int(h.headers.get("Content-Length") or 0)
    try:
        body = json.loads(h.rfile.read(length) or b"{}")
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def handle(h, method: str, u, query, srv) -> bool:
    if u.path != "/api/tile-looks":
        return False
    if method == "GET":
        tileset_key = query.get("tileset", [""])[0]
        if not srv.is_known_tile_looks_key(tileset_key):
            h._json({"error": "unknown tileset"}, 404)
            return True
        h._json(looks_response(tileset_key, srv))
        return True
    body = read_body(h)
    problem = change_problem(body, srv)
    if problem:
        h._json({"error": problem}, 400)
        return True
    apply_change(body, srv)
    srv.forget_atlases()
    h._json(looks_response(str(body["tileset"]), srv))
    return True
