"""Region editing: a World <-> JSON document round trip, and the downstream stages
re-run on an edited world with the owner's paint kept as overrides.

The document is what the web editor holds and sends back. Grids are base64 of
little-endian arrays (``{"dtype": "f4", "b64": ...}``); everything else is plain
JSON (cities, landmarks, links, routes). Editor-only grids:

  paint_biome  0 = none, else the char code of a biome painted by hand
  paint_water  0 = none, 1 forced land, 2 forced sea, 3 forced lake
  dirty        cells the terrain brushes touched since the last water pass

Rivers are never painted: they come from the water stage (rain routed downhill over
the height, a river wherever enough of it gathers), so reshaping the ground moves them.

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

from . import drawn, network, terrain
from .world import CELL, City, Landmark, World

FORMAT = "pokeroutes-region-edit"
STAGES = ["water", "climate", "biomes", "routes"]
# world attribute grids
WGRIDS = {"height": "f4", "land": "u1", "landmass": "u1", "lake": "u1", "river": "u1", "flow": "f4", "recv": "i4",
          "temp": "f4", "moist": "f4", "tiers": "u1", "biome": "c", "lagoon": "u1"}
# w.overrides grids (legend effects)
OGRIDS = {"spit": "u1", "no_route": "u1", "trench": "u1", "isles": "u1", "no_lake": "u1", "lake_extra": "u1",
          "heat": "f4", "rain": "f4", "upland": "f4", "moist_noise": "f4"}
EGRIDS = {"paint_biome": "u1", "paint_water": "u1", "dirty": "u1", "legend_biome": "u1"}
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
        "preset": w.preset, "geography": w.geography,
        "strait": [int(v) for v in w.strait] if w.strait else None, "wind": list(w.wind), "meta": meta, "grids": grids,
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
    w = World(int(doc["seed"]), W=int(W), H=int(H), preset=doc.get("preset", "arklarna"),
              geography=doc.get("geography", ""))
    shape = (w.H, w.W)
    g = with_landmass_grid(doc["grids"])
    for k in WGRIDS:
        a = dec(g[k], shape)
        if k == "biome":
            a = _chars(a)
        elif k in BOOL:
            a = a.astype(bool)
        elif k == "landmass" or k == "tiers":
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
    w.strait = tuple(doc["strait"]) if doc.get("strait") else None
    w.wind = tuple(doc.get("wind", (1.0, 0.0)))
    w.cities = [City(**{k: v for k, v in c.items() if k != "map"}) for c in doc.get("cities", [])]
    w.landmarks = [Landmark(**l) for l in doc.get("landmarks", [])]
    w.links = doc.get("links", [])
    w.routes = doc.get("routes", [])
    w.overrides["story"] = list(doc.get("story") or [])
    w.log = list(doc.get("log", []))
    return w


def with_landmass_grid(grids: dict) -> dict:
    """Documents saved before presets existed call the land labels ``half``."""
    if "landmass" in grids or "half" not in grids:
        return grids
    renamed = {k: v for k, v in grids.items() if k != "half"}
    renamed["landmass"] = grids["half"]
    return renamed


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
    w.overrides["_edit"]["dirty"] = np.zeros(w.land.shape, np.uint8)


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
    network.build_network(w, keep=keep, plan=w.links, variant=int(w.overrides.get("route_variant", 0)))
    w.overrides.pop("network", None)


RUN = {"water": stage_water, "climate": stage_climate, "biomes": stage_biomes, "routes": stage_routes}
TOO_FEW_TOWNS_MESSAGE = "place at least two towns first"


class RegionEditRefused(ValueError):
    """An edit that cannot run on this document; the message is meant for the owner."""


def has_something_to_lay_routes_between(w: World) -> bool:
    has_two_towns = len(w.cities) >= 2
    has_hand_links = any(link.get("kind") == "user" for link in w.links)
    has_pinned_routes = any(route.get("pinned") for route in w.routes)
    can_lay_routes = has_two_towns or has_hand_links or has_pinned_routes
    return can_lay_routes


def next_route_variant(doc: dict) -> int:
    route_variant = int(doc.get("route_variant", 0) or 0) + 1
    return route_variant


def recompute(doc: dict, stages: List[str]) -> dict:
    w = doc_to_world(doc)
    todo = [s for s in STAGES if s in stages or "all" in stages]
    too_few_towns = "routes" in todo and not has_something_to_lay_routes_between(w)
    skipped_routes = too_few_towns and not w.routes
    if skipped_routes and todo == ["routes"]:
        raise RegionEditRefused(TOO_FEW_TOWNS_MESSAGE)
    if skipped_routes:
        todo.remove("routes")
    route_variant = next_route_variant(doc) if "routes" in todo else int(doc.get("route_variant", 0) or 0)
    w.overrides["route_variant"] = route_variant
    for s in todo:
        RUN[s](w)
        w.log.append(f"editor: recomputed {s}" + (f" (network variant {route_variant})" if s == "routes" else ""))
    stale = [s for s in doc.get("stale", []) if s not in todo]
    out = finish(w, doc, stale)
    out["route_variant"] = route_variant
    if too_few_towns:
        out["message"] = f"routes not laid: {TOO_FEW_TOWNS_MESSAGE}"
    return out


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
    network.build_network(w, keep=list(w.routes), plan=plan, variant=int(doc.get("route_variant", 0) or 0))
    w.overrides.pop("network", None)
    new = [r for r in w.routes if r["name"] not in before]
    for r in new:
        r["pinned"] = True
    w.log.append(f"editor: {name} laid {len(new)} route(s)" if new else f"editor: {name}: no path")
    out = finish(w, doc, doc.get("stale", []))
    out["added"] = [r["name"] for r in new]
    return out


def draw_route(doc: dict, waypoints: list, legs: Optional[list] = None, replace: Optional[str] = None) -> dict:
    """Lay a route painted freehand (the stroke's corners, see drawn.py). ``replace`` names
    an earlier drawn line to redraw. Every other route stays; the new ones come back pinned."""
    w = doc_to_world(doc)
    if len(waypoints) < 2:
        raise ValueError("drag to paint a route")
    if replace:
        drawn.without_drawn_line(w, replace)
    line = dict(name=replace or drawn.next_line_name(w), waypoints=waypoints, legs=legs or [])
    added, touches = drawn.add_drawn_line(w, line)
    pin_drawn_routes_and_what_they_touch(w, added, touches)
    w.log.append(f"editor: {line['name']} drawn as {', '.join(added)}")
    out = finish(w, doc, doc.get("stale", []))
    out["added"] = added
    out["line"] = line["name"]
    return out


def pin_drawn_routes_and_what_they_touch(w: World, added: List[str], touches: List[dict]) -> None:
    touched_routes = {ref.get("route") for ref in touches}
    for route in w.routes:
        if route["name"] in added:
            route["pinned"] = True
            route["drawn"] = True
        if route["name"] in touched_routes:
            route["pinned"] = True


TOWN_SIZE = (10, 9)
TOWN_MAP_MARGIN = 2


def default_town_name(w: World) -> str:
    taken = {c.name for c in w.cities}
    number = 1
    while f"Town {number}" in taken:
        number += 1
    return f"Town {number}"


def unique_town_id(w: World, name: str) -> str:
    base = network.ascii_id(name).lower() or "town"
    taken = {c.id for c in w.cities}
    town_id, number = base, 2
    while town_id in taken:
        town_id = f"{base}{number}"
        number += 1
    return town_id


def town_rect(w: World, x: int, y: int) -> List[int]:
    width, height = TOWN_SIZE
    x0 = min(max(TOWN_MAP_MARGIN, x - width // 2), w.W - TOWN_MAP_MARGIN - width)
    y0 = min(max(TOWN_MAP_MARGIN, y - height // 2), w.H - TOWN_MAP_MARGIN - height)
    return [x0, y0, x0 + width, y0 + height]


def rects_overlap(a: List[int], b: List[int]) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def landmass_name(w: World, x: int, y: int) -> str:
    if w.landmass is None or not w.landmass[y, x]:
        return ""
    return f"landmass {int(w.landmass[y, x])}"


def add_town(doc: dict, x: int, y: int, name: Optional[str] = None) -> dict:
    """Place a town centred on cell (x, y), which has to be dry land. Routes go
    stale; the next routes pass lays roads to it."""
    w = doc_to_world(doc)
    x, y = int(x), int(y)
    if not (0 <= x < w.W and 0 <= y < w.H):
        raise ValueError("that cell is off the map")
    if not w.land[y, x] or w.lake[y, x]:
        raise ValueError("a town has to stand on dry land")
    name = (str(name or "").strip() or default_town_name(w))[:40]
    if any(c.name == name for c in w.cities) or any(r["name"] == name for r in w.routes):
        raise ValueError(f"the name {name!r} is taken")
    rect = town_rect(w, x, y)
    if any(rects_overlap(rect, c.rect) for c in w.cities):
        raise ValueError("too close to another town")
    town = City(unique_town_id(w, name), name, rect, gym="", honours="", half=landmass_name(w, x, y), size="town")
    w.cities.append(town)
    w.log.append(f"editor: placed {name} at {x}, {y}")
    out = finish(w, doc, stale_with_routes(doc))
    out["added_town"] = town.id
    return out


def place_generated_town(w: World, x: int, y: int) -> Optional[City]:
    rect = town_rect(w, x, y)
    if any(rects_overlap(rect, c.rect) for c in w.cities):
        return None
    name = default_town_name(w)
    town = City(unique_town_id(w, name), name, rect, gym="", honours="", half=landmass_name(w, x, y), size="town")
    w.cities.append(town)
    return town


def generate_map(doc: dict) -> dict:
    """The whole map on the current geography: towns placed where the land suits
    them, then the route network between them. Pinned towns and pinned routes stay;
    every other town and route is replaced."""
    from .town_sites import pick_town_sites

    w = doc_to_world(doc)
    route_variant = next_route_variant(doc)
    removed_town_names = {c.name for c in w.cities if not c.pinned}
    w.cities = [c for c in w.cities if c.pinned]
    sites = pick_town_sites(w, route_variant, [(c.cx, c.cy) for c in w.cities])
    placed_towns = [town for town in (place_generated_town(w, x, y) for x, y in sites) if town is not None]
    if not has_something_to_lay_routes_between(w):
        raise RegionEditRefused("this map has no room for two towns: raise or paint more dry land first")
    w.overrides["route_variant"] = route_variant
    stage_routes(w)
    w.log.append(f"editor: generated the map: {len(placed_towns)} towns, {len(w.routes)} routes (network variant {route_variant})")
    out = finish(w, doc, [stage for stage in doc.get("stale", []) if stage != "routes"])
    out["route_variant"] = route_variant
    out["added_towns"] = [town.id for town in placed_towns]
    out["message"] = f"placed {len(placed_towns)} towns and laid {len(out['routes'])} routes"
    for beat in out.get("story") or []:
        if beat.get("at") in removed_town_names:
            beat["at"] = ""
    return out


def delete_town(doc: dict, town_id: str) -> dict:
    w = doc_to_world(doc)
    town = w.city(town_id)
    if town is None:
        raise ValueError(f"no town {town_id!r}")
    w.cities = [c for c in w.cities if c.id != town_id]
    for route in w.routes:
        route["exits"] = [e for e in route.get("exits", []) if e.get("to") != town.name]
    w.log.append(f"editor: removed {town.name}")
    out = finish(w, doc, stale_with_routes(doc))
    for beat in out.get("story") or []:
        if beat.get("at") == town.name:
            beat["at"] = ""
    return out


def stale_with_routes(doc: dict) -> List[str]:
    stale = set(doc.get("stale", [])) | {"routes"}
    return [stage for stage in STAGES if stage in stale]


def finish(w: World, doc: dict, stale: List[str]) -> dict:
    out = world_to_doc(w)
    out["stale"] = stale
    out["routes"] = with_specs(w)
    for k in ("name", "story", "route_variant"):  # story beats are written by hand and ride along
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

def generate_doc(seed: int, preset: str = "generic", geography_kind: Optional[str] = None) -> dict:
    from .pipeline import build

    w = build(seed, routes=True, strip=False, preset=preset, geography_kind=geography_kind)
    w.overrides.pop("network", None)
    out = world_to_doc(w)
    out["routes"] = with_specs(w)
    out["name"] = f"seed-{seed}" if preset == "generic" else f"{preset}-{seed}"
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
        doc["grids"] = with_landmass_grid(doc["grids"])
        doc.setdefault("preset", "arklarna")
        w = doc_to_world(doc)  # validates
        doc["routes"] = with_specs(w)
        return doc
    return generate_doc(int(rj.get("seed", 1)), rj.get("preset", "arklarna"), rj.get("geography") or None)


def render_png(doc: dict, scale: int = 6) -> bytes:
    import io

    from .render import render_region

    w = doc_to_world(doc)
    buf = io.BytesIO()
    render_region(w, scale, "routes").save(buf, "PNG")
    return buf.getvalue()
