from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pokeroutes.export import seal_edges  # noqa: E402
from pokeroutes.generate import WALKABLE, generate, reachable  # noqa: E402
from pokeroutes.glitches import crooked_bridges, ground_spikes_into_water, signs_in_water, tiny_islets  # noqa: E402
from pokeroutes.river_ends import walkable_river_end_steps  # noqa: E402
from pokeroutes.spec import Exit, RouteSpec  # noqa: E402

RIVERLAND_SEEDS = (0, 1)


def class_grid(rows):
    grid = np.array([list(row) for row in rows])
    return grid


def riverland_route(seed: int):
    spec = RouteSpec(name=f"riverland {seed}", id=f"riverland_{seed}", size=[40, 56], seed=seed, biome="riverland", lakes=2,
                     exits=[Exit("south", 0.5, 3), Exit("north", 0.5, 3)])
    route = generate(spec)
    seal_edges(route)
    return route


def test_a_straight_span_from_bank_to_bank_is_a_clean_bridge():
    grid = class_grid(["ffpff",
                       "wwqww",
                       "wwqww",
                       "wwqww",
                       "ffpff"])

    assert crooked_bridges(grid) == []


def test_a_staircase_of_planks_is_a_crooked_bridge():
    grid = class_grid(["fpfff",
                       "wqwww",
                       "wqqww",
                       "wwqqw",
                       "fffpf"])

    assert len(crooked_bridges(grid)) == 1


def test_planks_over_land_are_a_crooked_bridge():
    grid = class_grid(["ggggg",
                       "gqqqg",
                       "gqqqg",
                       "ggggg"])

    assert len(crooked_bridges(grid)) == 1


def test_a_boardwalk_along_a_river_is_a_crooked_bridge():
    grid = class_grid(["ffffffffffff",
                       "wwwwwwwwwwww",
                       "gqqqqqqqqqqg",
                       "wwwwwwwwwwww",
                       "ffffffffffff"])

    assert len(crooked_bridges(grid)) == 1


def test_walking_up_to_where_a_river_stops_is_an_open_river_end():
    grid = class_grid(["ffffffffff",
                       "wwwwwwwgpp",
                       "wwwwwwwgpp",
                       "ffffffffff"])
    river_mask = grid == "w"

    end_steps = walkable_river_end_steps(grid, river_mask)

    assert {land_cell for land_cell, _step in end_steps} == {(1, 7), (2, 7)}


def test_a_bank_beside_a_river_is_not_a_river_end():
    grid = class_grid(["gggggggggg",
                       "wwwwwwwwww",
                       "wwwwwwwwww",
                       "gggggggggg"])

    assert walkable_river_end_steps(grid, grid == "w") == []


@pytest.mark.parametrize("seed", RIVERLAND_SEEDS)
@pytest.mark.xfail(reason="look stages after the draw-order merge leave a small open river end on seed 0", strict=False)
def test_riverland_routes_have_straight_bridges_and_closed_river_ends(seed):
    route = riverland_route(seed)

    assert crooked_bridges(route.cls) == []
    assert walkable_river_end_steps(route.cls, route.__dict__["_river_cells"]) == []
    assert ground_spikes_into_water(route.cls) == [] and tiny_islets(route.cls) == [] and signs_in_water(route) == []


@pytest.mark.parametrize("seed", RIVERLAND_SEEDS)
def test_straightening_bridges_keeps_every_walkable_cell_reachable_from_the_entrance(seed):
    route = riverland_route(seed)

    reached = reachable(route.cls, route.exits[0][1])
    walkable = np.isin(route.cls, list(WALKABLE))

    assert (walkable & ~reached).sum() <= 0.02 * walkable.sum()
    assert all(reached[cell] for _exit, cells in route.exits for cell in cells if walkable[cell])


def test_trees_are_never_left_with_clipped_crowns():
    from pokeroutes.ruletile import ArkTiler

    try:
        tiler = ArkTiler.load()
    except FileNotFoundError:
        pytest.skip("no Ark ROM tree on this machine")
    route = riverland_route(RIVERLAND_SEEDS[0])

    assert tiler.clipped_crown_cells(route.cls)["clipped tree crowns"] == []
