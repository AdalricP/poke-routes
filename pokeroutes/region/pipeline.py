"""The region pipeline. Each stage leaves an image for the stage strip.

Land and sea of a kind the seed picks -> erosion/rivers -> climate/biomes ->
routes. No landmarks and no towns: those are placed by hand in the editor (or by
"Generate map"), and routes are laid between the towns once there are some.
"""
from __future__ import annotations

import time
from typing import Optional

from . import geography, terrain
from .render import render_region
from .world import World

STRIP_SCALE = 3
PRESETS = ("generic",)


def build(seed: int, routes: bool = True, strip: bool = True, preset: str = "generic",
          geography_kind: Optional[str] = None) -> World:
    if preset not in PRESETS:
        raise ValueError(f"unknown geography preset {preset!r}; pick one of {', '.join(PRESETS)}")
    w = World(seed, preset=preset)
    t0 = time.time()

    def snap(title: str, stage: str) -> None:
        if strip:
            w.stages.append((title, render_region(w, STRIP_SCALE, stage)))
        w.log.append(f"[{time.time() - t0:5.1f}s] {title}")

    build_generic(w, snap, geography_kind)
    if routes:
        from . import network

        network.build_network(w)
        snap("routes", "routes")
    return w


def shared_terrain(w: World, snap) -> None:
    terrain.erode(w)
    terrain.hydrology(w)
    terrain.climate(w)
    terrain.hydrology(w)
    snap("2 erosion, rivers, lakes", "erosion")
    terrain.make_tiers(w)
    terrain.biomes(w)
    snap("3 climate -> biomes", "climate")


def build_generic(w: World, snap, geography_kind: Optional[str]) -> None:
    geography.land_and_sea(w, geography_kind)
    geography.heightmap(w)
    snap(f"1 terrain: {w.geography}", "terrain")
    shared_terrain(w, snap)
