#!/usr/bin/env python3
"""Route tools for the headless agent runs (Agent tab / Improve box): a tiny stdio MCP
server, stdlib only, so the agent can tool-call the generator instead of scripting it.

    python3 webui/route_tools.py       (improve.py passes it to `claude` via --mcp-config)

Everything runs in-process on the code of the checkout this file sits in (the job's
worktree), and pokeroutes is re-imported on every generate, so generator edits show up
without restarting anything. Output goes under out/agent/ (gitignored). No network.

Tools: list_specs, spec_fields, generate_route, route_grid, paint_route, generate_region,
check_examples. Generated routes are kept in memory by id for route_grid / paint_route.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import io
import json
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "agent"
EXAMPLES = ("bernsteinweg", "erlenbruch", "kiefernheide")
_routes: dict = {}   # id -> (spec, route, tiler)
_wire = sys.stdout   # MCP messages only; anything the generator prints goes to stderr
sys.stdout = sys.stderr
sys.path.insert(0, str(ROOT))


def _fresh():
    """Re-import pokeroutes so edits made during the job are used."""
    for k in [k for k in sys.modules if k == "pokeroutes" or k.startswith("pokeroutes.")]:
        del sys.modules[k]
    from pokeroutes import cli
    return cli


def _inside(p: str, base: Path = ROOT) -> Path:
    q = (ROOT / p).resolve()
    if base.resolve() not in (q, *q.parents):
        raise ValueError(f"{p} is outside {base}")
    return q


def _write(route_id: str, spec, route, tiler: str) -> dict:
    cli = _fresh()
    out = OUT / route_id
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        cli._generate_and_write(spec, argparse.Namespace(out=str(out), stages=True, tileset=tiler), route=route)
    rep = json.loads((out / "report.json").read_text())
    return {"id": route_id, "summary": log.getvalue().strip().splitlines()[0] if log.getvalue().strip() else "",
            "size": [route.w, route.h], "report": rep,
            "images": {n: str(out / f) for n, f in (("tiled", "emerald.png"), ("plan", "plan.png"), ("stages", "stages.png"))
                       if (out / f).exists()},
            "emerald_dir": str(out / "emerald")}


# ------------------------------------------------------------------ tools

def list_specs(a):
    specs = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "examples").glob("*.yaml"))
    specs += sorted(str(p.relative_to(ROOT)) for p in (ROOT / "region_out").glob("*/routes/*.yaml"))
    return {"specs": specs, "generated": sorted(_routes)}


def spec_fields(a):
    _fresh()
    from pokeroutes import places, spec
    from pokeroutes.classify import CLASS_NAMES

    def dflt(f):
        if f.default is not dataclasses.MISSING:
            return f.default
        if f.default_factory is not dataclasses.MISSING:
            return f.default_factory()
        return None
    return {"fields": {f.name: dflt(f) for f in dataclasses.fields(spec.RouteSpec)},
            "exit_fields": {f.name: dflt(f) for f in dataclasses.fields(spec.Exit)},
            "biomes": list(spec.BIOMES), "archetypes": sorted(getattr(places, "ARCHETYPES", {})),
            "classes": CLASS_NAMES}


def generate_route(a):
    _fresh()
    from pokeroutes.export import seal_edges
    from pokeroutes.generate import generate
    from pokeroutes.spec import Exit, RouteSpec

    spec = RouteSpec.load(_inside(a.get("spec") or "examples/bernsteinweg.yaml"))
    known = {f.name for f in dataclasses.fields(RouteSpec)}
    for k, v in (a.get("overrides") or {}).items():
        if k not in known:
            raise ValueError(f"unknown spec field {k!r} (see spec_fields)")
        setattr(spec, k, [Exit(**e) for e in v] if k == "exits" else v)
    if a.get("seed") is not None:
        spec.seed = int(a["seed"])
    route = generate(spec)
    seal_edges(route)
    tiler = "ark" if a.get("tiler") == "ark" else "learned"
    rid = f"{spec.id}-{spec.seed}" + ("-ark" if tiler == "ark" else "")
    _routes[rid] = (spec, route, tiler)
    return _write(rid, spec, route, tiler)


def _get(a):
    rid = str(a.get("id", ""))
    if rid not in _routes:
        raise ValueError(f"no generated route {rid!r}; known: {sorted(_routes)}")
    return rid, _routes[rid]


def route_grid(a):
    rid, (_spec, route, _t) = _get(a)
    x0, y0 = max(0, int(a.get("x0", 0))), max(0, int(a.get("y0", 0)))
    x1, y1 = min(route.w, int(a.get("x1", route.w))), min(route.h, int(a.get("y1", route.h)))
    rows = ["".join(r[x0:x1]) for r in route.cls[y0:y1]]
    return {"id": rid, "origin": [x0, y0], "rows": rows,
            "zones": [{"kind": z.kind, **{k: str(v) for k, v in getattr(z, "meta", {}).items() if k != "area"}}
                      for z in route.zones],
            "events": [{"kind": e.kind, "x": int(e.x), "y": int(e.y)} for e in route.events]}


def paint_route(a):
    rid, (spec, route, tiler) = _get(a)
    h, w = route.cls.shape
    n = 0
    for c in a.get("cells") or []:
        x, y, k = int(c[0]), int(c[1]), str(c[2])
        if 0 <= x < w and 0 <= y < h and len(k) == 1:
            route.cls[y, x] = k
            n += 1
    for r in a.get("rects") or []:
        x0, y0, x1, y1, k = int(r[0]), int(r[1]), int(r[2]), int(r[3]), str(r[4])
        if len(k) == 1:
            route.cls[max(0, y0):min(h, y1 + 1), max(0, x0):min(w, x1 + 1)] = k
            n += max(0, min(w, x1 + 1) - max(0, x0)) * max(0, min(h, y1 + 1) - max(0, y0))
    res = _write(rid, spec, route, tiler)
    res["painted"] = n
    return res


def generate_region(a):
    cli = _fresh()
    specs = [str(_inside(s)) for s in a.get("specs") or []]
    if not specs:
        raise ValueError("give specs: a list of spec paths")
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        cli.cmd_region(argparse.Namespace(specs=specs, stages=False, viewer=False,
                                          tileset="ark" if a.get("tiler") == "ark" else "learned",
                                          special_budget=a.get("special_budget")))
    return {"log": log.getvalue().strip().splitlines()}


def check_examples(a):
    res = {}
    for name in EXAMPLES:
        p = subprocess.run([sys.executable, "-m", "pokeroutes", "generate", f"examples/{name}.yaml"],
                           cwd=ROOT, capture_output=True, text=True, timeout=600)
        res[name] = {"ok": p.returncode == 0,
                     "output": (p.stdout + p.stderr).strip().splitlines()[-4:]}
    return res


S, I, N, O = {"type": "string"}, {"type": "integer"}, {"type": "number"}, {"type": "object"}
TOOLS = {
    "list_specs": (list_specs, "List route spec files (examples/, region_out/) and routes generated in this session.", {}),
    "spec_fields": (spec_fields, "Every RouteSpec field with its default, exit fields, biomes, special-area "
                                 "archetypes and the class codes used in the route grid.", {}),
    "generate_route": (generate_route, "Generate one route from a spec file, like `python3 -m pokeroutes generate`, "
                       "with optional field overrides. Writes the tiled render (tiled), the class plan and a stage sheet "
                       "as PNGs plus the Emerald files under out/agent/<id>/ and returns their paths, the route report "
                       "and an id for route_grid / paint_route. Read the PNGs to look at the result.",
                       {"spec": {**S, "description": "spec path relative to the repo, default examples/bernsteinweg.yaml"},
                        "seed": I, "overrides": {**O, "description": "spec fields to change, e.g. {\"branches\": 3, \"lakes\": 0}"},
                        "tiler": {**S, "enum": ["learned", "ark"]}}),
    "route_grid": (route_grid, "The class grid (one char per metatile, see spec_fields.classes) of a generated route, "
                   "optionally just the box x0..x1, y0..y1 (exclusive), with its zones and events.",
                   {"id": S, "x0": I, "y0": I, "x1": I, "y1": I}),
    "paint_route": (paint_route, "Hand-fix a generated route: set classes on cells ([x, y, cls]) and/or filled "
                    "rectangles ([x0, y0, x1, y1, cls], inclusive), then re-tile and re-export it (same outputs as "
                    "generate_route). Lasts for this session only; to keep a fix, change the spec or the generator.",
                    {"id": S, "cells": {"type": "array", "items": {"type": "array"}},
                     "rects": {"type": "array", "items": {"type": "array"}}}),
    "generate_region": (generate_region, "Generate several specs as one region, like `python3 -m pokeroutes region` "
                        "(no repeated special areas, exits lined up). Output goes to out/<route id>/.",
                        {"specs": {"type": "array", "items": S}, "tiler": {**S, "enum": ["learned", "ark"]},
                         "special_budget": I}),
    "check_examples": (check_examples, "Run the three example specs through the CLI (the standard check before "
                       "committing) and report which pass.", {}),
}


# ------------------------------------------------------------------ MCP over stdio

def _send(msg):
    _wire.write(json.dumps(msg, default=str) + "\n")
    _wire.flush()


def handle(req):
    m, rid = req.get("method"), req.get("id")
    if rid is None:
        return None   # notifications (initialized, cancelled) need no answer
    if m == "initialize":
        return {"protocolVersion": (req.get("params") or {}).get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}}, "serverInfo": {"name": "poke-routes", "version": "0.1"}}
    if m == "tools/list":
        return {"tools": [{"name": n, "description": d,
                           "inputSchema": {"type": "object", "properties": p}} for n, (_f, d, p) in TOOLS.items()]}
    if m == "tools/call":
        p = req.get("params") or {}
        if p.get("name") not in TOOLS:
            return {"content": [{"type": "text", "text": f"unknown tool {p.get('name')}"}], "isError": True}
        try:
            out = TOOLS[p["name"]][0](p.get("arguments") or {})
            return {"content": [{"type": "text", "text": json.dumps(out, default=str)}]}
        except Exception as e:
            traceback.print_exc()
            return {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
    if m == "ping":
        return {}
    raise KeyError(m)


def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        try:
            res = handle(req)
        except KeyError as e:
            _send({"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": -32601, "message": f"no method {e}"}})
            continue
        if res is not None:
            _send({"jsonrpc": "2.0", "id": req["id"], "result": res})


if __name__ == "__main__":
    main()
