"""Water the map says the route crosses. When a route's link on the region map
runs over sea or a lake, the route has that water across its main path and you
Surf over it: while the route is built the lanes out there are kept as bridges
(so every stage still sees one connected route), and once it is finished they
become water again and a Surf gate is recorded on the route.

A route laid over open sea (region ``mode: sea``) is open water from edge to
edge: sandy islets with a grass heart, shallow sandbars and sea rocks (single,
2x2 clusters and ridges that shape channels) are scattered over it, never on the
surf line between the exits, and patches of Dive water lie in the deepest parts."""
from __future__ import annotations

from typing import List, Tuple

import numpy as np
from scipy.ndimage import binary_dilation, binary_erosion, distance_transform_edt

from .generate import WALKABLE, Route, Zone
from .noise import fbm

Cell = Tuple[int, int]

EVENT_KINDS_THAT_STAND_ON_GROUND = ("item_ball", "hidden_item", "sign", "berry_tree", "npc", "cut_tree",
                                    "rock_smash", "strength_boulder", "gate_switch", "sealed_gate")
SURF_LINE_CLEARANCE_IN_METATILES = 3
ISLET_COUNT_PER_THOUSAND_CELLS = 1.6
ISLET_RADIUS_RANGE_IN_METATILES = (2.0, 4.2)
ISLET_GRASS_HEART_SHARE_OF_RADIUS = 0.55
SANDBAR_COUNT_PER_THOUSAND_CELLS = 0.8
SANDBAR_LENGTH_RANGE_IN_METATILES = (5, 11)
SEA_ROCK_SHARE_OF_OPEN_SEA = 0.004
SEA_ROCK_SIDE_IN_METATILES = 2
EDGE_MARGIN_FOR_SEA_DRESSING = 3
SEA_ROCK_CLUSTER_SHARE_OF_ROCKS = 0.6
SEA_RIDGE_COUNT_PER_THOUSAND_CELLS = 0.9
SEA_RIDGE_LENGTH_RANGE_IN_METATILES = (4, 10)
SEA_RIDGE_WIDTH_IN_METATILES = 3
DIVE_DISTANCE_FROM_LAND_IN_METATILES = 7.0
DIVE_PATCH_NOISE_THRESHOLD = 0.55
DIVE_PATCH_NOISE_SCALE = 5.0


def open_surf_crossings(route: Route) -> int:
    from .region_water import SURF_CROSSING_KEY

    remembered = route.__dict__.get(SURF_CROSSING_KEY) or {}
    opened_count = 0
    for water_class, crossing_mask in remembered.items():
        crossing_cells = crossing_mask & np.isin(route.cls, list(WALKABLE))
        route.cls[crossing_cells] = water_class
        route.protect[crossing_cells] = False
        opened_count += int(crossing_cells.sum())
    is_open_sea = route_lies_on_open_sea(route)
    if is_open_sea:
        opened_count += flood_exit_mouths_with_sea(route)
        dress_open_sea(route)
    if not opened_count:
        return 0
    drop_things_left_standing_in_water(route)
    route.gates.append(surf_gate_record(route, opened_count, is_open_sea))
    route.log.append(f"surf: {opened_count} cells of the main path are the map's water; Surf needed")
    return opened_count


def route_lies_on_open_sea(route: Route) -> bool:
    region_block = route.spec.region if isinstance(getattr(route.spec, "region", None), dict) else {}
    lies_on_open_sea = region_block.get("mode") == "sea"
    return lies_on_open_sea


def sea_class() -> str:
    from .stages import code

    sea = code("SEA", "w")
    return sea


def flood_exit_mouths_with_sea(route: Route) -> int:
    sea = sea_class()
    flooded_count = 0
    for _exit, cells in route.exits:
        for cell in cells:
            for depth_cell in mouth_cells_inward(route, _exit, cell):
                if str(route.cls[depth_cell]) in WALKABLE:
                    route.cls[depth_cell] = sea
                    flooded_count += 1
    return flooded_count


