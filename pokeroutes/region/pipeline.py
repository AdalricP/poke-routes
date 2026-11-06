"""The region pipeline: terrain -> erosion/rivers -> climate/biomes -> legendary
carving -> cities -> routes. Each stage leaves an image for the stage strip."""
from __future__ import annotations

import time

import numpy as np
from PIL import ImageOps

from . import legends, settle, terrain
from .render import render_region
from .world import World

STRIP_SCALE = 3


def _flip_image(im, fx: bool, fy: bool):
    if fx:
        im = ImageOps.mirror(im)
    if fy:
        im = ImageOps.flip(im)
    return im


def build(seed: int, routes: bool = True, strip: bool = True) -> World:
    w = World(seed)
    t0 = time.time()

    def snap(title: str, stage: str) -> None:
        if strip:
            w.stages.append((title, render_region(w, STRIP_SCALE, stage)))
        w.log.append(f"[{time.time() - t0:5.1f}s] {title}")

    terrain.coastline(w)
    terrain.heightmap(w)
    snap("1 terrain: fBm + domain warp", "terrain")
    terrain.erode(w)
    terrain.hydrology(w)
    terrain.climate(w)
    terrain.hydrology(w)  # second pass: rain now follows the climate
    snap("2 erosion, rivers, lakes", "erosion")
    terrain.make_tiers(w)
    terrain.biomes(w)
    snap("3 climate -> biomes", "climate")
    legends.carve(w)
    # the stages above lay the region out in one frame; each seed picks its own way round
    fx, fy = (bool(b) for b in np.random.default_rng((seed, 7)).integers(0, 2, 2))
    w.mirror(fx, fy)
    w.stages = [(t, _flip_image(im, fx, fy)) for t, im in w.stages]
    terrain.hydrology(w)
    terrain.climate(w)
    terrain.make_tiers(w)
    terrain.biomes(w)
    snap("4 legendary carving", "legends")
    settle.settle(w)
    snap("5 cities", "cities")
    if routes:
        from . import network

        network.build_network(w)
        snap("6 route graph + roles", "routes")
    return w
