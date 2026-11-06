from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.ndimage import binary_dilation

from .generate import WALKABLE
from .raised_ground_dressing import grow_cluster, is_drawn_as_tall_grass, is_inside
from .scenery_layer import (RAISED_GROUND_CLASS, cell_is_under_tree_crown_reach, cells_under_tree_crown_reach,
                             footprint_of_prop, place_prop, placed_props)

GRASS_CLASS = "g"
TREE_CLASS = "f"
LEDGE_CLASSES = "sejx"
SHORE_CLASSES = "dbwh"
PLAIN_WALKABLE_GROUND = "gtpd"
EXIT_MOUTH_KEPT_CLEAR_IN_CELLS = 3
EDGE_BONUS = 3.0
PATH_BONUS = 1.5
SHORE_BONUS = 4.0
SPACING_BETWEEN_CLUSTERS_IN_CELLS = 2
DENSITY_MULTIPLIER = 2.2
MIXED_PROP_SHARE_IN_A_CLUSTER = 0.3

Cell = Tuple[int, int]


@dataclass(frozen=True)
class DetailRecipe:
    name: str
    prop_names: Tuple[str, ...]
    clusters_per_thousand_cells_by_biome: Dict[str, float]
    cluster_sizes: Tuple[int, int]
    hugs: str
    mixes_props_in_a_cluster: bool = False


LAND_DEFAULT = "default"
DETAIL_RECIPES: Tuple[DetailRecipe, ...] = (
    DetailRecipe("flower patch", ("flowers0", "flowers1", "flowers2", "flowers3"),
                 {"plains": 5.0, "meadow": 5.0, "beach": 3.0, "forest": 3.0, "riverland": 4.0, LAND_DEFAULT: 1.0},
                 (6, 14), "open", mixes_props_in_a_cluster=True),
    DetailRecipe("mushroom row", ("mushroom0", "mushroom1", "mushroom2"),
                 {"forest": 5.0, "riverland": 3.5, "meadow": 3.0, "plains": 2.0, "beach": 1.5, LAND_DEFAULT: 0.5},
                 (2, 4), "edge", mixes_props_in_a_cluster=True),
    DetailRecipe("rock cluster", ("pebbles",),
                 {"beach": 5.0, "mountain": 4.0, "snow": 3.0, "desert": 3.0, "volcanic": 3.0, "riverland": 2.0, LAND_DEFAULT: 1.5},
                 (1, 3), "shore"),
    DetailRecipe("fallen log", ("log",),
                 {"forest": 1.2, "riverland": 1.0, "meadow": 0.8, "plains": 0.6, "beach": 0.6, LAND_DEFAULT: 0.2},
                 (1, 1), "edge"),
    DetailRecipe("stump", ("stump",),
                 {"forest": 0.8, "riverland": 0.6, "meadow": 0.6, "plains": 0.4, "beach": 0.3, LAND_DEFAULT: 0.1},
                 (1, 1), "edge"),
    DetailRecipe("sapling row", ("tree_small",),
                 {"forest": 1.5, "riverland": 1.0, "meadow": 1.0, "plains": 0.7, "beach": 0.4, LAND_DEFAULT: 0.3},
                 (2, 4), "edge"),
    DetailRecipe("boulder", ("boulder",),
                 {"mountain": 1.0, "snow": 0.8, "desert": 0.8, "plains": 0.4, "beach": 0.5, "riverland": 0.5, LAND_DEFAULT: 0.3},
                 (1, 1), "open"),
    DetailRecipe("leaf pile", ("leaves",),
                 {"forest": 0.5, "meadow": 0.4, "riverland": 0.3, LAND_DEFAULT: 0.0},
                 (1, 1), "edge"),
)


def scatter_small_details(route, recipes: Sequence[DetailRecipe] = DETAIL_RECIPES) -> None:
    surroundings = Surroundings.of(route)
    for recipe in recipes:
        clusters_per_thousand = recipe.clusters_per_thousand_cells_by_biome.get(
            route.spec.biome, recipe.clusters_per_thousand_cells_by_biome.get(LAND_DEFAULT, 0.0))
        cluster_count = int(round(DENSITY_MULTIPLIER * clusters_per_thousand * route.h * route.w / 1000.0 * (0.7 + 0.6 * route.rng.random())))
        for _cluster_number in range(cluster_count):
            place_one_cluster(route, recipe, surroundings)
    route.snap("small details")


