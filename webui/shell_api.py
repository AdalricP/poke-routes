"""Editor shell backend (kept out of server.py, like region_api.py / improve.py).

Pages
    GET /                        static/shell.html (the editor app; also /?region=..., /?view=...)
    GET /index.html              302 -> /            (old route bench URL)
    GET /region.html             302 -> /?view=region (old region page URL)

API
    GET  /api/atlas?tiler=ark    metatile atlas for the PokeRoutes tilesets (learned atlas: no query)
    GET  /api/atlas.png?tiler=ark
    GET  /api/tilers             which tilers this server can use
    GET  /api/paint-palette?id=ID   what the Tiles tab can paint on that route (paint_palette.py)
    GET  /api/scene?id=ID        scene.json of a generated route (attachment)
    GET  /api/region-specs.zip?seed=N   region.json + routes/*.yaml of a region
    POST /api/spec-parse {text}  parse a spec file (YAML or JSON) into an object
    POST /api/retile             refused for Ark-tiled routes (the paint backend re-tiles
                                 with the learned tiler only); everything else passes through
"""
from __future__ import annotations

import io
import json
import threading
import traceback
import zipfile
from pathlib import Path
from urllib.parse import parse_qs

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
ROOT = HERE.parent
MAX_SPEC = 512 * 1024

_ark = None
_ark_atlases: dict = {}
_ark_overlay_token = None
_ark_tilers_by_secondary: dict = {}
_lock = threading.Lock()


# ------------------------------------------------------------------ Ark tiler

def ark_tiler(secondary: str = ""):
    global _ark, _ark_overlay_token
    from pokeroutes.tileset_overlay import ARK_TILESET_PAIR_NAME, overlay_file_change_token

    with _lock:
        overlay_token = overlay_file_change_token(ARK_TILESET_PAIR_NAME)
        if _ark is None or overlay_token != _ark_overlay_token:
            from pokeroutes.ruletile import ArkTiler

            _ark, _ark_overlay_token = ArkTiler.load(), overlay_token
            _ark_atlases.clear()
            _ark_tilers_by_secondary.clear()
        if not secondary or secondary == _ark.secondary or secondary not in _ark.full_class_table.get("secondary_tilesets", {}):
            return _ark
        if secondary not in _ark_tilers_by_secondary:
            _ark_tilers_by_secondary[secondary] = _ark.for_secondary(secondary)
        return _ark_tilers_by_secondary[secondary]


def ark_secondary_for_spec(spec) -> str:
    from pokeroutes.ruletile_secondaries import secondary_for_spec

    chosen_secondary = secondary_for_spec(ark_tiler().full_class_table, spec)
    return chosen_secondary


def ark_tiler_for_spec(spec):
    tiler_for_spec = ark_tiler(ark_secondary_for_spec(spec))
    return tiler_for_spec


def ark_available() -> tuple:
    try:
        ark_tiler()
        return True, ""
    except Exception as e:  # no ROM tree with the Ark tilesets on this machine
        return False, f"{type(e).__name__}: {e}"


def ark_tiles(route, warnings):
    """(final Layout, [metatile grid per snapshot], seconds spent on the final layout)
    with the Ark rule tiler."""
    import time

    import numpy as np
    from pokeroutes.ruletile import tile_route

    t = ark_tiler_for_spec(route.spec)
    t0 = time.time()
    final = tile_route(t, route)
    t_final = time.time() - t0
    tiles = []
    for _name, cls in route.snapshots:
        if cls.shape != route.cls.shape:
            tiles.append(None)
        elif np.array_equal(cls, route.cls):
            tiles.append(final.metatile)
        else:
            tiles.append(t.tile(cls).metatile)
    return final, tiles, t_final


def ark_atlas_url(shade: str, png: bool = False, secondary: str = "") -> str:
    import tile_looks
    from urllib.parse import urlencode

    from pokeroutes.ruletile_secondaries import WOODLAND_SECONDARY

    query_fields = {"tiler": "ark", "shade": shade, "v": tile_looks.version()}
    if secondary and secondary != WOODLAND_SECONDARY:
        query_fields["secondary"] = secondary
    query = urlencode(query_fields)
    url = ("/api/atlas.png?" if png else "/api/atlas?") + query
    return url


def ark_default_tile_motions(secondary: str = ""):
    from pokeroutes.breeze import breeze_plan_for_ark_class_table
    from pokeroutes.idle_motion import default_motions, default_prop_motions_from_ark_class_table, prop_cells_from_ark_class_table

    class_table = ark_tiler(secondary).c
    game_animated = ark_tiler(secondary).pair().animated_metatiles()
    defaults = default_motions(breeze_plan_for_ark_class_table(class_table), default_prop_motions_from_ark_class_table(class_table), game_animated)
    return defaults, prop_cells_from_ark_class_table(class_table)


