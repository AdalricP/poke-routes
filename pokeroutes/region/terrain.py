"""Terrain: noise (fBm + domain warp), erosion, rivers, lakes, climate and biomes,
run on whatever land mask the geography step drew. Every function edits the World
in place."""
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


# ------------------------------------------------------------------ hydrology helpers

def shore_distance(land: np.ndarray) -> np.ndarray:
    if not (~land).any():
        return np.full(land.shape, float(max(land.shape)))
    return ndi.distance_transform_edt(land)


def drain_outlet(land: np.ndarray) -> np.ndarray:
    """Water leaves the map through the sea and through any land on the map edge
    (the land goes on past it)."""
    map_edge = np.zeros_like(land)
    map_edge[0, :] = map_edge[-1, :] = map_edge[:, 0] = map_edge[:, -1] = True
    return ~land | map_edge


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
    sea = drain_outlet(land)
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

RAIN_ON_DRY_LAND_PER_CELL, RAIN_ON_WET_LAND_PER_CELL = 0.6, 1.4
SNOWLINE_HEIGHT_QUANTILE = 0.88
SNOWMELT_AT_THE_SUMMIT_PER_CELL = 7.0
COLDEST_TEMPERATURE_WITHOUT_SNOW = 0.35
SPRING_HEIGHT_QUANTILES = (0.55, 0.85)
LAND_CELLS_PER_SPRING = 1400
FEWEST_SPRINGS, MOST_SPRINGS = 2, 8
SPRING_SHARE_OF_A_RIVER = 0.9
SMALLEST_SPRING_SPACING_IN_CELLS = 10
RIVER_FLOW_SHARE_OF_LAND_CELLS = 0.008
SMALLEST_RIVER_FLOW = 36.0
MEANDER_NOISE_SCALE_IN_CELLS = 7.0
STEEPEST_SLOPE_THAT_MEANDERS = 0.012
MEANDER_PULL = 1.6


def hydrology(w: World, max_lakes: int = 3, river_cells: int = 0) -> None:
    """Lakes fill the deepest basins; water from rain, snowmelt and springs runs
    downhill (bending through flat lowland) and a river is wherever enough of it
    gathers, so rivers rise in the mountains and run on into a lake or the sea."""
    land = w.land
    sea = drain_outlet(land)
    settle_lakes(w, sea, max_lakes)
    filled_surface = priority_fill(w.height, sea, eps=1e-5)
    receivers = meandering_receivers(w, filled_surface, sea)
    water_in = rain_per_cell(w) + snowmelt_per_cell(w)
    river_flow_threshold = float(river_cells or max(SMALLEST_RIVER_FLOW, land.sum() * RIVER_FLOW_SHARE_OF_LAND_CELLS))
    springs = spring_cells(w)
    water_in = water_in + springs * SPRING_SHARE_OF_A_RIVER * river_flow_threshold
    flow = accumulate(filled_surface, receivers, water_in * land)
    w.recv, w.flow = receivers, flow
    w.river = land & ~w.lake & (flow >= river_flow_threshold)
    w.sea = ~land
    w.overrides["springs"] = springs
    w.log.append(f"hydrology: {int(w.lake.sum())} lake cells, {int(w.river.sum())} river cells, "
                 f"{int(springs.sum())} springs (threshold {river_flow_threshold:.0f})")


def settle_lakes(w: World, sea: np.ndarray, max_lakes: int) -> None:
    land = w.land
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
    if w.lake is not None:
        lakes |= w.lake
    lakes &= ~w.overrides.get("no_lake", np.zeros_like(land))
    breached_surface = np.where(basins & ~lakes, filled, h)
    w.height = np.where(lakes, filled, breached_surface)
    w.lake = lakes & land


def rain_per_cell(w: World) -> np.ndarray:
    wetness = w.moist if w.moist is not None else np.full(w.land.shape, 0.5)
    rain = RAIN_ON_DRY_LAND_PER_CELL + (RAIN_ON_WET_LAND_PER_CELL - RAIN_ON_DRY_LAND_PER_CELL) * wetness
    return rain


