"""Per-route spec export: one poke-routes YAML per route rectangle, with the
region's view of that rectangle in a ``region`` sub-dict (role, elevation tiers,
water, rivers, waterfalls, coast, biome mix/gradient, critical path, landmarks)."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Dict, List

import numpy as np
import yaml

from .world import BIOME_NAMES, CELL, SPEC_BIOME, World

ROOT = Path(__file__).resolve().parents[2]
SPECIES_FILE_REL = "../../../../arklarna/design/pokedex.json"  # from region_out/<seed>/routes/

MEANDER = {"straight": 0.45, "turn": 0.55, "crossroads": 0.5, "dead_end": 0.65, "coastal": 0.5,
           "mountain_pass": 0.35, "river_crossing": 0.5, "loop": 0.6, "sea": 0.3}
TALL = {"meadow": 0.4, "forest": 0.3, "riverland": 0.35, "beach": 0.2, "desert": 0.1, "mountain": 0.15, "volcanic": 0.08}
MUSIC = {"forest": "MUS_ROUTE104", "meadow": "MUS_ROUTE101", "riverland": "MUS_ROUTE119", "beach": "MUS_ROUTE110",
         "desert": "MUS_ROUTE111", "mountain": "MUS_ROUTE113", "volcanic": "MUS_ROUTE113"}
SIDES = ("north", "east", "south", "west")


def _rows(a: np.ndarray) -> List[str]:
    return ["".join(str(v) for v in row) for row in a]


def _at(side: str, local_cell: int, w_mt: int, h_mt: int, width: int) -> float:
    """'at' such that pokeroutes' exit_cells() puts the exit centred on this cell."""
    c = local_cell * CELL + CELL // 2
    span = (w_mt if side in ("north", "south") else h_mt) - 1 - 2 * width
    return round(min(1.0, max(0.0, (c - width) / max(1, span))), 4)


