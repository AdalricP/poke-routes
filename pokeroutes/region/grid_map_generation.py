from __future__ import annotations

import heapq
from types import SimpleNamespace
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

from . import map_grid, network
from .map_grid import GridCell, ROUTE_MARK, TOWN_MARK
from .town_sites import pick_town_sites
from .world import World

COST_OF_A_SEA_GRID_CELL = 9.0
COST_OF_A_GRID_CELL_WITHOUT_DRY_GROUND = 12.0
COST_OF_A_TURN = 3.0
COST_OF_RUNNING_ON_AN_EARLIER_ROUTE = 0.8
COST_OF_RUNNING_BESIDE_AN_EARLIER_ROUTE = 10.0
COST_OF_RUNNING_BESIDE_ANOTHER_TOWN = 40.0
COST_OF_THE_GRID_BORDER = 4.0
SMALLEST_GAP_BETWEEN_TOWNS_IN_REGION_CELLS = 24
SMALLEST_GAP_BETWEEN_TOWNS_IN_GRID_CELLS = 2


def grid_cell_costs(w: World, variant: int) -> np.ndarray:
    grid_width, grid_height = map_grid.grid_size_of_world(w)
    region_cell_costs = network.land_cost(w, variant)
    costs = np.zeros((grid_height, grid_width))
    for row in range(grid_height):
        for column in range(grid_width):
            costs[row, column] = cost_of_one_grid_cell(w, region_cell_costs, (column, row), (grid_width, grid_height))
    return costs


def cost_of_one_grid_cell(w: World, region_cell_costs: np.ndarray, cell: GridCell, grid_size: Tuple[int, int]) -> float:
    border_cost = COST_OF_THE_GRID_BORDER if cell[0] in (0, grid_size[0] - 1) or cell[1] in (0, grid_size[1] - 1) else 0.0
    if map_grid.is_sea_grid_cell(w, cell):
        return COST_OF_A_SEA_GRID_CELL + border_cost
    left, top, right, bottom = map_grid.region_rect_of_grid_cells([cell], map_grid.region_cells_per_grid_cell(w))
    block_costs = region_cell_costs[top:bottom, left:right]
    finite_costs = block_costs[np.isfinite(block_costs)]
    land_cost = float(np.median(finite_costs)) if finite_costs.size else COST_OF_A_GRID_CELL_WITHOUT_DRY_GROUND
    grid_cell_cost = land_cost + border_cost
    return grid_cell_cost


def chebyshev_distance(cell: GridCell, other_cell: GridCell) -> int:
    distance = max(abs(cell[0] - other_cell[0]), abs(cell[1] - other_cell[1]))
    return distance


def smallest_town_gap_in_grid_cells(size: int) -> int:
    smallest_gap = max(SMALLEST_GAP_BETWEEN_TOWNS_IN_GRID_CELLS, round(SMALLEST_GAP_BETWEEN_TOWNS_IN_REGION_CELLS / size))
    return smallest_gap


def is_good_town_cell(w: World, cell: GridCell, marks: Dict[GridCell, str]) -> bool:
    if not map_grid.is_inside_grid(cell, map_grid.grid_size_of_world(w)) or cell in marks:
        return False
    if map_grid.is_sea_grid_cell(w, cell):
        return False
    town_cells = [town_cell for town_cell, mark in marks.items() if mark == TOWN_MARK]
    smallest_gap = smallest_town_gap_in_grid_cells(map_grid.region_cells_per_grid_cell(w))
    keeps_a_gap = all(chebyshev_distance(cell, town_cell) >= smallest_gap for town_cell in town_cells)
    return keeps_a_gap


def with_generated_towns(w: World, marks: Dict[GridCell, str], variant: int) -> Dict[GridCell, str]:
    size = map_grid.region_cells_per_grid_cell(w)
    kept_centres = [map_grid.centre_region_cell_of_grid_cell(cell, size) for cell, mark in marks.items() if mark == TOWN_MARK]
    town_marks = dict(marks)
    for site in pick_town_sites(w, variant, kept_centres):
        cell = map_grid.grid_cell_of_region_cell(site, size)
        if is_good_town_cell(w, cell, town_marks):
            town_marks[cell] = TOWN_MARK
    return town_marks


def town_stops(marks: Dict[GridCell, str]) -> List[SimpleNamespace]:
    groups = map_grid.town_groups_of(marks)
    stops = [SimpleNamespace(id=str(index), cells=set(cells), cx=float(np.mean([cell[0] for cell in cells])),
                             cy=float(np.mean([cell[1] for cell in cells]))) for index, cells in enumerate(groups)]
    return stops


def town_pairs_to_join(stops: Sequence[SimpleNamespace], variant: int, seed: int) -> List[Tuple[SimpleNamespace, SimpleNamespace]]:
    pairs = network.town_tree(list(stops), variant, seed) + network.extra_town_pairs(list(stops), variant, seed)
    return pairs


def cells_beside(cells: Set[GridCell]) -> Set[GridCell]:
    beside = {neighbour for cell in cells for neighbour in map_grid.neighbours_of(cell)} - cells
    return beside


def entry_cost(cell: GridCell, costs: np.ndarray, marks: Dict[GridCell, str], cells_beside_other_towns: Set[GridCell],
               cells_beside_routes: Set[GridCell]) -> float:
    if marks.get(cell) == ROUTE_MARK:
        return COST_OF_RUNNING_ON_AN_EARLIER_ROUTE
    beside_route_cost = COST_OF_RUNNING_BESIDE_AN_EARLIER_ROUTE if cell in cells_beside_routes else 0.0
    beside_town_cost = COST_OF_RUNNING_BESIDE_ANOTHER_TOWN if cell in cells_beside_other_towns else 0.0
    cost = float(costs[cell[1], cell[0]]) + beside_route_cost + beside_town_cost
    return cost


