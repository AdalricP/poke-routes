"""Cross paths between braided lanes (forest and meadow). A side lane that leaves
the main path and rejoins it runs beside it through the trees; a short cross
path through the tree belt between them, where both run straight, ties the two
together so there are several genuine ways through: you can switch lanes half
way, and the ways to the exit weave instead of running side by side."""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np

from .braid import braids_to_the_exit, jogs_in, v_at
from .generate import Route, Zone

Cell = Tuple[int, int]

CROSS_PATH_WIDTH_IN_METATILES = 2
STRAIGHT_RUN_NEEDED_EITHER_SIDE_OF_A_CROSS_PATH = 3
SMALLEST_TREE_BELT_FOR_A_CROSS_PATH = 2
LONGEST_CROSS_PATH_IN_METATILES = 12
SMALLEST_SPACING_BETWEEN_CROSS_PATHS = 14
LANE_LENGTH_PER_CROSS_PATH = 18
CLEARANCE_FROM_LANE_ENDS = 6


def weave_cross_paths(route: Route, plan, carve) -> int:
    if not braids_to_the_exit(route.spec):
        return 0
    woven_count = 0
    for section in plan.sections:
        main_lane = plan.lane(section.lanes[0])
        side_lanes = [plan.lane(lane_id) for lane_id in section.lanes[1:]]
        for side_lane in side_lanes:
            if side_lane.fate != "full" or side_lane.kind != "loop" or "prof" not in side_lane.meta:
                continue
            parent_lane = plan.lane(side_lane.parent) if side_lane.parent is not None else main_lane
            parent_profile = parent_lane.meta.get("prof") or section.prof
            for cross_path_cells in cross_paths_between(route, plan, parent_profile, side_lane):
                carve(route, cross_path_cells)
                route.zones.append(Zone("loop", cross_path_cells, f"cross path to lane {side_lane.id}",
                                        {"cross_path": True, "lane": side_lane.id}))
                woven_count += 1
    if woven_count:
        route.log.append(f"weave: {woven_count} cross paths tie the braided lanes together")
    return woven_count


def cross_paths_between(route: Route, plan, parent_profile, side_lane) -> List[List[Cell]]:
    frame = side_lane.frame
    start_u, end_u = side_lane.meta["u_s"] + CLEARANCE_FROM_LANE_ENDS, side_lane.meta["u_e"] - CLEARANCE_FROM_LANE_ENDS
    wanted_count = max(1, (end_u - start_u) // LANE_LENGTH_PER_CROSS_PATH)
    candidate_us = [along for along in range(start_u, end_u)
                    if is_straight_on_both(parent_profile, side_lane.meta["prof"], along)]
    route.rng.shuffle(candidate_us)
    chosen_paths: List[List[Cell]] = []
    chosen_us: List[int] = []
    for along in candidate_us:
        if len(chosen_paths) >= wanted_count:
            break
        if any(abs(along - other) < SMALLEST_SPACING_BETWEEN_CROSS_PATHS for other in chosen_us):
            continue
        cells = cross_path_cells_at(route, plan, frame, parent_profile, side_lane, along)
        if cells is None:
            continue
        chosen_paths.append(cells)
        chosen_us.append(along)
    return chosen_paths


def is_straight_on_both(parent_profile, side_profile, along: int) -> bool:
    reach = STRAIGHT_RUN_NEEDED_EITHER_SIDE_OF_A_CROSS_PATH + CROSS_PATH_WIDTH_IN_METATILES
    has_no_jog_nearby = not jogs_in(parent_profile, along - reach, along + reach) \
        and not jogs_in(side_profile, along - reach, along + reach)
    return has_no_jog_nearby


def cross_path_cells_at(route: Route, plan, frame, parent_profile, side_lane, along: int) -> Optional[List[Cell]]:
    parent_v, side_v = v_at(parent_profile, along), v_at(side_lane.meta["prof"], along)
    low_v, high_v = sorted((parent_v, side_v))
    if high_v - low_v > LONGEST_CROSS_PATH_IN_METATILES:
        return None
    cells, belt_cells = [], []
    for across in range(low_v, high_v + 1):
        for offset in range(CROSS_PATH_WIDTH_IN_METATILES):
            cell = frame.yx(along + offset, across)
            if not route.inside(*cell, 3):
                return None
            cells.append(cell)
            if plan.owner[cell] < 0:
                belt_cells.append(cell)
    belt_depth = len(belt_cells) // CROSS_PATH_WIDTH_IN_METATILES
    if belt_depth < SMALLEST_TREE_BELT_FOR_A_CROSS_PATH:
        return None
    if not all(is_plain_wall(route, cell) for cell in belt_cells):
        return None
    if not owners_are_only_the_two_lanes(plan, cells, side_lane):
        return None
    return cells


def is_plain_wall(route: Route, cell: Cell) -> bool:
    from .stages import wall_class

    is_wall = str(route.cls[cell]) in ("f", wall_class(route)) and not route.protect[cell]
    return is_wall


def owners_are_only_the_two_lanes(plan, cells: List[Cell], side_lane) -> bool:
    allowed_owners = {-1, side_lane.id, side_lane.parent if side_lane.parent is not None else -2}
    main_lane_ids = {lane.id for lane in plan.lanes if lane.kind == "main"}
    owners = {int(plan.owner[cell]) for cell in cells}
    only_the_two_lanes = all(owner in allowed_owners or (side_lane.parent is None and owner in main_lane_ids)
                             for owner in owners)
    return only_the_two_lanes
