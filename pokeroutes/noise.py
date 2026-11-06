"""Small dependency-free value noise (fractal) on numpy grids."""
from __future__ import annotations

import numpy as np


def _smooth(t):
    return t * t * (3 - 2 * t)


def value_noise(h: int, w: int, scale: float, rng: np.random.Generator) -> np.ndarray:
    gh, gw = int(h / scale) + 2, int(w / scale) + 2
    lattice = rng.random((gh, gw))
    ys = np.arange(h) / scale
    xs = np.arange(w) / scale
    y0, x0 = ys.astype(int), xs.astype(int)
    ty, tx = _smooth(ys - y0)[:, None], _smooth(xs - x0)[None, :]
    a = lattice[y0][:, x0]
    b = lattice[y0][:, x0 + 1]
    c = lattice[y0 + 1][:, x0]
    d = lattice[y0 + 1][:, x0 + 1]
    return (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty


def fbm(h: int, w: int, scale: float, rng: np.random.Generator, octaves: int = 4, gain: float = 0.5) -> np.ndarray:
    out = np.zeros((h, w))
    amp, total = 1.0, 0.0
    for o in range(octaves):
        out += amp * value_noise(h, w, max(scale / (2 ** o), 1.0), rng)
        total += amp
        amp *= gain
    return out / total


def stretched_fbm(h: int, w: int, scale: float, rng: np.random.Generator, octaves: int = 2) -> np.ndarray:
    raw_noise = fbm(h, w, scale, rng, octaves=octaves)
    noise_range = max(1e-6, float(raw_noise.max() - raw_noise.min()))
    stretched_to_zero_to_one = (raw_noise - raw_noise.min()) / noise_range
    return stretched_to_zero_to_one
