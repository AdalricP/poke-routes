"""What the bench's Tiles tab can paint on one generated route, per tiler.

GET /api/paint-palette?id=ROUTE_ID ->
    {tiler, tileset,
     terrain: [{code, name, group, sample: [4 metatile ids, 2x2 row-major], snap_blocks, two_wide}],
     unavailable: [{code, name, reason}],
     objects: [{id, name, group, cells: [[metatile ids]], classes: [[class per cell]]}],
     tiles: {metatile id: class painted under that exact tile}}

Terrain is every class the route's tiler can draw (the Ark class table, or the classes a
learned model has enough corpus cells for), each with a 2x2 sample tiled by that tiler.
Objects are exact multi-cell stamps: the Ark class table's props, or whole General trees
and flowers for the learned tilesets. A blocked object cell is class "o".
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pokeroutes.classify import BLOCKED_CLASSES, CLASS_NAMES, LEDGE_CLASSES  # noqa: E402

OBJECT_CLASS = "o"
PROP_NAMES = {"tree_small": "small tree"}
SAMPLE_GRID_SIZE = 6
SAMPLE_CORNER = 2
GROUP_ORDER = ("ground", "water", "trees", "rock", "ledges")
LEARNED_OBJECTS = (
    {"id": "tree", "name": "tree", "cells": [[0x00E], [0x016]], "classes": [["f"], ["f"]]},
    {"id": "pine", "name": "pine", "cells": [[0x01E, 0x01F], [0x026, 0x027]], "classes": [["f", "f"], ["f", "f"]]},
    {"id": "round_tree", "name": "round tree", "cells": [[0x1D4, 0x1D5], [0x1DC, 0x1DD]],
     "classes": [["f", "f"], ["f", "f"]], "tileset": "general_fortree"},
    {"id": "flowers", "name": "flowers", "cells": [[0x004]], "classes": [["g"]]},
)
_palette_cache: dict = {}


def handle_get(h, query, srv) -> bool:
    route_id = query.get("id", [""])[0]
    if route_id not in srv._results:
        h._json({"error": "unknown or expired route id; generate again"}, 404)
        return True
    h._json(palette_for_route(srv, route_id))
    return True


def palette_for_route(srv, route_id: str) -> dict:
    route, _layout = srv._results[route_id]
    if route_id in srv._ark_ids:
        return cached_palette("ark", lambda: ark_palette())
    tileset_name = srv.route_tileset(route.spec)
    return cached_palette(tileset_name, lambda: learned_palette(srv, tileset_name))


def cached_palette(key: str, build) -> dict:
    if key not in _palette_cache:
        _palette_cache[key] = build()
    return _palette_cache[key]


def paintable_class_codes(srv, route_id: str) -> set:
    if route_id in srv._ark_ids:
        from pokeroutes.ruletile import FALLBACK

        return (set(ark_tiler().C) | set(FALLBACK)) - {OBJECT_CLASS}
    return set(CLASS_NAMES) - {OBJECT_CLASS}


def blocked_class_codes(srv, route_id: str) -> set:
    if route_id not in srv._ark_ids:
        return set(BLOCKED_CLASSES) | {OBJECT_CLASS}
    table_classes = ark_tiler().C
    blocked_letters = {letter for letter, entry in table_classes.items() if entry.get("collision")}
    return blocked_letters | set(BLOCKED_CLASSES) | {OBJECT_CLASS}


def ark_tiler():
    import shell_api

    return shell_api.ark_tiler()


# ------------------------------------------------------------------ Ark

def ark_palette() -> dict:
    from pokeroutes.ruletile import FALLBACK

    tiler = ark_tiler()
    terrain = [ark_terrain_entry(tiler, letter, entry) for letter, entry in tiler.C.items()]
    unavailable = [{"code": code, "name": class_name(code),
                    "reason": f"no Ark art; draws as {class_name(FALLBACK[code])}"}
                   for code in CLASS_NAMES if code not in tiler.C and code in FALLBACK and code != OBJECT_CLASS]
    objects = [ark_object_entry(prop_name, prop) for prop_name, prop in tiler.c.get("props", {}).items()
               if not is_replaced_by_fence_class(tiler, prop_name)]
    return {"tiler": "ark", "tileset": "ark", "terrain": sorted_terrain(terrain), "unavailable": unavailable,
            "objects": objects, "tiles": ark_exact_tile_classes(tiler)}


FENCE_AUTOTILE = "fence4"
OLD_FENCE_PROP_PREFIX = "fence_"


def is_replaced_by_fence_class(tiler, prop_name: str) -> bool:
    has_fence_class = any(entry.get("autotile") == FENCE_AUTOTILE for entry in tiler.C.values())
    is_old_fence_piece = has_fence_class and prop_name.startswith(OLD_FENCE_PROP_PREFIX)
    return is_old_fence_piece


def ark_terrain_entry(tiler, letter: str, entry: dict) -> dict:
    autotile_kind = entry.get("autotile")
    is_tree = autotile_kind == "tree2x2"
    sample_grid = sample_class_grid(letter, runs_vertically=autotile_kind == "column",
                                    is_ledge=autotile_kind in ("row", "column", FENCE_AUTOTILE))
    sample = crop_sample(tiler.tile(sample_grid).metatile)
    return {"code": letter, "name": entry.get("name") or class_name(letter),
            "group": ark_group(letter, entry), "sample": sample, "snap_blocks": is_tree, "two_wide": is_tree or letter == "w"}


def ark_group(letter: str, entry: dict) -> str:
    autotile_kind = entry.get("autotile")
    if autotile_kind in ("row", "column"):
        return "ledges"
    if autotile_kind == "tree2x2":
        return "trees"
    if autotile_kind == "waterfall" or letter == "w":
        return "water"
    if entry.get("collision"):
        return "rock"
    return "ground"


def ark_object_entry(prop_name: str, prop: dict) -> dict:
    metatile_rows = prop["cells"]
    blocked_rows = prop.get("blocked") or [[prop.get("collision", 0)] * len(row) for row in metatile_rows]
    class_rows = [[OBJECT_CLASS if blocked else "g" for blocked in row] for row in blocked_rows]
    return {"id": prop_name, "name": prop_display_name(prop_name), "group": "objects",
            "cells": metatile_rows, "classes": class_rows}


def prop_display_name(prop_name: str) -> str:
    if prop_name in PROP_NAMES:
        return PROP_NAMES[prop_name]
    direction_names = {"_h": " (across)", "_v": " (down)"}
    for suffix, words in direction_names.items():
        if prop_name.endswith(suffix):
            return prop_name[: -len(suffix)].replace("_", " ") + words
    numbered = re.fullmatch(r"([a-z_]+?)_?(\d+|[a-z])", prop_name)
    if numbered and numbered.group(1) in ("flowers", "mushroom", "scorch"):
        return f"{numbered.group(1).replace('_', ' ')} {variant_label(numbered.group(2))}"
    return prop_name.replace("_", " ")


def variant_label(variant: str) -> str:
    if variant.isdigit():
        return str(int(variant) + 1)
    return str(ord(variant) - ord("a") + 1)


def ark_exact_tile_classes(tiler) -> dict:
    class_of_metatile = tiler.c.get("class_of_metatile", {})
    prop_classes = {}
    for prop in tiler.c.get("props", {}).values():
        entry = ark_object_entry("", prop)
        for metatile_row, class_row in zip(entry["cells"], entry["classes"]):
            prop_classes.update({str(metatile_id): code for metatile_id, code in zip(metatile_row, class_row)})
    exact_classes = {}
    for metatile_id, code in class_of_metatile.items():
        exact_classes[metatile_id] = prop_classes.get(metatile_id) or ("g" if code == OBJECT_CLASS else code)
    return exact_classes


# ------------------------------------------------------------------ learned

def learned_palette(srv, tileset_name: str) -> dict:
    fast_tiler = srv.tiler(tileset_name)
    autotiler = fast_tiler.at
    terrain, unavailable = [], []
    for code in CLASS_NAMES:
        if code == OBJECT_CLASS:
            continue
        if not autotiler.known(code):
            unavailable.append({"code": code, "name": class_name(code),
                                "reason": f"the {tileset_name} maps have none to learn from; draws as "
                                          f"{class_name(autotiler.resolve(code))}"})
            continue
        terrain.append(learned_terrain_entry(fast_tiler, code))
    objects = [learned_object_entry(entry) for entry in LEARNED_OBJECTS if entry.get("tileset", tileset_name) == tileset_name]
    return {"tiler": "learned", "tileset": tileset_name, "terrain": sorted_terrain(terrain), "unavailable": unavailable,
            "objects": objects, "tiles": learned_exact_tile_classes(autotiler)}


def learned_terrain_entry(fast_tiler, code: str) -> dict:
    runs_vertically = ledge_runs_vertically(code)
    sample_grid = sample_class_grid(code, runs_vertically=runs_vertically, is_ledge=code in LEDGE_CLASSES)
    sample = crop_sample(fast_tiler.tile(sample_grid, seed=1).metatile)
    return {"code": code, "name": class_name(code), "group": learned_group(code), "sample": sample,
            "snap_blocks": False, "two_wide": code in "fw"}


def learned_group(code: str) -> str:
    if code in LEDGE_CLASSES:
        return "ledges"
    if code == "f":
        return "trees"
    if code in "wbha":
        return "water"
    if code in BLOCKED_CLASSES:
        return "rock"
    return "ground"


def learned_object_entry(entry: dict) -> dict:
    return {"id": entry["id"], "name": entry["name"], "group": "objects",
            "cells": entry["cells"], "classes": entry["classes"]}


def learned_exact_tile_classes(autotiler) -> dict:
    exact_classes = {}
    for metatile_id, code in autotiler.classes.items():
        is_blocked = autotiler.blocked.get(metatile_id, 0) > 0.5
        exact_classes[str(metatile_id)] = learned_tile_class(code, is_blocked)
    return exact_classes


def learned_tile_class(code: str, is_blocked: bool) -> str:
    if code != OBJECT_CLASS:
        return code
    return OBJECT_CLASS if is_blocked else "g"


# ------------------------------------------------------------------ shared

def class_name(code: str) -> str:
    return CLASS_NAMES.get(code, code)


def ledge_runs_vertically(code: str) -> bool:
    name = class_name(code)
    return code in LEDGE_CLASSES and ("east" in name or "west" in name)


def sample_class_grid(code: str, runs_vertically: bool, is_ledge: bool) -> np.ndarray:
    grid = np.full((SAMPLE_GRID_SIZE, SAMPLE_GRID_SIZE), "g", dtype="<U1")
    if is_ledge and runs_vertically:
        grid[:, SAMPLE_CORNER] = code
        return grid
    if is_ledge:
        grid[SAMPLE_CORNER, :] = code
        return grid
    if code == "u":
        grid[SAMPLE_CORNER, SAMPLE_CORNER] = code
        return grid
    if code == "n":
        grid[: SAMPLE_CORNER + 2, :] = "k"
        grid[SAMPLE_CORNER + 1, SAMPLE_CORNER] = code
        return grid
    if code == "a":
        grid[:, :] = "w"
        grid[SAMPLE_CORNER:SAMPLE_CORNER + 2, :] = code
        return grid
    grid[:, :] = code
    return grid


def crop_sample(metatile_grid: np.ndarray) -> list:
    block = metatile_grid[SAMPLE_CORNER:SAMPLE_CORNER + 2, SAMPLE_CORNER:SAMPLE_CORNER + 2]
    return [int(metatile_id) for metatile_id in block.ravel()]


def sorted_terrain(terrain: list) -> list:
    def order_key(entry):
        ledge_position = ledge_order(entry["name"]) if entry["group"] == "ledges" else 0
        return GROUP_ORDER.index(entry["group"]), ledge_position

    return sorted(terrain, key=order_key)


def ledge_order(name: str) -> int:
    for position, direction in enumerate(("south", "north", "east", "west")):
        if direction in name:
            return position
    return len("snew")
