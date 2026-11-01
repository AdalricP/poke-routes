"""Editor shell backend (kept out of server.py, like region_api.py / improve.py).

Pages
    GET /                        static/shell.html (the editor app; also /?region=..., /?view=...)
    GET /index.html              302 -> /            (old route bench URL)
    GET /region.html             302 -> /?view=region (old region page URL)

API
    GET  /api/atlas?tiler=ark    metatile atlas for the Arklarna tilesets (learned atlas: no query)
    GET  /api/atlas.png?tiler=ark
    GET  /api/tilers             which tilers this server can use
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
MAX_SPEC = 512 * 1024

_ark = None
_ark_atlas = None
_lock = threading.Lock()


# ------------------------------------------------------------------ Ark tiler

def ark_tiler():
    global _ark
    with _lock:
        if _ark is None:
            from pokeroutes.ruletile import ArkTiler

            _ark = ArkTiler.load()
        return _ark


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

    t = ark_tiler()
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


def ark_atlas(srv):
    global _ark_atlas
    if _ark_atlas is None:
        from pokeroutes.ruletile import PRIMARY_SYMBOL, SECONDARY_SYMBOL

        _ark_atlas = srv.build_atlas(ark_tiler().pair(), PRIMARY_SYMBOL, SECONDARY_SYMBOL, "/api/atlas.png?tiler=ark")
    return _ark_atlas


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
                return h._json({"error": "Ark tiler unavailable: " + err}, 404) or True
            png, meta = ark_atlas(srv)
            if u.path == "/api/atlas.png":
                return h._send(200, png, "image/png") or True
            return h._json(meta) or True
        if u.path == "/api/tilers":
            ok, err = ark_available()
            return h._json({"tilers": [
                {"id": "learned", "label": "Learned (Emerald General + Fortree)", "ok": True},
                {"id": "ark", "label": "Ark rule tiles (ArkOutdoor + ArkWoodland)", "ok": ok, "error": err},
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
            except Exception as e:
                return h._json({"error": f"could not read the spec: {e}"}, 400) or True
            return h._json(json.loads(json.dumps(spec, default=str))) or True
        if u.path == "/api/retile":
            # peek at the id without consuming the body for the real handler
            n = int(h.headers.get("Content-Length") or 0)
            raw = h.rfile.read(n) if n else b""
            try:
                gid = str(json.loads(raw or b"{}").get("id", ""))
            except (ValueError, AttributeError):
                gid = ""
            if gid in srv._ark_ids:
                return h._json({"error": "painting an Ark-tiled route isn't supported yet; switch the tiler to Learned"}, 400) or True
            h.rfile = io.BytesIO(raw)
            return False
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
