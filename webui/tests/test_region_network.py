"""Regression tests for pokeroutes/region/network.py, reproducing OpenQodex review
findings 1, 2, 3 and 14 from review 20261103-050703-8d699b12f828.

Worlds here are hand-built (flat meadow, no rivers/lakes) rather than run through the
full terrain pipeline, so these stay fast and exercise the network code directly.

    python3 -m pytest -q webui/tests/test_region_network.py
"""
from __future__ import annotations

import numpy as np
import pytest

from pokeroutes.region.world import City, World
from pokeroutes.region import network


def make_world(W: int = 80, H: int = 80) -> World:
    w = World(seed=1, W=W, H=H)
    w.land = np.ones((H, W), dtype=bool)
    w.height = np.zeros((H, W), dtype=float)
    w.landmass = np.ones((H, W), dtype=np.int8)
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


def make_four_city_world() -> World:
    """A flat world with four towns in its corners."""
    w = make_world()
    w.cities = [city("tvangsburg", 8, 8), city("pilsau", 70, 8),
                city("huettenau", 8, 70), city("kaup", 70, 70)]
    return w


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


# ---------------------------------------------------------------- finding 3

def test_rebuild_keeps_hand_link_referencing_an_unpinned_route():
    """network.py:823 (user_link/build_network): a hand-drawn link that names an
    unpinned route by name used to vanish from w.links for good on a rebuild, because
    the unpinned route gets a fresh (possibly different) name and the ref can no
    longer resolve."""
    w = make_four_city_world()
    network.build_network(w, keep=[], plan=[])
    some_route = w.routes[0]["name"]
    ul = dict(name="Hand link 1", kind="user", src_ref={"route": some_route}, dst_ref={"city": "tvangsburg"})
    plan = list(w.links) + [ul]

    network.build_network(w, keep=[], plan=plan)

    names = {l["name"] for l in w.links}
    assert "Hand link 1" in names


# ---------------------------------------------------------------- finding 14

def test_lay_around_lays_goal_piece_past_a_single_pinned_route():
    """network.py:809-810 (lay_around/_linked): with one pinned route on a branch,
    add_kept restores that route's connection back to its source city. _linked()
    counted that backward connection as 'linked onward' and skipped laying the piece
    toward the link's goal, so the branch never reached its landmark."""
    w = make_world(W=60, H=60)
    w.cities = [city("basecity", 10, 10)]
    net = network.Network(w)
    city_k = 0

    # a pinned route flush against the city, with no onward connection of its own
    kA = net.add_node("route", [16, 5, 30, 16], name="Route 1", pinned=True,
                      link="Test branch", mode="land", kind_link="branch")
    net.nodes[kA]["paths"] = [[(x, 10) for x in range(16, 30)]]
    net.conns.append(dict(a=city_k, b=kA, cell_a=(15, 10), cell_b=(16, 10),
                          side_a="east", side_b="west", link="Test branch"))

    link = dict(name="Test branch", kind="branch", src=[(city_k, 0.0)], goal=(50, 10),
                mode="land", landmark="lm")
    network.lay_around(net, link, [kA])

    assert len(link["routes"]) > 1, "no new route was laid toward the goal"
    assert net.occ[10, 50] >= 0, "the goal cell was never reached"


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
