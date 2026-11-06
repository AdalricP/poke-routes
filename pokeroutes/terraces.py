"""Mountain, snow and volcanic routes as stacked terraces (after Route 112, Mt.
Chimney, Mt. Pyre and the owner's mountain references).

The ground climbs toward the north in contour bands a few rows tall. Each band
edge that faces south is a cliff face two metatiles tall, running the width of
the shelf in long runs that jog up or down a step now and then, so the faces
read as parallel contour lines stacked up the mountain. The walkable parts are
the lanes the route already has plus a narrow shelf of floor beside them; the
rest is rock. Where a lane crosses a face it climbs on stairs; a side lane may
drop over it as a one-way ledge instead, wherever that strands nobody. Shelves
get boulders and tufts of grass, snow shelves snowy pines and a frozen pond, and
the top of a volcanic route a crater of lava inside a ring of cliff.

Runs once the route is connected; it never cuts off a walkable cell that was
reachable before (anything that would is put back the way it was)."""
from __future__ import annotations

from typing import Dict, List, Set, Tuple

import numpy as np
from scipy.ndimage import binary_dilation, distance_transform_cdt, label

from .generate import WALKABLE, Route, Zone, can_leave, reachable
from .noise import fbm

Cell = Tuple[int, int]

TERRACE_BIOMES = ("mountain", "snow", "volcanic")
BAND_HEIGHT_RANGE_IN_METATILES = (5, 8)
CONTOUR_JOG_RUN_RANGE_IN_METATILES = (3, 8)
CONTOUR_JOG_AMPLITUDE_IN_BANDS = 0.45
SHELF_REACH_RANGE_IN_METATILES = (1, 5)
FACE_HEIGHT_IN_METATILES = 2
WIDEST_CROSSING_CLIMBED_ON_STAIRS_ALONE = 4
STAIRS_WIDTH_IN_A_WIDE_CROSSING = 3
SIDE_LANE_LEDGE_CHANCE = 0.6
BOULDER_SHARE_OF_SHELF_FLOOR = 0.05
TUFT_COUNT_PER_HUNDRED_SHELF_CELLS = 1.2
SNOWY_PINE_COUNT_PER_HUNDRED_SHELF_CELLS = 1.0
MOUNTAIN_TREE_COUNT_PER_HUNDRED_SHELF_CELLS = 0.25
FROZEN_POND_RADIUS_RANGE_IN_METATILES = (1.6, 2.6)
CRATER_LAVA_RADIUS_RANGE_IN_METATILES = (2.6, 4.2)
CRATER_RIM_WIDTH_IN_METATILES = 2
LEDGE_SOUTH = "s"
ROCK_TOP_CLASS = "r"


def sculpt_terraces(route: Route) -> None:
    if route.spec.biome not in TERRACE_BIOMES:
        return
    walkable_before = np.isin(route.cls, list(WALKABLE))
    reach_before = reachable(route.cls, route.exits[0][1])
    levels = contour_levels(route)
    open_shelves(route, walkable_before, levels)
    draw_contours_across_the_rock(route, levels)
    crossings = cut_cliff_faces(route, levels, walkable_before)
    side_faces = cut_side_faces(route, levels, walkable_before)
    settled = settle_until_nothing_is_cut_off(route, crossings, reach_before)
    rock_back_unreachable_shelf(route, walkable_before)
    dressing_counts = dress_shelves(route, walkable_before, reach_before)
    route.zones.append(Zone("terraces", [], "terraces", {"levels": int(levels.max() - levels.min() + 1)}))
    route.log.append(f"terraces: {int(levels.max() - levels.min() + 1)} contour bands, {len(crossings)} climbs "
                     f"({sum(1 for crossing in crossings if crossing['kind'] == 'ledge')} as ledges), "
                     f"{side_faces} side-face cells, {settled} climbs made plain stairs to keep everything reachable; "
                     + ", ".join(f"{count} {name}" for name, count in dressing_counts.items()))