@dataclass
class Surroundings:
    taken: np.ndarray
    kept_clear: np.ndarray
    placement_weight: Dict[str, np.ndarray]

    @classmethod
    def of(cls, route) -> "Surroundings":
        taken = np.zeros(route.cls.shape, dtype=bool)
        for placed_prop in placed_props(route):
            for cell_key in placed_prop["under"]:
                cell_y, cell_x = (int(part) for part in cell_key.split(","))
                taken[cell_y, cell_x] = True
        kept_clear = cells_kept_clear(route)
        surroundings = cls(taken, kept_clear, placement_weights(route))
        return surroundings


def cells_kept_clear(route) -> np.ndarray:
    exit_mouths = np.zeros(route.cls.shape, dtype=bool)
    for _exit, exit_cells in route.exits:
        for exit_cell in exit_cells:
            exit_mouths[exit_cell] = True
    reach = 2 * EXIT_MOUTH_KEPT_CLEAR_IN_CELLS + 1
    kept_clear = binary_dilation(exit_mouths, np.ones((reach, reach), dtype=bool))
    events = np.zeros(route.cls.shape, dtype=bool)
    for event in route.events:
        events[event.y, event.x] = True
    kept_clear |= binary_dilation(events, np.ones((3, 3), dtype=bool))
    for fixed_cell in route.__dict__.get("_ark_fixed") or {}:
        kept_clear[fixed_cell] = True
    for zone_cell in route.zone_cells("special"):
        kept_clear[zone_cell] = True
    return kept_clear


def placement_weights(route) -> Dict[str, np.ndarray]:
    near_edges = binary_dilation(np.isin(route.cls, list(TREE_CLASS + LEDGE_CLASSES + RAISED_GROUND_CLASS)), np.ones((3, 3), dtype=bool))
    on_the_way = np.zeros(route.cls.shape, dtype=bool)
    for path_cell in route.critical_path:
        on_the_way[path_cell] = True
    near_the_way = binary_dilation(on_the_way, np.ones((5, 5), dtype=bool))
    near_shore = binary_dilation(np.isin(route.cls, list(SHORE_CLASSES)), np.ones((3, 3), dtype=bool)) & ~np.isin(route.cls, list(SHORE_CLASSES))
    base = np.ones(route.cls.shape)
    weights = {"open": base + EDGE_BONUS * 0.4 * near_edges + PATH_BONUS * near_the_way,
               "edge": 0.1 * base + EDGE_BONUS * near_edges + PATH_BONUS * 0.3 * near_the_way,
               "shore": 0.15 * base + SHORE_BONUS * near_shore + EDGE_BONUS * 0.3 * near_edges}
    return weights


def is_grass_to_draw_on(route, surroundings: Surroundings, cell: Cell) -> bool:
    if not is_inside(route, *cell) or surroundings.taken[cell] or surroundings.kept_clear[cell]:
        return False
    if cell_is_under_tree_crown_reach(route.cls, cell):
        return False
    cell_class = str(route.cls[cell])
    on_grass = cell_class == GRASS_CLASS or (cell_class == RAISED_GROUND_CLASS and not is_drawn_as_tall_grass(route, cell))
    return on_grass


def can_block(route, surroundings: Surroundings, footprint: List[Cell]) -> bool:
    from .stepped_terraces import is_safe_to_block

    if not all(is_grass_to_draw_on(route, surroundings, cell) for cell in footprint):
        return False
    on_field = [cell for cell in footprint if route.cls[cell] == GRASS_CLASS]
    if not on_field:
        return True
    critical_cells = set(map(tuple, route.critical_path))
    if any(cell in critical_cells or touches_a_ledge(route, cell) for cell in on_field):
        return False
    is_walkable = np.isin(route.cls, list(WALKABLE))
    for cell in on_field:
        if not is_safe_to_block(is_walkable, *cell):
            return False
        is_walkable[cell] = False
    return True


