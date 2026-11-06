"""Turn a grid of semantic classes into concrete metatiles.

Uses a model produced by :mod:`pokeroutes.learn`. Each cell starts from the
metatiles real maps use for the same 3x3 class neighbourhood (backing off to a
plus-shaped neighbourhood, then to the class alone), then a few sweeps of
iterated conditional modes pull neighbouring metatiles into pairs that
actually occur side by side in the corpus.

Classes the model has (almost) never seen are swapped for their
:data:`pokeroutes.classify.FALLBACK` (e.g. sand -> path in a forest model)
before tiling, and each placed metatile gets the collision / elevation real maps
give that metatile *for that class* (the mountain-top tile is floor in one place
and wall in another).
"""
from __future__ import annotations

import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from .classify import BLOCKED_CLASSES, FALLBACK, SURFABLE_CLASSES, WALKABLE_CLASSES, seal_waterfalls
from .layout import Layout
from .learn import cross, neighbourhood

ALPHA = 0.05  # smoothing for unseen adjacencies
MIN_CLASS_COUNT = 20  # fewer corpus cells than this -> use the class's fallback


class Autotiler:
    def __init__(self, model: dict):
        self.m = model
        self.ctx9: Dict[str, Dict[int, int]] = {k: {int(a): b for a, b in v.items()} for k, v in model["ctx9"].items()}
        self.ctx5: Dict[str, Dict[int, int]] = {k: {int(a): b for a, b in v.items()} for k, v in model["ctx5"].items()}
        self.adj_h = {int(k): {int(a): b for a, b in v.items()} for k, v in model["adj_h"].items()}
        self.adj_v = {int(k): {int(a): b for a, b in v.items()} for k, v in model["adj_v"].items()}
        self.h_tot = {k: sum(v.values()) for k, v in self.adj_h.items()}
        self.v_tot = {k: sum(v.values()) for k, v in self.adj_v.items()}
        self.classes = {int(k): v for k, v in model["classes"].items()}
        self.blocked = {int(k): v for k, v in model["blocked"].items()}
        self.elevation = {int(k): v for k, v in model["elevation"].items()}
        by_class: Dict[str, Counter] = defaultdict(Counter)
        for key, cnt in self.ctx5.items():
            for mid, n in cnt.items():
                by_class[key[0]][mid] += n
        self.by_class = by_class
        self.n_metatiles = max(self.classes) + 1
        self.cell = {k: tuple(v) for k, v in model.get("cell", {}).items()}

    # -- class grid preparation -------------------------------------------
    def known(self, c: str) -> bool:
        return sum(self.by_class.get(c, {}).values()) >= MIN_CLASS_COUNT

    def resolve(self, c: str) -> str:
        """``c`` itself if the model knows it, else the first known class down its fallback chain."""
        seen = set()
        cur = c
        while not self.known(cur) and cur in FALLBACK and cur not in seen:
            seen.add(cur)
            cur = FALLBACK[cur]
        return cur if self.known(cur) else c

    def prepare(self, cls: np.ndarray) -> np.ndarray:
        """Swap classes this model can't draw for their fallbacks."""
        cls = np.asarray(cls)
        out = cls.copy()
        for c in np.unique(cls):
            r = self.resolve(str(c))
            if r != c:
                out[cls == c] = r
        return out

    def cell_props(self, mid: int, c: str):
        """(collision, elevation) for metatile ``mid`` painted as class ``c``."""
        hit = self.cell.get(f"{mid}:{c}")
        if hit:
            return (1 if hit[0] > 0.5 else 0), int(hit[1])
        if c in WALKABLE_CLASSES or c in SURFABLE_CLASSES:
            col = 0
        elif c in BLOCKED_CLASSES:
            col = 1
        else:
            col = 1 if self.blocked.get(mid, 0) > 0.5 else 0
        return col, self.elevation.get(mid, 3)

    def finish(self, grid: np.ndarray, cls: np.ndarray) -> Layout:
        mt = np.asarray(grid).astype(np.uint16)
        col = np.zeros(mt.shape, dtype=np.uint8)
        el = np.zeros(mt.shape, dtype=np.uint8)
        for (y, x), m in np.ndenumerate(mt):
            col[y, x], el[y, x] = self.cell_props(int(m), str(cls[y, x]))
        return Layout(mt, col, el)

    @classmethod
    def load(cls, path: Path) -> "Autotiler":
        return cls(json.loads(Path(path).read_text()))

    # -- scoring -----------------------------------------------------------
    def _context(self, n9: str) -> Dict[int, int]:
        c = self.ctx9.get(n9)
        if c and sum(c.values()) >= 2:
            return c
        c5 = self.ctx5.get(cross(n9))
        if c5:
            return c5
        return dict(self.by_class.get(n9[4], {}))

    def _p_pair(self, table, totals, a: Optional[int], b: int) -> float:
        if a is None:
            return 0.0
        row = table.get(a)
        if not row:
            return math.log(ALPHA / 50)
        return math.log((row.get(b, 0) + ALPHA) / (totals[a] + ALPHA * 50))

    def _score(self, grid, y, x, mid, ctx_total, ctx) -> float:
        h, w = grid.shape
        s = math.log((ctx.get(mid, 0) + 0.01) / (ctx_total + 0.01))
        left = int(grid[y, x - 1]) if x > 0 and grid[y, x - 1] >= 0 else None
        up = int(grid[y - 1, x]) if y > 0 and grid[y - 1, x] >= 0 else None
        right = int(grid[y, x + 1]) if x < w - 1 and grid[y, x + 1] >= 0 else None
        down = int(grid[y + 1, x]) if y < h - 1 and grid[y + 1, x] >= 0 else None
        s += self._p_pair(self.adj_h, self.h_tot, left, mid)
        s += self._p_pair(self.adj_v, self.v_tot, up, mid)
        if right is not None:
            s += self._p_pair(self.adj_h, self.h_tot, mid, right)
        if down is not None:
            s += self._p_pair(self.adj_v, self.v_tot, mid, down)
        return s

    # -- main --------------------------------------------------------------
    def tile(self, cls: np.ndarray, seed: int = 0, sweeps: int = 3, temperature: float = 0.35,
             fixed: Optional[Dict] = None) -> Layout:
        """``cls`` is an (H, W) array of single-char class codes.

        ``fixed`` maps (y, x) -> metatile id for cells that must not change
        (hand-placed features, stamps)."""
        rng = random.Random(seed)
        cls = self.prepare(seal_waterfalls(cls))
        h, w = cls.shape
        grid = np.full((h, w), -1, dtype=np.int32)
        fixed = fixed or {}
        for (y, x), mid in fixed.items():
            grid[y, x] = mid
        ctxs = {}
        for y in range(h):
            for x in range(w):
                ctx = self._context(neighbourhood(cls, y, x))
                ctxs[y, x] = (ctx, sum(ctx.values()) or 1)

        def pick(y, x, temp):
            ctx, tot = ctxs[y, x]
            if not ctx:
                return 1
            cands = sorted(ctx, key=lambda m: -ctx[m])[:24]
            scores = [self._score(grid, y, x, m, tot, ctx) for m in cands]
            if temp <= 0:
                return cands[int(np.argmax(scores))]
            mx = max(scores)
            weights = [math.exp((s - mx) / temp) for s in scores]
            return rng.choices(cands, weights)[0]

        for y in range(h):
            for x in range(w):
                if (y, x) not in fixed:
                    grid[y, x] = pick(y, x, temperature)
        cells = [(y, x) for y in range(h) for x in range(w) if (y, x) not in fixed]
        for sweep in range(sweeps):
            rng.shuffle(cells)
            t = temperature * (0.5 ** (sweep + 1))
            for y, x in cells:
                grid[y, x] = pick(y, x, t)

        return self.finish(grid, cls)
