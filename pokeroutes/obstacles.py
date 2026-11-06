"""Field-move obstacles (Cut trees, smashable rocks, Strength boulders) that really
block: each one ends up standing in a one-wide gap, on walkable ground, so the
only way past is the field move. Runs after the route is connected.

An obstacle in a lane two or three wide gets the rest of the lane's width beside
it walled off (two cells deep along the lane, so tree walls stay 2x2), but only
where that cuts nothing else off; one that can't be seated that way, or that
stands on water, trees or rock, is taken away."""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import numpy as np

from .generate import DIRS4, WALKABLE, Event, Route, reachable

Cell = Tuple[int, int]

FIELD_MOVE_OBSTACLE_KINDS = ("cut_tree", "rock_smash", "strength_boulder")
WIDEST_LANE_AN_OBSTACLE_CAN_BE_SEATED_IN = 4
GROUND_CLASSES_AN_OBSTACLE_CAN_STAND_ON = "gptdvyc"


def seat_field_move_obstacles_in_single_gaps(route: Route) -> None:
    special_area_cells = set(route.zone_cells("special"))
    kept_events: List[Event] = []
    removed_count, seated_count = 0, 0
    for event in route.events:
        if event.kind not in FIELD_MOVE_OBSTACLE_KINDS or (event.y, event.x) in special_area_cells:
            kept_events.append(event)
            continue
        outcome = seat_single_obstacle(route, event)
        if outcome == "removed":
            removed_count += 1
            continue
        seated_count += outcome == "seated"
        kept_events.append(event)
    route.events = kept_events
    route.log.append(f"obstacles: {seated_count} seated in a one-wide gap, {removed_count} taken away (on water/rock or bypassable)")


def seat_single_obstacle(route: Route, event: Event) -> str:
    obstacle_cell = (event.y, event.x)
    if str(route.cls[obstacle_cell]) not in WALKABLE:
        return "removed"
    if blocks_on_its_own(route, obstacle_cell):
        return "already"
    cross_section = narrowest_cross_section_through(route, obstacle_cell)
    if cross_section is None:
        return "removed"
    if wall_off_beside_obstacle(route, obstacle_cell, cross_section):
        return "seated"
    return "removed"


def blocks_on_its_own(route: Route, obstacle_cell: Cell) -> bool:
    open_neighbour_count = sum(1 for neighbour in neighbours_inside(route, obstacle_cell)
                               if str(route.cls[neighbour]) in WALKABLE)
    has_at_most_two_ways_in = open_neighbour_count <= 2
    return has_at_most_two_ways_in


def neighbours_inside(route: Route, cell: Cell) -> List[Cell]:
    neighbours = [(cell[0] + step_y, cell[1] + step_x) for step_y, step_x in DIRS4
                  if route.inside(cell[0] + step_y, cell[1] + step_x)]
    return neighbours


def walkable_run_through(route: Route, obstacle_cell: Cell, step: Tuple[int, int]) -> List[Cell]:
    run = []
    for direction in (1, -1):
        for distance in range(1, WIDEST_LANE_AN_OBSTACLE_CAN_BE_SEATED_IN + 1):
            cell = (obstacle_cell[0] + step[0] * distance * direction, obstacle_cell[1] + step[1] * distance * direction)
            if not route.inside(*cell) or str(route.cls[cell]) not in WALKABLE:
                break
            run.append(cell)
    return run


def narrowest_cross_section_through(route: Route, obstacle_cell: Cell) -> Optional[Tuple[List[Cell], Tuple[int, int]]]:
    across_rows = walkable_run_through(route, obstacle_cell, (1, 0))
    across_columns = walkable_run_through(route, obstacle_cell, (0, 1))
    candidates = [(across_rows, (0, 1)), (across_columns, (1, 0))]
    candidates = [candidate for candidate in candidates if len(candidate[0]) < WIDEST_LANE_AN_OBSTACLE_CAN_BE_SEATED_IN]
    if not candidates:
        return None
    narrowest = min(candidates, key=lambda candidate: len(candidate[0]))
    return narrowest


def wall_off_beside_obstacle(route: Route, obstacle_cell: Cell, cross_section) -> bool:
    from .stages import wall_class

    side_cells, travel_step = cross_section
    reach_before = reachable(route.cls, route.exits[0][1])
    for depth in (2, 1):
        plug_cells = plug_cells_for_depth(route, obstacle_cell, side_cells, travel_step, depth)
        if not plug_cells or any(cell_is_kept_open(route, cell) for cell in plug_cells):
            continue
        previous_classes = [route.cls[cell] for cell in plug_cells]
        for cell in plug_cells:
            route.cls[cell] = wall_class(route)
        if cuts_nothing_off(route, reach_before, plug_cells):
            return True
        for cell, previous_class in zip(plug_cells, previous_classes):
            route.cls[cell] = previous_class
    return False


def plug_cells_for_depth(route: Route, obstacle_cell: Cell, side_cells: Sequence[Cell], travel_step: Tuple[int, int],
                         depth: int) -> List[Cell]:
    plug_cells = []
    for layer in range(depth):
        for side_cell in side_cells:
            cell = (side_cell[0] + travel_step[0] * layer, side_cell[1] + travel_step[1] * layer)
            if route.inside(*cell, 1) and str(route.cls[cell]) in WALKABLE:
                plug_cells.append(cell)
    return plug_cells


def cell_is_kept_open(route: Route, cell: Cell) -> bool:
    exit_cells = {exit_cell for _exit, cells in route.exits for exit_cell in cells}
    event_cells = {(event.y, event.x) for event in route.events}
    is_kept_open = cell in exit_cells or cell in event_cells
    return is_kept_open


def cuts_nothing_off(route: Route, reach_before: np.ndarray, plug_cells: Sequence[Cell]) -> bool:
    reach_after = reachable(route.cls, route.exits[0][1])
    lost = reach_before & ~reach_after
    for cell in plug_cells:
        lost[cell] = False
    keeps_everything = not lost.any()
    return keeps_everything
