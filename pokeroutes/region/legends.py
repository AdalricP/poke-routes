"""Legendary carving: deterministic 'effects' that reshape the terrain and leave
landmarks. Each one edits height / biome overrides and records a Landmark with a
footprint and a short story note (provisional, like every name here)."""
from __future__ import annotations

import math
from typing import List, Tuple

import numpy as np
from scipy import ndimage as ndi

from .world import Landmark, World


def _grid(w: World):
    ys, xs = np.mgrid[0:w.H, 0:w.W].astype(float)
    return xs, ys


def _add_biome(w: World, code: str, mask: np.ndarray) -> None:
    w.overrides.setdefault("biome", []).append((code, mask))


def _bbox(mask: np.ndarray, pad: int = 0) -> List[int]:
    ys, xs = np.nonzero(mask)
    return [int(xs.min()) - pad, int(ys.min()) - pad, int(xs.max()) + 1 + pad, int(ys.max()) + 1 + pad]


def _addf(w: World, key: str, arr: np.ndarray) -> None:
    w.overrides[key] = w.overrides.get(key, 0) + arr


# ------------------------------------------------------------------ Groudon

def groudon(w: World) -> None:
    """The Glutberg: a cone raised on the Oberland uplands, a crater, lava tongues
    running downhill, and an ash plume blown east by the westerlies that leaves
    volcanic slopes near the cone and ash badlands (desert) further out."""
    xs, ys = _grid(w)
    dcoast = ndi.distance_transform_edt(w.land)
    hs = ndi.gaussian_filter(np.where(w.land, w.height, 0), 3)
    up = w.overrides.get("upland", 0)
    py_, px_ = np.unravel_index(int(np.argmax(np.where(w.landmass == 1, hs, -1))), hs.shape)
    dpk = np.hypot(xs - px_, ys - py_)
    # on the shoulder of the main range (not on its top), on the side facing the lowlands
    score = np.where((w.landmass == 1) & (dcoast >= 12), hs + 0.3 * up - 0.02 * np.abs(dpk - 13) + 0.004 * xs, -1)
    score[:, int(w.W * 0.55):] = -1
    cy, cx = np.unravel_index(int(np.argmax(score)), score.shape)
    r = np.hypot(xs - cx, ys - cy)
    peak = float(w.height[w.land].max())
    rough = 1 + 0.25 * (np.sin(np.arctan2(ys - cy, xs - cx) * 5 + w.seed) * np.cos(r * 0.9))
    w.height = w.height + np.where(w.land, 0.75 * peak * np.exp(-(r * rough / 6.0) ** 2) - 0.2 * peak * np.exp(-(r / 1.5) ** 2), 0)
    # lava tongues: steepest descent from the crater rim in five directions
    lava = np.zeros_like(w.land)
    rng = np.random.default_rng(w.seed * 11 + 5)
    h = w.height + rng.random(w.height.shape) * 0.02  # jitter so the tongues wander
    for k in range(3):
        a = 2 * math.pi * k / 3 + rng.uniform(0, 2)
        y, x = int(round(cy + 2.0 * math.sin(a))), int(round(cx + 2.0 * math.cos(a)))
        for step in range(int(rng.integers(6, 11))):
            lava[y, x] = True
            if step % 2 == 0 and rng.random() < 0.6:
                lava[min(w.H - 1, y + 1), x] = True
            nb = [(h[y + dy, x + dx], y + dy, x + dx) for dy, dx in ((0, 1), (1, 0), (0, -1), (-1, 0))
                  if 0 < y + dy < w.H - 1 and 0 < x + dx < w.W - 1 and not lava[y + dy, x + dx]]
            if not nb:
                break
            z, ny, nx = min(nb)
            if z >= h[y, x] + 0.01 or not w.land[ny, nx]:
                break
            y, x = ny, nx
    lava &= w.land
    lava |= (r < 1.8) & w.land
    # ash plume downwind (east)
    dx, dy = xs - cx, ys - cy
    along, across = dx, np.abs(dy + 0.15 * dx)
    from .terrain import Fbm, norm01

    pn = norm01(Fbm(rng, 4)(xs / 6, ys / 6))
    reach = 22 + 10 * pn
    plume = (along > -3) & (across < 3 + 0.40 * np.maximum(along, 0) * (0.6 + 0.8 * pn)) & (along < reach)
    near = ((r < 6.0 + 2 * pn) | (plume & (along < 8 + 6 * pn))) & ~(r < 1.6)
    _add_biome(w, "a", plume & ~near & w.land)
    _add_biome(w, "v", near & w.land)
    _add_biome(w, "l", lava)
    _addf(w, "heat", 0.35 * np.exp(-r / 9.0))
    _addf(w, "rain", -0.35 * plume * np.exp(-np.maximum(along, 0) / 30))
    w.overrides["no_route"] = w.overrides.get("no_route", np.zeros_like(w.land)) | lava
    w.overrides["volcano"] = (int(cx), int(cy))
    w.landmarks.append(Landmark(
        "glutberg", "Groudon", "Glutberg", "volcano", int(cx), int(cy), _bbox(r < 8),
        "Dormant volcano over the foundries. Groudon sleeps under it; its heat keeps the slopes "
        "warm in winter and its old lava tongues still steam. The ash blows east and has turned the "
        "land downwind into badlands where ground dragons den.", True))