def contour_levels(route: Route) -> np.ndarray:
    rng = route.rng
    band_height = int(rng.integers(*BAND_HEIGHT_RANGE_IN_METATILES))
    column_offsets = stepped_column_offsets(route.w, band_height, rng)
    rows = np.arange(route.h)[:, None]
    height_above_the_south_edge = (route.h - 1 - rows) + column_offsets[None, :]
    levels = np.floor_divide(height_above_the_south_edge, band_height).astype(int)
    return levels


def stepped_column_offsets(width: int, band_height: int, rng: np.random.Generator) -> np.ndarray:
    offsets = np.zeros(width, dtype=int)
    amplitude = max(2, int(round(band_height * CONTOUR_JOG_AMPLITUDE_IN_BANDS * 2)))
    column = 0
    current_offset = int(rng.integers(-amplitude, amplitude + 1))
    while column < width:
        run = int(rng.integers(*CONTOUR_JOG_RUN_RANGE_IN_METATILES))
        offsets[column:column + run] = current_offset
        column += run
        step = int(rng.choice([-2, -1, 1, 2]))
        current_offset = int(np.clip(current_offset + step, -amplitude, amplitude))
    return offsets


def open_shelves(route: Route, walkable_before: np.ndarray, levels: np.ndarray) -> None:
    from scipy.ndimage import distance_transform_edt

    from .stages import ground_class, wall_class

    reach_noise = fbm(route.h, route.w, 5.0, route.rng)
    low_reach, high_reach = SHELF_REACH_RANGE_IN_METATILES
    shelf_reach = low_reach + np.round((reach_noise - reach_noise.min()) / max(1e-6, np.ptp(reach_noise)) * (high_reach - low_reach))
    distance_from_walkable, (nearest_y, nearest_x) = distance_transform_edt(~walkable_before, return_indices=True)
    on_the_level_of_the_nearest_lane = levels == levels[nearest_y, nearest_x]
    shelf = (distance_from_walkable <= shelf_reach) & ~walkable_before & on_the_level_of_the_nearest_lane
    shelf &= np.isin(route.cls, [wall_class(route), "r", "k"]) & ~special_area_mask(route)
    shelf[:2, :] = shelf[-2:, :] = False
    shelf[:, :2] = shelf[:, -2:] = False
    route.cls[shelf] = ground_class(route)


def draw_contours_across_the_rock(route: Route, levels: np.ndarray) -> None:
    from .stages import code, wall_class

    rock_mass = np.isin(route.cls, [wall_class(route), "r"]) & ~special_area_mask(route)
    face = face_rows_of_boundaries(levels) | side_boundaries(levels)
    route.cls[rock_mass] = ROCK_TOP_CLASS
    route.cls[rock_mass & face] = code("CLIFF", "r")


def side_boundaries(levels: np.ndarray) -> np.ndarray:
    side = np.zeros(levels.shape, dtype=bool)
    side[:, :-1] |= levels[:, :-1] > levels[:, 1:]
    side[:, 1:] |= levels[:, 1:] > levels[:, :-1]
    return side


def special_area_mask(route: Route) -> np.ndarray:
    special = np.zeros(route.cls.shape, dtype=bool)
    for cell in route.zone_cells("special"):
        special[cell] = True
    return special


def face_rows_of_boundaries(levels: np.ndarray) -> np.ndarray:
    drops_to_the_south = np.zeros(levels.shape, dtype=bool)
    drops_to_the_south[:-1] = levels[:-1] > levels[1:]
    face = drops_to_the_south.copy()
    for lift in range(1, FACE_HEIGHT_IN_METATILES):
        face[:-lift] |= drops_to_the_south[lift:]
    return face


