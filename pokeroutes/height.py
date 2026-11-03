"""One height map for the whole world, sampled where a route sits.

The noise lattice is hashed from world coordinates (not drawn from the route's
rng), so two routes that touch see the same hills along their shared edge. A
route's place in the world is ``spec.world`` (metatiles), its region origin, or one
picked from its seed.
Height is cut into tiers on a coarse block grid, so every step between tiers is
a straight run a d-pad player can read: a ledge, a waterfall, stairs.

The whole world also tilts gently down to the south. GBA tiles can only show a
drop that faces the camera (south ledges, waterfalls falling down-screen), so
on a tilted world most steps are ones the map can draw.
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

WORLD_SEED = 1729
BLOCK = 6  # metatiles per tier block: steps run straight for at least this long
WAVELENGTH = 72.0  # metatiles across one big hill
# metres-ish per tier: the mountain shows every small step, lowland only the big ones
TIER_STEP = {"mountain": 0.07, "volcanic": 0.08, "riverland": 0.09}
DEFAULT_STEP = 0.11
TILT = 0.004  # height lost per metatile walked south


def _hash(ix: np.ndarray, iy: np.ndarray, octave: int) -> np.ndarray:
    h = (ix.astype(np.int64) * 374761393 + iy.astype(np.int64) * 668265263
         + (WORLD_SEED + octave * 9973) * 2246822519) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((h ^ (h >> 16)) & 0xFFFF) / 65535.0


def world_height(xs: np.ndarray, ys: np.ndarray, octaves: int = 3) -> np.ndarray:
    """Height 0..1 at world points (xs, ys broadcast together), before the tilt."""
    out, amp, total, scale = 0.0, 1.0, 0.0, WAVELENGTH
    for o in range(octaves):
        fx, fy = xs / scale, ys / scale
        x0, y0 = np.floor(fx), np.floor(fy)
        tx, ty = fx - x0, fy - y0
        tx, ty = tx * tx * (3 - 2 * tx), ty * ty * (3 - 2 * ty)
        a, b = _hash(x0, y0, o), _hash(x0 + 1, y0, o)
        c, d = _hash(x0, y0 + 1, o), _hash(x0 + 1, y0 + 1, o)
        out = out + amp * ((a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty)
        total += amp
        amp *= 0.5
        scale /= 2
    return out / total


def origin(spec) -> Tuple[int, int]:
    """World (x, y) of the route's north-west corner, in metatiles."""
    w = getattr(spec, "world", None)
    if w:
        return int(w[0]), int(w[1])
    reg = spec.region if isinstance(getattr(spec, "region", None), dict) else {}
    if reg.get("origin"):
        cz = int(reg.get("cell", 4) or 4)
        return int(reg["origin"][0]) * cz, int(reg["origin"][1]) * cz
    seed = int(getattr(spec, "seed", 0) or 0)  # a free-standing route: somewhere picked by its seed
    return (seed * 1009) % 4096, (seed * 617) % 4096


def tiers(spec, h: int, w: int) -> Optional[np.ndarray]:
    """Tier per metatile from the world height map, blocky (BLOCK x BLOCK)."""
    ox, oy = origin(spec)
    # blocks are aligned to the world grid, so neighbours cut the same steps
    bx0, by0 = ox // BLOCK, oy // BLOCK
    bx1, by1 = (ox + w - 1) // BLOCK + 1, (oy + h - 1) // BLOCK + 1
    bxs = (np.arange(bx0, bx1) + 0.5) * BLOCK
    bys = (np.arange(by0, by1) + 0.5) * BLOCK
    hm = world_height(bxs[None, :], bys[:, None]) - TILT * bys[:, None]
    step = TIER_STEP.get(getattr(spec, "biome", ""), DEFAULT_STEP)
    g = np.floor(hm / step).astype(np.int32)
    up = np.kron(g, np.ones((BLOCK, BLOCK), dtype=np.int32))
    sy, sx = oy - by0 * BLOCK, ox - bx0 * BLOCK
    return up[sy:sy + h, sx:sx + w]
