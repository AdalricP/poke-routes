"""Web-UI-local copies of the small export helpers the UI shows (encounter tables,
route report, GBA size check), so the UI keeps working while pokeroutes/export.py
is being reworked. Snapshot of export.py as of 2026-11-01; only reads the route."""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Optional

import numpy as np

WALKABLE = set("gpt")
DIRS4 = ((-1, 0), (1, 0), (0, -1), (0, 1))
MAX_MAP_DATA_SIZE = 10240
BIOME_HABITATS = {
    "forest": {"forest", "deep_forest", "tall_grass", "meadow"},
    "meadow": {"meadow", "tall_grass", "flower_field", "farmland"},
    "riverland": {"riverbank", "lake", "marsh", "meadow"},
}
RARITY_WEIGHT = {"common": 40, "uncommon": 14, "rare": 4, "very_rare": 1}
LAND_RATES = [20, 20, 10, 10, 10, 10, 5, 5, 4, 4, 1, 1]
WATER_RATES = [60, 30, 5, 4, 1]


def gba_size_ok(w: int, h: int) -> bool:
    return (w + 15) * (h + 14) <= MAX_MAP_DATA_SIZE


def _dex(route) -> list:
    path = getattr(route.spec, "species_file", "")
    if not path or not Path(path).exists():
        return []
    return json.loads(Path(path).read_text())


def _specials(route):
    return [z for z in route.zones if z.kind == "special" and "area" in z.meta]


def encounters(route) -> dict:
    dex = _dex(route)
    if not dex:
        return {}
    habs = BIOME_HABITATS.get(route.spec.biome, set())
    table = []
    for sp in dex:
        if not sp.get("wild_level") or sp.get("rarity") not in RARITY_WEIGHT:
            continue
        if habs & set(sp.get("habitats", [])):
            table.append({"name": sp["name"], "types": sp.get("types", []), "levels": sp["wild_level"],
                          "weight": RARITY_WEIGHT[sp["rarity"]], "rarity": sp["rarity"]})
    areas = {}
    by_name = {sp["name"]: sp for sp in dex}
    for z in _specials(route):
        a = z.meta["area"]
        if a.get("species") in by_name:
            sp = by_name[a["species"]]
            lv = sp.get("wild_level") or [20, 30]
            areas[a["id"]] = [{"name": sp["name"], "types": sp.get("types", []), "levels": lv, "weight": 30,
                               "rarity": sp.get("rarity"), "shiny_boost": a.get("shiny_boost", 1)}] + \
                             [dict(t, weight=max(1, t["weight"] // 4)) for t in table[:6]]
    return {"route": table, "areas": areas}


def _slot_fill(cands: list, rates: list) -> list:
    if not cands:
        return []
    total_w = sum(c["weight"] for c in cands)
    total_r = sum(rates)
    got = [0.0] * len(cands)
    out = []
    for r in rates:
        i = max(range(len(cands)), key=lambda k: cands[k]["weight"] / total_w - got[k] / total_r)
        got[i] += r
        out.append(cands[i])
    return out


def emerald_encounters(route) -> Optional[dict]:
    dex = _dex(route)
    if not dex:
        return None
    lo, hi = getattr(route.spec, "levels", [2, 10])
    rng = random.Random(route.spec.seed)
    specials = {z.meta["area"].get("species") for z in _specials(route)}
    habs = BIOME_HABITATS.get(route.spec.biome, set())

    def cand(sp, weight):
        wl = sp.get("wild_level")
        if not wl or not sp.get("species") or wl[0] > hi:
            return None
        a, b = max(wl[0], lo), min(wl[1], hi)
        return {"species": sp["species"], "min_level": int(min(a, b)), "max_level": int(b), "weight": weight}

    land, water = [], []
    for sp in dex:
        w = RARITY_WEIGHT.get(sp.get("rarity"), 0)
        sh = set(sp.get("habitats", []))
        if sp.get("name") in specials:
            w = max(w, RARITY_WEIGHT["uncommon"])
        if not w:
            continue
        if (habs & sh or sp.get("name") in specials) and not ({"lake", "riverbank"} >= sh and "Water" in sp.get("types", [])):
            c = cand(sp, w)
            if c:
                land.append(c)
        if sh & {"lake", "riverbank", "marsh"} and "Water" in sp.get("types", []):
            c = cand(sp, w)
            if c:
                water.append(c)
    entry = {}
    for name, pool, rates, rate, cap in (("land_mons", land, LAND_RATES, 20, 7), ("water_mons", water, WATER_RATES, 4, 4)):
        if name == "water_mons" and not (route.cls == "w").any():
            continue
        rng.shuffle(pool)
        pool.sort(key=lambda c: (-c["weight"], c["min_level"]))
        rare = [c for c in pool if c["weight"] <= RARITY_WEIGHT["rare"]]
        main = [c for c in pool if c["weight"] > RARITY_WEIGHT["rare"]][: cap - 1] or pool[: cap - 1]
        slots = _slot_fill(main, rates)
        if rare and len(slots) > 2 and rare[0] not in main:
            slots[-1] = rare[0]
        if slots:
            entry[name] = {"encounter_rate": rate,
                           "mons": [{k: c[k] for k in ("min_level", "max_level", "species")} for c in slots]}
    return entry if "land_mons" in entry else None


def reachable(cls: np.ndarray, starts) -> np.ndarray:
    """Flood fill honouring one-way south ledges ('s')."""
    h, w = cls.shape
    seen = np.zeros((h, w), dtype=bool)
    stack = [tuple(s) for s in starts if cls[tuple(s)] in WALKABLE]
    for s in stack:
        seen[s] = True
    while stack:
        y, x = stack.pop()
        for dy, dx in DIRS4:
            ny, nx = y + dy, x + dx
            if not (0 <= ny < h and 0 <= nx < w) or seen[ny, nx]:
                continue
            c = cls[ny, nx]
            if c == "s":
                if dy == 1 and ny + 1 < h and cls[ny + 1, nx] in WALKABLE and not seen[ny + 1, nx]:
                    seen[ny + 1, nx] = True
                    stack.append((ny + 1, nx))
                continue
            if c in WALKABLE:
                seen[ny, nx] = True
                stack.append((ny, nx))
    return seen


def report(route) -> dict:
    from pokeroutes.gates import class_grid_with_route_gates_open

    walk = np.isin(route.cls, list(WALKABLE))
    starts = route.exits[0][1] if route.exits else []
    seen = reachable(class_grid_with_route_gates_open(route), starts)
    return {
        "size": [route.w, route.h],
        "walkable_cells": int(walk.sum()),
        "reachable_fraction": round(float((seen & walk).sum() / max(1, walk.sum())), 3),
        "classes": {k: int((route.cls == k).sum()) for k in "fgptwrs"},
        "zones": {k: sum(1 for z in route.zones if z.kind == k) for k in sorted({z.kind for z in route.zones})},
        "events": {k: sum(1 for e in route.events if e.kind == k) for k in sorted({e.kind for e in route.events})},
        "special_areas": [z.name for z in route.zones if z.kind == "special"],
        "gates": list(getattr(route, "gates", [])),
    }