def cut_cliff_faces(route: Route, levels: np.ndarray, walkable_before: np.ndarray) -> List[dict]:
    from .stages import code

    cliff = code("CLIFF", "r")
    face = face_rows_of_boundaries(levels)
    open_ground = np.isin(route.cls, list(WALKABLE))
    face_on_shelf = face & open_ground & ~walkable_before & ~special_area_mask(route)
    face_on_shelf[:2, :] = face_on_shelf[-2:, :] = False
    route.cls[face_on_shelf] = cliff
    crossings = []
    boundary_rows = np.zeros(levels.shape, dtype=bool)
    boundary_rows[:-1] = levels[:-1] > levels[1:]
    for boundary_y in range(FACE_HEIGHT_IN_METATILES, route.h - 2):
        crossing_columns = np.nonzero(boundary_rows[boundary_y] & walkable_before[boundary_y]
                                      & walkable_before[boundary_y - 1])[0]
        for run in np.split(crossing_columns, np.nonzero(np.diff(crossing_columns) > 1)[0] + 1):
            if len(run) == 0:
                continue
            crossing = climb_one_crossing(route, boundary_y, [int(column) for column in run], cliff)
            if crossing:
                crossings.append(crossing)
    return crossings


def face_cells_of_crossing(boundary_y: int, columns: List[int]) -> List[Cell]:
    cells = [(boundary_y - lift, column) for lift in range(FACE_HEIGHT_IN_METATILES) for column in columns]
    return cells


