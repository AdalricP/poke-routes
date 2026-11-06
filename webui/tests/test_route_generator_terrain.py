"""Route generator terrain: no automatic people, field-move obstacles that really
block, rivers that stay whole, map water you Surf over, terraced mountains.

    python3 -m pytest -q webui/tests/test_route_generator_terrain.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from pokeroutes.export import report, seal_edges  # noqa: E402
from pokeroutes.generate import WALKABLE, generate, reachable  # noqa: E402
from pokeroutes.gates import class_grid_with_route_gates_open  # noqa: E402
from pokeroutes.obstacles import FIELD_MOVE_OBSTACLE_KINDS  # noqa: E402
from pokeroutes.rivers import RIVER_CELLS_KEY  # noqa: E402
from pokeroutes.spec import RouteSpec  # noqa: E402

SEEDS = (1, 2, 3)


def example_route(name: str, seed: int, **overrides):
    spec = RouteSpec.load(ROOT / "examples" / name)
    spec.seed = seed
    for field_name, value in overrides.items():
        setattr(spec, field_name, value)
    route = generate(spec)
    seal_edges(route)
    return route


def test_no_people_are_placed_on_a_route_by_the_generator():
    for seed in SEEDS:
        route = example_route("bernsteinweg.yaml", seed)
        roles = [event.data.get("role") for event in route.events if event.kind == "npc"]
        assert all(role in ("researcher", "blocker") for role in roles), roles


def test_every_field_move_obstacle_stands_in_a_gap_on_ground():
    for seed in SEEDS:
        route = example_route("erlenbruch.yaml", seed)
        open_grid = class_grid_with_route_gates_open(route)
        special_cells = set(route.zone_cells("special"))
        for event in route.events:
            if event.kind not in FIELD_MOVE_OBSTACLE_KINDS or (event.y, event.x) in special_cells:
                continue
            assert str(route.cls[event.y, event.x]) in WALKABLE
            open_sides = sum(1 for step_y, step_x in ((1, 0), (-1, 0), (0, 1), (0, -1))
                             if str(route.cls[event.y + step_y, event.x + step_x]) in WALKABLE)
            blocked_grid = open_grid.copy()
            blocked_grid[event.y, event.x] = "f"
            cuts_something_off = (reachable(open_grid, route.exits[0][1]) & ~reachable(blocked_grid, route.exits[0][1])).sum() > 1
            assert open_sides <= 2 or cuts_something_off


def test_riverland_rivers_cross_the_map_and_stay_whole():
    route = example_route("bernsteinweg.yaml", 4, biome="riverland", tileset="general_fortree")
    remembered = route.__dict__.get(RIVER_CELLS_KEY)
    assert remembered is not None and remembered.any()
    wet = np.isin(route.cls, ["w", "q", "a"])
    assert (wet | ~remembered).all()
    rows, columns = np.nonzero(remembered)
    spans_the_map = (rows.min() <= 1 and rows.max() >= route.h - 2) or (columns.min() <= 1 and columns.max() >= route.w - 2)
    assert spans_the_map


def test_open_sea_route_is_water_you_surf_across():
    spec = RouteSpec.load(ROOT / "examples/bernsteinweg.yaml")
    spec.biome, spec.tileset = "beach", "general_slateport"
    region_height, region_width = spec.height // 4, spec.width // 4
    spec.region = {"cell": 4, "mode": "sea", "water": {"grid": ["~" * region_width] * region_height},
                   "path": [[region_width // 2, row] for row in range(region_height)], "rivers": []}
    route = generate(spec)
    seal_edges(route)
    assert any(gate.get("kind") == "surf" and gate.get("built") for gate in route.gates)
    assert float(np.isin(route.cls, ["b", "w"]).mean()) > 0.6
    assert report(route)["reachable_fraction"] == 1.0


def test_mountain_climbs_terraces_on_stairs_and_stays_reachable():
    for biome, tileset in (("mountain", "general_lavaridge"), ("snow", "general_fallarbor"), ("volcanic", "general_lavaridge")):
        route = example_route("kiefernheide.yaml", 2, biome=biome, tileset=tileset)
        assert (route.cls == "z").any()
        assert (route.cls == "k").sum() > route.w * 3
        assert report(route)["reachable_fraction"] == 1.0
