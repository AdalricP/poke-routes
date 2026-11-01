"""Terrain contexts: where an area can sit, how to recognise such a spot on a
generated route, and a blank canvas of that terrain for previews and sub-maps.

A context is the *surroundings* the species needs, not the area itself: a
Pikachu den wants a rocky rise inside a forest, a Mudkip slide wants a muddy
river bend, a Zubat roost wants a cave. ``score`` looks at the cells around a
candidate footprint and says how well they fit.
"""
from __future__ import annotations

import math
from typing import Dict, List, Tuple

import numpy as np

Cell = Tuple[int, int]

# context -> route biomes it occurs in, how far from the lanes, default visibility
CONTEXTS: Dict[str, dict] = {
    "deep_forest": {"biomes": ["forest"], "dist": (6, 18), "visibility": "hidden",
                    "doc": "interior of old forest, away from the road, trees on all sides"},
    "forest": {"biomes": ["forest"], "dist": (3, 12), "visibility": "glimpse",
               "doc": "ordinary woodland a little off the lane"},
    "forest_edge": {"biomes": ["forest", "meadow", "farmland"], "dist": (2, 10), "visibility": "visible",
                    "doc": "where woods meet open ground"},
    "rocky_rise": {"biomes": ["forest", "meadow", "mountain", "riverland"], "dist": (4, 14), "visibility": "glimpse",
                   "doc": "a rocky knoll or outcrop standing out of the surrounding ground"},
    "meadow": {"biomes": ["meadow", "riverland", "forest", "farmland"], "dist": (2, 10), "visibility": "visible",
               "doc": "open grassland"},
    "farmland": {"biomes": ["meadow", "farmland", "riverland"], "dist": (1, 8), "visibility": "visible",
                 "doc": "fields, pens and barns near people"},
    "town_edge": {"biomes": ["meadow", "farmland", "riverland", "forest", "coast"], "dist": (1, 8), "visibility": "visible",
                  "doc": "the outskirts of a settlement: walls, gardens, old buildings"},
    "ruins": {"biomes": ["forest", "meadow", "mountain", "riverland", "coast"], "dist": (3, 16), "visibility": "glimpse",
              "doc": "tumbled stone of an abandoned building"},
    "riverbank": {"biomes": ["riverland", "meadow", "forest"], "dist": (1, 12), "visibility": "glimpse",
                  "doc": "beside running water"},
    "lakeshore": {"biomes": ["riverland", "forest", "meadow"], "dist": (1, 12), "visibility": "visible",
                  "doc": "edge of a lake or big pond"},
    "marsh": {"biomes": ["riverland", "meadow"], "dist": (2, 14), "visibility": "glimpse",
              "doc": "wet ground, pools and reeds"},
    "cliff_base": {"biomes": ["mountain", "forest", "meadow", "coast"], "dist": (2, 12), "visibility": "glimpse",
                   "doc": "at the foot of a rock face"},
    "cliff_top": {"biomes": ["mountain", "coast"], "dist": (2, 12), "visibility": "visible",
                  "doc": "a ledge or crag high up"},
    "mountain": {"biomes": ["mountain"], "dist": (2, 14), "visibility": "glimpse", "doc": "upland rock and scree"},
    "sea_shore": {"biomes": ["coast"], "dist": (1, 12), "visibility": "visible", "doc": "beach, cove or headland"},
    "sea": {"biomes": ["coast"], "dist": (2, 20), "visibility": "visible", "doc": "out on the water"},
    "dunes": {"biomes": ["coast", "desert"], "dist": (2, 14), "visibility": "glimpse", "doc": "loose sand"},
    "volcanic": {"biomes": ["volcanic", "mountain"], "dist": (2, 14), "visibility": "glimpse", "doc": "ash and hot rock"},
    "snowfield": {"biomes": ["snow", "mountain"], "dist": (2, 14), "visibility": "glimpse", "doc": "snow above the trees"},
    "cave": {"biomes": ["cave", "mountain"], "dist": (0, 20), "visibility": "hidden", "doc": "inside rock"},
}


def lane_mask(route) -> np.ndarray:
    m = np.zeros(route.cls.shape, dtype=bool)
    for z in getattr(route, "zones", []):
        if z.kind in ("main", "loop", "branch", "dead_end"):
            for (y, x) in z.cells:
                m[y, x] = True
    for (y, x) in getattr(route, "critical_path", []) or []:
        m[y, x] = True
    return m


