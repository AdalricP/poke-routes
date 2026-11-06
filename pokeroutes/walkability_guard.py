from __future__ import annotations

from typing import Callable

from .generate import WALKABLE, can_leave, reachable


def walkable_cells_of(route, cells) -> list:
    walkable_cells = [tuple(cell) for cell in cells if str(route.cls[tuple(cell)]) in WALKABLE]
    return walkable_cells


def entrance_cells_on_land(route) -> list:
    for _exit, cells in route.exits:
        walkable_exit_cells = walkable_cells_of(route, cells)
        if walkable_exit_cells:
            return walkable_exit_cells
    walkable_path_cells = walkable_cells_of(route, route.critical_path[:1])
    return walkable_path_cells


def walkability_summary(route) -> tuple:
    entrance_cells = entrance_cells_on_land(route)
    exit_cells = [cell for _exit, cells in route.exits for cell in cells]
    reached = reachable(route.cls, entrance_cells)
    leavable = can_leave(route.cls, exit_cells)
    every_exit_reached = all(any(reached[cell] for cell in cells) for _exit, cells in route.exits
                             if walkable_cells_of(route, cells))
    trapped_count = int((reached & ~leavable).sum())
    summary = (every_exit_reached, trapped_count)
    return summary


def is_no_worse(before: tuple, after: tuple) -> bool:
    every_exit_reached_before, trapped_before = before
    every_exit_reached_after, trapped_after = after
    no_worse = (every_exit_reached_after or not every_exit_reached_before) and trapped_after <= trapped_before
    return no_worse


def run_keeping_walkability(route, stage_name: str, change_route: Callable) -> None:
    if not route.exits:
        change_route(route)
        return
    classes_before = route.cls.copy()
    summary_before = walkability_summary(route)
    change_route(route)
    summary_after = walkability_summary(route)
    if is_no_worse(summary_before, summary_after):
        return
    route.cls[:] = classes_before
    route.log.append(f"{stage_name}: undone, it would have cut a way through the route ({summary_before} -> {summary_after})")
