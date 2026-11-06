"""Bridges as the games build them: one straight span over water, bank to bank.

Lanes that cross a river or a lake while the route is laid out become bridge
cells wherever they happen to meet the water, so a lane crossing a slanted river
leaves a staircase of planks, and planks can end up over dry land or as stubs.
Once the route is finished every such bridge is taken up (its cells go back to
water, or to ground where no water touches them) and the banks it joined are
joined again by the shortest straight crossing near it: a 1 or 2 wide span that
has water on both flanks and walkable ground at both ends. If no straight
crossing exists the original bridge stays.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
from scipy.ndimage import label

from .generate import WALKABLE, Route
from .glitches import cells_of_components, is_clean_bridge

Cell = Tuple[int, int]

BRIDGE_CLASS = "q"
WATER_CLASSES_A_BRIDGE_SPANS = "wb"
LAND_CLASSES_A_BRIDGE_LANDS_ON = "".join(sorted(set(WALKABLE) - {"q", "h"}))
SEARCH_REACH_AROUND_A_BRIDGE_IN_METATILES = 8
LONGEST_SPAN_IN_METATILES = 12
SEAM_RING_DEPTH_IN_METATILES = 2
DISTANCE_PENALTY_PER_METATILE_FROM_THE_OLD_BRIDGE = 0.15
SMALLEST_BANK_WORTH_A_BRIDGE_IN_CELLS = 3
FLANK_CLASSES_THAT_MAY_BECOME_WATER = "frkou"
FLOODING_PENALTY_PER_CELL = 1.0
WIDEST_LANE_ALONG_THE_WATER_IN_METATILES = 4
LAND_CLASSES_A_WAY_ROUND_MAY_CROSS = LAND_CLASSES_A_BRIDGE_LANDS_ON + "fro"
CARVING_COST_PER_BLOCKED_CELL = 3.0
LONGEST_WAY_ROUND_COST = 60.0
STEPS = ((0, 1), (1, 0), (0, -1), (-1, 0))


def protected_ring_mask(route: Route) -> np.ndarray:
    ring = np.ones(route.cls.shape, dtype=bool)
    depth = SEAM_RING_DEPTH_IN_METATILES
    ring[depth:-depth, depth:-depth] = False
    return ring


def crooked_bridge_components(route: Route) -> List[List[Cell]]:
    class_grid = route.cls
    crooked = [cells for cells in cells_of_components(class_grid == BRIDGE_CLASS) if not is_clean_bridge(class_grid, cells)]
    return crooked


def inward_step_of_ring_cell(route: Route, cell: Cell) -> Optional[Cell]:
    depth = SEAM_RING_DEPTH_IN_METATILES
    steps_by_edge = ((cell[0] == depth - 1, (1, 0)), (cell[0] == route.h - depth, (-1, 0)),
                     (cell[1] == depth - 1, (0, 1)), (cell[1] == route.w - depth, (0, -1)))
    inward_steps = [step for is_on_inner_ring_row, step in steps_by_edge if is_on_inner_ring_row]
    inward_step = inward_steps[0] if inward_steps else None
    return inward_step


def extend_seam_bridge_inward(route: Route, ring: np.ndarray, ring_cell: Cell) -> None:
    step = inward_step_of_ring_cell(route, ring_cell)
    if step is None:
        return
    cell = (ring_cell[0] + step[0], ring_cell[1] + step[1])
    extension_cells = []
    while is_spannable_water(route.cls, ring, cell) and len(extension_cells) <= LONGEST_SPAN_IN_METATILES:
        extension_cells.append(cell)
        cell = (cell[0] + step[0], cell[1] + step[1])
    if not is_bridge_land(route.cls, cell):
        return
    for extension_cell in extension_cells:
        route.cls[extension_cell] = BRIDGE_CLASS


def neighbour_classes(class_grid: np.ndarray, cell: Cell, reach: int) -> List[str]:
    y, x = cell
    window = class_grid[max(0, y - reach):y + reach + 1, max(0, x - reach):x + reach + 1]
    classes = [str(letter) for letter in window.ravel()]
    return classes


def class_under_taken_up_bridge(class_grid: np.ndarray, cell: Cell, river_mask: np.ndarray, ground_class: str) -> str:
    nearby_classes = neighbour_classes(class_grid, cell, 1)
    nearby_water = [letter for letter in nearby_classes if letter in WATER_CLASSES_A_BRIDGE_SPANS]
    if river_mask[cell] or nearby_water:
        water_class = Counter(nearby_water).most_common(1)[0][0] if nearby_water else "w"
        return water_class
    land_class = land_class_under(class_grid, cell, ground_class)
    return land_class


def land_class_under(class_grid: np.ndarray, cell: Cell, ground_class: str) -> str:
    nearby_land = [letter for letter in neighbour_classes(class_grid, cell, 1) if letter in LAND_CLASSES_A_BRIDGE_LANDS_ON]
    land_class = Counter(nearby_land).most_common(1)[0][0] if nearby_land else ground_class
    return land_class


def runs_along_the_water(bridge_cells: List[Cell]) -> bool:
    if not bridge_cells:
        return False
    height_in_cells = max(y for y, _x in bridge_cells) - min(y for y, _x in bridge_cells) + 1
    width_in_cells = max(x for _y, x in bridge_cells) - min(x for _y, x in bridge_cells) + 1
    is_long_thin_strip = (max(height_in_cells, width_in_cells) > LONGEST_SPAN_IN_METATILES
                          and min(height_in_cells, width_in_cells) <= WIDEST_LANE_ALONG_THE_WATER_IN_METATILES)
    return is_long_thin_strip


def take_up_bridge(route: Route, bridge_cells: List[Cell], river_mask: np.ndarray, ground_class: str) -> None:
    original_grid = route.cls.copy()
    lane_keeps_its_ground = runs_along_the_water(bridge_cells)
    for cell in bridge_cells:
        route.cls[cell] = (land_class_under(original_grid, cell, ground_class) if lane_keeps_its_ground
                           else class_under_taken_up_bridge(original_grid, cell, river_mask, ground_class))


def walkable_labels(class_grid: np.ndarray) -> np.ndarray:
    labelled, _count = label(np.isin(class_grid, list(WALKABLE)))
    return labelled


def bank_labels_beside(class_grid: np.ndarray, labelled: np.ndarray, bridge_cells: List[Cell]) -> Set[int]:
    height, width = class_grid.shape
    reach = 1
    labels = {int(labelled[y + dy, x + dx]) for y, x in bridge_cells for dy in range(-reach, reach + 1) for dx in range(-reach, reach + 1)
              if 0 <= y + dy < height and 0 <= x + dx < width and labelled[y + dy, x + dx]}
    return labels


def is_bridge_land(class_grid: np.ndarray, cell: Cell) -> bool:
    y, x = cell
    is_land = 0 <= y < class_grid.shape[0] and 0 <= x < class_grid.shape[1] and str(class_grid[y, x]) in LAND_CLASSES_A_BRIDGE_LANDS_ON
    return is_land


def is_spannable_water(class_grid: np.ndarray, ring: np.ndarray, cell: Cell) -> bool:
    y, x = cell
    is_water = (0 <= y < class_grid.shape[0] and 0 <= x < class_grid.shape[1] and not ring[cell]
                and str(class_grid[y, x]) in WATER_CLASSES_A_BRIDGE_SPANS)
    return is_water


def span_from(class_grid: np.ndarray, ring: np.ndarray, start: Cell, step: Cell) -> Optional[Tuple[List[Cell], Cell]]:
    span_cells = []
    cell = (start[0] + step[0], start[1] + step[1])
    while is_spannable_water(class_grid, ring, cell) and len(span_cells) <= LONGEST_SPAN_IN_METATILES:
        span_cells.append(cell)
        cell = (cell[0] + step[0], cell[1] + step[1])
    if not span_cells or len(span_cells) > LONGEST_SPAN_IN_METATILES or not is_bridge_land(class_grid, cell):
        return None
    return span_cells, cell


@dataclass(frozen=True)
class CrossingPlan:
    bridge_cells: Tuple[Cell, ...]
    flooded_cells: Tuple[Cell, ...]


def rectangle_of(cells) -> Tuple[range, range]:
    rows = range(min(y for y, _x in cells), max(y for y, _x in cells) + 1)
    columns = range(min(x for _y, x in cells), max(x for _y, x in cells) + 1)
    return rows, columns


def flank_cells_of_span(span_cells: List[Cell], runs_east_west: bool) -> List[Cell]:
    rows, columns = rectangle_of(span_cells)
    if runs_east_west:
        flank_cells = [(y, x) for x in columns[1:-1] for y in (rows[0] - 1, rows[-1] + 1)]
        return flank_cells
    flank_cells = [(y, x) for y in rows[1:-1] for x in (columns[0] - 1, columns[-1] + 1)]
    return flank_cells


def is_floodable(class_grid: np.ndarray, ring: np.ndarray, cell: Cell) -> bool:
    y, x = cell
    floodable = (0 <= y < class_grid.shape[0] and 0 <= x < class_grid.shape[1] and not ring[cell]
                 and str(class_grid[y, x]) in FLANK_CLASSES_THAT_MAY_BECOME_WATER)
    return floodable


def crossing_plan_for(class_grid: np.ndarray, ring: np.ndarray, span_cells: List[Cell], runs_east_west: bool) -> Optional[CrossingPlan]:
    dry_flanks = [cell for cell in flank_cells_of_span(span_cells, runs_east_west)
                  if is_inside(class_grid, cell) and str(class_grid[cell]) not in WATER_CLASSES_A_BRIDGE_SPANS]
    if not all(is_floodable(class_grid, ring, cell) for cell in dry_flanks):
        return None
    water_class = Counter(str(class_grid[cell]) for cell in span_cells).most_common(1)[0][0]
    trial_grid = class_grid.copy()
    for cell in dry_flanks:
        trial_grid[cell] = water_class
    for cell in span_cells:
        trial_grid[cell] = BRIDGE_CLASS
    if not is_clean_bridge(trial_grid, span_cells):
        return None
    plan = CrossingPlan(tuple(span_cells), tuple(dry_flanks))
    return plan


def is_inside(class_grid: np.ndarray, cell: Cell) -> bool:
    inside = 0 <= cell[0] < class_grid.shape[0] and 0 <= cell[1] < class_grid.shape[1]
    return inside


def widened_plan(class_grid: np.ndarray, ring: np.ndarray, labelled: np.ndarray, start: Cell, step: Cell,
                 narrow_plan: CrossingPlan, far_bank: Cell) -> CrossingPlan:
    runs_east_west = step[0] == 0
    for across in ((step[1], step[0]), (-step[1], -step[0])):
        beside_start = (start[0] + across[0], start[1] + across[1])
        if not is_bridge_land(class_grid, beside_start) or labelled[beside_start] != labelled[start]:
            continue
        beside_span = span_from(class_grid, ring, beside_start, step)
        if beside_span is None or len(beside_span[0]) != len(narrow_plan.bridge_cells) or labelled[beside_span[1]] != labelled[far_bank]:
            continue
        wide_plan = crossing_plan_for(class_grid, ring, list(narrow_plan.bridge_cells) + beside_span[0], runs_east_west)
        if wide_plan is not None:
            return wide_plan
    return narrow_plan


def crossing_score(plan: CrossingPlan, centre: Tuple[float, float]) -> float:
    middle = plan.bridge_cells[len(plan.bridge_cells) // 2]
    distance = abs(middle[0] - centre[0]) + abs(middle[1] - centre[1])
    score = (len(plan.bridge_cells) + FLOODING_PENALTY_PER_CELL * len(plan.flooded_cells)
             + DISTANCE_PENALTY_PER_METATILE_FROM_THE_OLD_BRIDGE * distance)
    return score


def candidate_starts(class_grid: np.ndarray, labelled: np.ndarray, wanted_labels: Set[int], old_bridge: List[Cell]) -> List[Cell]:
    reach = SEARCH_REACH_AROUND_A_BRIDGE_IN_METATILES
    top = max(0, min(y for y, _x in old_bridge) - reach)
    bottom = min(class_grid.shape[0], max(y for y, _x in old_bridge) + reach + 1)
    left = max(0, min(x for _y, x in old_bridge) - reach)
    right = min(class_grid.shape[1], max(x for _y, x in old_bridge) + reach + 1)
    starts = [(y, x) for y in range(top, bottom) for x in range(left, right)
              if int(labelled[y, x]) in wanted_labels and is_bridge_land(class_grid, (y, x))]
    return starts


def best_crossing(class_grid: np.ndarray, ring: np.ndarray, labelled: np.ndarray, wanted_labels: Set[int],
                  old_bridge: List[Cell]) -> Optional[CrossingPlan]:
    centre = (float(np.mean([y for y, _x in old_bridge])), float(np.mean([x for _y, x in old_bridge])))
    best_score, best_plan = None, None
    for start in candidate_starts(class_grid, labelled, wanted_labels, old_bridge):
        for step in STEPS:
            span = span_from(class_grid, ring, start, step)
            if span is None or int(labelled[span[1]]) not in wanted_labels or labelled[span[1]] == labelled[start]:
                continue
            plan = crossing_plan_for(class_grid, ring, span[0], step[0] == 0)
            if plan is None:
                continue
            score = crossing_score(plan, centre)
            if best_score is None or score < best_score:
                best_score, best_plan = score, widened_plan(class_grid, ring, labelled, start, step, plan, span[1])
    return best_plan


def build_crossing(route: Route, plan: CrossingPlan) -> None:
    water_class = Counter(str(route.cls[cell]) for cell in plan.bridge_cells).most_common(1)[0][0]
    for cell in plan.flooded_cells:
        route.cls[cell] = water_class
    for cell in plan.bridge_cells:
        route.cls[cell] = BRIDGE_CLASS


def anchor_cells_of_banks(labelled: np.ndarray, bank_labels: Set[int]) -> List[Cell]:
    anchors = []
    for bank_label in sorted(bank_labels):
        bank_cells = np.argwhere(labelled == bank_label)
        if len(bank_cells) >= SMALLEST_BANK_WORTH_A_BRIDGE_IN_CELLS:
            anchors.append((int(bank_cells[0][0]), int(bank_cells[0][1])))
    return anchors


def labels_of_anchors(labelled: np.ndarray, anchor_cells: List[Cell]) -> Set[int]:
    labels = {int(labelled[cell]) for cell in anchor_cells}
    return labels


def rebuild_crossings(route: Route, ring: np.ndarray, anchor_cells: List[Cell], old_bridge: List[Cell], ground_class: str) -> bool:
    labelled = walkable_labels(route.cls)
    bank_labels = labels_of_anchors(labelled, anchor_cells)
    while len(bank_labels) > 1:
        crossing_plan = best_crossing(route.cls, ring, labelled, bank_labels, old_bridge)
        if crossing_plan is not None:
            build_crossing(route, crossing_plan)
        elif not carve_way_round_over_land(route, ring, labelled, bank_labels, ground_class):
            return False
        labelled = walkable_labels(route.cls)
        bank_labels = labels_of_anchors(labelled, anchor_cells)
    return True


def step_cost_over_land(class_grid: np.ndarray, ring: np.ndarray, cell: Cell) -> float:
    letter = str(class_grid[cell])
    if ring[cell] or letter not in LAND_CLASSES_A_WAY_ROUND_MAY_CROSS:
        return float("inf")
    step_cost = 1.0 if letter in WALKABLE else CARVING_COST_PER_BLOCKED_CELL
    return step_cost


def cheapest_way_between_banks(class_grid: np.ndarray, ring: np.ndarray, labelled: np.ndarray, from_label: int,
                               to_labels: Set[int]) -> Optional[List[Cell]]:
    import heapq

    starts = [(int(y), int(x)) for y, x in np.argwhere(labelled == from_label)]
    cost_so_far: Dict[Cell, float] = {cell: 0.0 for cell in starts}
    came_from: Dict[Cell, Optional[Cell]] = {cell: None for cell in starts}
    frontier = [(0.0, cell) for cell in starts]
    heapq.heapify(frontier)
    while frontier:
        cost, cell = heapq.heappop(frontier)
        if int(labelled[cell]) in to_labels:
            return way_back_from(came_from, cell)
        if cost > LONGEST_WAY_ROUND_COST or cost > cost_so_far.get(cell, float("inf")):
            continue
        for step in STEPS:
            neighbour = (cell[0] + step[0], cell[1] + step[1])
            if not (0 <= neighbour[0] < class_grid.shape[0] and 0 <= neighbour[1] < class_grid.shape[1]):
                continue
            neighbour_cost = cost + step_cost_over_land(class_grid, ring, neighbour)
            if neighbour_cost < cost_so_far.get(neighbour, float("inf")):
                cost_so_far[neighbour], came_from[neighbour] = neighbour_cost, cell
                heapq.heappush(frontier, (neighbour_cost, neighbour))
    return None


def way_back_from(came_from: Dict[Cell, Optional[Cell]], end_cell: Cell) -> List[Cell]:
    way = [end_cell]
    while came_from[way[-1]] is not None:
        way.append(came_from[way[-1]])
    return way


def carve_way_round_over_land(route: Route, ring: np.ndarray, labelled: np.ndarray, bank_labels: Set[int], ground_class: str) -> bool:
    first_label = min(bank_labels)
    way = cheapest_way_between_banks(route.cls, ring, labelled, first_label, bank_labels - {first_label})
    if way is None:
        return False
    for cell in way:
        if str(route.cls[cell]) not in WALKABLE:
            route.cls[cell] = ground_class
    return True


def straighten_bridge(route: Route, bridge_cells: List[Cell], river_mask: np.ndarray, ground_class: str) -> bool:
    ring = protected_ring_mask(route)
    grid_before = route.cls.copy()
    inner_cells = [cell for cell in bridge_cells if not ring[cell]]
    take_up_bridge(route, inner_cells, river_mask, ground_class)
    for ring_cell in [cell for cell in bridge_cells if ring[cell]]:
        extend_seam_bridge_inward(route, ring, ring_cell)
    labelled = walkable_labels(route.cls)
    anchor_cells = anchor_cells_of_banks(labelled, bank_labels_beside(route.cls, labelled, bridge_cells))
    if rebuild_crossings(route, ring, anchor_cells, bridge_cells, ground_class):
        return True
    route.cls[...] = grid_before
    return False


def straighten_bridges(route: Route) -> int:
    from .rivers import RIVER_CELLS_KEY
    from .stages import ground_class

    river_mask = route.__dict__.get(RIVER_CELLS_KEY)
    river_mask = river_mask if river_mask is not None else np.zeros(route.cls.shape, dtype=bool)
    crooked = crooked_bridge_components(route)
    straightened_count = sum(1 for bridge_cells in crooked if straighten_bridge(route, bridge_cells, river_mask, ground_class(route)))
    if crooked:
        route.log.append(f"bridges: {straightened_count} of {len(crooked)} crooked bridges rebuilt as straight spans")
    return straightened_count