def metro_path(start_town: SimpleNamespace, end_town: SimpleNamespace, all_town_cells: Set[GridCell], costs: np.ndarray,
               marks: Dict[GridCell, str]) -> List[GridCell]:
    grid_size = (costs.shape[1], costs.shape[0])
    cells_beside_other_towns = cells_beside(all_town_cells - start_town.cells - end_town.cells)
    cells_beside_routes = cells_beside({cell for cell, mark in marks.items() if mark == ROUTE_MARK})
    goal_cells = cells_beside(end_town.cells) - all_town_cells
    frontier: List[Tuple[float, int, GridCell, Optional[GridCell]]] = []
    came_from: Dict[Tuple[GridCell, Optional[GridCell]], Tuple[GridCell, Optional[GridCell]]] = {}
    best_cost: Dict[Tuple[GridCell, Optional[GridCell]], float] = {}
    counter = 0
    for cell in sorted(cells_beside(start_town.cells) - all_town_cells, key=map_grid.scan_order):
        if not map_grid.is_inside_grid(cell, grid_size):
            continue
        heading = next((step for step in map_grid.STEPS if (cell[0] - step[0], cell[1] - step[1]) in start_town.cells), None)
        start_cost = entry_cost(cell, costs, marks, cells_beside_other_towns, cells_beside_routes)
        best_cost[(cell, heading)] = start_cost
        heapq.heappush(frontier, (start_cost, counter, cell, heading))
        counter += 1
    while frontier:
        cost_so_far, _order, cell, heading = heapq.heappop(frontier)
        if cost_so_far > best_cost.get((cell, heading), np.inf):
            continue
        if cell in goal_cells:
            return walked_back_path(came_from, (cell, heading))
        for step in map_grid.STEPS:
            following = (cell[0] + step[0], cell[1] + step[1])
            if not map_grid.is_inside_grid(following, grid_size) or following in all_town_cells:
                continue
            turn_cost = COST_OF_A_TURN if heading is not None and step != heading else 0.0
            following_cost = cost_so_far + turn_cost + entry_cost(following, costs, marks, cells_beside_other_towns, cells_beside_routes)
            if following_cost >= best_cost.get((following, step), np.inf):
                continue
            best_cost[(following, step)] = following_cost
            came_from[(following, step)] = (cell, heading)
            heapq.heappush(frontier, (following_cost, counter, following, step))
            counter += 1
    return []


def walked_back_path(came_from: Dict, last_state: Tuple[GridCell, Optional[GridCell]]) -> List[GridCell]:
    path = [last_state[0]]
    state = last_state
    while state in came_from:
        state = came_from[state]
        path.append(state[0])
    forward_path = path[::-1]
    return forward_path


def with_path_assigned(assignment: Dict[GridCell, str], path: Sequence[GridCell]) -> Dict[GridCell, str]:
    fresh_cells = [cell for cell in path if cell not in assignment]
    extended = dict(assignment)
    for run in map_grid.connected_groups(fresh_cells):
        fresh_key = map_grid.next_free_route_key(extended)
        extended.update({cell: fresh_key for cell in run})
    return extended


def with_generated_routes(w: World, marks: Dict[GridCell, str], assignment: Dict[GridCell, str],
                          variant: int) -> Tuple[Dict[GridCell, str], Dict[GridCell, str]]:
    costs = grid_cell_costs(w, variant)
    stops = town_stops(marks)
    all_town_cells = {cell for stop in stops for cell in stop.cells}
    route_marks = dict(marks)
    route_assignment = dict(assignment)
    for start_town, end_town in town_pairs_to_join(stops, variant, w.seed):
        path = metro_path(start_town, end_town, all_town_cells, costs, route_marks)
        route_marks.update({cell: ROUTE_MARK for cell in path})
        route_assignment = with_path_assigned(route_assignment, path)
    return route_marks, route_assignment


def kept_marks_and_assignment(w: World) -> Tuple[Dict[GridCell, str], Dict[GridCell, str]]:
    grid_size = map_grid.grid_size_of_world(w)
    size = map_grid.region_cells_per_grid_cell(w)
    pinned_routes = [route for route in w.routes if route.get("pinned")]
    assignment = {cell: route.get("grid_key") or f"pinned{index}" for index, route in enumerate(pinned_routes)
                  for cell in map_grid.old_route_grid_cells(route, size)}
    pinned_town_cells = [cell for town in w.cities if town.pinned for cell in map_grid.old_town_grid_cells(town, size)]
    marks = map_grid.with_cells_painted({}, list(assignment), "route", grid_size)
    marks = map_grid.with_cells_painted(marks, pinned_town_cells, "town", grid_size)
    kept_assignment = {cell: key for cell, key in assignment.items() if marks.get(cell) == ROUTE_MARK}
    return marks, kept_assignment


def generated_marks_and_assignment(w: World, variant: int) -> Tuple[Dict[GridCell, str], Dict[GridCell, str]]:
    marks, assignment = kept_marks_and_assignment(w)
    marks_with_towns = with_generated_towns(w, marks, variant)
    marks_with_routes, route_assignment = with_generated_routes(w, marks_with_towns, assignment, variant)
    return marks_with_routes, route_assignment
