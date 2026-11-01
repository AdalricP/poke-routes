"""Terrain: coastline, heightmap (fBm + domain warp), erosion, rivers, lakes,
climate and biomes. Every function edits the World in place."""
from __future__ import annotations

import heapq
import math
from typing import Tuple

import numpy as np
from scipy import ndimage as ndi

from .world import World

N8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
D8 = np.array([math.hypot(dy, dx) for dy, dx in N8])


# ------------------------------------------------------------------ noise

class Lattice:
    """Periodic cubic-spline value noise, sampled at any float coordinate."""

    def __init__(self, rng: np.random.Generator, n: int = 32):
        self.n = n
        self.lat = ndi.spline_filter(rng.random((n, n)), order=3, mode="grid-wrap")

    def __call__(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        return ndi.map_coordinates(self.lat, [np.mod(y, self.n), np.mod(x, self.n)],
                                   order=3, mode="grid-wrap", prefilter=False)


class Fbm:
    def __init__(self, rng: np.random.Generator, octaves: int = 5, gain: float = 0.5):
        self.layers = [Lattice(rng) for _ in range(octaves)]
        self.gain = gain
        self.off = rng.random((octaves, 2)) * 32

    def __call__(self, x, y, ridged: bool = False):
        out = np.zeros_like(x, dtype=float)
        amp, total, f = 1.0, 0.0, 1.0
        for k, lay in enumerate(self.layers):
            n = lay(x * f + self.off[k, 0], y * f + self.off[k, 1])
            if ridged:
                n = (1.0 - np.abs(2 * n - 1)) ** 2
            out += amp * n
            total += amp
            amp *= self.gain
            f *= 2.0
        return out / total


def _grid(w: World) -> Tuple[np.ndarray, np.ndarray]:
    ys, xs = np.mgrid[0:w.H, 0:w.W].astype(float)
    return xs, ys


def norm01(a: np.ndarray, mask=None) -> np.ndarray:
    v = a[mask] if mask is not None else a
    lo, hi = float(v.min()), float(v.max())
    return (a - lo) / max(1e-9, hi - lo)


# ------------------------------------------------------------------ coastline

def _seg_dist(px, py, ax, ay, bx, by):
    abx, aby = bx - ax, by - ay
    t = np.clip(((px - ax) * abx + (py - ay) * aby) / max(1e-9, abx * abx + aby * aby), 0, 1)
    return np.hypot(px - (ax + t * abx), py - (ay + t * aby))


def coastline(w: World) -> None:
    """Two halves: the Oberland mainland (west) and the Nehrung (east): a sand spit
    running from the strait at Pilsau to the Samland block, with the lagoon (Haff)
    between them. Shapes are fixed by the lore; their outlines come from the seed."""
    rng = w.rng
    xs, ys = _grid(w)
    u, v = (xs + 0.5) / w.W, (ys + 0.5) / w.H
    warp = Fbm(rng, 4)
    wu = u + 0.09 * (norm01(warp(xs / 18, ys / 18)) - 0.5) * 2
    wv = v + 0.09 * (norm01(warp(xs / 18 + 7.3, ys / 18 + 3.1)) - 0.5) * 2
    j = lambda s: float(rng.uniform(-s, s))  # noqa: E731

    def ell(cx, cy, rx, ry):
        return 1.0 - ((wu - cx) / rx) ** 2 - ((wv - cy) / ry) ** 2

    main = np.maximum(ell(0.335 + j(0.02), 0.62 + j(0.02), 0.28, 0.35),
                      ell(0.53 + j(0.015), 0.39, 0.115, 0.13))
    main = np.maximum(main, ell(0.18 + j(0.03), 0.40 + j(0.03), 0.13, 0.13))  # western headland
    east = ell(0.885 + j(0.01), 0.50 + j(0.02), 0.075, 0.23)
    coast_noise = norm01(Fbm(rng, 5, gain=0.65)((xs / 7), (ys / 7))) - 0.5
    mainland = (main + coast_noise * 0.7) > 0
    eastland = (east + coast_noise * 0.7) > 0

    # the strait: tip of the mainland lobe; the spit starts just across it
    sx, sy = 0.645 + j(0.006), 0.30 + j(0.015)
    pts = [(sx + 0.014, sy - 0.005), (0.70 + j(0.01), sy - 0.06 + j(0.01)),
           (0.78 + j(0.01), 0.19 + j(0.01)), (0.85, 0.22), (0.885, 0.31)]
    spit_d = np.full(mainland.shape, 1e9)
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        spit_d = np.minimum(spit_d, _seg_dist(xs, ys, ax * w.W, ay * w.H, bx * w.W, by * w.H))
    spit_w = 1.9 + 1.0 * Fbm(rng, 3)(xs / 6, ys / 6)
    spit = spit_d < spit_w
    eastland |= spit
    # the mainland always reaches the strait: a neck of land out to the spit's start
    neck = _seg_dist(xs, ys, 0.53 * w.W, 0.39 * w.H, (sx - 0.014) * w.W, sy * w.H) < 3.2
    mainland |= neck & ~spit
    # Haff: the lagoon between the mainland's east coast and the Nehrung
    haff = (u > 0.60) & (u < 0.84) & (v > 0.24) & ~spit
    mainland &= ~(haff & (main + coast_noise * 0.7 < 0.12))
    eastland &= ~(haff & (east + coast_noise * 0.7 < 0.12)) | spit
    m = 3  # a ring of sea around the map edge
    for a in (mainland, eastland):
        a[:m, :] = a[-m:, :] = False
        a[:, :m] = False
        a[:, -m:] = False

    def tidy(a):
        lab, n = ndi.label(a)
        if n == 0:
            return a
        sizes = ndi.sum(a, lab, range(1, n + 1))
        a = np.isin(lab, [i + 1 for i, sz in enumerate(sizes) if sz >= 12])
        holes = ndi.binary_fill_holes(a) & ~a
        lab, n = ndi.label(holes)
        for i in range(1, n + 1):
            if (lab == i).sum() < 6:
                a[lab == i] = True
        return a

    eastland = tidy(eastland)
    # keep only the Nehrung component that holds the spit
    lab, n = ndi.label(eastland)
    cnt = np.bincount(lab[spit & eastland].ravel(), minlength=n + 1)
    cnt[0] = 0
    eastland = lab == int(np.argmax(cnt))
    # the halves never touch: a water gap of >= 2 cells, narrowest at the strait
    near_east = ndi.distance_transform_edt(~eastland) <= 2.5
    mainland = tidy(mainland & ~near_east)
    lab, n = ndi.label(mainland)
    sizes = ndi.sum(mainland, lab, range(1, n + 1))
    big = int(np.argmax(sizes)) + 1
    islets = mainland & (lab != big)
    mainland = lab == big
    land = mainland | eastland | islets

    # the strait must be narrow: if the halves ended up far apart, the spit grows back
    # toward the mainland until a ~2-cell channel is left
    de = ndi.distance_transform_edt(~eastland)
    dm = np.where(mainland, de, np.inf)
    k = np.unravel_index(int(np.argmin(dm)), dm.shape)
    if dm[k] > 4:
        dmn = ndi.distance_transform_edt(~mainland)
        ek = np.unravel_index(int(np.argmin(np.where(eastland, dmn, np.inf))), dm.shape)
        L = float(np.hypot(k[0] - ek[0], k[1] - ek[1]))
        t = max(0.0, (L - 3.0) / L)
        tx, ty = ek[1] + (k[1] - ek[1]) * t, ek[0] + (k[0] - ek[0]) * t
        ext = _seg_dist(xs, ys, ek[1], ek[0], tx, ty) < 1.8
        ext &= ndi.distance_transform_edt(~mainland) > 2.5
        eastland |= ext
        spit |= ext
        land = mainland | eastland | islets
    # the strait is where the two halves come closest
    de = ndi.distance_transform_edt(~eastland)
    dm = np.where(mainland, de, np.inf)
    k = np.unravel_index(int(np.argmin(dm)), dm.shape)
    dmn = ndi.distance_transform_edt(~mainland)
    ek = np.unravel_index(int(np.argmin(np.where(eastland, dmn, np.inf))), dm.shape)
    gx, gy = (k[1] + ek[1]) / 2, (k[0] + ek[0]) / 2

    w.land = land
    w.strait = (int(round(gx)), int(round(gy)))
    half = np.zeros(land.shape, dtype=np.int8)
    half[mainland] = 1
    half[eastland] = 2
    if islets.any():
        d1 = ndi.distance_transform_edt(~mainland)
        d2 = ndi.distance_transform_edt(~eastland)
        half[islets & (d1 <= d2)] = 1
        half[islets & (d2 < d1)] = 2
    w.half = half
    w.lagoon = haff & ~land
    w.overrides["spit"] = spit & land
    w.log.append(f"coast: {land.mean() * 100:.0f}% land, strait at {w.strait}")


# ------------------------------------------------------------------ height

def heightmap(w: World) -> None:
    rng = w.rng
    xs, ys = _grid(w)
    u, v = (xs + 0.5) / w.W, (ys + 0.5) / w.H
    land = w.land
    dcoast = ndi.distance_transform_edt(land)
    dsea = ndi.distance_transform_edt(~land)

    # domain warp: sample the hills at coordinates pushed around by another fBm
    q = Fbm(rng, 4)
    qx = q(xs / 30, ys / 30)
    qy = q(xs / 30 + 5.2, ys / 30 + 1.3)
    hills = Fbm(rng, 5)(xs / 26 + 3.5 * qx, ys / 26 + 3.5 * qy)
    ridges = Fbm(rng, 5, gain=0.55)(xs / 20 + 2.0 * qx, ys / 20 + 2.0 * qy, ridged=True)

    # the Oberland uplands rise in the south-west; the Samland block is hilly
    ux, uy = 0.24 + rng.uniform(-0.04, 0.04), 0.74 + rng.uniform(-0.04, 0.04)
    upland = np.exp(-(((u - ux) / 0.17) ** 2 + ((v - uy) / 0.2) ** 2))
    samland = np.exp(-(((u - 0.885) / 0.07) ** 2 + ((v - 0.52) / 0.19) ** 2))
    inland = 1 - np.exp(-dcoast / 7.0)

    hills = norm01(hills) - 0.5
    # a second, lower belt of hills across the middle of the Oberland
    belt = np.exp(-(((v - 0.48 - rng.uniform(-0.05, 0.05)) / 0.10) ** 2)) * (u < 0.62)
    h = 0.06 + 0.24 * inland + hills * 0.34 * inland
    h += ridges * 0.55 * upland * inland
    h += ridges * 0.20 * belt * inland
    h += (ridges * 0.25 + 0.06) * samland * inland
    h = np.where(land, np.maximum(h, 0.012), 0.0)
    sea = -(0.04 + 0.42 * (1 - np.exp(-dsea / 7.0))) - (hills - 0.5) * 0.05
    w.height = np.where(land, h, sea)
    w.overrides["upland"] = upland


# ------------------------------------------------------------------ hydrology helpers

def priority_fill(h: np.ndarray, outlet: np.ndarray, eps: float = 0.0) -> np.ndarray:
    """Priority-flood depression filling: every land cell can drain to an outlet."""
    H, W = h.shape
    out = h.copy()
    done = outlet.copy()
    rim = outlet & ndi.binary_dilation(~outlet, structure=np.ones((3, 3)))
    heap = [(float(out[y, x]), int(y), int(x)) for y, x in zip(*np.nonzero(rim))]
    heapq.heapify(heap)
    while heap:
        z, y, x = heapq.heappop(heap)
        for dy, dx in N8:
            ny, nx = y + dy, x + dx
            if 0 <= ny < H and 0 <= nx < W and not done[ny, nx]:
                done[ny, nx] = True
                if out[ny, nx] <= z + eps:
                    out[ny, nx] = z + eps
                heapq.heappush(heap, (float(out[ny, nx]), ny, nx))
    return out


def d8(h: np.ndarray, sink: np.ndarray):
    """Steepest-descent receiver (flat index, -1 for sinks) and slope per cell."""
    H, W = h.shape
    pad = np.pad(h, 1, constant_values=np.inf)
    drops = np.stack([(h - pad[1 + dy:1 + dy + H, 1 + dx:1 + dx + W]) / D8[k] for k, (dy, dx) in enumerate(N8)])
    k = np.argmax(drops, axis=0)
    slope = np.take_along_axis(drops, k[None], 0)[0]
    ys, xs = np.mgrid[0:H, 0:W]
    dy = np.array([o[0] for o in N8])[k]
    dx = np.array([o[1] for o in N8])[k]
    recv = (ys + dy) * W + (xs + dx)
    recv[(slope <= 0) | sink] = -1
    return recv.ravel(), np.maximum(slope, 0)


def accumulate(h: np.ndarray, recv: np.ndarray, rain: np.ndarray) -> np.ndarray:
    a = rain.ravel().astype(float).copy()
    rl = recv.tolist()
    al = a.tolist()
    for i in np.argsort(-h.ravel(), kind="stable").tolist():
        r = rl[i]
        if r >= 0:
            al[r] += al[i]
    return np.array(al).reshape(h.shape)


# ------------------------------------------------------------------ erosion

def erode(w: World, iters: int = 30) -> None:
    """Stream-power hydraulic erosion (rivers cut valleys, sediment settles in flats)
    plus a thermal pass that slumps slopes steeper than the talus angle."""
    h = w.height.copy()
    land = w.land
    sea = ~land
    for it in range(iters):
        if it % 5 == 0:
            hf = priority_fill(h, sea, eps=1e-5)
            fill_add = hf - h
        else:
            hf = h + fill_add
        recv, slope = d8(hf, sea)
        A = accumulate(hf, recv, land.astype(float))
        E = np.minimum(0.0022 * np.sqrt(A) * slope.reshape(h.shape), 0.02) * land
        h -= E
        # deposition where the channel flattens out
        flat = (slope.reshape(h.shape) < 0.004) & land
        dep = 0.0008 * np.sqrt(A) * flat
        h += np.minimum(dep, 0.004)
        # thermal slumping
        for dy, dx in ((0, 1), (1, 0), (0, -1), (-1, 0)):
            nb = np.roll(h, (dy, dx), axis=(0, 1))
            excess = np.maximum(h - nb - 0.07, 0) * 0.2 * land
            h -= excess
            h += np.roll(excess, (-dy, -dx), axis=(0, 1))
        h = np.where(land, np.maximum(h, 0.008), w.height)
    w.height = h
    w.log.append("erosion: %d iterations" % iters)


# ------------------------------------------------------------------ rivers and lakes

def hydrology(w: World, max_lakes: int = 3, river_cells: int = 0) -> None:
    land = w.land
    sea = ~land
    h = w.height
    filled = priority_fill(h, sea)
    depth = np.where(land, filled - h, 0)
    basins = depth > 0.003
    lab, n = ndi.label(basins)
    lakes = np.zeros_like(land)
    cand = []
    for i in range(1, n + 1):
        m = lab == i
        if m.sum() >= 5 and depth[m].max() > 0.008:
            cand.append((float(depth[m].sum()), i))
    for _, i in sorted(cand, reverse=True)[:max_lakes]:
        lakes |= lab == i
    lakes = lakes | w.overrides.get("lake_extra", np.zeros_like(land))
    if w.lake is not None:  # lakes found on an earlier pass were levelled; keep them
        lakes |= w.lake
    lakes &= ~w.overrides.get("no_lake", np.zeros_like(land))
    # small basins are breached: the surface is the filled one
    h2 = np.where(basins & ~lakes, filled, h)
    w.height = np.where(lakes, filled, h2)
    w.lake = lakes & land
    hf = priority_fill(w.height, sea, eps=1e-5)
    recv, _ = d8(hf, sea)
    rain = land * (0.6 + 0.8 * (w.moist if w.moist is not None else 0.5))
    A = accumulate(hf, recv, rain)
    w.recv, w.flow = recv, A
    thr = river_cells or max(30, int(land.sum() * 0.0045))
    w.river = land & ~w.lake & (A >= thr)
    w.sea = sea
    w.log.append(f"hydrology: {int(w.lake.sum())} lake cells, {int(w.river.sum())} river cells (thr {thr})")


# ------------------------------------------------------------------ climate and biomes

def climate(w: World) -> None:
    rng = w.rng
    xs, ys = _grid(w)
    v = (ys + 0.5) / w.H
    land = w.land
    hl = np.where(land, w.height, 0)
    hn = hl / max(1e-6, float(hl.max()))
    temp = 0.62 + 0.28 * (v - 0.5) - 0.85 * hn + w.overrides.get("heat", 0)
    # westerlies: moisture is picked up over water, rained out when the air climbs
    m = np.zeros_like(hl)
    cur = np.ones(w.H)
    prev = np.zeros(w.H)
    for x in range(w.W):
        col = hl[:, x]
        wet = ~land[:, x] | w.lake[:, x] if w.lake is not None else ~land[:, x]
        rise = np.maximum(col - prev, 0)
        cur = np.where(wet, np.minimum(1.0, cur + 0.25), cur * 0.986 - 2.2 * rise)
        cur = np.clip(cur, 0, 1)
        m[:, x] = cur
        prev = col
    m = ndi.gaussian_filter(m, 1.5)
    dcoast = ndi.distance_transform_edt(land)
    driver = ndi.distance_transform_edt(~(w.river | w.lake)) if w.river is not None else dcoast
    noise = norm01(Fbm(rng, 4, gain=0.6)(xs / 12, ys / 12)) - 0.5
    moist = 0.16 + 0.34 * m + 0.18 * np.exp(-dcoast / 8) + 0.22 * np.exp(-driver / 3) + 0.55 * noise
    moist += w.overrides.get("rain", 0)
    w.temp, w.moist = temp, np.clip(moist, 0, 1)


def make_tiers(w: World) -> None:
    """Five elevation tiers on land (0 lowland .. 4 peaks) from height quantiles."""
    land = w.land
    q = np.quantile(w.height[land], [0.30, 0.58, 0.80, 0.93])
    t = np.digitize(w.height, q).astype(np.int8)
    t[~land] = 0
    w.tiers = t
    w.overrides["tier_q"] = q


def biomes(w: World) -> None:
    land = w.land
    h, t, m = w.height, w.temp, w.moist
    hn = np.where(land, h, 0) / max(1e-6, float(h[land].max()))
    dsea = ndi.distance_transform_edt(land)
    near_river = ndi.binary_dilation(w.river | w.lake, iterations=2)
    slope = np.hypot(*np.gradient(h))
    b = np.full(h.shape, "m", dtype="<U1")
    # thresholds from the land's own moisture spread, so every seed gets fields and forests
    q = lambda a, p: float(np.quantile(a[land], p))  # noqa: E731
    b[m > q(m, 0.50)] = "f"
    b[m > q(m, 0.84)] = "F"
    b[(m < q(m, 0.07)) & (t > q(t, 0.45))] = "d"
    b[near_river & (slope < 0.02) & (hn < 0.35) & (m > 0.45)] = "w"
    b[(dsea <= 1.5) & (hn < 0.22)] = "b"
    b[w.overrides.get("spit", np.zeros_like(land))] = "b"
    b[w.tiers >= 4] = "M"
    b[(w.tiers >= 3) & (slope > 0.05)] = "M"
    b[(w.height > np.quantile(w.height[land], 0.985)) & (t < 0.25)] = "S"
    for code, mask in w.overrides.get("biome", []):
        b[mask & land] = code
    b[~land] = "~"
    b[~land & (h < -0.30)] = "="
    b[w.lake] = "o"
    w.biome = b
