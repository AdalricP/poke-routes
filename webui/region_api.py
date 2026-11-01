"""Region page backend for the web UI (kept out of server.py on purpose).

GET /api/region?seed=N        region.json for that seed (generated on first use, then cached)
GET /api/region.png?seed=N    the bare map image the page draws route boxes over
GET /api/region-spec?seed=N&id=ROUTE   one route's full YAML spec as JSON
GET /?region=...              index.html with region-bridge.js injected (carries the
                              region block into /api/generate)
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "region_out"
_lock = threading.Lock()


def _seed(q) -> int:
    try:
        return max(0, min(2 ** 31 - 1, int(q.get("seed", ["1"])[0])))
    except ValueError:
        return 1


def _ensure(seed: int) -> Path:
    d = OUT / str(seed)
    with _lock:
        if not (d / "region.json").exists() or not (d / "region_map.png").exists():
            from pokeroutes.region.cli import generate

            generate(seed, d, strip=False, verbose=False)
    return d


def handle(h, u, static: Path) -> None:
    q = parse_qs(u.query)
    if u.path == "/api/region":
        d = _ensure(_seed(q))
        return h._send(200, (d / "region.json").read_bytes(), "application/json")
    if u.path == "/api/region.png":
        d = _ensure(_seed(q))
        return h._send(200, (d / "region_map.png").read_bytes(), "image/png")
    if u.path == "/api/region-spec":
        import yaml

        d = _ensure(_seed(q))
        rid = "".join(ch for ch in q.get("id", [""])[0] if ch.isalnum())
        p = d / "routes" / f"{rid}.yaml"
        if not rid or not p.exists():
            return h._json({"error": "unknown route"}, 404)
        return h._json(yaml.safe_load(p.read_text()))
    # index.html with the bridge script
    html = (static / "index.html").read_text()
    html = html.replace("</body>", '<script src="region-bridge.js"></script>\n</body>')
    return h._send(200, html.encode(), "text/html; charset=utf-8")