def climb_one_crossing(route: Route, boundary_y: int, columns: List[int], cliff: str) -> dict:
    from .stages import code

    stairs = code("STAIRS", "p")
    face_cells = face_cells_of_crossing(boundary_y, columns)
    if any(cell in set(route.zone_cells("special")) for cell in face_cells):
        return {}
    previous_classes = {cell: str(route.cls[cell]) for cell in face_cells}
    critical_cells = set(route.critical_path)
    on_main_path = any(cell in critical_cells for cell in face_cells)
    if len(columns) <= WIDEST_CROSSING_CLIMBED_ON_STAIRS_ALONE:
        use_ledge = not on_main_path and route.rng.random() < SIDE_LANE_LEDGE_CHANCE
        if use_ledge:
            for column in columns:
                route.cls[boundary_y - 1, column] = LEDGE_SOUTH
            kind = "ledge"
        else:
            for cell in face_cells:
                route.cls[cell] = stairs
            kind = "stairs"
    else:
        middle = len(columns) // 2
        stair_columns = set(columns[max(0, middle - STAIRS_WIDTH_IN_A_WIDE_CROSSING // 2):
                                    middle - STAIRS_WIDTH_IN_A_WIDE_CROSSING // 2 + STAIRS_WIDTH_IN_A_WIDE_CROSSING])
        for cell in face_cells:
            route.cls[cell] = stairs if cell[1] in stair_columns else cliff
        kind = "wide"
    crossing = {"kind": kind, "cells": face_cells, "previous": previous_classes}
    return crossing


def cut_side_faces(route: Route, levels: np.ndarray, walkable_before: np.ndarray) -> int:
    from .stages import code

    cliff = code("CLIFF", "r")
    higher_on_the_left = np.zeros(levels.shape, dtype=bool)
    higher_on_the_right = np.zeros(levels.shape, dtype=bool)
    higher_on_the_left[:, :-1] = levels[:, :-1] > levels[:, 1:]
    higher_on_the_right[:, 1:] = levels[:, 1:] > levels[:, :-1]
    side_face = (higher_on_the_left | higher_on_the_right) & np.isin(route.cls, list(WALKABLE))
    side_face &= ~walkable_before & ~special_area_mask(route)
    route.cls[side_face] = cliff
    side_face_count = int(side_face.sum())
    return side_face_count


def settle_until_nothing_is_cut_off(route: Route, crossings: List[dict], reach_before: np.ndarray) -> int:
    from .stages import code

    stairs = code("STAIRS", "p")
    exit_cells = [cell for _exit, cells in route.exits for cell in cells]
    settled_count = 0
    for crossing in crossings:
        if not cuts_something_off(route, reach_before, exit_cells):
            break
        if crossing["kind"] == "stairs":
            continue
        for cell in crossing["cells"]:
            route.cls[cell] = stairs if crossing["kind"] == "ledge" else crossing["previous"][cell]
        settled_count += 1
    return settled_count


def cuts_something_off(route: Route, reach_before: np.ndarray, exit_cells: List[Cell]) -> bool:
    reach_now = reachable(route.cls, route.exits[0][1])
    walkable_now = np.isin(route.cls, list(WALKABLE))
    lost = reach_before & walkable_now & ~reach_now
    trapped = reach_now & ~can_leave(route.cls, exit_cells)
    is_cut_off = bool(lost.any() or trapped.any())
    return is_cut_off


def rock_back_unreachable_shelf(route: Route, walkable_before: np.ndarray) -> None:
    from .stages import wall_class

    reach_now = reachable(route.cls, route.exits[0][1])
    unreachable_shelf = np.isin(route.cls, list(WALKABLE)) & ~reach_now & ~walkable_before
    route.cls[unreachable_shelf] = wall_class(route)


def shelf_floor_mask(route: Route, walkable_before: np.ndarray) -> np.ndarray:
    from .stages import ground_class

    floor = (route.cls == ground_class(route)) & ~walkable_before & ~special_area_mask(route)
    return floor


def dress_shelves(route: Route, walkable_before: np.ndarray, reach_before: np.ndarray) -> Dict[str, int]:
    counts = {"boulders": place_shelf_boulders(route, walkable_before, reach_before),
              "grass tufts": place_grass_tufts(route, walkable_before)}
    if route.spec.biome == "snow":
        counts["snowy pines"] = place_trees_on_shelves(route, walkable_before, reach_before, SNOWY_PINE_COUNT_PER_HUNDRED_SHELF_CELLS)
        counts["frozen ponds"] = place_frozen_pond(route, walkable_before)
    if route.spec.biome == "mountain":
        counts["trees"] = place_trees_on_shelves(route, walkable_before, reach_before, MOUNTAIN_TREE_COUNT_PER_HUNDRED_SHELF_CELLS)
    if route.spec.biome == "volcanic":
        counts["craters"] = place_crater(route, walkable_before)
    return counts


def keeps_everything_reachable(route: Route, reach_before: np.ndarray) -> bool:
    exit_cells = [cell for _exit, cells in route.exits for cell in cells]
    keeps = not cuts_something_off(route, reach_before & np.isin(route.cls, list(WALKABLE)), exit_cells)
    return keeps


def place_shelf_boulders(route: Route, walkable_before: np.ndarray, reach_before: np.ndarray) -> int:
    from .stages import code

    rng = route.rng
    boulder = code("BOULDER", "r")
    floor = shelf_floor_mask(route, walkable_before)
    candidates = list(zip(*np.nonzero(floor)))
    wanted_count = int(len(candidates) * BOULDER_SHARE_OF_SHELF_FLOOR)
    placed: List[Cell] = []
    for candidate_index in rng.permutation(len(candidates)):
        if len(placed) >= wanted_count:
            break
        cell = tuple(map(int, candidates[candidate_index]))
        if any(abs(cell[0] - other[0]) <= 2 and abs(cell[1] - other[1]) <= 2 for other in placed):
            continue
        route.cls[cell] = boulder
        placed.append(cell)
    reach_now = reachable(route.cls, route.exits[0][1])
    for cell in placed:
        if not all(reach_now[neighbour] for neighbour in walkable_neighbours(route, cell)):
            route.cls[cell] = shelf_ground(route)
    if not keeps_everything_reachable(route, reach_before):
        for cell in placed:
            route.cls[cell] = shelf_ground(route)
        return 0
    placed_count = sum(1 for cell in placed if route.cls[cell] == boulder)
    return placed_count


def shelf_ground(route: Route) -> str:
    from .stages import ground_class

    ground = ground_class(route)
    return ground


def walkable_neighbours(route: Route, cell: Cell) -> List[Cell]:
    neighbours = [(cell[0] + step_y, cell[1] + step_x) for step_y, step_x in ((1, 0), (-1, 0), (0, 1), (0, -1))
                  if route.inside(cell[0] + step_y, cell[1] + step_x)
                  and str(route.cls[cell[0] + step_y, cell[1] + step_x]) in WALKABLE]
    return neighbours


def place_grass_tufts(route: Route, walkable_before: np.ndarray) -> int:
    from .stages import grass_class

    rng = route.rng
    floor = shelf_floor_mask(route, walkable_before)
    candidates = list(zip(*np.nonzero(floor)))
    wanted_count = int(len(candidates) / 100 * TUFT_COUNT_PER_HUNDRED_SHELF_CELLS)
    tuft_class = grass_class(route)
    placed_count = 0
    for candidate_index in rng.permutation(len(candidates))[:wanted_count]:
        top, left = map(int, candidates[candidate_index])
        block = floor[top:top + 2, left:left + 3]
        if block.shape != (2, 3) or not block.all():
            continue
        route.cls[top:top + 2, left:left + 3] = tuft_class
        placed_count += 1
    return placed_count


def place_trees_on_shelves(route: Route, walkable_before: np.ndarray, reach_before: np.ndarray, per_hundred: float) -> int:
    rng = route.rng
    floor = shelf_floor_mask(route, walkable_before)
    aligned_blocks = [(top, left) for top in range(2, route.h - 3, 2) for left in range(2, route.w - 3, 2)
                      if floor[top:top + 2, left:left + 2].all()]
    wanted_count = int(floor.sum() / 100 * per_hundred)
    placed_count = 0
    for block_index in rng.permutation(len(aligned_blocks)):
        if placed_count >= wanted_count:
            break
        top, left = aligned_blocks[block_index]
        if not floor[top:top + 2, left:left + 2].all():
            continue
        route.cls[top:top + 2, left:left + 2] = "f"
        if not keeps_everything_reachable(route, reach_before):
            route.cls[top:top + 2, left:left + 2] = shelf_ground(route)
            continue
        floor[max(0, top - 1):top + 3, max(0, left - 1):left + 3] = False
        placed_count += 1
    return placed_count


def place_frozen_pond(route: Route, walkable_before: np.ndarray) -> int:
    from .stages import code

    floor = shelf_floor_mask(route, walkable_before)
    roomy = distance_transform_cdt(np.pad(floor, 1), metric="chessboard")[1:-1, 1:-1]
    if roomy.max() < 2:
        return 0
    centre_y, centre_x = np.unravel_index(int(np.argmax(roomy)), roomy.shape)
    radius = min(float(roomy.max()) - 0.4, route.rng.uniform(*FROZEN_POND_RADIUS_RANGE_IN_METATILES))
    yy, xx = np.mgrid[:route.h, :route.w]
    pond = (np.hypot(yy - centre_y, (xx - centre_x) * 0.75) <= radius) & floor
    route.cls[pond] = code("ICE", "h")
    route.zones.append(Zone("lake", [tuple(map(int, cell)) for cell in zip(*np.nonzero(pond))], "frozen pond", {"frozen": True}))
    return 1


def place_crater(route: Route, walkable_before: np.ndarray) -> int:
    from scipy.ndimage import distance_transform_edt

    from .stages import code

    away_from_lanes = ~walkable_before & ~special_area_mask(route)
    away_from_lanes[:3, :] = away_from_lanes[-3:, :] = False
    away_from_lanes[:, :3] = away_from_lanes[:, -3:] = False
    room = distance_transform_edt(away_from_lanes)
    northern_weight = np.linspace(1.0, 0.25, route.h)[:, None]
    centre_y, centre_x = np.unravel_index(int(np.argmax(room * northern_weight)), room.shape)
    lava_radius = min(float(room[centre_y, centre_x]) - CRATER_RIM_WIDTH_IN_METATILES - 0.5,
                      route.rng.uniform(*CRATER_LAVA_RADIUS_RANGE_IN_METATILES))
    if lava_radius < 1.5:
        return 0
    yy, xx = np.mgrid[:route.h, :route.w]
    distance = np.hypot(yy - centre_y, (xx - centre_x) * 0.8)
    lava = (distance <= lava_radius) & away_from_lanes
    rim = (distance <= lava_radius + CRATER_RIM_WIDTH_IN_METATILES) & ~lava & away_from_lanes
    route.cls[rim] = code("CLIFF", "r")
    route.cls[lava] = code("LAVA", "r")
    rock_back_unreachable_shelf(route, walkable_before)
    route.zones.append(Zone("lake", [tuple(map(int, cell)) for cell in zip(*np.nonzero(lava))], "crater", {"crater": True}))
    return 1
