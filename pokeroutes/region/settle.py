"""Settlements: the four cities on sites that make sense for what they are."""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
from scipy import ndimage as ndi

from .world import City, World

SIZES = {"tvangsburg": (14, 12), "pilsau": (11, 10), "huettenau": (11, 10), "kaup": (8, 8)}


def _rect(cx: float, cy: float, wh: Tuple[int, int]) -> List[int]:
    x0 = int(round(cx - wh[0] / 2))
    y0 = int(round(cy - wh[1] / 2))
    return [x0, y0, x0 + wh[0], y0 + wh[1]]


def _overlaps(a: List[int], b: List[int], gap: int = 0) -> bool:
    return not (a[2] + gap <= b[0] or b[2] + gap <= a[0] or a[3] + gap <= b[1] or b[3] + gap <= a[1])


def _inside(w: World, r: List[int], m: int = 4) -> bool:
    return r[0] >= m and r[1] >= m and r[2] <= w.W - m and r[3] <= w.H - m


def _best(w: World, wh, score_fn, cands, avoid: List[List[int]]) -> Optional[List[int]]:
    best, bs = None, -1e18
    for (cx, cy) in cands:
        r = _rect(cx, cy, wh)
        if not _inside(w, r) or any(_overlaps(r, a, 6) for a in avoid):
            continue
        s = score_fn(r, cx, cy)
        if s > bs:
            best, bs = r, s
    return best


def settle(w: World) -> None:
    land = w.land
    slope = np.hypot(*np.gradient(w.height))
    avoid: List[List[int]] = []
    hard = np.zeros_like(land)
    for lm in w.landmarks:
        if lm.kind in ("volcano", "den", "burn", "tower", "islands", "seacave", "station"):
            x0, y0, x1, y1 = lm.footprint
            hard[max(0, y0):y1, max(0, x0):x1] = True
    hard |= w.overrides.get("no_route", np.zeros_like(land))

    def frac(mask, r):
        return float(mask[r[1]:r[3], r[0]:r[2]].mean())

    # Pilsau: on the strait, half on each shore
    sx, sy = w.strait
    wh = SIZES["pilsau"]
    cands = [(sx + dx, sy + dy) for dx in range(-6, 7) for dy in range(-6, 7)]

    def pil_score(r, cx, cy):
        a, b = frac(land & (w.half == 1), r), frac(land & (w.half == 2), r)
        if min(a, b) < 0.10:  # the port must stand on both shores
            return -1e9 + a + b
        return 2 * min(a, b) + a + b - frac(hard, r) * 9 - 0.03 * np.hypot(cx - sx, cy - sy)

    pil = _best(w, wh, pil_score, cands, avoid)
    avoid.append(pil)

    # Tvangsburg: on the lower Preyl, near (not on) the coast, close to the Brandwald
    px, py = w.overrides.get("capital_site", (w.W // 3, w.H // 2))
    wh = SIZES["tvangsburg"]
    dsea = ndi.distance_transform_edt(land)
    cands = [(px + dx, py + dy) for dx in range(-8, 9) for dy in range(-8, 9)]
    tow = w.landmark("gold_feather_tower")

    def tv_score(r, cx, cy):
        s = frac(land, r) * 6 - frac(hard, r) * 20 + frac(w.river, r) * 25
        s -= abs(float(dsea[int(cy), int(cx)]) - 9) * 0.15
        s -= frac(slope > 0.03, r) * 3
        if tow:
            s -= abs(np.hypot(tow.x - cx, tow.y - cy) - 16) * 0.08
        return s

    tv = _best(w, wh, tv_score, cands, avoid)
    avoid.append(tv)

    # Hüttenau: foundry town at the foot of the Glutberg, off the lava, toward the capital
    vx, vy = w.overrides.get("volcano", (w.W // 4, int(w.H * 0.7)))
    wh = SIZES["huettenau"]
    cands = [(vx + dx, vy + dy) for dx in range(-20, 21) for dy in range(-20, 21) if 10 <= np.hypot(dx, dy) <= 17]

    def hu_score(r, cx, cy):
        s = frac(land & (w.half == 1), r) * 8 - frac(hard, r) * 30 - frac(slope > 0.04, r) * 4
        s -= np.hypot(cx - (tv[0] + tv[2]) / 2, cy - (tv[1] + tv[3]) / 2) * 0.03
        return s

    hu = _best(w, wh, hu_score, cands, avoid)
    avoid.append(hu)

    # Kaup: small dune village on the Nehrung, on the coast, between the spit and Romowe
    wh = SIZES["kaup"]
    neh = (w.half == 2)
    spit = w.overrides.get("spit", np.zeros_like(land))
    den = w.landmark("romowe")
    dcoast = ndi.distance_transform_edt(land)
    cands = [(int(x), int(y)) for y, x in zip(*np.nonzero(neh & ~spit & (dcoast <= 4)))]

    def ka_score(r, cx, cy):
        s = frac(neh & ~spit, r) * 8 - frac(hard, r) * 30 + frac(~land, r) * 1.5
        if den:
            dd = np.hypot(den.x - cx, den.y - cy)
            s -= abs(dd - 16) * 0.12
        s -= abs(cy - sy - 18) * 0.03
        return s

    ka = _best(w, wh, ka_score, cands, avoid)

    w.cities = [
        City("tvangsburg", "Tvangsburg", tv, "Rock", "Ho-Oh", "Oberland", "capital",
             "Capital on the lower Preyl, built over the burned Sambi fort. Player starts here."),
        City("pilsau", "Pilsau", pil, "Fighting", "Kyogre", "strait", "port",
             "Fortified port on the strait. The ferry slip joins the Oberland to the Nehrung."),
        City("huettenau", "Hüttenau", hu, "Dragon", "Groudon", "Oberland", "foundry town",
             "Foundry town at the foot of the Glutberg; warm ground, slag heaps, dragon-keepers."),
        City("kaup", "Kaup", ka, "Grass", "Lugia", "Nehrung", "village",
             "One-street dune village with a school, a pier and a Thursday market."),
    ]
    for c in w.cities:
        if c.rect is None:
            raise RuntimeError(f"no site found for {c.name}")
    w.log.append("cities: " + ", ".join(f"{c.name} {c.rect}" for c in w.cities))
