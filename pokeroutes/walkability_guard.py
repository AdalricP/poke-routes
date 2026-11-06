from __future__ import annotations

from typing import Callable, Dict, FrozenSet, Set, Tuple

import numpy as np
from scipy.ndimage import label

from .generate import WALKABLE, can_leave, reachable

FIELD_MOVE_AND_STORY_OBSTACLE_KINDS = ("cut_tree", "rock_smash", "strength_boulder", "story_blocker")
LANDING_STEP_BY_LEDGE = {"s": (1, 0), "j": (-1, 0), "e": (0, 1), "x": (0, -1)}
SURFABLE_CLASSES = "wb"
WALKABILITY_SUMMARY_KEY = "_walkability_summary"


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


def grid_with_obstacles_standing(route, can_surf: bool) -> np.ndarray:
    grid = np.array(route.cls, copy=True)
    for event in route.events:
        if event.kind in FIELD_MOVE_AND_STORY_OBSTACLE_KINDS:
            grid[event.y, event.x] = "f"
    if can_surf:
        grid[np.isin(grid, list(SURFABLE_CLASSES))] = "p"
    return grid


def ledge_hops_between_pieces(grid: np.ndarray, pieces: np.ndarray) -> Dict[int, Set[int]]:
    hops: Dict[int, Set[int]] = {}
    height_in_cells, width_in_cells = grid.shape
    for ledge_letter, (step_y, step_x) in LANDING_STEP_BY_LEDGE.items():
        for ledge_y, ledge_x in np.argwhere(grid == ledge_letter):
            from_cell = (int(ledge_y) - step_y, int(ledge_x) - step_x)
            landing_cell = (int(ledge_y) + step_y, int(ledge_x) + step_x)
            inside = all(0 <= cell[0] < height_in_cells and 0 <= cell[1] < width_in_cells for cell in (from_cell, landing_cell))
            if inside and pieces[from_cell] and pieces[landing_cell]:
                hops.setdefault(int(pieces[from_cell]), set()).add(int(pieces[landing_cell]))
    return hops


def pieces_reached_from(start_pieces: Set[int], hops: Dict[int, Set[int]]) -> Set[int]:
    reached_pieces = set(start_pieces)
    stack = list(start_pieces)
    while stack:
        piece = stack.pop()
        for next_piece in hops.get(piece, ()):
            if next_piece not in reached_pieces:
                reached_pieces.add(next_piece)
                stack.append(next_piece)
    return reached_pieces


def exits_reached_on_grid(route, grid: np.ndarray, can_surf: bool) -> Set[Tuple[bool, int, int]]:
    pieces, _piece_count = label(np.isin(grid, list(WALKABLE)))
    hops = ledge_hops_between_pieces(grid, pieces)
    exit_pieces = [{int(pieces[tuple(cell)]) for cell in exit_cells} - {0} for _exit, exit_cells in route.exits]
    reached_pairs = set()
    for entrance_index, entrance_pieces in enumerate(exit_pieces):
        reached_pieces = pieces_reached_from(entrance_pieces, hops)
        reached_pairs |= {(can_surf, entrance_index, exit_index) for exit_index, other_pieces in enumerate(exit_pieces)
                          if exit_index != entrance_index and other_pieces & reached_pieces}
    return reached_pairs


def exits_reached_past_standing_obstacles(route) -> FrozenSet[Tuple[bool, int, int]]:
    reached_pairs = set()
    for can_surf in (False, True):
        reached_pairs |= exits_reached_on_grid(route, grid_with_obstacles_standing(route, can_surf), can_surf)
    exits_reached = frozenset(reached_pairs)
    return exits_reached


def grid_with_every_move_allowed(route) -> np.ndarray:
    grid = np.array(route.cls, copy=True)
    grid[np.isin(grid, list(SURFABLE_CLASSES))] = "p"
    grid[grid == "a"] = "p"
    return grid