def snowmelt_per_cell(w: World) -> np.ndarray:
    land = w.land
    if not land.any():
        return np.zeros(land.shape)
    snowline = float(np.quantile(w.height[land], SNOWLINE_HEIGHT_QUANTILE))
    summit = float(w.height[land].max())
    height_above_snowline = np.clip((w.height - snowline) / max(1e-9, summit - snowline), 0.0, 1.0)
    is_cold = w.temp < COLDEST_TEMPERATURE_WITHOUT_SNOW if w.temp is not None else np.ones(land.shape, bool)
    snow_cover = np.where(is_cold | (height_above_snowline > 0.5), height_above_snowline, 0.0)
    snowmelt = SNOWMELT_AT_THE_SUMMIT_PER_CELL * np.sqrt(snow_cover) * land
    return snowmelt


def spring_count(w: World) -> int:
    count = int(np.clip(round(int(w.land.sum()) / LAND_CELLS_PER_SPRING), FEWEST_SPRINGS, MOST_SPRINGS))
    return count


def spring_cells(w: World) -> np.ndarray:
    """Groundwater comes out on hillsides: a few mid-to-high cells, spread apart,
    the same ones every time the water stage runs on this seed."""
    land = w.land & ~(w.lake if w.lake is not None else np.zeros_like(w.land))
    springs = np.zeros(land.shape, bool)
    if not land.any():
        return springs
    lowest, highest = np.quantile(w.height[land], SPRING_HEIGHT_QUANTILES)
    candidates = land & (w.height >= lowest) & (w.height <= highest) & (shore_distance(w.land) >= 3)
    rows, columns = np.nonzero(candidates)
    rng = np.random.default_rng((int(w.seed), 23))
    for candidate_index in rng.permutation(rows.size).tolist():
        if springs.sum() >= spring_count(w):
            break
        row, column = int(rows[candidate_index]), int(columns[candidate_index])
        if is_near_a_spring(springs, row, column):
            continue
        springs[row, column] = True
    return springs


def is_near_a_spring(springs: np.ndarray, row: int, column: int) -> bool:
    reach = SMALLEST_SPRING_SPACING_IN_CELLS
    nearby = springs[max(0, row - reach):row + reach + 1, max(0, column - reach):column + reach + 1]
    near_a_spring = bool(nearby.any())
    return near_a_spring


def meander_direction_field(w: World) -> Tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng((int(w.seed), 29))
    xs, ys = _grid(w)
    angle_noise = Fbm(rng, 3, gain=0.5)(xs / MEANDER_NOISE_SCALE_IN_CELLS, ys / MEANDER_NOISE_SCALE_IN_CELLS)
    angle = norm01(angle_noise) * 4 * math.pi
    direction_x, direction_y = np.cos(angle), np.sin(angle)
    return direction_x, direction_y


def meandering_receivers(w: World, surface: np.ndarray, sink: np.ndarray) -> np.ndarray:
    """Steepest descent, except on flat ground: there each cell may turn toward a
    slowly swinging preferred direction, as long as the step still goes downhill,
    so lowland rivers bend instead of running in straight D8 steps."""
    H, W = surface.shape
    pad = np.pad(surface, 1, constant_values=np.inf)
    drops = np.stack([(surface - pad[1 + dy:1 + dy + H, 1 + dx:1 + dx + W]) / D8[k] for k, (dy, dx) in enumerate(N8)])
    steepest_drop = drops.max(axis=0)
    flatness = np.clip(1.0 - steepest_drop / STEEPEST_SLOPE_THAT_MEANDERS, 0.0, 1.0)
    direction_x, direction_y = meander_direction_field(w)
    alignment = np.stack([(dx * direction_x + dy * direction_y) / D8[k] for k, (dy, dx) in enumerate(N8)])
    downhill = drops > 0
    preference = drops / np.maximum(steepest_drop, 1e-12) + MEANDER_PULL * flatness * alignment
    preference = np.where(downhill, preference, -np.inf)
    chosen = np.argmax(preference, axis=0)
    rows, columns = np.mgrid[0:H, 0:W]
    step_y = np.array([offset[0] for offset in N8])[chosen]
    step_x = np.array([offset[1] for offset in N8])[chosen]
    receivers = (rows + step_y) * W + (columns + step_x)
    receivers[(steepest_drop <= 0) | sink] = -1
    flat_receivers = receivers.ravel()
    return flat_receivers