# ------------------------------------------------------------------ Kyogre

def kyogre(w: World) -> None:
    """A trench in the outer sea off the strait, and the storm coast it feeds: heavy
    rain on the nearest mainland coast, cliffs and sea caves."""
    xs, ys = _grid(w)
    dland = ndi.distance_transform_edt(~w.land)
    sx, sy = w.strait
    cand = (~w.land) & (dland >= 5) & (dland <= 10) & (ys < sy) & (xs > w.W * 0.30) & (xs < sx - 3)
    cand &= ~w.lagoon
    if not cand.any():
        cand = (~w.land) & (dland >= 4) & (ys < sy)
    score = np.where(cand, -np.hypot(xs - (sx - 18), ys - (sy - 8)) * 0.6 + dland, -1e9)
    ty, tx = np.unravel_index(int(np.argmax(score)), score.shape)
    e = ((xs - tx) / 9.0) ** 2 + ((ys - ty) / 2.6) ** 2
    trench = (e < 1) & ~w.land
    w.height = np.where(~w.land, w.height - 0.45 * np.exp(-e * 1.6), w.height)
    # storm coast: the mainland coast nearest the trench
    dtr = np.hypot(xs - tx, ys - ty)
    storm = w.land & (w.landmass == 1) & (dtr < 24)
    _addf(w, "rain", 0.45 * storm * np.exp(-dtr / 18))
    # sea caves: the highest coastal cell on that stretch, raised into cliffs
    coast = w.land & ndi.binary_dilation(~w.land) & storm
    if coast.any():
        hc = np.where(coast, w.height + 0.002 * -dtr, -1)
        cy, cx = np.unravel_index(int(np.argmax(hc)), hc.shape)
    else:
        cy, cx = ty + 4, tx
    rc = np.hypot(xs - cx, ys - cy)
    w.height = np.where(w.land, w.height + 0.10 * np.exp(-(rc / 3.5) ** 2), w.height)
    w.overrides["trench"] = trench
    w.landmarks.append(Landmark(
        "kyogre_deep", "Kyogre", "the Deep Rinne", "trench", int(tx), int(ty), _bbox(trench),
        "A trench in the outer sea where the water turns black. Kyogre lies at the bottom. Storm "
        "tides start here, and Pilsau's fishermen leave the season's first fish for 'the one underneath'.",
        False, "sea"))
    w.landmarks.append(Landmark(
        "kyogre_caves", "Kyogre", "Brandung Caves", "seacave", int(cx), int(cy), _bbox(rc < 3.5),
        "Sea caves cut into the cliffs of the storm coast, facing the Deep Rinne. At low tide the "
        "back chamber drains and shows old carvings of a whale.", True))


# ------------------------------------------------------------------ Rayquaza

