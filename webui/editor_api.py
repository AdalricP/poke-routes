"""Region editor + bench paint backend (kept out of server.py, like region_api.py).

POST /api/editor/generate   {seed, geography?} -> editor document for a fresh region (no towns yet)
                            geography: continent | coast | ... (generic only; default: the seed picks)
GET  /api/editor/list                              -> saved regions under region_out/
POST /api/editor/load       {name}                 -> document from region_out/<name>/region.json
POST /api/editor/recompute  {doc, stages: [...]}   -> document with those stages re-run
                            stages: water | climate | biomes | routes | all
                            routes: towns and routes derived again from the painted Map grid
POST /api/editor/generate-map {doc}                -> document with towns and metro-style routes painted
                            on the Map grid (unpinned towns and routes replaced, pinned kept);
                            "message" for the log
POST /api/editor/paint-map  {doc, cells, brush, route_key?} -> document with Map-grid cells [[gx, gy], ...]
                            painted (brush route | town | erase); neighbouring town cells are one town;
                            route cells belong to the route they were painted for (route_key: a route's
                            grid_key or "new"; left out, a stroke starting on a route extends it, else a
                            new route); each route carries its cells in path order (grid_cells)
POST /api/editor/regrid-map {doc, cell}            -> document with towns and routes snapped onto a Map grid
                            of `cell` region cells a square (4, 6, 8, 10, 12 or 16)
POST /api/editor/resize-region {doc, width, height} -> document grown or cropped to width x height Map squares;
                            new area gets land and sea continuing the old coast, places outside are dropped
POST /api/editor/save       {doc, name}            -> writes region_out/<name>/ (region.json, routes/*.yaml)
POST /api/editor/spec       {doc, id}              -> one route's full spec (with its region block)
POST /api/editor/route-chain-specs {doc, id}       -> {route, maps: [{name, rect, spec}]}: the full spec of every map of
                            the route holding `id` (a route or one of its chain maps), in chain order
POST /api/editor/render     {doc, scale}           -> PNG of the edited region (server-side render)
POST /api/editor/export     {doc}                  -> zip: region.json + routes/*.yaml
POST /api/retile            {id, cells: [[x, y, cls], ...]}
                            bench paint: set classes on a generated route and re-tile it;
                            the export zip then carries the painted map. A cell given both a
                            class and a "#metatile" is an object (a whole tree): it gets that
                            exact metatile and its neighbours keep theirs. Classes are the
                            ones the route's tiler takes (paint_palette.paintable_class_codes);
                            "o" only on an object cell, which is then blocked
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
highest_metatile_id = 1023
OBJECT_CLASS = "o"
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
            geography_kind = b.get("geography") or None
            return h._json(edit.generate_doc(seed, geography_kind=geography_kind))
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
        if p == "/api/editor/generate-map":
            return h._json(edit.generate_map(b["doc"]))
        if p == "/api/editor/regrid-map":
            return h._json(edit.regrid_map(b["doc"], int(b.get("cell") or 0)))
        if p == "/api/editor/resize-region":
            return h._json(edit.resize_region(b["doc"], int(b.get("width") or 0), int(b.get("height") or 0)))
        if p == "/api/editor/paint-map":
            return h._json(edit.paint_map(b["doc"], list(b.get("cells") or []), str(b.get("brush") or ""), b.get("route_key") or None))
        if p == "/api/editor/save":
            name = _name(b.get("name") or b["doc"].get("name"))
            with _lock:
                paths = edit.save(b["doc"], OUT / name)
            return h._json({"name": name, "dir": str(OUT / name), "routes": len(paths["routes"])})
        if p == "/api/editor/spec":
            s = edit.full_spec(b["doc"], str(b.get("id", "")))
            return h._json(s) if s else h._json({"error": "unknown route"}, 404)
        if p == "/api/editor/route-chain-specs":
            chain_of_route = edit.route_chain_specs(b["doc"], str(b.get("id", "")))
            return h._json(chain_of_route) if chain_of_route else h._json({"error": "unknown route"}, 404)
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
    except edit.RegionEditRefused as refusal:
        return h._json({"error": str(refusal)}, 400)
    except (KeyError, ValueError, TypeError) as e:
        import traceback

        traceback.print_exc()
        return h._json({"error": f"{type(e).__name__}: {e}"}, 400)
    return h._json({"error": "not found"}, 404)


def retile(srv, b: dict) -> dict:
    gid = str(b.get("id", ""))
    if gid not in srv._results:
        raise ValueError("unknown or expired route id; generate again")
    import paint_palette

    route, final = srv._results[gid]
    allowed_class_codes = paint_palette.paintable_class_codes(srv, gid)
    class_cells, metatile_cells = split_painted_cells_into_classes_and_metatiles(b.get("cells") or [], route.cls.shape, allowed_class_codes)

    painted_class_grid = route.cls.copy()
    for cell_x, cell_y, class_code in class_cells:
        painted_class_grid[cell_y, cell_x] = class_code
    painted_ledges = any(class_code in LEDGE_CLASSES for _, _, class_code in class_cells)
    if painted_ledges and not route_tiler_joins_ledge_corners(srv, gid):
        painted_class_grid = defuse_ledge_corners(painted_class_grid)
    object_cells = {(x, y) for x, y, _ in class_cells} & {(x, y) for x, y, _ in metatile_cells}
    layout = retile_class_grid_with_route_tiler(srv, gid, route, final, painted_class_grid, object_cells)
    metatile_overrides = metatile_overrides_by_route_id.setdefault(gid, {})
    for cell_x, cell_y, _class_code in class_cells:
        metatile_overrides.pop((cell_x, cell_y), None)
    metatile_overrides.update({(cell_x, cell_y): metatile_id for cell_x, cell_y, metatile_id in metatile_cells})
    blocked_class_codes = paint_palette.blocked_class_codes(srv, gid)
    for (cell_x, cell_y), metatile_id in metatile_overrides.items():
        apply_exact_metatile(layout, cell_x, cell_y, metatile_id, painted_class_grid[cell_y, cell_x] in blocked_class_codes)
    retiled_cell_count = int((layout.metatile != final.metatile).sum())
    route.cls = painted_class_grid
    srv._results[gid] = (route, layout)

    retile_result = {"id": gid, "painted": len(class_cells) + len(metatile_cells), "cls": ["".join(row) for row in painted_class_grid],
                     "tiles": layout.metatile.astype(int).ravel().tolist(), "retiled": retiled_cell_count}
    return retile_result


def apply_exact_metatile(layout, cell_x, cell_y, metatile_id, is_blocked) -> None:
    layout.metatile[cell_y, cell_x] = metatile_id
    if not is_blocked:
        return
    layout.collision[cell_y, cell_x] = 1
    layout.elevation[cell_y, cell_x] = 0


def split_painted_cells_into_classes_and_metatiles(painted_cells, grid_shape, allowed_class_codes) -> tuple:
    grid_height, grid_width = grid_shape
    class_cells, metatile_cells = [], []
    for painted_cell in painted_cells[:20000]:
        cell_x, cell_y, painted_value = int(painted_cell[0]), int(painted_cell[1]), str(painted_cell[2])
        if not (0 <= cell_y < grid_height and 0 <= cell_x < grid_width):
            continue
        if painted_value in allowed_class_codes or painted_value == OBJECT_CLASS:
            class_cells.append((cell_x, cell_y, painted_value))
            continue
        if painted_value.startswith("#") and painted_value[1:].isdigit() and int(painted_value[1:]) <= highest_metatile_id:
            metatile_cells.append((cell_x, cell_y, int(painted_value[1:])))
    metatile_positions = {(cell_x, cell_y) for cell_x, cell_y, _metatile_id in metatile_cells}
    class_cells = [cell for cell in class_cells if cell[2] != OBJECT_CLASS or cell[:2] in metatile_positions]
    painted_cells_by_kind = (class_cells, metatile_cells)
    return painted_cells_by_kind


def route_tiler_joins_ledge_corners(srv, gid) -> bool:
    if gid not in srv._ark_ids:
        return False
    import shell_api

    return shell_api.ark_tiler().joins_ledge_corners()


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
    tiling_grid = painted_class_grid.copy()
    exact_positions = set(object_cells) | set(metatile_overrides_by_route_id.get(gid, {}))
    for cell_x, cell_y in exact_positions:
        if tiling_grid[cell_y, cell_x] == OBJECT_CLASS:
            tiling_grid[cell_y, cell_x] = "g"
    learned_layout = srv.tiler(srv.route_tileset(route.spec)).tile(tiling_grid, seed=route_seed, fixed=fixed_metatiles)
    # newly (re)tiled grass lands on the plain metatile; texture it like a fresh
    # generate() does, or a painted island's grass stays a flat, untextured block
    learned_layout = srv.G().ground.texture_open_ground(learned_layout, painted_class_grid, route_seed)
    return learned_layout
