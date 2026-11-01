"""A faster drop-in for ``Autotiler.tile`` used by the web UI.

Same algorithm, same random stream, same output as
:meth:`pokeroutes.autotile.Autotiler.tile` (checked by ``python3 fasttile.py``);
it just precomputes the log tables and candidate lists and works on plain
Python lists instead of indexing numpy arrays per cell, which is ~10x quicker.
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pokeroutes.autotile import ALPHA, Autotiler  # noqa: E402
from pokeroutes.layout import Layout  # noqa: E402
from pokeroutes.learn import neighbourhood  # noqa: E402


class FastTiler:
    def __init__(self, at: Autotiler):
        self.at = at
        self.no_row = math.log(ALPHA / 50)
        ids = set(at.classes) | set(at.adj_h) | set(at.adj_v)
        for t in (at.adj_h, at.adj_v):
            for row in t.values():
                ids |= set(row)
        for c in list(at.ctx9.values()) + list(at.ctx5.values()):
            ids |= set(c)
        self.n = max(ids) + 2
        self.H = self._dense(at.adj_h, at.h_tot)
        self.V = self._dense(at.adj_v, at.v_tot)
        self._ctx_cache: Dict[str, Tuple[List[int], List[float]]] = {}
        self.blocked = at.blocked
        self.elevation = at.elevation

    def _dense(self, table, totals) -> np.ndarray:
        """M[a, b] = log p(b next to a), exactly as Autotiler._p_pair computes it."""
        m = np.full((self.n, self.n), self.no_row, dtype=np.float64)
        for a, row in table.items():
            if not row:
                continue
            den = totals[a] + ALPHA * 50
            m[a, :] = math.log(ALPHA / den)
            for b, cnt in row.items():
                m[a, b] = math.log((cnt + ALPHA) / den)
        return m

    def _ctx(self, n9: str):
        hit = self._ctx_cache.get(n9)
        if hit is None:
            ctx = self.at._context(n9)
            tot = sum(ctx.values()) or 1
            cands = sorted(ctx, key=lambda m: -ctx[m])[:24]
            base = [math.log((ctx.get(m, 0) + 0.01) / (tot + 0.01)) for m in cands]
            hit = (cands, np.array(cands, dtype=np.int64), np.array(base, dtype=np.float64))
            self._ctx_cache[n9] = hit
        return hit

    def tile(self, cls: np.ndarray, seed: int = 0, sweeps: int = 3, temperature: float = 0.35,
             fixed: Optional[np.ndarray] = None) -> Layout:
        """``fixed``: optional (H, W) int array, metatile id for cells that must not
        change and -1 elsewhere (same meaning as Autotiler.tile's ``fixed`` dict)."""
        rng = random.Random(seed)
        cls = self.at.prepare(cls)
        h, w = cls.shape
        grid = [[-1] * w for _ in range(h)] if fixed is None else [[int(v) for v in r] for r in fixed]
        ctxs = [[self._ctx(n9) for n9 in r] for r in keys(cls)]
        H, V = self.H, self.V
        exp = math.exp

        def pick(y, x, temp):
            cands, ca, base = ctxs[y][x]
            if not cands:
                return 1
            row = grid[y]
            left = row[x - 1] if x > 0 and row[x - 1] >= 0 else None
            up = grid[y - 1][x] if y > 0 and grid[y - 1][x] >= 0 else None
            right = row[x + 1] if x < w - 1 and row[x + 1] >= 0 else None
            down = grid[y + 1][x] if y < h - 1 and grid[y + 1][x] >= 0 else None
            # same summation order as Autotiler._score so floats match bit for bit
            s = base
            if left is not None:
                s = s + H[left, ca]
            if up is not None:
                s = s + V[up, ca]
            if right is not None:
                s = s + H[ca, right]
            if down is not None:
                s = s + V[ca, down]
            scores = s.tolist()
            if temp <= 0:
                return cands[int(np.argmax(scores))]
            mx = max(scores)
            return rng.choices(cands, [exp((s - mx) / temp) for s in scores])[0]

        cells = [(y, x) for y in range(h) for x in range(w) if fixed is None or fixed[y, x] < 0]
        for y, x in cells:  # row-major, like the original
            grid[y][x] = pick(y, x, temperature)
        for sweep in range(sweeps):
            rng.shuffle(cells)
            t = temperature * (0.5 ** (sweep + 1))
            for y, x in cells:
                grid[y][x] = pick(y, x, t)

        return self.at.finish(np.array(grid), cls)

    def tile_stage(self, cls: np.ndarray, final_cls: np.ndarray, final: Layout, seed: int = 0) -> Layout:
        """Tile an intermediate snapshot. Cells whose 3x3 class neighbourhood is the
        same as in the finished route keep the finished route's metatile (so tiles
        don't flicker between stages); only the rest is re-tiled around them."""
        same = np.array(keys(cls)) == np.array(keys(final_cls))
        if same.all():
            return final
        fixed = np.where(same, final.metatile.astype(np.int64), -1)
        return self.tile(cls, seed=seed, fixed=fixed)


def keys(cls: np.ndarray) -> List[List[str]]:
    """3x3 neighbourhood key of every cell (edge-clamped, as learn.neighbourhood)."""
    p = np.pad(cls, 1, mode="edge")
    h, w = cls.shape
    parts = [p[dy:dy + h, dx:dx + w] for dy in range(3) for dx in range(3)]
    out = parts[0].astype(object)
    for q in parts[1:]:
        out = out + q.astype(object)
    return out.tolist()


if __name__ == "__main__":
    import time

    at = Autotiler.load(ROOT / "pokeroutes/models/general_fortree.json")
    ft = FastTiler(at)
    rs = np.random.default_rng(0)
    cls = rs.choice(list("ffffggptw"), size=(18, 22))
    cls[5:12, 6:14] = "f"
    a = at.tile(cls, seed=5)
    t = time.time()
    b = ft.tile(cls, seed=5)
    print("fast", round(time.time() - t, 3), "s; identical:", bool((a.metatile == b.metatile).all()
          and (a.collision == b.collision).all() and (a.elevation == b.elevation).all()))
    fx = {(y, x): int(a.metatile[y, x]) for y in range(3, 9) for x in range(2, 15)}
    fa = np.full(cls.shape, -1)
    for (y, x), m in fx.items():
        fa[y, x] = m
    a2, b2 = at.tile(cls, seed=9, fixed=fx), ft.tile(cls, seed=9, fixed=fa)
    print("with fixed identical:", bool((a2.metatile == b2.metatile).all()))
    print("keys ok:", all(keys(cls)[y][x] == neighbourhood(cls, y, x) for y in range(18) for x in range(22)))