def gated_entrances_still_reach_other_exits(route) -> FrozenSet[int]:
    if not route.gates or not route.exits:
        return frozenset()
    grid = grid_with_every_move_allowed(route)
    entrance_indices_that_still_reach_out = set()
    for gate_record in route.gates:
        if not gate_record.get("built"):
            continue
        entrance_index = gate_record.get("entrance")
        if entrance_index is None or not (0 <= entrance_index < len(route.exits)):
            continue
        entrance_cells = walkable_cells_of(route, route.exits[entrance_index][1])
        if not entrance_cells:
            continue
        other_exit_cells = [cell for position, (_, cells) in enumerate(route.exits)
                            if position != entrance_index for cell in walkable_cells_of(route, cells)]
        reached = reachable(grid, entrance_cells)
        if any(reached[cell] for cell in other_exit_cells):
            entrance_indices_that_still_reach_out.add(entrance_index)
    return frozenset(entrance_indices_that_still_reach_out)


def walkability_summary(route) -> tuple:
    entrance_cells = entrance_cells_on_land(route)
    exit_cells = [cell for _exit, cells in route.exits for cell in cells]
    reached = reachable(route.cls, entrance_cells)
    leavable = can_leave(route.cls, exit_cells)
    every_exit_reached = all(any(reached[cell] for cell in cells) for _exit, cells in route.exits
                             if walkable_cells_of(route, cells))
    trapped_count = int((reached & ~leavable).sum())
    summary = (every_exit_reached, trapped_count, exits_reached_past_standing_obstacles(route),
              gated_entrances_still_reach_other_exits(route))
    return summary


def remembered_walkability_summary(route) -> tuple:
    classes_fingerprint = (route.cls.tobytes(), tuple((event.kind, event.y, event.x) for event in route.events))
    remembered = route.__dict__.get(WALKABILITY_SUMMARY_KEY)
    if remembered is not None and remembered[0] == classes_fingerprint:
        return remembered[1]
    summary = walkability_summary(route)
    route.__dict__[WALKABILITY_SUMMARY_KEY] = (classes_fingerprint, summary)
    return summary


def is_no_worse(before: tuple, after: tuple) -> bool:
    every_exit_reached_before, trapped_before, gated_exits_before, gates_still_open_before = before
    every_exit_reached_after, trapped_after, gated_exits_after, gates_still_open_after = after
    no_new_way_round_a_gate = gated_exits_after <= gated_exits_before
    no_gate_newly_sealed_shut = gates_still_open_before <= gates_still_open_after
    no_worse = ((every_exit_reached_after or not every_exit_reached_before) and trapped_after <= trapped_before
               and no_new_way_round_a_gate and no_gate_newly_sealed_shut)
    return no_worse


def keep_seam_bands_as_they_were(route, classes_before) -> None:
    from .scenery_layer import PLACED_PROPS_KEY
    from .stepped_terraces import seam_band_cells

    band_cells = seam_band_cells(route)
    route.cls[band_cells] = classes_before[band_cells]
    kept_props = [placed_prop for placed_prop in route.__dict__.get(PLACED_PROPS_KEY) or []
                  if not any(band_cells[tuple(int(part) for part in cell_key.split(","))] for cell_key in placed_prop["under"])]
    route.__dict__[PLACED_PROPS_KEY] = kept_props


def run_keeping_walkability(route, stage_name: str, change_route: Callable) -> None:
    if not route.exits:
        classes_before = route.cls.copy()
        change_route(route)
        keep_seam_bands_as_they_were(route, classes_before)
        return
    from .scenery_layer import DRAWN_CLASS_KEY, PLACED_PROPS_KEY

    classes_before = route.cls.copy()
    drawn_classes_before = dict(route.__dict__.get(DRAWN_CLASS_KEY) or {})
    placed_props_before = list(route.__dict__.get(PLACED_PROPS_KEY) or [])
    summary_before = remembered_walkability_summary(route)
    change_route(route)
    keep_seam_bands_as_they_were(route, classes_before)
    summary_after = remembered_walkability_summary(route)
    if is_no_worse(summary_before, summary_after):
        return
    route.cls[:] = classes_before
    route.__dict__[DRAWN_CLASS_KEY] = drawn_classes_before
    route.__dict__[PLACED_PROPS_KEY] = placed_props_before
    route.log.append(f"{stage_name}: undone, it would have cut a way through the route ({summary_before} -> {summary_after})")