def rayquaza(w: World) -> None:
    """Romowe: a ring of peaks on the Nehrung's Samland block around a sheltered,
    green valley, with one gap. A pillar stands in the middle."""
    xs, ys = _grid(w)
    neh = (w.landmass == 2) & ~w.overrides.get("spit", np.zeros_like(w.land))
    d = ndi.distance_transform_edt(neh)
    cy, cx = np.unravel_index(int(np.argmax(d)), d.shape)
    R = float(min(6.5, max(4.0, d[cy, cx] - 2.0)))
    r = np.hypot(xs - cx, ys - cy)
    ang = np.arctan2(ys - cy, xs - cx)
    # the gap faces the strait (west-north-west), where people come from
    sx, sy = w.strait
    gap_a = math.atan2(sy - cy, sx - cx) * 0.4 + math.pi * 0.6
    da = np.abs((ang - gap_a + np.pi) % (2 * np.pi) - np.pi)
    ring = np.exp(-((r - R) / 1.5) ** 2) * (1 - 0.85 * np.exp(-(da / 0.32) ** 2))
    peak = float(w.height[w.land].max())
    w.height = np.where(w.land, w.height + 0.55 * peak * ring, w.height)
    inner = (r < R - 1.0) & w.land
    plateau = float(np.percentile(w.height[w.land], 70))
    # the valley floor tilts down toward the gap so it drains instead of filling into a lake
    toward = (xs - cx) * math.cos(gap_a) + (ys - cy) * math.sin(gap_a)
    w.height = np.where(inner, plateau - 0.006 * toward, w.height)
    gapcut = (da < 0.45) & (r >= R - 1.5) & (r < R + 2.5) & w.land
    w.height = np.where(gapcut, np.minimum(w.height, plateau - 0.006 * toward), w.height)
    ridge = (ring > 0.45) & w.land
    _add_biome(w, "M", ridge)
    _add_biome(w, "g", inner)
    w.overrides["no_lake"] = w.overrides.get("no_lake", np.zeros_like(w.land)) | (r < R + 1)
    _addf(w, "rain", 0.3 * (r < R + 2))
    w.overrides["den"] = (int(cx), int(cy), R, gap_a)
    gx, gy = int(round(cx + (R + 1.5) * math.cos(gap_a))), int(round(cy + (R + 1.5) * math.sin(gap_a)))
    w.landmarks.append(Landmark(
        "romowe", "Rayquaza", "Romowe", "den", int(cx), int(cy), _bbox(r < R + 1.5),
        "The lost grove of the Sambi, inside a ring of peaks with one way in. Dragons nest on the "
        "ridges; the valley floor is warm, wet and overgrown. The green one sleeps at the pillar.", True))
    w.landmarks.append(Landmark(
        "sky_pillar", "Rayquaza", "Kriwe's Pillar", "pillar", int(cx), int(cy), [int(cx) - 1, int(cy) - 1, int(cx) + 2, int(cy) + 2],
        "A broken stone pillar in the middle of Romowe where the last Kriwe was seen in 52 AL.", False))
    w.overrides["den_gate"] = (gx, gy)


# ------------------------------------------------------------------ Lugia

def lugia(w: World) -> None:
    """The Silver Skerries: a few small islands far out in the northern sea, with
    whirlpools in the channels between them."""
    xs, ys = _grid(w)
    dland = ndi.distance_transform_edt(~w.land)
    cand = (~w.land) & (ys < w.H * 0.4) & (ys > 8) & (xs > 10) & (xs < w.W - 10) & ~w.lagoon
    dtr = np.full(dland.shape, 99.0)
    tr = w.landmark("kyogre_deep")
    if tr:
        dtr = np.hypot(xs - tr.x, ys - tr.y)
    cand &= dtr > 14
    cy, cx = np.unravel_index(int(np.argmax(np.where(cand, np.minimum(dland, 11) - 0.05 * np.abs(xs - w.W * 0.40), -1))), dland.shape)
    rng = np.random.default_rng(w.seed * 7 + 3)
    isl = np.zeros_like(w.land)
    hv = np.zeros(w.land.shape)
    spots = []
    for k in range(4):
        a = 2 * math.pi * k / 4 + rng.uniform(-0.4, 0.4)
        rr = 0 if k == 0 else rng.uniform(5.0, 6.5)
        ix, iy = cx + rr * math.cos(a), cy + rr * 0.75 * math.sin(a)
        rad = rng.uniform(1.3, 2.0) if k else 2.6
        m = np.hypot(xs - ix, ys - iy) < rad
        isl |= m
        hv = np.maximum(hv, np.where(m, 0.04 + 0.08 * (1 - np.hypot(xs - ix, ys - iy) / rad), 0))
        spots.append((ix, iy))
    isl &= dland > 2
    w.land = w.land | isl
    w.height = np.where(isl, hv + 0.01, w.height)
    w.landmass = np.where(isl, 3, w.landmass).astype(np.int8)
    # whirlpools: sea cells between pairs of islands
    pools = []
    for (ax, ay), (bx, by) in zip(spots[1:], spots[2:] + spots[1:2]):
        px, py = int(round((ax + bx) / 2)), int(round((ay + by) / 2))
        if not w.land[py, px]:
            pools.append((px, py))
    fp = _bbox(isl, pad=1)
    w.overrides["isles"] = isl
    w.landmarks.append(Landmark(
        "silver_skerries", "Lugia", "Silver Skerries", "islands", int(cx), int(cy), fp,
        "Bare islands in the open sea, ringed by whirlpools that only calm when a silver wing is "
        "seen overhead. Kaup's counting rhyme starts here. Lugia roosted on the big one before it "
        "was taken.", True, "sea"))
    for i, (px, py) in enumerate(pools):
        w.landmarks.append(Landmark(f"whirlpool_{i}", "Lugia", "whirlpool", "whirlpool", px, py, [px, py, px + 1, py + 1],
                                    "A standing whirlpool between the Skerries.", False, "sea"))


# ------------------------------------------------------------------ Ho-Oh and the beasts

