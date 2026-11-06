"""The region pipeline. Each stage leaves an image for the stage strip.

generic (the default): land and sea of a kind the seed picks -> erosion/rivers ->
climate/biomes -> routes. No landmarks and no towns: those are placed by hand in
the editor, and routes are laid between the towns once there are some.

arklarna (opt in): the owner's own map. Its fixed coastline, legendary carving and
four towns, then routes between them, exactly as before presets existed.
"""
from __future__ import annotations

import time
from typing import Optional

from . import geography, legends, settle, terrain
from .render import render_region
from .world import World

STRIP_SCALE = 3
PRESETS = ("generic", "arklarna")


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

    if preset == "arklarna":
        build_arklarna(w, snap)
    else:
        build_generic(w, snap, geography_kind)
    if routes:
        from . import network

        network.build_network(w)
        snap("routes" if preset == "generic" else "6 route graph + roles", "routes")
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


def build_arklarna(w: World, snap) -> None:
    w.geography = "arklarna"
    terrain.arklarna_coastline(w)
    terrain.arklarna_heightmap(w)
    snap("1 terrain: fBm + domain warp", "terrain")
    shared_terrain(w, snap)
    legends.carve(w)
    terrain.hydrology(w)
    terrain.climate(w)
    terrain.make_tiers(w)
    terrain.biomes(w)
    snap("4 legendary carving", "legends")
    settle.settle(w)
    snap("5 cities", "cities")