def touches_a_ledge(route, cell: Cell) -> bool:
    touches = any(is_inside(route, cell[0] + step_y, cell[1] + step_x) and str(route.cls[cell[0] + step_y, cell[1] + step_x]) in LEDGE_CLASSES
                  for step_y in (-1, 0, 1) for step_x in (-1, 0, 1))
    return touches


def prop_blocks(prop_table: dict, prop_name: str) -> bool:
    blocks = bool(prop_table[prop_name].get("collision", 0))
    return blocks


def put_prop(route, surroundings: Surroundings, prop_table: dict, prop_name: str, top_left_cell: Cell) -> bool:
    footprint = footprint_of_prop(prop_table, prop_name, top_left_cell)
    if prop_blocks(prop_table, prop_name):
        if not can_block(route, surroundings, footprint):
            return False
        for cell in footprint:
            route.cls[cell] = RAISED_GROUND_CLASS
    elif not all(is_grass_to_draw_on(route, surroundings, cell) for cell in footprint):
        return False
    place_prop(route, prop_name, top_left_cell, footprint)
    for cell in footprint:
        surroundings.taken[cell] = True
    return True


def pick_cluster_start(route, surroundings: Surroundings, recipe: DetailRecipe) -> Optional[Cell]:
    weights = surroundings.placement_weight[recipe.hugs]
    free_grass = np.isin(route.cls, [GRASS_CLASS, RAISED_GROUND_CLASS]) & ~surroundings.kept_clear & ~cells_under_tree_crown_reach(route.cls)
    spaced = free_grass & ~binary_dilation(surroundings.taken, np.ones((2 * SPACING_BETWEEN_CLUSTERS_IN_CELLS + 1,) * 2, dtype=bool))
    candidate_weights = np.where(spaced, weights, 0.0).ravel()
    total_weight = float(candidate_weights.sum())
    if total_weight <= 0:
        return None
    chosen_index = int(route.rng.choice(candidate_weights.size, p=candidate_weights / total_weight))
    start_cell = (chosen_index // route.w, chosen_index % route.w)
    return start_cell


@lru_cache(maxsize=1)
def prop_table_of_the_ark_tileset() -> dict:
    from .ruletile import ArkTiler

    try:
        prop_table = ArkTiler.load().c.get("props", {})
    except FileNotFoundError:
        return {}
    return prop_table


def place_one_cluster(route, recipe: DetailRecipe, surroundings: Surroundings) -> None:
    prop_table = cached_prop_table(route)
    usable_props = [prop_name for prop_name in recipe.prop_names if prop_name in prop_table]
    if not usable_props:
        return
    start_cell = pick_cluster_start(route, surroundings, recipe)
    if start_cell is None:
        return
    cluster_size = int(route.rng.integers(recipe.cluster_sizes[0], recipe.cluster_sizes[1] + 1))
    hugs_edges = recipe.hugs == "edge"
    can_hold: Callable[[Cell], bool] = (lambda cell: is_grass_to_draw_on(route, surroundings, cell) and (not hugs_edges or touches_an_edge(route, cell)))
    cluster_cells = grow_cluster(route, start_cell, cluster_size, can_hold)
    patch_prop = usable_props[int(route.rng.integers(0, len(usable_props)))]
    for cluster_cell in cluster_cells:
        mixes_here = recipe.mixes_props_in_a_cluster and route.rng.random() < MIXED_PROP_SHARE_IN_A_CLUSTER
        prop_name = usable_props[int(route.rng.integers(0, len(usable_props)))] if mixes_here else patch_prop
        put_prop(route, surroundings, prop_table, prop_name, cluster_cell)


def touches_an_edge(route, cell: Cell) -> bool:
    touches = any(is_inside(route, cell[0] + step_y, cell[1] + step_x)
                  and str(route.cls[cell[0] + step_y, cell[1] + step_x]) in TREE_CLASS + LEDGE_CLASSES
                  for step_y, step_x in ((1, 0), (-1, 0), (0, 1), (0, -1)))
    return touches


def cached_prop_table(route) -> dict:
    prop_table = route.__dict__.get("_prop_table")
    if prop_table is None:
        prop_table = prop_table_of_the_ark_tileset()
        route.__dict__["_prop_table"] = prop_table
    return prop_table