def _gradient(codes: np.ndarray, path: List[List[int]], rect) -> dict:
    """Dominant biome near the start of the path vs near its end."""
    x0, y0, x1, y1 = rect
    if len(path) < 4:
        return {}
    third = max(1, len(path) // 3)

    def dom(cells):
        vals = []
        for x, y in cells:
            sub = codes[max(y0, y - 2):min(y1, y + 3), max(x0, x - 2):min(x1, x + 3)].ravel()
            vals.extend(v for v in sub if v not in "~=o")
        return Counter(vals).most_common(1)[0][0] if vals else None

    a, b = dom(path[:third]), dom(path[-third:])
    if not a or not b or a == b:
        return {}
    (sx, sy), (ex, ey) = path[0], path[-1]
    dx, dy = ex - sx, ey - sy
    axis = ("west->east" if dx > 0 else "east->west") if abs(dx) >= abs(dy) else ("north->south" if dy > 0 else "south->north")
    return {"from": BIOME_NAMES[a], "to": BIOME_NAMES[b], "from_spec": SPEC_BIOME[a], "to_spec": SPEC_BIOME[b],
            "axis": axis, "along": "critical path"}


def route_region(w: World, r: dict, idx: int) -> dict:
    x0, y0, x1, y1 = r["rect"]
    sub = (slice(y0, y1), slice(x0, x1))
    W_ = x1 - x0
    tiers = w.tiers[sub].copy()
    land = w.land[sub]
    biome = w.biome[sub]
    # finer height: 0-9 over the whole region's land range, for smooth ramps
    hl = w.height[sub]
    q = float(w.height[w.land].max())
    fine = np.where(land, np.clip((hl / q * 9.99).astype(int), 0, 9), 0)
    water = np.full(tiers.shape, ".", dtype="<U1")
    water[~land] = "~"
    water[~land & (hl < -0.30)] = "="
    water[w.lake[sub]] = "o"
    water[w.river[sub]] = "r"
    # rivers: chains of river cells in downstream order, local coords
    Wg = w.W
    riv_cells = set(zip(*np.nonzero(w.river[sub])))
    starts = []
    has_up = set()
    for (ly, lx) in riv_cells:
        gi = (ly + y0) * Wg + (lx + x0)
        rv = int(w.recv[gi])
        if rv >= 0:
            ry, rx = divmod(rv, Wg)
            if (ry - y0, rx - x0) in riv_cells:
                has_up.add((ry - y0, rx - x0))
    starts = [c for c in riv_cells if c not in has_up]
    rivers, falls = [], []
    seen = set()
    for st in sorted(starts):
        chain = []
        c = st
        while c in riv_cells and c not in seen:
            seen.add(c)
            chain.append(c)
            gi = (c[0] + y0) * Wg + (c[1] + x0)
            rv = int(w.recv[gi])
            if rv < 0:
                break
            ry, rx = divmod(rv, Wg)
            nxt = (ry - y0, rx - x0)
            t0 = int(w.tiers[c[0] + y0, c[1] + x0])
            t1 = int(w.tiers[ry, rx]) if w.land[ry, rx] else 0
            if t1 < t0 and 0 <= nxt[0] < y1 - y0 and 0 <= nxt[1] < W_:
                falls.append({"x": int(c[1]), "y": int(c[0]), "to": [int(nxt[1]), int(nxt[0])], "from_tier": t0, "to_tier": t1})
            if not (0 <= nxt[0] < y1 - y0 and 0 <= nxt[1] < W_):
                # leaves the rectangle: note the side
                break
            c = nxt
        if len(chain) >= 2:
            fl = float(w.flow[chain[-1][0] + y0, chain[-1][1] + x0])
            rivers.append({"cells": [[int(x), int(y)] for y, x in chain], "flow": round(fl, 1),
                           "width": 1 if fl < 120 else (2 if fl < 400 else 3)})
    # coast: which sides have sea along them, and how much of the map is sea
    sea = ~land
    coast_sides = []
    for side, edge in (("north", sea[0, :]), ("south", sea[-1, :]), ("west", sea[:, 0]), ("east", sea[:, -1])):
        if edge.mean() > 0.3:
            coast_sides.append(side)
    land_codes = biome[np.isin(biome, list("~=o"), invert=True)]
    mix = Counter(land_codes.ravel().tolist()) if land_codes.size else Counter(biome.ravel().tolist())
    tot = sum(mix.values())
    mix_named = {BIOME_NAMES[k]: round(v / tot, 3) for k, v in mix.most_common() if v / tot >= 0.03}
    path = [[x - x0, y - y0] for x, y in r["path"]]
    lms = []
    for lm in w.landmarks:
        if x0 <= lm.x < x1 and y0 <= lm.y < y1:
            lms.append({"id": lm.id, "name": lm.name, "kind": lm.kind, "legendary": lm.legendary,
                        "x": lm.x - x0, "y": lm.y - y0, "story": lm.story})
    goal = None
    if r.get("goal"):
        gx, gy = r["goal"]
        goal = {"x": gx - x0, "y": gy - y0, "landmark": r.get("goal_landmark"),
                "side": _far_side(gx - x0, gy - y0, W_, y1 - y0)}
    return {
        "version": 1,
        "seed": w.seed,
        "index": idx,
        "cell": CELL,
        "origin": [x0, y0],
        "rect": r["rect"],
        "role": r["role"],
        "shape": r["shape"],
        "tags": r["tags"],
        "mode": r["mode"],
        "link": r["link"],
        "elevation": {
            "levels": 5,
            "note": "tiers 0 lowland .. 4 peaks, one char per region cell (cell x cell metatiles); "
                    "a step between neighbouring cells is a cliff, ledge or stairs band",
            "tiers": _rows(tiers),
            "fine": _rows(fine),
        },
        "water": {"legend": {".": "land", "~": "sea", "=": "deep sea", "o": "lake", "r": "river"}, "grid": _rows(water)},
        "rivers": rivers,
        "waterfalls": falls,
        "coast": {"sides": coast_sides, "sea_fraction": round(float(sea.mean()), 3)},
        "biome": {"dominant": BIOME_NAMES.get(r.get("biome_code"), "meadow"), "mix": mix_named,
                  "grid": _rows(biome), "legend": {k: v for k, v in BIOME_NAMES.items()},
                  "gradient": _gradient(w.biome, r["path"], r["rect"])},
        "path": path,
        "goal": goal,
        "landmarks": lms,
    }


def _far_side(x, y, W_, H_) -> str:
    d = {"west": x, "east": W_ - 1 - x, "north": y, "south": H_ - 1 - y}
    return min(d, key=d.get)


def route_spec(w: World, r: dict, idx: int) -> dict:
    x0, y0, x1, y1 = r["rect"]
    wm, hm = (x1 - x0) * CELL, (y1 - y0) * CELL
    reg = route_region(w, r, idx)
    code = r.get("biome_code") or "m"
    biome = SPEC_BIOME.get(code, "forest")
    if r["mode"] == "sea":
        biome = "beach"
    # exits: the through-path of the route's own link first (generator joins exits 0 and 1)
    own = [e for e in r["exits"] if e["link"] == r["link"]]
    other = [e for e in r["exits"] if e["link"] != r["link"]]
    exits = []
    for e in own + other:
        side = e["side"]
        lc = e["cell"][1] - y0 if side in ("east", "west") else e["cell"][0] - x0
        exits.append({"side": side, "at": _at(side, lc, wm, hm, 3), "width": 3, "to": e["to_map"] or ""})
    area = wm * hm
    role = r["role"]
    weather = "WEATHER_SUNNY"
    mix = reg["biome"]["mix"]
    if mix.get("ash badlands", 0) + mix.get("volcanic", 0) > 0.3:
        weather = "WEATHER_VOLCANIC_ASH"
    elif biome == "desert":
        weather = "WEATHER_SANDSTORM"
    elif float(np.mean(w.overrides.get("rain", np.zeros_like(w.height))[y0:y1, x0:x1])) > 0.2:
        weather = "WEATHER_RAIN"
    elif mix.get("deep forest", 0) > 0.4:
        weather = "WEATHER_SHADE"
    spec = {
        "name": r["name"],
        "id": r["id"],
        "size": [wm, hm],
        "seed": int(w.seed) * 1000 + idx,
        "biome": biome,
        "exits": exits,
        "meander": MEANDER.get(role, 0.5),
        "branches": 2 + int(area > 4000) + int(role == "crossroads"),
        "loops": 1 + int(role == "loop"),
        "dead_ends": 1 + int(area > 4500),
        "clearings": 2 + int(area > 4000),
        "lakes": 1 if (reg["water"]["grid"] and any("o" in row for row in reg["water"]["grid"])) else 0,
        "tall_grass": TALL.get(biome, 0.3),
        "hidden_paths": 2,
        "hidden_items": 3 + int(area > 4500),
        "special_areas": ["auto", "auto"] + (["auto"] if area > 5000 else []),
        "species_file": SPECIES_FILE_REL,
        "music": MUSIC.get(biome, "MUS_ROUTE104"),
        "weather": weather,
        "max_lanes": 3 if role in ("crossroads", "loop", "straight") else 2,
        "dead_end_chance": 0.45 if role == "dead_end" else 0.3,
        "ledge_chance": min(0.7, 0.25 + 0.1 * len(set("".join(reg["elevation"]["tiers"])))),
        "region": reg,
    }
    if role == "dead_end" and reg["goal"]:
        spec["endings"] = [{"kind": "special", "side": reg["goal"]["side"], "area": reg["goal"]["landmark"] or "auto"}]
    return spec


class _Dumper(yaml.SafeDumper):
    pass


def _flow_list(dumper, data):
    # short lists of scalars inline ([x, y], exits stay block)
    if len(data) <= 4 and all(isinstance(v, (int, float, str)) and not isinstance(v, bool) for v in data) and \
            all(not isinstance(v, str) or len(v) < 12 for v in data):
        return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True)
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data)