def main_river_mouth(w: World) -> Tuple[int, int, List[Tuple[int, int]]]:
    """The Oberland river with the most flow at the sea, and its stem upstream."""
    W = w.W
    riv = w.river & (w.landmass == 1)
    mouth_mask = riv & ndi.binary_dilation(~w.land)
    if not mouth_mask.any():
        mouth_mask = riv
    ys_, xs_ = np.mgrid[0:w.H, 0:w.W]
    sx, sy = w.strait
    dst = np.hypot(xs_ - sx, ys_ - sy)
    fl = np.where(mouth_mask & (dst > 14), np.log1p(w.flow) - 0.045 * dst, -1e9)
    my, mx = np.unravel_index(int(np.argmax(fl)), fl.shape)
    # walk upstream along the biggest tributary
    inflow = {}
    for i in np.flatnonzero(w.river.ravel()):
        r = int(w.recv[i])
        if r >= 0:
            inflow.setdefault(r, []).append(int(i))
    stem = [(mx, my)]
    cur = my * W + mx
    for _ in range(60):
        ups = inflow.get(cur, [])
        if not ups:
            break
        cur = max(ups, key=lambda i: w.flow.ravel()[i])
        stem.append((cur % W, cur // W))
    return mx, my, stem


def hooh(w: World) -> None:
    """Brandwald: a forest Ho-Oh burned and that grew back, with the Gold Feather
    Tower in the middle and the three beasts posted around it. It sits a short walk
    from the lower Preyl, where the capital will stand."""
    xs, ys = _grid(w)
    mx, my, stem = main_river_mouth(w)
    k = min(len(stem) - 1, 9)
    px, py = stem[k]
    w.overrides["capital_site"] = (px, py)
    w.overrides["preyl"] = stem
    dcoast = ndi.distance_transform_edt(w.land)
    dr = np.hypot(xs - px, ys - py)
    driver = ndi.distance_transform_edt(~w.river)
    taken = np.zeros_like(w.land)
    for lm in w.landmarks:
        x0, y0, x1, y1 = lm.footprint
        taken[max(0, y0 - 4):y1 + 4, max(0, x0 - 4):x1 + 4] = True
    ok = (w.landmass == 1) & (dcoast >= 6) & (dr >= 15) & (dr <= 24) & (driver >= 2) & ~taken
    if not ok.any():
        ok = (w.landmass == 1) & (dcoast >= 4) & (dr >= 12)
    score = np.where(ok, w.moist - 0.01 * dr, -9)
    ty, tx = np.unravel_index(int(np.argmax(score)), score.shape)
    r = np.hypot(xs - tx, ys - ty)
    burn = (r < 6.5 + 1.2 * np.sin(np.arctan2(ys - ty, xs - tx) * 3)) & w.land
    _add_biome(w, "B", burn)
    _add_biome(w, "F", (r >= 6.5) & (r < 9) & w.land & (w.biome == "f"))
    w.landmarks.append(Landmark(
        "brandwald", "Ho-Oh", "Brandwald", "burn", int(tx), int(ty), _bbox(burn),
        "A forest Ho-Oh set alight on the night Tvangste burned, grown back thick with birch and "
        "fireweed. Old charred trunks still stand among the young trees.", False))
    w.landmarks.append(Landmark(
        "gold_feather_tower", "Ho-Oh", "Gold Feather Tower", "tower", int(tx), int(ty), [int(tx) - 1, int(ty) - 1, int(tx) + 2, int(ty) + 2],
        "A tower older than the Order, in the middle of the Brandwald. The Feast of the Gold "
        "Feather starts from its foot each autumn. Ho-Oh is said to land on the roof.", True))
    beasts = [("raikou", "Raikou's Watch", "Raikou keeps watch on the north road; lightning-split trees around it."),
              ("entei", "Entei's Hearth", "Entei lies on a scorched rise that never grows back."),
              ("suicune", "Suicune's Spring", "Suicune guards a clear spring at the edge of the burn.")]
    a0 = math.atan2(py - ty, px - tx)
    pos = []
    for i in range(3):
        a = a0 + math.pi / 3 + 2 * math.pi * i / 3
        pos.append((int(round(tx + 4.5 * math.cos(a))), int(round(ty + 4.5 * math.sin(a)))))
    # Suicune takes the station closest to water
    wet = ndi.distance_transform_edt(~(w.river | w.lake | ~w.land))
    order = sorted(range(3), key=lambda i: wet[pos[i][1], pos[i][0]])
    names = {order[0]: beasts[2], order[1]: beasts[0], order[2]: beasts[1]}
    for i, (sx, sy) in enumerate(pos):
        bid, nm, note = names[i]
        w.landmarks.append(Landmark(f"{bid}_station", bid.title(), nm, "station", sx, sy, [sx, sy, sx + 1, sy + 1],
                                    note + " Can't be caught until Ho-Oh is.", False))


def carve(w: World) -> None:
    groudon(w)
    kyogre(w)
    rayquaza(w)
    lugia(w)
    hooh(w)