def mouth_cells_inward(route: Route, exit_object, cell: Cell) -> List[Cell]:
    from .generate import inward

    cells = [inward(route, exit_object, cell, depth) for depth in range(4)]
    return cells


def drop_things_left_standing_in_water(route: Route) -> None:
    def stands_on_ground(event) -> bool:
        return event.kind not in EVENT_KINDS_THAT_STAND_ON_GROUND or str(route.cls[event.y, event.x]) in WALKABLE

    route.events = [event for event in route.events if stands_on_ground(event)]
    route.props = [prop for prop in route.props
                   if "y" not in prop or str(route.cls[int(prop["y"]), int(prop["x"])]) in WALKABLE]


def surf_gate_record(route: Route, opened_count: int, is_open_sea: bool) -> dict:
    note = ("open sea: Surf from exit to exit" if is_open_sea
            else "the map's water lies across the main path: Surf over it")
    gate_record = {"kind": "surf", "built": True, "from_map_water": True, "cells_of_water_on_the_path": opened_count,
                   "note": note}
    return gate_record


def surf_line_mask(route: Route) -> np.ndarray:
    line = np.zeros(route.cls.shape, dtype=bool)
    for cell in route.critical_path:
        line[cell] = True
    for _exit, cells in route.exits:
        for cell in cells:
            line[cell] = True
    if not line.any():
        return line
    clearance = binary_dilation(line, iterations=SURF_LINE_CLEARANCE_IN_METATILES)
    return clearance


def open_sea_mask(route: Route) -> np.ndarray:
    open_sea = route.cls == sea_class()
    open_sea[:EDGE_MARGIN_FOR_SEA_DRESSING, :] = False
    open_sea[-EDGE_MARGIN_FOR_SEA_DRESSING:, :] = False
    open_sea[:, :EDGE_MARGIN_FOR_SEA_DRESSING] = False
    open_sea[:, -EDGE_MARGIN_FOR_SEA_DRESSING:] = False
    return open_sea


def dress_open_sea(route: Route) -> None:
    keep_clear = surf_line_mask(route)
    islet_count = place_islets(route, keep_clear)
    sandbar_count = place_sandbars(route, keep_clear)
    ridge_count = place_sea_ridges(route, keep_clear)
    cluster_count = place_sea_rock_clusters(route, keep_clear)
    rock_count = place_sea_rocks(route, keep_clear)
    dive_cell_count = place_dive_water(route)
    route.log.append(f"open sea: {islet_count} islets, {sandbar_count} sandbars, {ridge_count} rock ridges, "
                     f"{cluster_count} rock clusters, {rock_count} sea rocks, {dive_cell_count} Dive water cells")


def place_islets(route: Route, keep_clear: np.ndarray) -> int:
    from .stages import code

    rng = route.rng
    wanted_count = int(round(route.h * route.w / 1000 * ISLET_COUNT_PER_THOUSAND_CELLS))
    sand = code("SAND", "p")
    yy, xx = np.mgrid[:route.h, :route.w]
    wobble = fbm(route.h, route.w, 4.0, rng)
    placed_count = 0
    for _attempt in range(wanted_count * 8):
        if placed_count >= wanted_count:
            break
        radius = rng.uniform(*ISLET_RADIUS_RANGE_IN_METATILES)
        centre_y, centre_x = int(rng.integers(0, route.h)), int(rng.integers(0, route.w))
        distance = np.hypot(yy - centre_y, (xx - centre_x) * 0.8) + (wobble - 0.5) * 2.0
        islet = distance <= radius
        if not islet.any() or not (islet <= open_sea_mask(route)).all():
            continue
        if (binary_dilation(islet, iterations=2) & (keep_clear | ~(route.cls == sea_class()))).any():
            continue
        heart = binary_erosion(islet, iterations=1) & (distance <= radius * ISLET_GRASS_HEART_SHARE_OF_RADIUS)
        route.cls[islet] = sand
        route.cls[heart] = "g"
        if radius >= 3.6:
            plant_tree_on_islet(route, centre_y, centre_x)
        route.zones.append(Zone("clearing", [tuple(map(int, cell)) for cell in zip(*np.nonzero(islet))], "islet",
                                {"islet": True}))
        placed_count += 1
    return placed_count