def lane_distance(route) -> np.ndarray:
    """Chebyshev-ish distance (BFS, 8-neighbour) from every cell to the nearest lane cell."""
    cache = route.__dict__.setdefault("_area_lane_dist", {})
    key = len(getattr(route, "zones", []))
    if key in cache:
        return cache[key]
    lanes = lane_mask(route)
    h, w = lanes.shape
    d = np.full((h, w), 999, dtype=np.int32)
    from collections import deque

    q = deque()
    for y, x in zip(*np.nonzero(lanes)):
        d[y, x] = 0
        q.append((int(y), int(x)))
    while q:
        y, x = q.popleft()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w and d[ny, nx] > d[y, x] + 1:
                    d[ny, nx] = d[y, x] + 1
                    q.append((ny, nx))
    cache.clear()
    cache[key] = d
    return d


def _frac(a: np.ndarray, chars: str) -> float:
    return float(np.isin(a, list(chars)).mean()) if a.size else 0.0


def features(route, y0: int, x0: int, h: int, w: int, pad: int = 4) -> dict:
    cls = route.cls
    H, W = cls.shape
    box = cls[max(0, y0):y0 + h, max(0, x0):x0 + w]
    Y0, X0, Y1, X1 = max(0, y0 - pad), max(0, x0 - pad), min(H, y0 + h + pad), min(W, x0 + w + pad)
    around = cls[Y0:Y1, X0:X1].copy()
    inner = np.zeros(around.shape, dtype=bool)
    inner[y0 - Y0:y0 - Y0 + h, x0 - X0:x0 - X0 + w] = True
    ringc = around[~inner]
    return {
        "trees_around": _frac(ringc, "f"), "trees_in": _frac(box, "f"),
        "open_in": _frac(box, "gtpdvy"), "water_near": _frac(around, "wbh"), "water_in": _frac(box, "wbh"),
        "rock_near": _frac(around, "rku"), "sand_near": _frac(around, "d"), "sea_near": _frac(around, "b"),
        "ash_near": _frac(around, "vyl"),
    }


def context_fit(context: str, f: dict) -> float:
    """0 (wrong place) .. ~3 (ideal), from the features of a candidate box."""
    t, o, wn, r = f["trees_around"], f["open_in"], f["water_near"], f["rock_near"]
    if context == "deep_forest":
        return 3 * t + 1.5 * f["trees_in"] - 4 * wn
    if context == "forest":
        return 2 * t + f["trees_in"] - 2 * wn
    if context == "forest_edge":
        return 3 - 6 * abs(t - 0.45) + o
    if context == "rocky_rise":
        return 1.5 + 3 * r + t - 2 * wn
    if context in ("meadow", "farmland", "town_edge"):
        return 2 * o + (1 - t) - 2 * wn
    if context == "ruins":
        return 1.5 + 0.5 * t + 0.5 * o - wn
    if context == "riverbank":
        return (4 * min(wn, 0.35) if wn > 0.03 else -3) - 3 * f["water_in"]
    if context in ("lakeshore", "marsh"):
        return 5 * min(wn, 0.45) if wn > 0.05 else -3
    if context in ("cliff_base", "cliff_top", "mountain"):
        return 4 * min(r, 0.5) if r > 0.05 else 0.3
    if context in ("sea_shore", "sea"):
        return 4 * min(f["sea_near"] + f["sand_near"], 0.6) if (f["sea_near"] + f["sand_near"]) > 0.05 else -3
    if context == "dunes":
        return 4 * f["sand_near"]
    if context == "volcanic":
        return 4 * f["ash_near"] + r
    return 1.0


# ---------------------------------------------------------------- canvases

