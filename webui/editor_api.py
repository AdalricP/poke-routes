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
                            the export zip then carries the painted map. A cell given both a
                            class and a "#metatile" is an object (a whole tree): it gets that
                            exact metatile and its neighbours keep theirs
"""
from __future__ import annotations

import copy
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
from pokeroutes.classify import LEDGE_CLASSES, defuse_ledge_corners  # noqa: E402
OUT = ROOT / "region_out"
MAX_BODY = 48 * 1024 * 1024
PAINT_CLASSES = set("gptwfrsexaqdbhyvkzlnu")  # every class but "o" (other)
highest_metatile_id = 1023
metatile_overrides_by_route_id: dict = {}
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
    from urllib.parse import urlparse

    from pokeroutes.region import edit

    # only this page may post: a JSON body forces a CORS preflight (which fails) and a
    # foreign Origin is refused, so another site can't write files through the browser
    origin = h.headers.get("Origin")
    if origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost"):
        return h._json({"error": "forbidden origin"}, 403)
    if not (h.headers.get("Content-Type") or "").startswith("application/json"):
        return h._json({"error": "send JSON (Content-Type: application/json)"}, 415)
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
    gid = str(b.get("id", ""))
    if gid not in srv._results:
        raise ValueError("unknown or expired route id; generate again")
    route, final = srv._results[gid]
    class_cells, metatile_cells = split_painted_cells_into_classes_and_metatiles(b.get("cells") or [], route.cls.shape)

    painted_class_grid = route.cls.copy()
    for cell_x, cell_y, class_code in class_cells:
        painted_class_grid[cell_y, cell_x] = class_code
    if any(class_code in LEDGE_CLASSES for _, _, class_code in class_cells):
        painted_class_grid = defuse_ledge_corners(painted_class_grid)
    object_cells = {(x, y) for x, y, _ in class_cells} & {(x, y) for x, y, _ in metatile_cells}
    layout = retile_class_grid_with_route_tiler(srv, gid, route, final, painted_class_grid, object_cells)
    metatile_overrides = metatile_overrides_by_route_id.setdefault(gid, {})
    for cell_x, cell_y, _class_code in class_cells:
        metatile_overrides.pop((cell_x, cell_y), None)
    metatile_overrides.update({(cell_x, cell_y): metatile_id for cell_x, cell_y, metatile_id in metatile_cells})
    for (cell_x, cell_y), metatile_id in metatile_overrides.items():
        layout.metatile[cell_y, cell_x] = metatile_id
    retiled_cell_count = int((layout.metatile != final.metatile).sum())
    route.cls = painted_class_grid
    srv._results[gid] = (route, layout)

    retile_result = {"id": gid, "painted": len(class_cells) + len(metatile_cells), "cls": ["".join(row) for row in painted_class_grid],
                     "tiles": layout.metatile.astype(int).ravel().tolist(), "retiled": retiled_cell_count}
    return retile_result


def split_painted_cells_into_classes_and_metatiles(painted_cells, grid_shape) -> tuple:
    grid_height, grid_width = grid_shape
    class_cells, metatile_cells = [], []
    for painted_cell in painted_cells[:20000]:
        cell_x, cell_y, painted_value = int(painted_cell[0]), int(painted_cell[1]), str(painted_cell[2])
        if not (0 <= cell_y < grid_height and 0 <= cell_x < grid_width):
            continue
        if painted_value in PAINT_CLASSES:
            class_cells.append((cell_x, cell_y, painted_value))
            continue
        if painted_value.startswith("#") and painted_value[1:].isdigit() and int(painted_value[1:]) <= highest_metatile_id:
            metatile_cells.append((cell_x, cell_y, int(painted_value[1:])))
    painted_cells_by_kind = (class_cells, metatile_cells)
    return painted_cells_by_kind


def retile_class_grid_with_route_tiler(srv, gid, route, final, painted_class_grid, object_cells=()):
    from fasttile import keys

    if gid in srv._ark_ids:
        import shell_api
        from pokeroutes.ruletile import tile_route

        route_with_painted_classes = copy.copy(route)
        route_with_painted_classes.cls = painted_class_grid
        ark_layout = tile_route(shell_api.ark_tiler(), route_with_painted_classes)
        return ark_layout
    # an object's own class change must not re-tile the cells around it (a painted tree
    # would sprout half-crowns on the grass below it)
    context_grid = painted_class_grid.copy()
    for cell_x, cell_y in object_cells:
        context_grid[cell_y, cell_x] = route.cls[cell_y, cell_x]
    unchanged_neighbourhoods = np.array(keys(context_grid)) == np.array(keys(route.cls))
    fixed_metatiles = np.where(unchanged_neighbourhoods, final.metatile.astype(np.int64), -1)
    route_seed = int(getattr(route.spec, "seed", 0))
    learned_layout = srv.tiler(srv.route_tileset(route.spec)).tile(painted_class_grid, seed=route_seed, fixed=fixed_metatiles)
    # newly (re)tiled grass lands on the plain metatile; texture it like a fresh
    # generate() does, or a painted island's grass stays a flat, untextured block
    learned_layout = srv.G().ground.texture_open_ground(learned_layout, painted_class_grid, route_seed)
    return learned_layout