def plant_tree_on_islet(route: Route, centre_y: int, centre_x: int) -> None:
    top, left = centre_y - centre_y % 2, centre_x - centre_x % 2
    surrounding_block = route.cls[max(top - 1, 0):top + 3, max(left - 1, 0):left + 3]
    if surrounding_block.shape != (4, 4) or (surrounding_block == sea_class()).any():
        return
    route.cls[top:top + 2, left:left + 2] = "f"


def place_sandbars(route: Route, keep_clear: np.ndarray) -> int:
    from .stages import code

    rng = route.rng
    wanted_count = int(round(route.h * route.w / 1000 * SANDBAR_COUNT_PER_THOUSAND_CELLS))
    shallow = code("SHALLOW", "w")
    placed_count = 0
    for _attempt in range(wanted_count * 8):
        if placed_count >= wanted_count:
            break
        length = int(rng.integers(*SANDBAR_LENGTH_RANGE_IN_METATILES))
        bar_height, bar_width = (2, length) if rng.random() < 0.5 else (length, 2)
        if bar_height >= route.h or bar_width >= route.w:
            continue
        top, left = int(rng.integers(0, route.h - bar_height)), int(rng.integers(0, route.w - bar_width))
        rows, columns = slice(top, top + bar_height), slice(left, left + bar_width)
        bar = np.zeros(route.cls.shape, dtype=bool)
        bar[rows, columns] = True
        if not bar.any() or not (bar <= open_sea_mask(route)).all() or (binary_dilation(bar, iterations=1) & keep_clear).any():
            continue
        route.cls[bar] = shallow
        placed_count += 1
    return placed_count


def place_sea_rocks(route: Route, keep_clear: np.ndarray) -> int:
    from .stages import code

    rng = route.rng
    rock = code("BOULDER", "r")
    candidates = open_sea_mask(route) & ~keep_clear
    candidate_cells = list(zip(*np.nonzero(candidates)))
    wanted_count = int(len(candidate_cells) * SEA_ROCK_SHARE_OF_OPEN_SEA * (1 - SEA_ROCK_CLUSTER_SHARE_OF_ROCKS))
    placed_count = 0
    for cell_index in rng.permutation(len(candidate_cells))[:wanted_count * 3]:
        if placed_count >= wanted_count:
            break
        top, left = candidate_cells[cell_index]
        neighbourhood = route.cls[top - 1:top + SEA_ROCK_SIDE_IN_METATILES + 1, left - 1:left + SEA_ROCK_SIDE_IN_METATILES + 1]
        if neighbourhood.shape != (SEA_ROCK_SIDE_IN_METATILES + 2,) * 2 or not (neighbourhood == sea_class()).all():
            continue
        if keep_clear[top:top + SEA_ROCK_SIDE_IN_METATILES, left:left + SEA_ROCK_SIDE_IN_METATILES].any():
            continue
        route.cls[top:top + SEA_ROCK_SIDE_IN_METATILES, left:left + SEA_ROCK_SIDE_IN_METATILES] = rock
        placed_count += 1
    return placed_count


def is_free_open_sea(route: Route, block: np.ndarray, keep_clear: np.ndarray) -> bool:
    is_inside_open_sea = bool(block.any()) and bool((block <= open_sea_mask(route)).all())
    is_far_from_everything = not (binary_dilation(block, iterations=2) & (keep_clear | (route.cls != sea_class()))).any()
    is_free = is_inside_open_sea and is_far_from_everything
    return is_free


