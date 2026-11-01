"""Translate pinned pieces to the Arklarna tilesets (gTileset_ArkOutdoor + ArkWoodland).

The tiles-outdoor agent's data/tilesets/metatile_classes_ark.json lists props by name;
ids are looked up there at runtime because a tileset rebuild can shift them. Stock
General/Fortree pieces map by meaning (tiles agent, 2026-11-01):
  fence -> fence_h (left end / middle / right end), fence_v;  sign -> sign;  flowers -> flowers0;
  ark_special pieces (split_oak, fallen_log, scorch_*) -> same names.
Pieces with no Ark art yet (1x1 boulders, rock mounds, rock hollow, cave mouth, old tree,
gravel, pale grass) are dropped from ``fixed`` and handed back as class overrides so
ArkTiler paints them (rock 'r', tree 'f', grass 'g').
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Optional, Tuple

Cell = Tuple[int, int]
_ROOTS = [os.environ.get("ARK_ROM", ""), "/Users/aryan/Desktop/experiments/arklarna/rom-wt/tiles-outdoor",
          "/Users/aryan/Desktop/experiments/arklarna/rom"]
_cache: dict = {}


def classes_json() -> Optional[dict]:
    if "json" not in _cache:
        _cache["json"] = None
        for r in _ROOTS:
            p = Path(r) / "data/tilesets/metatile_classes_ark.json" if r else None
            if p and p.exists():
                _cache["json"] = json.loads(p.read_text())
                break
    return _cache["json"]


FENCE = {"fence_tl": ("fence_h", 0, 0), "fence_bl": ("fence_h", 0, 0), "fence_h": ("fence_h", 0, 1),
         "fence_tr": ("fence_h", 0, 2), "fence_br": ("fence_h", 0, 2), "fence_l": ("fence_v", 0, 0),
         "fence_r": ("fence_v", 0, 0)}
SAME = {"split_oak", "fallen_log", "scorch_a", "scorch_b", "scorch_c", "sign"}
AS_CLASS = {"boulder": "r", "boulder_grey": "r", "rock_mound": "r", "rock_hollow": "r", "cave_mouth": "r",
            "old_tree": "f", "gravel": "p", "tree_door": "f"}


def translate(pieces: Dict[Cell, tuple]) -> Tuple[Dict[Cell, int], Dict[Cell, str]]:
    c = classes_json()
    if c is None:
        raise FileNotFoundError("metatile_classes_ark.json not found (set ARK_ROM)")
    props = c["props"]
    fixed, cls = {}, {}
    for cell, (name, dy, dx) in pieces.items():
        if name in SAME and name in props:
            fixed[cell] = props[name]["cells"][dy][dx]
        elif name in FENCE and FENCE[name][0] in props:
            pn, py, px = FENCE[name]
            fixed[cell] = props[pn]["cells"][py][px]
        elif name == "flowers" and "flowers0" in props:
            fixed[cell] = props["flowers0"]["cells"][0][0]
        elif name.startswith("pale_"):
            cls[cell] = "g"
        else:
            cls[cell] = AS_CLASS.get(name, "r")
    return fixed, cls
