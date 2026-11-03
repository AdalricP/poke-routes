"""Regression tests for pokeroutes/region/network.py, reproducing OpenQodex review
findings 1, 2, 3 and 14 from review 20261103-050703-8d699b12f828.

Worlds here are hand-built (flat meadow, no rivers/lakes) rather than run through the
full terrain pipeline, so these stay fast and exercise the network code directly.

    python3 -m pytest -q webui/tests/test_region_network.py
"""
from __future__ import annotations

import numpy as np
import pytest

from pokeroutes.region.world import City, Landmark, World
from pokeroutes.region import network


def make_world(W: int = 80, H: int = 80) -> World:
    w = World(seed=1, W=W, H=H)
    w.land = np.ones((H, W), dtype=bool)
    w.height = np.zeros((H, W), dtype=float)
    w.half = np.ones((H, W), dtype=np.int8)
    w.sea = ~w.land
    w.lake = np.zeros((H, W), dtype=bool)
    w.river = np.zeros((H, W), dtype=bool)
    w.temp = np.zeros((H, W), dtype=float)
    w.moist = np.zeros((H, W), dtype=float)
    w.biome = np.full((H, W), "m", dtype="<U1")
    w.tiers = np.zeros((H, W), dtype=np.int8)
    w.overrides = {}
    w.cities = []
    w.landmarks = []
    w.links = []
    w.routes = []
    w.log = []
    return w


def city(cid: str, cx: int, cy: int) -> City:
    return City(id=cid, name=cid.title(), rect=[cx - 3, cy - 3, cx + 3, cy + 3],
                gym="", honours="", half=1, size="s")


# ---------------------------------------------------------------- finding 1

def test_name_routes_sets_biome_code_for_pinned_nodes():
    """network.py:521-523 (name_routes): pinned nodes used to be skipped before
    biome_code was ever computed, so route_json wrote biome_code=None for every kept
    route and export fell back to 'm' (meadow) for it."""
    w = make_world()
    w.biome[:] = "m"
    w.biome[2:6, 2:6] = "f"  # forest under the pinned route's rect
    net = network.Network(w)
    net.nodes = []
    pinned = dict(kind="route", pinned=True, name="Route 1", id="Route1", map="MAP_ROUTE1",
                  rect=[2, 2, 6, 6], paths=[], idx=0)
    net.nodes.append(pinned)

    network.name_routes(w, net)

    assert pinned.get("biome_code") == "f"


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
