"""Region editor + bench paint backend (kept out of server.py, like region_api.py).

POST /api/editor/generate   {seed}                 -> editor document for a fresh region
GET  /api/editor/list                              -> saved regions under region_out/
POST /api/editor/load       {name}                 -> document from region_out/<name>/region.json
POST /api/editor/recompute  {doc, stages: [...]}   -> document with those stages re-run
                            stages: water | climate | biomes | routes | all
POST /api/editor/add-route  {doc, src, dst}        -> document with a new link laid between two
                            places; refs are {"city": id} | {"route": name} | {"landmark": id}
POST /api/editor/save       {doc, name}            -> writes region_out/<name>/ (region.json, routes/*.yaml)
POST /api/editor/spec       {doc, id}              -> one route's full spec (with its region block)
POST /api/editor/render     {doc, scale}           -> PNG of the edited region (server-side render)
POST /api/editor/export     {doc}                  -> zip: region.json + routes/*.yaml
POST /api/retile            {id, cells: [[x, y, cls], ...]}
                            bench paint: set classes on a generated route and re-tile it;
                            the export zip then carries the painted map
"""
from __future__ import annotations

import io
import json
import re
import sys
import tempfile
import threading
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "region_out"
MAX_BODY = 48 * 1024 * 1024
PAINT_CLASSES = set("fgptwrsexa")
_lock = threading.Lock()


def _name(s) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", str(s or "")).strip("-")[:40]
    if not s:
        raise ValueError("bad region name")
    return s


def _body(h) -> dict:
    n = int(h.headers.get("Content-Length") or 0)
    if n > MAX_BODY:
        raise ValueError("request too large")
    d = json.loads(h.rfile.read(n) or b"{}")
    if not isinstance(d, dict):
        raise ValueError("body must be a JSON object")
    return d


def get(h, u) -> None:
    if u.path == "/api/editor/list":
        items = []
        if OUT.exists():
            for d in sorted(OUT.iterdir()):
                f = d / "region.json"
                if d.is_dir() and f.exists():
                    try:
                        edited = '"editor"' in f.read_text()
                    except OSError:
                        continue
                    items.append({"name": d.name, "edited": edited, "mtime": int(f.stat().st_mtime)})
        return h._json({"regions": items})
    return h._json({"error": "not found"}, 404)


def post(h, u, srv) -> None:
    from pokeroutes.region import edit

    try:
        b = _body(h)
    except ValueError as e:
        return h._json({"error": f"bad request: {e}"}, 400)
    p = u.path
    try:
        if p == "/api/editor/generate":
            seed = max(0, min(2 ** 31 - 1, int(b.get("seed", 1))))
            return h._json(edit.generate_doc(seed))
        if p == "/api/editor/load":
            f = OUT / _name(b.get("name")) / "region.json"
            if not f.exists():
                return h._json({"error": "no such region"}, 404)
            doc = edit.load(f)
            doc["name"] = f.parent.name
            return h._json(doc)
        if p == "/api/editor/recompute":
            stages = [s for s in b.get("stages", []) if s in edit.STAGES or s == "all"]
            return h._json(edit.recompute(b["doc"], stages))
        if p == "/api/editor/add-route":
            return h._json(edit.add_route(b["doc"], b.get("src") or {}, b.get("dst") or {}))
        if p == "/api/editor/save":
            name = _name(b.get("name") or b["doc"].get("name"))
            with _lock:
                paths = edit.save(b["doc"], OUT / name)
            return h._json({"name": name, "dir": str(OUT / name), "routes": len(paths["routes"])})
        if p == "/api/editor/spec":
            s = edit.full_spec(b["doc"], str(b.get("id", "")))
            return h._json(s) if s else h._json({"error": "unknown route"}, 404)
        if p == "/api/editor/render":
            scale = max(2, min(10, int(b.get("scale", 6))))
            return h._send(200, edit.render_png(b["doc"], scale), "image/png")
        if p == "/api/editor/export":
            name = _name(b["doc"].get("name") or f"seed-{b['doc'].get('seed', 0)}")
            with tempfile.TemporaryDirectory() as td:
                edit.save(b["doc"], Path(td))
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                    for f in sorted(Path(td).rglob("*")):
                        if f.is_file() and f.suffix in (".json", ".yaml"):
                            z.write(f, f"{name}/{f.relative_to(td)}")
            return h._send(200, buf.getvalue(), "application/zip",
                           {"Content-Disposition": f'attachment; filename="{name}_specs.zip"'})
        if p == "/api/retile":
            return h._json(retile(srv, b))
    except (KeyError, ValueError, TypeError) as e:
        import traceback

        traceback.print_exc()
        return h._json({"error": f"{type(e).__name__}: {e}"}, 400)
    return h._json({"error": "not found"}, 404)


def retile(srv, b: dict) -> dict:
    """Paint classes onto a generated route (the bench's last result) and re-tile.
    Cells whose 3x3 class neighbourhood didn't change keep their metatile."""
    from fasttile import keys

    gid = str(b.get("id", ""))
    if gid not in srv._results:
        raise ValueError("unknown or expired route id; generate again")
    route, final = srv._results[gid]
    cls = route.cls.copy()
    n = 0
    for c in (b.get("cells") or [])[:20000]:
        x, y, k = int(c[0]), int(c[1]), str(c[2])
        if 0 <= y < cls.shape[0] and 0 <= x < cls.shape[1] and k in PAINT_CLASSES:
            cls[y, x] = k
            n += 1
    same = np.array(keys(cls)) == np.array(keys(route.cls))
    fixed = np.where(same, final.metatile.astype(np.int64), -1)
    seed = int(getattr(route.spec, "seed", 0))
    lay = srv.tiler().tile(cls, seed=seed, fixed=fixed)
    route.cls = cls
    srv._results[gid] = (route, lay)
    return {"id": gid, "painted": n, "cls": ["".join(r) for r in cls],
            "tiles": lay.metatile.astype(int).ravel().tolist(), "retiled": int((~same).sum())}
