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
biomes (tiers, biomes) and routes (towns and routes derived from the painted Map grid,
map_grid.py; ``map_grid`` in the document holds the painted cells).
"""
from __future__ import annotations

import base64
import json
import math
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
from scipy import ndimage as ndi

from . import map_grid, terrain
from .world import CELL, LEGACY_BIOME_REPLACEMENT, City, Landmark, World

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
    doc["map_grid"] = map_grid.map_grid_of_world(w)
    if extra:
        doc.update(extra)
    return doc


def _plain(v):
    return json.loads(json.dumps(v, default=lambda o: o.item() if hasattr(o, "item") else list(o)))


def doc_to_world(doc: dict) -> World:
    if doc.get("format") != FORMAT:
        raise ValueError("not a region editor document")
    W, H = doc["size"]
    w = World(int(doc["seed"]), W=int(W), H=int(H), preset="generic", geography=doc.get("geography", ""))
    shape = (w.H, w.W)
    g = with_landmass_grid(doc["grids"])
    for k in WGRIDS:
        a = dec(g[k], shape)
        if k == "biome":
            a = with_legacy_biomes_replaced(_chars(a))
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
    w.overrides["biome"] = [(chr(c), lb == c) for c in np.unique(lb) if c and chr(c) not in LEGACY_BIOME_REPLACEMENT]
    w.overrides["_edit"] = {k: dec(g[k], shape) for k in EGRIDS if k != "legend_biome"}
    w.overrides["_edit"]["paint_biome"] = with_legacy_paint_replaced(w.overrides["_edit"]["paint_biome"])
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
    w.overrides["map_grid"] = doc.get("map_grid")
    w.log = list(doc.get("log", []))
    return w


def with_legacy_biomes_replaced(biome_chars: np.ndarray) -> np.ndarray:
    replaced = biome_chars.copy()
    for legacy_code, replacement_code in LEGACY_BIOME_REPLACEMENT.items():
        replaced[replaced == legacy_code] = replacement_code
    return replaced


def with_legacy_paint_replaced(paint_biome: np.ndarray) -> np.ndarray:
    replaced = paint_biome.copy()
    for legacy_code, replacement_code in LEGACY_BIOME_REPLACEMENT.items():
        replaced[replaced == ord(legacy_code)] = ord(replacement_code)
    return replaced


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
    map_grid.map_from_map_grid(w, map_grid.map_grid_of_world(w))


RUN = {"water": stage_water, "climate": stage_climate, "biomes": stage_biomes, "routes": stage_routes}


class RegionEditRefused(ValueError):
    """An edit that cannot run on this document; the message is meant for the owner."""


def next_route_variant(doc: dict) -> int:
    route_variant = int(doc.get("route_variant", 0) or 0) + 1
    return route_variant


def recompute(doc: dict, stages: List[str]) -> dict:
    w = doc_to_world(doc)
    todo = [s for s in STAGES if s in stages or "all" in stages]
    for s in todo:
        RUN[s](w)
        w.log.append(f"editor: recomputed {s}")
    stale = [s for s in doc.get("stale", []) if s not in todo]
    out = finish(w, doc, stale)
    return out


def story_with_town_names_followed(story: List[dict], merged_away: Dict[str, str]) -> List[dict]:
    followed_story = [dict(beat, at=merged_away.get(beat.get("at"), beat.get("at"))) for beat in story]
    return followed_story


def story_without_lost_places(story: List[dict], place_names: set) -> List[dict]:
    kept_story = [dict(beat, at=beat.get("at") if beat.get("at") in place_names else "") for beat in story]
    return kept_story


def place_names_of(w: World) -> set:
    place_names = {town.name for town in w.cities} | {route["name"] for route in w.routes}
    return place_names


def paint_map(doc: dict, cells: list, brush: str, route_key: Optional[str] = None) -> dict:
    """Paint Map-grid cells (``[[gx, gy], ...]``) with the route, town or erase brush and
    derive every town and route from the painted grid (see map_grid.py). ``route_key``
    picks the route the cells go to: an existing route's ``grid_key`` or "new" (both
    take the cells from other routes); left out, a stroke that starts on a route
    extends it and any other stroke starts a new route."""
    w = doc_to_world(doc)
    stored_map_grid = map_grid.map_grid_of_world(w)
    marks, assignment = map_grid.painted_map(map_grid.marks_of_map_grid(stored_map_grid), map_grid.assignment_of_map_grid(stored_map_grid),
                                             cells, brush, map_grid.grid_size_of_world(w), route_key or None)
    merged_away = map_grid.map_from_marks(w, marks, assignment)
    w.log.append(f"editor: painted {len(cells)} map cell(s) with the {brush} brush")
    out = finish(w, doc, [stage for stage in doc.get("stale", []) if stage != "routes"])
    if "story" in out:
        out["story"] = story_without_lost_places(story_with_town_names_followed(out["story"], merged_away), place_names_of(w))
    return out


def regrid_map(doc: dict, region_cells_per_grid_cell: int) -> dict:
    """Snap every town and route onto a Map grid of another cell size (see
    map_grid_square_size.regrid_world) and grow the region to whole squares."""
    from .map_grid_square_size import EARLIER_SQUARE_SIZES_KEY, regrid_world
    from .region_resize import RegionResizeProblem, resize_region as resize_world_region

    w = doc_to_world(doc)
    regrid_world(w, int(region_cells_per_grid_cell))
    square_size = map_grid.region_cells_per_grid_cell(w)
    whole_squares_across, whole_squares_down = math.ceil(w.W / square_size), math.ceil(w.H / square_size)
    if (whole_squares_across * square_size, whole_squares_down * square_size) != (w.W, w.H):
        earlier_square_sizes = w.overrides["map_grid"].get(EARLIER_SQUARE_SIZES_KEY) or {}
        try:
            resize_world_region(w, whole_squares_across, whole_squares_down)
        except RegionResizeProblem as problem:
            raise RegionEditRefused(str(problem)) from problem
        w.overrides["map_grid"][EARLIER_SQUARE_SIZES_KEY] = earlier_square_sizes
    w.log.append(f"editor: map grid set to {region_cells_per_grid_cell} region cells a square ({w.W} x {w.H} cells)")
    out = finish(w, doc, [stage for stage in doc.get("stale", []) if stage != "routes"])
    return out


def resize_region(doc: dict, width_in_squares: int, height_in_squares: int) -> dict:
    """Grow or crop the region to a number of Map squares (see region_resize.py)."""
    from .region_resize import RegionResizeProblem, resize_region as resize_world_region

    w = doc_to_world(doc)
    try:
        merged_away = resize_world_region(w, int(width_in_squares), int(height_in_squares))
    except RegionResizeProblem as problem:
        raise RegionEditRefused(str(problem)) from problem
    w.log.append(f"editor: region resized to {width_in_squares} x {height_in_squares} squares ({w.W} x {w.H} cells)")
    out = finish(w, doc, [stage for stage in doc.get("stale", []) if stage != "routes"])
    if "story" in out:
        out["story"] = story_without_lost_places(story_with_town_names_followed(out["story"], merged_away), place_names_of(w))
    return out


def generate_map(doc: dict) -> dict:
    """The whole map on the current geography, on the Map grid: towns placed where the
    land suits them, then metro-style routes between them. Pinned towns and pinned
    routes stay; every other town and route is replaced."""
    from .grid_map_generation import generated_marks_and_assignment

    w = doc_to_world(doc)
    route_variant = next_route_variant(doc)
    marks, assignment = generated_marks_and_assignment(w, route_variant)
    w.cities = [c for c in w.cities if c.pinned]
    w.routes = [r for r in w.routes if r.get("pinned")]
    map_grid.map_from_marks(w, marks, assignment)
    if len(w.cities) < 2:
        raise RegionEditRefused("this map has no room for two towns: raise or paint more dry land first")
    w.log.append(f"editor: generated the map: {len(w.cities)} towns, {len(w.routes)} routes (network variant {route_variant})")
    out = finish(w, doc, [stage for stage in doc.get("stale", []) if stage != "routes"])
    out["route_variant"] = route_variant
    out["message"] = f"placed {len(w.cities)} towns and laid {len(out['routes'])} routes"
    if "story" in out:
        out["story"] = story_without_lost_places(out["story"], place_names_of(w))
    return out


def finish(w: World, doc: dict, stale: List[str]) -> dict:
    out = world_to_doc(w)
    out["stale"] = stale
    out["routes"] = with_specs(w)
    for k in ("name", "story", "route_variant", "events"):  # story beats and event rules are written by hand and ride along
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
    attach_chain_summaries(w, out)
    return out


def attach_chain_summaries(w: World, routes_for_inspector: List[dict]) -> None:
    from .route_chain import chain_maps_of_world, chain_summary

    try:
        chain_maps = chain_maps_of_world(w)
    except Exception as chain_error:
        w.log.append(f"route chains unavailable: {type(chain_error).__name__}: {chain_error}")
        return
    for route in routes_for_inspector:
        route["chain"] = chain_summary(chain_maps, route["name"])


def full_spec(doc: dict, rid: str) -> Optional[dict]:
    from .export import chain_map_specs, route_spec

    w = doc_to_world(doc)
    spec_of_chain_map = next((spec for spec in chain_map_specs(w) if rid in (spec["id"], spec["name"])), None)
    if spec_of_chain_map is not None:
        return spec_of_chain_map
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
        doc["grids"] = with_landmass_grid(doc["grids"])
        doc["preset"] = "generic"
        w = doc_to_world(doc)
        if not doc.get("map_grid"):
            map_grid.map_from_map_grid(w, map_grid.map_grid_of_world(w))
            return finish(w, doc, doc.get("stale", []))
        doc["routes"] = with_specs(w)
        return doc
    return generate_doc(int(rj.get("seed", 1)), geography_kind=rj.get("geography") or None)


def render_png(doc: dict, scale: int = 6) -> bytes:
    import io

    from .render import render_region

    w = doc_to_world(doc)
    buf = io.BytesIO()
    render_region(w, scale, "routes").save(buf, "PNG")
    return buf.getvalue()