def ark_atlas(srv, shade: str, secondary: str = ""):
    import tile_looks
    from pokeroutes.breeze import breeze_plan_for_ark_class_table
    from pokeroutes.idle_motion import motions_with_overrides
    from pokeroutes.tree_shade import ShadedTilesetPair, floor_plan_for_shade

    tiler = ark_tiler(secondary)
    cache_key = (shade, tiler.secondary)
    with _lock:
        cached = _ark_atlases.get(cache_key)
    if cached is not None:
        return cached
    breeze_plan = breeze_plan_for_ark_class_table(tiler.c)
    defaults, prop_cells_by_metatile = ark_default_tile_motions(tiler.secondary)
    tileset_pair = tiler.pair()
    motions = motions_with_overrides(defaults, tile_looks.motion_overrides("ark"), prop_cells_by_metatile,
                                     set(tileset_pair.animated_metatiles()))
    shaded_pair = ShadedTilesetPair(tileset_pair, floor_plan_for_shade(tiler.c, shade), tiler.grass)
    cached = srv.build_atlas(shaded_pair, tiler.primary_symbol, tiler.secondary_symbol,
                             ark_atlas_url(shade, png=True, secondary=tiler.secondary),
                             breeze_plan, motions, prop_cells_by_metatile, {"shade": shade, "secondary": tiler.secondary})
    with _lock:
        _ark_atlases[cache_key] = cached
    return cached


def forget_ark_atlases() -> None:
    with _lock:
        _ark_atlases.clear()


# ------------------------------------------------------------------ helpers

def _attachment(h, name: str, data: bytes, ctype: str):
    return h._send(200, data, ctype, {"Content-Disposition": f'attachment; filename="{name}"'})


def _seed(q) -> int:
    try:
        return max(0, min(2 ** 31 - 1, int(q.get("seed", ["1"])[0])))
    except ValueError:
        return 1


def _redirect(h, where: str):
    h.send_response(302)
    h.send_header("Location", where)
    h.send_header("Content-Length", "0")
    h.end_headers()


# ------------------------------------------------------------------ routing

def handle(h, method: str, u, srv) -> bool:
    """Serve the request and return True, or return False to let server.py handle it."""
    q = parse_qs(u.query)
    import people_and_events_api
    import route_items

    if people_and_events_api.handle(h, method, u, q, srv):
        return True
    if route_items.handle(h, method, u, q, srv):
        return True
    if u.path.startswith("/api/tile-editor/"):
        import tile_overrides

        return tile_overrides.handle(h, method, u, q, srv)
    import playtest_api

    if playtest_api.handle(h, method, u, q, srv):
        return True
    import tile_looks

    if tile_looks.handle(h, method, u, q, srv):
        return True
    if method == "GET":
        if u.path in ("/", ""):
            return h._send(200, (STATIC / "shell.html").read_bytes(), "text/html; charset=utf-8") or True
        if u.path == "/index.html":
            rest = ("?" + u.query) if u.query else ""
            return _redirect(h, "/" + rest) or True
        if u.path == "/region.html":
            return _redirect(h, "/?view=region") or True
        if u.path in ("/api/atlas", "/api/atlas.png") and q.get("tiler", [""])[0] == "ark":
            ok, err = ark_available()
            if not ok:
                return h._json({"error": "PokeRoutes tiler unavailable: " + err}, 404) or True
            from pokeroutes.tree_shade import SHADE_CHOICES

            requested_shade = q.get("shade", ["leaves"])[0]
            png, meta = ark_atlas(srv, requested_shade if requested_shade in SHADE_CHOICES and requested_shade != "auto" else "leaves",
                                  q.get("secondary", [""])[0])
            if u.path == "/api/atlas.png":
                return h._send(200, png, "image/png") or True
            return h._json(meta) or True
        if u.path == "/api/paint-palette":
            import paint_palette

            return paint_palette.handle_get(h, q, srv)
        if u.path == "/api/tilers":
            ok, err = ark_available()
            return h._json({"tilers": [
                {"id": "learned", "label": "Learned (Emerald General + Fortree)", "ok": True},
                {"id": "ark", "label": "PokeRoutes tiles", "ok": ok, "error": err},
            ]}) or True
        if u.path == "/api/scene":
            gid = q.get("id", [""])[0]
            if gid not in srv._results:
                return h._json({"error": "unknown or expired route id; generate again"}, 404) or True
            route, _final = srv._results[gid]
            scene = srv.G().export.scene_dict(route)
            body = json.dumps(scene, separators=(",", ":"), default=int).encode()
            return _attachment(h, f"{route.spec.id}_scene.json", body, "application/json") or True
        if u.path == "/api/region-specs.zip":
            import region_api

            seed = _seed(q)
            d = region_api._ensure(seed)
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for f in sorted(d.rglob("*")):
                    if f.is_file() and f.suffix in (".json", ".yaml"):
                        z.write(f, f"region-{seed}/{f.relative_to(d)}")
            return _attachment(h, f"region-{seed}_specs.zip", buf.getvalue(), "application/zip") or True
        return False

    if method == "POST":
        if u.path == "/api/spec-parse":
            try:
                n = int(h.headers.get("Content-Length") or 0)
                if n > MAX_SPEC:
                    raise ValueError("spec file too large")
                body = json.loads(h.rfile.read(n) or b"{}")
                text = body.get("text") if isinstance(body, dict) else None
                if not isinstance(text, str):
                    raise ValueError("expected {text: string}")
                import yaml

                spec = yaml.safe_load(text)  # JSON is YAML too
                if not isinstance(spec, dict):
                    raise ValueError("a spec file must hold a mapping")
                spec = json.loads(json.dumps(spec, default=str))
            except Exception as e:
                return h._json({"error": f"could not read the spec: {e}"}, 400) or True
            return h._json(spec) or True
        return False
    return False


def wrap(orig, method, srv):
    def handler(self):
        from urllib.parse import urlparse

        u = urlparse(self.path)
        try:
            if handle(self, method, u, srv):
                return None
        except Exception as e:
            traceback.print_exc()
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        return orig(self)
    return handler
