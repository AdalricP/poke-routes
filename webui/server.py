"""Local web UI for the poke-routes generator.

    python3 webui/server.py            # http://localhost:8790

Stdlib http.server only. Runs the generator stage by stage, autotiles every
stage snapshot into real Emerald metatiles (General + Fortree) and serves the
results as JSON; the browser draws them from a metatile atlas.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import time
import traceback
import uuid
import zipfile
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import route_extras  # noqa: E402
from fasttile import FastTiler  # noqa: E402
from pokeroutes.autotile import Autotiler  # noqa: E402
from pokeroutes.breeze import (BreezePlan, animation_sequence_for_swaying_metatile,  # noqa: E402
                               breeze_frames_for_tileset_pair, breeze_plan_for_learned_model)
from pokeroutes.spec import BIOMES, ROUTE_EFFECTS  # noqa: E402
from pokeroutes.tileset import NUM_PRIMARY_METATILES, load_pair  # noqa: E402

# The generator (spec / generate / places / export) is still being worked on, so it
# is imported through G() below, which reloads it whenever its source changes.

PORT = int(os.environ.get("PORT", 8790))
EMERALD = ROOT / "vendor" / "pokeemerald"
MODELS = ROOT / "pokeroutes" / "models"
TILESET = "general_fortree"  # forest / meadow / riverland; other biomes use their own learned model
# the Pokedex: $POKEROUTES_SPECIES_FILE, else the sibling arklarna checkout, else the original dev path
SPECIES_FILE = os.environ.get("POKEROUTES_SPECIES_FILE") or next((str(p) for p in (ROOT.parent / "arklarna" / "design" / "pokedex.json", Path("/Users/aryan/Desktop/experiments/arklarna/design/pokedex.json")) if p.is_file()), str(ROOT.parent / "arklarna" / "design" / "pokedex.json"))
STATIC = HERE / "static"
ATLAS_COLS = 32

# ------------------------------------------------------------------ cached heavy objects

_tilers: dict = {}
_pairs: dict = {}
_atlases: dict = {}
_lock = threading.Lock()


def route_tileset(spec) -> str:
    """The learned model / tileset pair for a route's biome (spec.tileset, picked from the
    biome: Route 111's for desert, Slateport's for beach...), General + Fortree if none."""
    ts = str(getattr(spec, "tileset", "") or TILESET)
    return ts if (MODELS / f"{ts}.json").is_file() else TILESET


def tiler(ts: str = TILESET) -> FastTiler:
    with _lock:
        if ts not in _tilers:
            _tilers[ts] = FastTiler(Autotiler.load(MODELS / f"{ts}.json"))
        return _tilers[ts]


def pair(ts: str = TILESET):
    with _lock:
        if ts not in _pairs:
            _pairs[ts] = load_pair(EMERALD / "data/tilesets", *ts.split("_", 1))
        return _pairs[ts]


def atlas_url(ts: str = TILESET, png: bool = False) -> str:
    return "/api/atlas" + (".png" if png else "") + ("" if ts == TILESET else f"?ts={ts}")


def atlas(ts: str = TILESET):
    """(png bytes, metadata) for every metatile of the pair, rendered once."""
    if ts not in _atlases:
        a, b = ts.split("_", 1)
        _atlases[ts] = build_atlas(pair(ts), f"gTileset_{a.title()}", f"gTileset_{b.title()}", atlas_url(ts, png=True),
                                   breeze_plan_for_learned_tileset(ts))
    return _atlases[ts]


def breeze_plan_for_learned_tileset(tileset_name: str) -> BreezePlan:
    learned_model_path = MODELS / f"{tileset_name}.json"
    if not learned_model_path.is_file():
        return BreezePlan()
    learned_model_breeze_plan = breeze_plan_for_learned_model(json.loads(learned_model_path.read_text()))
    return learned_model_breeze_plan


def build_atlas(p, primary_sym: str, secondary_sym: str, png_url: str, breeze_plan: BreezePlan = BreezePlan()):
    """(png bytes, metadata) for every metatile of a tileset pair, plus extra cells for the
    other frames of any metatile that animates (water, flowers, waterfalls...) so the browser
    can play them instead of showing a dead scene, and preview-only breeze frames (tree
    crowns, tall grass, flowers swaying a pixel) listed under "breeze"."""
    from PIL import Image

    n_pri, n_sec = p.metatile_count()
    ids = list(range(n_pri)) + [NUM_PRIMARY_METATILES + i for i in range(n_sec)]
    anim = p.animated_metatiles()  # metatile id -> frame count, for the ones that animate
    breeze_frames = breeze_frames_for_tileset_pair(p, breeze_plan, set(anim))
    extra = [(mid, p.render_metatile(mid, frame=f)) for mid, nf in anim.items() for f in range(1, nf)]
    extra_breeze = [(mid, offset, pixels) for mid, frames in breeze_frames.items() for offset, pixels in frames.items()]
    rows = (len(ids) + len(extra) + len(extra_breeze) + ATLAS_COLS - 1) // ATLAS_COLS
    img = np.zeros((rows * 16, ATLAS_COLS * 16, 4), dtype=np.uint8)

    def put(n, pixels):
        y, x = (n // ATLAS_COLS) * 16, (n % ATLAS_COLS) * 16
        img[y:y + 16, x:x + 16, :3] = pixels
        img[y:y + 16, x:x + 16, 3] = 255

    index = {}
    for n, mid in enumerate(ids):
        put(n, p.render_metatile(mid))
        index[str(mid)] = n
    anim_index: dict = {}
    for k, (mid, pixels) in enumerate(extra):
        n = len(ids) + k
        put(n, pixels)
        anim_index.setdefault(str(mid), []).append(n)
    breeze_cells: dict = {}
    for k, (mid, offset, pixels) in enumerate(extra_breeze):
        n = len(ids) + len(extra) + k
        put(n, pixels)
        breeze_cells.setdefault(mid, {})[offset] = n
    for mid, cells in breeze_cells.items():
        anim_index[str(mid)] = animation_sequence_for_swaying_metatile(index[str(mid)], cells)
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "PNG", optimize=True)
    meta = {"png": png_url, "cell": 16, "columns": ATLAS_COLS, "count": len(ids),
            "index": index, "primary": primary_sym, "secondary": secondary_sym, "anim": anim_index,
            "breeze": sorted(breeze_cells)}
    return buf.getvalue(), meta


# ------------------------------------------------------------------ worker pool (stage tiling)

_wtilers: dict = {}


def _winit():
    _wtilers[TILESET] = FastTiler(Autotiler.load(MODELS / f"{TILESET}.json"))


def _wtile(cls, final_cls, final_mt, final_col, final_el, seed, ts=TILESET):
    from pokeroutes.layout import Layout

    if ts not in _wtilers:
        _wtilers[ts] = FastTiler(Autotiler.load(MODELS / f"{ts}.json"))
    final = Layout(final_mt, final_col, final_el)
    return _wtilers[ts].tile_stage(cls, final_cls, final, seed=seed).metatile


_pool = None


def pool():
    global _pool
    if _pool is None:
        _pool = ProcessPoolExecutor(max_workers=max(1, min(6, (os.cpu_count() or 2) - 1)), initializer=_winit)
    return _pool


# ------------------------------------------------------------------ generator modules (hot reload)

import dataclasses  # noqa: E402
import importlib  # noqa: E402
import typing  # noqa: E402

GEN_MODULES = ["pokeroutes.noise", "pokeroutes.spec", "pokeroutes.classify", "pokeroutes.generate",
               "pokeroutes.places", "pokeroutes.export", "pokeroutes.ground"]
_gen_lock = threading.Lock()
_gen_stamp = None


class _Gen:
    pass


_G = _Gen()


def _stamp():
    return tuple(sorted((f.name, f.stat().st_mtime_ns) for f in (ROOT / "pokeroutes").glob("*.py")))


def G() -> _Gen:
    """Current generator modules; reloaded (in dependency order) when any source file changed."""
    global _gen_stamp
    with _gen_lock:
        st = _stamp()
        if st != _gen_stamp:
            for name in GEN_MODULES:
                if name in sys.modules:
                    importlib.reload(sys.modules[name])
                else:
                    try:
                        importlib.import_module(name)
                    except ModuleNotFoundError:
                        pass
            gen = sys.modules["pokeroutes.generate"]
            _G.spec = sys.modules["pokeroutes.spec"]
            _G.gen = gen
            _G.places = sys.modules.get("pokeroutes.places")
            _G.export = sys.modules["pokeroutes.export"]
            _G.ground = sys.modules["pokeroutes.ground"]
            _patch_snap(gen.Route)
            _gen_stamp = st
        return _G


def _patch_snap(Route):
    """Record how many zones / events exist at every snapshot, so the overlays can
    follow the build-up. Behaviour of the generator itself is unchanged."""
    if getattr(Route.snap, "_webui", False):
        return
    orig = Route.snap

    def snap(self, *a, **k):
        r = orig(self, *a, **k)
        marks = self.__dict__.setdefault("_webui_marks", [])
        while len(marks) < len(self.snapshots):
            marks.append((len(self.zones), len(self.events)))
        return r

    snap._webui = True
    Route.snap = snap


# ------------------------------------------------------------------ spec <-> json

# UI hints for fields we know; anything else in RouteSpec gets a generic control.
HINTS = {
    "seed": {"group": "route", "min": 0, "max": 2 ** 31 - 1},
    "width": {"group": "route", "min": 24, "max": 96, "step": 2},
    "height": {"group": "route", "min": 24, "max": 112, "step": 2},
    "biome": {"group": "route", "choices": list(BIOMES)},
    "effect": {"group": "route", "choices": list(ROUTE_EFFECTS)},
    "meander": {"group": "shape", "min": 0, "max": 1, "step": 0.05},
    "branches": {"group": "shape", "min": 0, "max": 8},
    "loops": {"group": "shape", "min": 0, "max": 4},
    "dead_ends": {"group": "shape", "min": 0, "max": 6},
    "clearings": {"group": "shape", "min": 0, "max": 8},
    "lakes": {"group": "shape", "min": 0, "max": 4},
    "tall_grass": {"group": "shape", "min": 0, "max": 0.9, "step": 0.05},
    "road": {"group": "shape", "label": "dirt road on the main path"},
    "hidden_paths": {"group": "secrets", "min": 0, "max": 6},
    "hidden_items": {"group": "secrets", "min": 0, "max": 12},
    "sections": {"group": "shape", "min": 1, "max": 8},
    "max_lanes": {"group": "shape", "min": 1, "max": 5},
    "merge_chance": {"group": "shape", "min": 0, "max": 1, "step": 0.05},
    "dead_end_chance": {"group": "shape", "min": 0, "max": 1, "step": 0.05},
    "ledge_chance": {"group": "shape", "min": 0, "max": 1, "step": 0.05},
    "extra_exits": {"group": "shape", "min": 0, "max": 4},
    "levels": {"group": "encounters", "min": 1, "max": 100},
}
# fields the UI handles specially or that make no sense to edit here
SKIP = {"name", "id", "size", "exits", "special_areas", "species_file", "tileset", "palette", "exclude_areas",
        "music", "weather", "map_name", "region"}
SIDES = ("north", "south", "east", "west")


def _kind(f, default):
    t = f.type if isinstance(f.type, str) else getattr(f.type, "__name__", str(f.type))
    t = t.replace("typing.", "")
    if isinstance(default, bool) or t == "bool":
        return "bool"
    if isinstance(default, int) or t == "int":
        return "int"
    if isinstance(default, float) or t == "float":
        return "float"
    if isinstance(default, str) or t == "str":
        return "str"
    if isinstance(default, list) and len(default) == 2 and all(isinstance(v, (int, float)) for v in default):
        return "range"
    if t.startswith(("List[str]", "list[str]")):
        return "strlist"
    return "other"


def _default(f):
    if f.default is not dataclasses.MISSING:
        return f.default
    if f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
        return f.default_factory()  # type: ignore[misc]
    return None


def spec_schema() -> dict:
    g = G()
    fields = []
    for f in dataclasses.fields(g.spec.RouteSpec):
        d = _default(f)
        if f.name == "size":
            fields.append({"name": "width", "kind": "int", "default": d[0], **HINTS["width"]})
            fields.append({"name": "height", "kind": "int", "default": d[1], **HINTS["height"]})
            continue
        if f.name in SKIP:
            continue
        k = _kind(f, d)
        if k in ("other", "strlist"):
            continue
        h = dict(HINTS.get(f.name, {}))
        if k in ("int", "float") and "min" not in h:
            if k == "float" and 0 <= (d or 0) <= 1:
                h.update(min=0, max=1, step=0.05)
            else:
                h.update(min=0, max=max(8, int(abs(d or 0) * 3)), step=1 if k == "int" else 0.1)
        if f.name == "seed":
            h["group"] = "route"
        fields.append({"name": f.name, "kind": k, "default": d, "group": h.pop("group", "other"), **h})
    exit_fields = []
    for f in dataclasses.fields(g.spec.Exit):
        exit_fields.append({"name": f.name, "kind": _kind(f, _default(f)), "default": _default(f)})
    return {"fields": fields, "exit_fields": exit_fields}


def _coerce(kind, v, default, lo=None, hi=None):
    try:
        if kind == "bool":
            out = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
        elif kind == "int":
            out = int(float(v))
        elif kind == "float":
            out = float(v)
        elif kind == "str":
            out = str(v)[:80]
        elif kind == "range":
            out = [type(default[0])(v[0]), type(default[1])(v[1])]
            out = sorted(out)
        else:
            out = v
    except (TypeError, ValueError, IndexError):
        return default
    if kind in ("int", "float") and lo is not None:
        out = max(lo, min(hi, out))
    if kind == "range" and lo is not None:
        out = [max(lo, min(hi, x)) for x in out]
    return out


def spec_from(params: dict):
    """Build a RouteSpec from request JSON, reading the dataclass fields at request
    time so new generator parameters pass straight through."""
    g = G()
    RouteSpec, Exit = g.spec.RouteSpec, g.spec.Exit
    kw = {}
    for f in dataclasses.fields(RouteSpec):
        if f.name in ("name", "id", "exits", "special_areas", "species_file", "size", "exclude_areas"):
            continue
        if f.name not in params:
            continue
        d = _default(f)
        k = _kind(f, d)
        h = HINTS.get(f.name, {})
        if k == "other" or k == "strlist":
            if isinstance(params[f.name], type(d)):
                kw[f.name] = params[f.name]
            continue
        lo, hi = h.get("min"), h.get("max")
        if k in ("int", "float") and lo is None:
            lo, hi = (-1e9, 1e9)
        v = _coerce(k, params[f.name], d, lo, hi)
        if "choices" in h and v not in h["choices"]:
            v = d
        kw[f.name] = v
    sz = _default(next(f for f in dataclasses.fields(RouteSpec) if f.name == "size"))
    kw["size"] = [_coerce("int", params.get("width", sz[0]), sz[0], 24, 96),
                  _coerce("int", params.get("height", sz[1]), sz[1], 24, 112)]
    exit_fields = {f.name: (_kind(f, _default(f)), _default(f)) for f in dataclasses.fields(Exit)}
    exits = []
    for e in (params.get("exits") or [])[:6]:
        if not isinstance(e, dict):
            continue
        ek = {}
        for name, (k, d) in exit_fields.items():
            if name in e and e[name] is not None and e[name] != "":
                ek[name] = _coerce(k, e[name], d, *((0.0, 1.0) if name == "at" else (1, 6) if name == "width" else (None, None)))
        if ek.get("side") not in SIDES:
            ek["side"] = "south"
        exits.append(Exit(**ek))
    if not exits:
        exits = [Exit(side="south", at=0.5), Exit(side="north", at=0.5)]
    kw["exits"] = exits
    kw["special_areas"] = [str(x)[:60] for x in (params.get("special_areas") or []) if str(x).strip()][:6]
    kw["species_file"] = SPECIES_FILE
    kw["name"] = str(params.get("name") or "Testweg")[:40]
    spec = RouteSpec(**kw)
    spec.id = "".join(ch for ch in spec.name.title() if ch.isalnum()) or "Route"
    return spec


def spec_json(spec) -> dict:
    d = dataclasses.asdict(spec)
    return {k: v for k, v in d.items() if k not in ("species_file",)}


def run_stages(spec):
    g = G()
    route = g.gen.generate(spec)
    marks = route.__dict__.get("_webui_marks") or [(len(route.zones), len(route.events))] * len(route.snapshots)
    return route, marks


_results: "OrderedDict[str, tuple]" = OrderedDict()
_ark_ids: set = set()  # result ids tiled with the Ark rule tiler (shell_api.py)


def _bbox(cells):
    ys = [c[0] for c in cells]
    xs = [c[1] for c in cells]
    return [int(min(xs)), int(min(ys)), int(max(xs)) + 1, int(max(ys)) + 1]  # x0, y0, x1, y1 (exclusive)


def _tile_stages(route, final, seed, warnings, ts=TILESET):
    """Metatile grid for every snapshot (worker pool, in-process fallback)."""
    global _pool
    snaps = route.snapshots
    tiles = [None] * len(snaps)
    jobs = {}
    for i, (name, cls) in enumerate(snaps):
        if cls.shape != route.cls.shape:
            continue
        if np.array_equal(cls, route.cls):
            tiles[i] = final.metatile
            continue
        prev = next((j for j in range(i) if snaps[j][1].shape == cls.shape and np.array_equal(snaps[j][1], cls)), None)
        if prev is not None:
            jobs[i] = prev
            continue
        try:
            jobs[i] = pool().submit(_wtile, cls, route.cls, final.metatile, final.collision, final.elevation, seed, ts)
        except Exception:
            _pool = None
            jobs[i] = None
    for i, f in jobs.items():
        if isinstance(f, int):
            continue
        try:
            tiles[i] = f.result(timeout=120) if f is not None else None
        except Exception as e:
            _pool = None  # a broken pool gets rebuilt next time
            warnings.append(f"stage tiling worker failed ({type(e).__name__}); tiled in-process")
        if tiles[i] is None:
            tiles[i] = tiler(ts).tile_stage(snaps[i][1], route.cls, final, seed=seed).metatile
    for i, f in jobs.items():
        if isinstance(f, int):
            tiles[i] = tiles[f]
    return tiles


def _zone_json(z) -> dict:
    meta = getattr(z, "meta", {}) or {}
    d = {"kind": z.kind, "name": getattr(z, "name", ""), "cells": [[int(c[0]), int(c[1])] for c in z.cells]}
    if z.kind in ("special", "clearing", "lake") and z.cells:
        d["bbox"] = _bbox(z.cells)
    if "ledge" in meta:
        try:
            d["ledge"] = [int(v) for v in meta["ledge"]]
        except (TypeError, ValueError):
            pass
    if z.kind == "special" and isinstance(meta.get("area"), dict):
        a = meta["area"]
        d["area"] = {k: a.get(k) for k in ("id", "name", "description", "species", "archetype",
                                               "terrain_marks", "shiny_boost", "habitats")}
    return d


def _event_json(e) -> dict:
    data = getattr(e, "data", {}) or {}
    return {"kind": e.kind, "y": int(e.y), "x": int(e.x), **{k: str(v) for k, v in data.items() if k not in ("kind", "x", "y")}}


def generate(params: dict) -> dict:
    """Run the generator and tile every stage. Only the generator itself and the
    final tiling are required; every other part degrades to a warning."""
    t0 = time.time()
    warnings = []
    spec = spec_from(params)
    route, marks = run_stages(spec)
    t_gen = time.time() - t0

    use = "ark" if params.get("tiler") == "ark" else "learned"
    ts = route_tileset(spec)
    final = tiles = None
    t_final = 0.0
    if use == "ark":
        import shell_api

        try:
            final, tiles, t_final = shell_api.ark_tiles(route, warnings)
        except Exception as e:
            traceback.print_exc()
            warnings.append(f"Ark tiler unavailable ({type(e).__name__}: {e}); used the learned tiler")
            use = "learned"
    if use == "learned":
        ft = tiler(ts)
        laid = G().ground.tile_with_false_walls(route, lambda c: ft.tile(c, seed=spec.seed))
        final = G().ground.texture_open_ground(laid, route.cls, spec.seed)  # same as the CLI
        t_final = time.time() - t0 - t_gen
        tiles = _tile_stages(route, final, spec.seed, warnings, ts)
    t_stages = time.time() - t0 - t_gen - t_final

    stages = []
    for i, (name, cls) in enumerate(route.snapshots):
        if tiles[i] is None:
            warnings.append(f"stage '{name}' has a different size; skipped")
            continue
        m = marks[i] if i < len(marks) else (len(route.zones), len(route.events))
        stages.append({"name": str(name), "cls": ["".join(r) for r in cls],
                       "tiles": tiles[i].astype(int).ravel().tolist(), "zones": m[0], "events": m[1]})
    if not stages or not np.array_equal(route.snapshots[-1][1], route.cls):
        stages.append({"name": "final", "cls": ["".join(r) for r in route.cls],
                       "tiles": final.metatile.astype(int).ravel().tolist(),
                       "zones": len(route.zones), "events": len(route.events)})

    def part(label, fn, default):
        try:
            return fn()
        except Exception as e:
            traceback.print_exc()
            warnings.append(f"{label} unavailable: {type(e).__name__}: {e}")
            return default

    zones = part("zones", lambda: [_zone_json(z) for z in route.zones], [])
    events = part("events", lambda: [_event_json(e) for e in route.events], [])
    exits = part("exits", lambda: [{"side": e.side, "to": getattr(e, "to", ""), "cells": [[int(y), int(x)] for (y, x) in c]}
                                   for e, c in route.exits], [])
    rep = part("report", lambda: route_extras.report(route), {})
    enc = part("encounters", lambda: route_extras.encounters(route), {})
    em = part("emerald encounters", lambda: route_extras.emerald_encounters(route), None)
    gid = uuid.uuid4().hex[:12]
    _results[gid] = (route, final)
    if use == "ark":
        _ark_ids.add(gid)
    while len(_results) > 12:
        _ark_ids.discard(_results.popitem(last=False)[0])
    return {
        "id": gid, "name": spec.name, "route_id": spec.id, "seed": spec.seed, "biome": getattr(spec, "biome", ""),
        "tiler": use, "tileset": "ark" if use == "ark" else ts,
        "atlas": "/api/atlas?tiler=ark" if use == "ark" else atlas_url(ts),
        "width": route.w, "height": route.h, "stages": stages, "zones": zones, "events": events, "exits": exits,
        "report": rep, "encounters": enc, "emerald_encounters": em,
        "log": part("log", lambda: [str(l) for l in route.log if l not in _stage_names()], []),
        "spec": part("spec", lambda: json.loads(json.dumps(spec_json(spec), default=str)), {}),
        "exportable": route_extras.gba_size_ok(route.w, route.h),
        "warnings": warnings,
        "timing": {"generate": round(t_gen, 2), "tile_final": round(t_final, 2), "tile_stages": round(t_stages, 2),
                   "total": round(time.time() - t0, 2)},
    }


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:  # an optional extra must not sink the whole response
        traceback.print_exc()
        return None


def _stage_names() -> set:
    try:
        return {n for n, _ in getattr(G().gen, "STAGES", [])}
    except Exception:
        return set()


def export_zip(gid: str) -> tuple:
    route, final = _results[gid]
    ts = route_tileset(route.spec)
    tl = tiler(ts)
    primary, secondary = ts.split("_", 1)
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "emerald"
        if not route_extras.gba_size_ok(route.w, route.h):
            raise ValueError(f"{route.w}x{route.h} is too big for the GBA map buffer: (w+15)*(h+14) must be <= 10240")
        note = None
        try:
            if gid in _ark_ids:
                import shell_api
                from pokeroutes.ruletile import write_ark_emerald

                write_ark_emerald(route, final, out, shell_api.ark_tiler())
            else:
                G().export.write_emerald(route, final, out, primary, secondary, borders=tl.at.m.get("borders"))
        except Exception as e:  # export.py is in flux: fall back to just the layout files
            traceback.print_exc()
            note = f"pokeroutes.export.write_emerald failed ({type(e).__name__}: {e}); only the layout was written."
            lay = out / "data/layouts" / route.spec.id
            lay.mkdir(parents=True, exist_ok=True)
            (lay / "map.bin").write_bytes(final.to_blockdata())
            if gid in _ark_ids:
                import shell_api
                from pokeroutes.ruletile import PRIMARY_SYMBOL, SECONDARY_SYMBOL

                border = shell_api.ark_tiler().border()
                primary_symbol, secondary_symbol = PRIMARY_SYMBOL, SECONDARY_SYMBOL
            else:
                border = (tl.at.m.get("borders") or {}).get("f") or [0x1D4, 0x1D5, 0x1DC, 0x1DD]
                primary_symbol, secondary_symbol = f"gTileset_{primary.title()}", f"gTileset_{secondary.title()}"
            (lay / "border.bin").write_bytes(np.array(border, dtype="<u2").tobytes())
            (lay / "layouts_entry.json").write_text(json.dumps({
                "id": f"LAYOUT_{route.spec.id.upper()}", "name": f"{route.spec.id}_Layout",
                "width": route.w, "height": route.h,
                "primary_tileset": primary_symbol, "secondary_tileset": secondary_symbol,
                "border_filepath": f"data/layouts/{route.spec.id}/border.bin",
                "blockdata_filepath": f"data/layouts/{route.spec.id}/map.bin", "layout_version": "emerald"}, indent=2))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(out.rglob("*")):
                if f.is_file():
                    z.write(f, f"{route.spec.id}/{f.relative_to(out)}")
            z.writestr(f"{route.spec.id}/spec.json", json.dumps(spec_json(route.spec), indent=2, default=str))
            if note:
                z.writestr(f"{route.spec.id}/EXPORT_NOTE.txt", note + "\n")
    return f"{route.spec.id}_porymap.zip", buf.getvalue()


def options() -> dict:
    try:
        return _options()
    except Exception as e:
        traceback.print_exc()
        return {"archetypes": ["grove", "hollow", "meadow", "pond", "stones"], "species": [], "warning": str(e)}


def _options() -> dict:
    pl = G().places
    areas = pl.load_species_areas(SPECIES_FILE)
    arche = set(getattr(pl, "ARCHETYPES", {})) | {a["archetype"] for a in pl.BUILTIN + areas}
    return {
        "archetypes": sorted(arche),
        "species": sorted({a["species"] for a in areas}),
        "zone_colors": {k: "#%02x%02x%02x" % v for k, v in getattr(G().export, "ZONE_COLORS", {}).items()},
    }


# ------------------------------------------------------------------ http

TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml", ".md": "text/plain"}


class Handler(BaseHTTPRequestHandler):
    server_version = "pokeroutes-webui"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    def _send(self, code, body: bytes, ctype: str, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, separators=(",", ":"), default=int).encode(), "application/json")

    def do_GET(self):
        u = urlparse(self.path)
        try:
            if u.path in ("/api/atlas", "/api/atlas.png"):
                ts = parse_qs(u.query).get("ts", [TILESET])[0]
                if not ("_" in ts and ts.replace("_", "").isalnum() and (MODELS / f"{ts}.json").is_file()):
                    return self._json({"error": "unknown tileset"}, 404)
                a = atlas(ts)
                return self._json(a[1]) if u.path == "/api/atlas" else self._send(200, a[0], "image/png")
            if u.path == "/api/options":
                return self._json(options())
            if u.path == "/api/spec-schema":
                return self._json(spec_schema())
            if u.path == "/api/export":
                gid = parse_qs(u.query).get("id", [""])[0]
                if gid not in _results:
                    return self._json({"error": "unknown or expired route id; generate again"}, 404)
                try:
                    name, data = export_zip(gid)
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
                return self._send(200, data, "application/zip",
                                  {"Content-Disposition": f'attachment; filename="{name}"'})
            if u.path.startswith("/api/editor"):
                import editor_api  # region editor (webui/editor_api.py)
                return editor_api.get(self, u)
            if u.path.startswith("/api/region"):
                import region_api  # region page (webui/region_api.py)
                return region_api.handle(self, u, STATIC)
            rel = "shell.html" if u.path in ("/", "") else u.path.lstrip("/")
            f = (STATIC / rel).resolve()
            if STATIC in f.parents and f.is_file():
                return self._send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))
            self._json({"error": "not found"}, 404)
        except Exception as e:  # pragma: no cover
            traceback.print_exc()
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path.startswith("/api/editor/") or u.path == "/api/retile":
            import editor_api  # region editor + bench paint (webui/editor_api.py)
            try:
                return editor_api.post(self, u, sys.modules[__name__])
            except Exception as e:
                traceback.print_exc()
                return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        if u.path != "/api/generate":
            return self._json({"error": "not found"}, 404)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            params = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(params, dict):
                raise ValueError("body must be a JSON object")
        except ValueError as e:
            return self._json({"error": f"bad request: {e}"}, 400)
        try:
            self._json(generate(params))
        except Exception as e:
            traceback.print_exc()
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)


# ------------------------------------------------------------------ improvements box (webui/improve.py)
# Routes /api/improve* to improve.py before the normal handlers; everything else is untouched.

def _with_improve(orig, method):
    def handler(self):
        u = urlparse(self.path)
        if u.path == "/api/improve" or u.path.startswith("/api/improve/"):
            import improve
            try:
                return improve.handle(self, method, u)
            except Exception as e:
                traceback.print_exc()
                return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        return orig(self)
    return handler


Handler.do_GET = _with_improve(Handler.do_GET, "GET")
Handler.do_POST = _with_improve(Handler.do_POST, "POST")

# editor shell (webui/shell_api.py): the entry page, old-URL redirects, Ark tiler atlas,
# scene.json / region-spec exports and spec-file parsing; everything else passes through.
import shell_api  # noqa: E402

Handler.do_GET = shell_api.wrap(Handler.do_GET, "GET", sys.modules[__name__])
Handler.do_POST = shell_api.wrap(Handler.do_POST, "POST", sys.modules[__name__])


def main():
    G()
    tiler()
    pair()
    atlas()
    pool().submit(int, 0).result()  # spin the workers up before the first request
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"poke-routes web UI on http://localhost:{PORT}", flush=True)
    import improve  # take the improve-queue lock (or show it read-only) and resume queued jobs
    improve.start(PORT)
    srv.serve_forever()


if __name__ == "__main__":
    main()