_Dumper.add_representer(list, _flow_list)


def write_all(w: World, out: Path) -> Dict[str, Path]:
    out = Path(out)
    rdir = out / "routes"
    rdir.mkdir(parents=True, exist_ok=True)
    for old in rdir.glob("*.yaml"):
        old.unlink()
    paths = {}
    specs = []
    for i, r in enumerate(w.routes):
        spec = route_spec(w, r, i)
        specs.append(spec)
        p = rdir / f"{r['id']}.yaml"
        head = (f"# {r['name']}: {r['role'].replace('_', ' ')} on the region grid at {r['rect']} (cells of {CELL} metatiles).\n"
                f"# Generated by pokeroutes.region (seed {w.seed}). The `region` block is the region's view of this map.\n")
        p.write_text(head + yaml.dump(spec, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=200))
        paths[r["id"]] = p
    (out / "region.json").write_text(json.dumps(region_json(w, specs), indent=1, ensure_ascii=False))
    return paths


def region_json(w: World, specs: List[dict]) -> dict:
    return {
        "seed": w.seed, "size": [w.W, w.H], "cell": CELL,
        "strait": list(w.strait),
        "cities": [c.json() for c in w.cities],
        "landmarks": [l.json() for l in w.landmarks],
        "links": w.links,
        "routes": [dict(r, spec={k: v for k, v in s.items() if k != "region"},
                        region={k: s["region"][k] for k in ("role", "shape", "tags", "coast", "goal", "landmarks")} |
                        {"biome": {k: s["region"]["biome"][k] for k in ("dominant", "mix", "gradient")}})
                   for r, s in zip(w.routes, specs)],
        "log": w.log,
    }