def ridge_cells(route: Route, top: int, left: int, length: int, runs_east_west: bool) -> np.ndarray:
    ridge = np.zeros(route.cls.shape, dtype=bool)
    for step in range(length):
        sway = int(round(np.sin(step * 0.45 + top * 0.3)))
        row = top + (sway if runs_east_west else step)
        column = left + (step if runs_east_west else sway)
        rows = slice(max(row, 0), row + (SEA_RIDGE_WIDTH_IN_METATILES if runs_east_west else 1))
        columns = slice(max(column, 0), column + (1 if runs_east_west else SEA_RIDGE_WIDTH_IN_METATILES))
        ridge[rows, columns] = True
    return ridge


def random_ridge_origin(route: Route, length: int) -> Tuple[int, int]:
    rng = route.rng
    top = int(rng.integers(EDGE_MARGIN_FOR_SEA_DRESSING, max(EDGE_MARGIN_FOR_SEA_DRESSING + 1, route.h - length - 3)))
    left = int(rng.integers(EDGE_MARGIN_FOR_SEA_DRESSING, max(EDGE_MARGIN_FOR_SEA_DRESSING + 1, route.w - length - 3)))
    return top, left


def place_sea_ridges(route: Route, keep_clear: np.ndarray) -> int:
    rng = route.rng
    wanted_count = int(round(route.h * route.w / 1000 * SEA_RIDGE_COUNT_PER_THOUSAND_CELLS))
    placed_count = 0
    for _attempt in range(wanted_count * 10):
        if placed_count >= wanted_count:
            break
        length = int(rng.integers(*SEA_RIDGE_LENGTH_RANGE_IN_METATILES))
        runs_east_west = bool(rng.random() < 0.5)
        top, left = random_ridge_origin(route, length)
        ridge = ridge_cells(route, top, left, length, runs_east_west)
        if not is_free_open_sea(route, ridge, keep_clear):
            continue
        route.cls[ridge] = "r"
        placed_count += 1
    return placed_count


def place_sea_rock_clusters(route: Route, keep_clear: np.ndarray) -> int:
    from .stages import code

    rng = route.rng
    rock = code("BOULDER", "r")
    candidate_cells = list(zip(*np.nonzero(open_sea_mask(route) & ~keep_clear)))
    wanted_count = int(len(candidate_cells) * SEA_ROCK_SHARE_OF_OPEN_SEA * SEA_ROCK_CLUSTER_SHARE_OF_ROCKS / 4)
    placed_count = 0
    for cell_index in rng.permutation(len(candidate_cells))[:wanted_count * 4]:
        if placed_count >= wanted_count:
            break
        top, left = (int(coordinate) for coordinate in candidate_cells[cell_index])
        cluster = np.zeros(route.cls.shape, dtype=bool)
        cluster[top:top + 2, left:left + 2] = True
        if cluster.sum() != 4 or not is_free_open_sea(route, cluster, keep_clear):
            continue
        route.cls[cluster] = rock
        placed_count += 1
    return placed_count


def place_dive_water(route: Route) -> int:
    from .classify import DIVE

    is_sea = route.cls == sea_class()
    distance_to_land = distance_transform_edt(np.pad(is_sea, 1, constant_values=True))[1:-1, 1:-1]
    patch_noise = fbm(route.h, route.w, DIVE_PATCH_NOISE_SCALE, route.rng)
    is_deep_enough = distance_to_land >= DIVE_DISTANCE_FROM_LAND_IN_METATILES
    dive = is_sea & is_deep_enough & (patch_noise >= DIVE_PATCH_NOISE_THRESHOLD)
    dive = binary_erosion(binary_dilation(dive, iterations=1), iterations=1) & is_sea & is_deep_enough
    route.cls[dive] = DIVE
    dive_cell_count = int(dive.sum())
    return dive_cell_count
