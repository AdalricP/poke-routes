"""Region editing: a World <-> JSON document round trip, and the downstream stages
re-run on an edited world with the owner's paint kept as overrides.

The document is what the web editor holds and sends back. Grids are base64 of
little-endian arrays (``{"dtype": "f4", "b64": ...}``); everything else is plain
JSON (cities, landmarks, links, routes). Editor-only grids:

  paint_biome  0 = none, else the char code of a biome painted by hand
  paint_water  0 = none, 1 forced land, 2 forced sea, 3 forced lake
  paint_river  0 = none, 1 river drawn by hand, 2 river erased by hand
  dirty        cells the terrain brushes touched since the last water pass

Stages (in order): water (lakes, rivers, flow), climate (temperature, moisture),
biomes (tiers, biomes) and routes (network, roles, names; pinned routes stay).
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
from scipy import ndimage as ndi

from . import network, terrain
from .world import CELL, City, Landmark, World

FORMAT = "pokeroutes-region-edit"
STAGES = ["water", "climate", "biomes", "routes"]
# world attribute grids
WGRIDS = {"height": "f4", "land": "u1", "half": "u1", "lake": "u1", "river": "u1", "flow": "f4", "recv": "i4",
          "temp": "f4", "moist": "f4", "tiers": "u1", "biome": "c", "lagoon": "u1"}
# w.overrides grids (legend effects)
OGRIDS = {"spit": "u1", "no_route": "u1", "trench": "u1", "isles": "u1", "no_lake": "u1", "lake_extra": "u1",
          "heat": "f4", "rain": "f4", "upland": "f4", "moist_noise": "f4"}
EGRIDS = {"paint_biome": "u1", "paint_water": "u1", "paint_river": "u1", "dirty": "u1", "legend_biome": "u1"}
META = ("den_gate", "volcano", "capital_site", "den", "tier_q", "preyl")
BOOL = {"land", "lake", "river", "lagoon", "spit", "no_route", "trench", "isles", "no_lake", "lake_extra"}


# ------------------------------------------------------------------ grids

def enc(a: np.ndarray, dtype: str) -> dict:
    if dtype == "c":
        a = np.vectorize(ord, otypes=[np.uint8])(a)
        dtype = "u1"
    a = np.ascontiguousarray(np.asarray(a).astype("<" + dtype if dtype != "u1" else "u1"))
    return {"dtype": dtype, "b64": base64.b64encode(a.tobytes()).decode("ascii")}


def dec(g: dict, shape) -> np.ndarray:
    dt = g["dtype"]
    a = np.frombuffer(base64.b64decode(g["b64"]), dtype=("<" + dt) if dt != "u1" else "u1")
    if a.size != shape[0] * shape[1]:
        raise ValueError(f"grid has {a.size} cells, expected {shape[0]}x{shape[1]}")
    return a.reshape(shape).copy()


def _chars(a: np.ndarray) -> np.ndarray:
    return np.vectorize(chr, otypes=["<U1"])(a)


# ------------------------------------------------------------------ world <-> doc

def world_to_doc(w: World, extra: Optional[dict] = None) -> dict:
    shape = (w.H, w.W)
    zeros = np.zeros(shape)
    grids = {}
    for k, dt in WGRIDS.items():
        v = getattr(w, k)
        if v is None:
            v = zeros
        if k == "recv":
            v = np.asarray(v).reshape(shape)
        grids[k] = enc(v, dt)
    for k, dt in OGRIDS.items():
        v = w.overrides.get(k, 0)
        v = np.broadcast_to(np.asarray(v, dtype=float), shape) if np.ndim(v) == 0 else v
        grids[k] = enc(v, dt)
    # legend biome overrides (an ordered list of masks) folded into one grid: last wins
    lb = np.zeros(shape, dtype=np.uint8)
    for code, mask in w.overrides.get("biome", []):
        lb[np.asarray(mask, bool)] = ord(code)
    ed = w.overrides.get("_edit", {})
    for k in EGRIDS:
        if k == "legend_biome":
            grids[k] = enc(lb, "u1")
        else:
            grids[k] = enc(ed.get(k, np.zeros(shape, np.uint8)), "u1")
    meta = {}
    for k in META:
        v = w.overrides.get(k)
        if v is not None:
            meta[k] = np.asarray(v).tolist() if isinstance(v, np.ndarray) else _plain(v)
    doc = {
        "format": FORMAT, "version": 1, "seed": int(w.seed), "size": [w.W, w.H], "cell": CELL,
        "strait": [int(v) for v in w.strait], "wind": list(w.wind), "meta": meta, "grids": grids,
        "cities": [c.json() for c in w.cities], "landmarks": [l.json() for l in w.landmarks],
        "links": _plain(w.links), "routes": _plain(w.routes), "stale": [], "log": list(w.log)[-40:],
    }
    if extra:
        doc.update(extra)
    return doc


def _plain(v):
    return json.loads(json.dumps(v, default=lambda o: o.item() if hasattr(o, "item") else list(o)))


def doc_to_world(doc: dict) -> World:
    if doc.get("format") != FORMAT:
        raise ValueError("not a region editor document")
    W, H = doc["size"]
    w = World(int(doc["seed"]), W=int(W), H=int(H))
    shape = (w.H, w.W)
    g = doc["grids"]
    for k in WGRIDS:
        a = dec(g[k], shape)
        if k == "biome":
            a = _chars(a)
        elif k in BOOL:
            a = a.astype(bool)
        elif k == "half" or k == "tiers":
            a = a.astype(np.int8)
        elif k == "recv":
            a = a.astype(np.int64).ravel()
        else:
            a = a.astype(float)
        setattr(w, k, a)
    for k in OGRIDS:
        a = dec(g[k], shape)
        w.overrides[k] = a.astype(bool) if k in BOOL else a.astype(float)
    lb = dec(g["legend_biome"], shape)
    w.overrides["biome"] = [(chr(c), lb == c) for c in np.unique(lb) if c]
    w.overrides["_edit"] = {k: dec(g[k], shape) for k in EGRIDS if k != "legend_biome"}
    for k, v in (doc.get("meta") or {}).items():
        w.overrides[k] = np.array(v) if k == "tier_q" else (tuple(v) if isinstance(v, list) and k != "preyl" else v)
    w.sea = ~w.land
    w.strait = tuple(doc.get("strait", (0, 0)))
    w.wind = tuple(doc.get("wind", (1.0, 0.0)))
    w.cities = [City(**{k: v for k, v in c.items() if k != "map"}) for c in doc.get("cities", [])]
    w.landmarks = [Landmark(**l) for l in doc.get("landmarks", [])]
    w.links = doc.get("links", [])
    w.routes = doc.get("routes", [])
    w.log = list(doc.get("log", []))
    return w


# ------------------------------------------------------------------ stages

def _paint(w: World, k: str) -> np.ndarray:
    return w.overrides["_edit"][k]


def stage_water(w: World) -> None:
    """Lakes, rivers and flow from the edited height. Lakes the brushes touched are
    let go (they re-form if the ground still holds water); painted lakes stay."""
    pw = _paint(w, "paint_water")
    dirty = _paint(w, "dirty").astype(bool)
    if w.lake is not None and dirty.any():
        lab, n = ndi.label(w.lake)
        hit = np.unique(lab[ndi.binary_dilation(dirty, iterations=1)])
        w.lake = w.lake & ~np.isin(lab, hit[hit > 0])
    w.overrides["lake_extra"] = (pw == 3) & w.land
    base_no = w.overrides.get("no_lake", np.zeros_like(w.land))
    w.overrides["no_lake"] = np.asarray(base_no, bool)
    hand_no = pw == 1
    if hand_no.any():
        w.lake = w.lake & ~hand_no
    w.overrides["no_lake"] = w.overrides["no_lake"] | hand_no
    terrain.hydrology(w)
    w.overrides["no_lake"] = np.asarray(base_no, bool)  # the legend's own mask stays as it was
    _apply_rivers(w)
    w.overrides["_edit"]["dirty"] = np.zeros(w.land.shape, np.uint8)


def _apply_rivers(w: World) -> None:
    pr = _paint(w, "paint_river")
    w.river = ((w.river | (pr == 1)) & ~(pr == 2)) & w.land & ~w.lake


def stage_climate(w: World) -> None:
    terrain.climate(w, reuse_noise=True)


def stage_biomes(w: World) -> None:
    terrain.make_tiers(w, w.overrides.get("tier_q"))
    terrain.biomes(w)
    pb = _paint(w, "paint_biome")
    m = (pb > 0) & w.land & ~w.lake
    if m.any():
        w.biome[m] = _chars(pb[m])


def stage_routes(w: World) -> None:
    keep = [r for r in w.routes if r.get("pinned")]
    network.build_network(w, keep=keep, plan=w.links)
    w.overrides.pop("network", None)


RUN = {"water": stage_water, "climate": stage_climate, "biomes": stage_biomes, "routes": stage_routes}


def recompute(doc: dict, stages: List[str]) -> dict:
    w = doc_to_world(doc)
    todo = [s for s in STAGES if s in stages or "all" in stages]
    for s in todo:
        RUN[s](w)
        w.log.append(f"editor: recomputed {s}")
    stale = [s for s in doc.get("stale", []) if s not in todo]
    return finish(w, doc, stale)


def add_route(doc: dict, src_ref: dict, dst_ref: dict) -> dict:
    """Lay one new link between two places; every existing route stays where it is,
    and the new routes come back pinned."""
    w = doc_to_world(doc)
    names = {l["name"] for l in w.links}
    k = 1
    while f"Hand link {k}" in names:
        k += 1
    name = f"Hand link {k}"
    before = {r["name"] for r in w.routes}
    plan = list(w.links) + [dict(name=name, kind="user", routes=[], src_ref=src_ref, dst_ref=dst_ref)]
    network.build_network(w, keep=list(w.routes), plan=plan)
    w.overrides.pop("network", None)
    new = [r for r in w.routes if r["name"] not in before]
    for r in new:
        r["pinned"] = True
    w.log.append(f"editor: {name} laid {len(new)} route(s)" if new else f"editor: {name}: no path")
    out = finish(w, doc, doc.get("stale", []))
    out["added"] = [r["name"] for r in new]
    return out


def finish(w: World, doc: dict, stale: List[str]) -> dict:
    out = world_to_doc(w)
    out["stale"] = stale
    out["routes"] = with_specs(w)
    for k in ("name",):
        if k in doc:
            out[k] = doc[k]
    return out


def with_specs(w: World) -> List[dict]:
    """Routes plus their derived spec (without the bulky region block), for the inspector."""
    from .export import route_spec

    out = []
    for i, r in enumerate(w.routes):
        r = dict(r)
        r.pop("spec", None)
        try:
            s = route_spec(w, r, i)
            r["spec"] = {k: v for k, v in s.items() if k != "region"}
        except Exception as e:  # a hand-broken route still shows up
            r["spec"] = {"error": f"{type(e).__name__}: {e}"}
        out.append(r)
    return out


def full_spec(doc: dict, rid: str) -> Optional[dict]:
    from .export import route_spec

    w = doc_to_world(doc)
    for i, r in enumerate(w.routes):
        if r["id"] == rid or r["name"] == rid:
            return route_spec(w, r, i)
    return None


# ------------------------------------------------------------------ files

def generate_doc(seed: int) -> dict:
    from .pipeline import build

    w = build(seed, routes=True, strip=False)
    w.overrides.pop("network", None)
    out = world_to_doc(w)
    out["routes"] = with_specs(w)
    out["name"] = f"seed-{seed}"
    return out


def save(doc: dict, out: Path) -> Dict[str, str]:
    """region.json (the usual summary plus an ``editor`` block holding the whole
    document), routes/<Id>.yaml for every route and a region_map.png."""
    from .export import write_all
    from .render import render_region

    w = doc_to_world(doc)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    paths = write_all(w, out)
    rj = json.loads((out / "region.json").read_text())
    ed = dict(doc)
    ed["routes"] = [{k: v for k, v in r.items() if k != "spec"} for r in doc.get("routes", [])]
    rj["editor"] = ed
    (out / "region.json").write_text(json.dumps(rj, ensure_ascii=False))
    render_region(w, 4, "routes").save(out / "region_map.png")
    return {"region": str(out / "region.json"), "routes": [str(p) for p in paths.values()]}


def load(path: Path) -> dict:
    rj = json.loads(Path(path).read_text())
    if "editor" in rj:
        doc = rj["editor"]
        w = doc_to_world(doc)  # validates
        doc["routes"] = with_specs(w)
        return doc
    return generate_doc(int(rj.get("seed", 1)))


def render_png(doc: dict, scale: int = 6) -> bytes:
    import io

    from .render import render_region

    w = doc_to_world(doc)
    buf = io.BytesIO()
    render_region(w, scale, "routes").save(buf, "PNG")
    return buf.getvalue()