# ------------------------------------------------------------------ climate and biomes

def climate(w: World, reuse_noise: bool = False) -> None:
    """``reuse_noise``: use the moisture noise from the last call instead of drawing
    new noise (the editor re-runs climate on an edited map without reshuffling it)."""
    rng = w.rng
    xs, ys = _grid(w)
    v = (ys + 0.5) / w.H
    land = w.land
    hl = np.where(land, w.height, 0)
    hn = hl / max(1e-6, float(hl.max()))
    temp = 0.62 + 0.28 * (v - 0.5) - 0.85 * hn + w.overrides.get("heat", 0)
    # the prevailing wind (westerlies unless the layout was mirrored): moisture is picked
    # up over water, rained out when the air climbs
    m = np.zeros_like(hl)
    cur = np.ones(w.H)
    prev = np.zeros(w.H)
    for x in (range(w.W) if w.wind[0] >= 0 else range(w.W - 1, -1, -1)):
        col = hl[:, x]
        wet = ~land[:, x] | w.lake[:, x] if w.lake is not None else ~land[:, x]
        rise = np.maximum(col - prev, 0)
        cur = np.where(wet, np.minimum(1.0, cur + 0.25), cur * 0.986 - 2.2 * rise)
        cur = np.clip(cur, 0, 1)
        m[:, x] = cur
        prev = col
    m = ndi.gaussian_filter(m, 1.5)
    dcoast = shore_distance(land)
    driver = ndi.distance_transform_edt(~(w.river | w.lake)) if w.river is not None else dcoast
    noise = w.overrides.get("moist_noise") if reuse_noise else None
    if noise is None:
        noise = norm01(Fbm(rng, 4, gain=0.6)(xs / 12, ys / 12)) - 0.5
    w.overrides["moist_noise"] = noise
    moist = 0.16 + 0.34 * m + 0.18 * np.exp(-dcoast / 8) + 0.22 * np.exp(-driver / 3) + 0.55 * noise
    moist += w.overrides.get("rain", 0)
    w.temp, w.moist = temp, np.clip(moist, 0, 1)


def make_tiers(w: World, q=None) -> None:
    """Five elevation tiers on land (0 lowland .. 4 peaks) from height quantiles
    (or from fixed thresholds ``q``, so edits don't shift every tier on the map)."""
    land = w.land
    if q is None:
        q = np.quantile(w.height[land], [0.30, 0.58, 0.80, 0.93])
    q = np.asarray(q, dtype=float)
    t = np.digitize(w.height, q).astype(np.int8)
    t[~land] = 0
    w.tiers = t
    w.overrides["tier_q"] = q


def biomes(w: World) -> None:
    land = w.land
    h, t, m = w.height, w.temp, w.moist
    hn = np.where(land, h, 0) / max(1e-6, float(h[land].max()))
    dsea = shore_distance(land)
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
    b[w.tiers >= 4] = "M"
    b[(w.tiers >= 3) & (slope > 0.05)] = "M"
    b[(w.height > np.quantile(w.height[land], 0.985)) & (t < 0.25)] = "S"
    for code, mask in w.overrides.get("biome", []):
        b[mask & land] = code
    b[~land] = "~"
    b[~land & (h < -0.30)] = "="
    b[w.lake] = "o"
    w.biome = b