def canvas(context: str, w: int, h: int, rng: np.random.Generator) -> Tuple[np.ndarray, List[Cell]]:
    """A plain patch of terrain of this context, with a lane running along the
    bottom, so an area can be stamped and judged in isolation. Returns (cls, lane)."""
    g = np.full((h, w), "f", dtype="<U1")
    yy, xx = np.mgrid[0:h, 0:w]
    n = rng.random((h, w))
    lane_y = h - 3
    open_ = "g"
    if context in ("meadow", "farmland", "town_edge", "forest_edge", "ruins", "rocky_rise", "lakeshore",
                   "riverbank", "marsh", "cliff_base", "mountain", "cliff_top"):
        g[:] = "g"
        # tree clumps at the margins
        for _ in range(int(w * h / 90)):
            cy, cx = rng.integers(0, h), rng.integers(0, w)
            if 3 < cx < w - 4 and 3 < cy < h - 4 and context not in ("forest_edge",):
                continue
            g[(yy - cy) ** 2 + (xx - cx) ** 2 <= 4] = "f"
        if context == "forest_edge":
            g[:, : w // 4] = "f"
        g[(n < 0.10) & (g == "g")] = "t"
    if context in ("deep_forest", "forest"):
        g[:] = "f"
    if context == "riverbank":
        mid = 2
        for x in range(w):
            mid += rng.integers(-1, 2) * (x % 3 == 0)
            mid = int(np.clip(mid, 1, 3))
            g[mid: mid + 3, x] = "w"
    if context == "lakeshore":
        g[(yy - 0) ** 2 / (h * 0.35) ** 2 + (xx - w / 2) ** 2 / (w * 0.6) ** 2 < 1] = "w"
    if context == "marsh":
        for _ in range(6):
            cy, cx = rng.integers(2, h - 5), rng.integers(2, w - 2)
            g[((yy - cy) / 1.6) ** 2 + ((xx - cx) / 2.4) ** 2 < 1] = "w"
        g[(n < 0.35) & (g == "g")] = "t"
    if context in ("cliff_base", "mountain", "cliff_top"):
        g[: 3 + (n[0] * 2).astype(int).max(), :] = "r"
        g[:3, :] = "r"
        for _ in range(4):
            cy, cx = rng.integers(3, h - 5), rng.integers(1, w - 1)
            g[((yy - cy) / 1.2) ** 2 + ((xx - cx) / 1.6) ** 2 < 1] = "r"
    if context == "rocky_rise":
        for _ in range(3):
            cy, cx = rng.integers(1, h - 4), rng.integers(1, w - 1)
            if abs(cx - w / 2) < w / 4:
                continue
            g[((yy - cy) / 1.3) ** 2 + ((xx - cx) / 1.8) ** 2 < 1] = "r"
    if context in ("sea_shore", "sea", "dunes"):
        g[:] = "d"
        g[: h // 3 if context != "sea" else h - 5, :] = "b"
        if context == "dunes":
            g[: 2, :] = "b"
    if context == "volcanic":
        g[:] = "v"
        g[n < 0.08] = "u"
        g[:2, :] = "r"
    if context == "snowfield":
        g[:] = "g"  # no snow tileset yet; grass stands in
    if context == "cave":
        g[:] = "k"
        g[2:h - 2, 2:w - 2] = "v"
        open_ = "v"
    lane = [(lane_y, x) for x in range(w)] + [(lane_y + 1, x) for x in range(w)]
    for (y, x) in lane:
        g[y, x] = "p" if context not in ("cave", "sea_shore", "dunes", "volcanic") else open_ if context == "cave" else g[y, x]
        if g[y, x] in "fbwrk":
            g[y, x] = "p"
    g[lane_y + 2:, :] = "f" if context in ("deep_forest", "forest") else g[lane_y + 2:, :]
    return g, lane


def interior_canvas(kind: str, w: int, h: int, rng: np.random.Generator) -> np.ndarray:
    """Blank interior of a sub-map: walls everywhere, a floor to stamp into, the way
    out at the bottom centre."""
    g = np.full((h, w), "k", dtype="<U1")
    if kind == "forest_glade":
        g[:] = "f"
        yy, xx = np.mgrid[0:h, 0:w]
        g[((yy - h / 2) / (h / 2 - 2)) ** 2 + ((xx - w / 2) / (w / 2 - 2)) ** 2 < 1] = "g"
        g[h - 3:, w // 2 - 1: w // 2 + 1] = "g"
        return g
    floor = "v"
    g[2:h - 2, 2:w - 2] = floor
    g[h - 2:, w // 2] = floor
    if kind in ("cave", "grotto"):
        n = rng.random((h, w))
        edge = np.zeros((h, w), dtype=bool)
        edge[2:4, :] = True
        edge[:, 2:4] = True
        edge[:, w - 4:w - 2] = True
        g[edge & (n < 0.35) & (g == floor)] = "k"
    return g
