from __future__ import annotations

from types import SimpleNamespace
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np

from . import network
from .world import City, World

GridCell = Tuple[int, int]
RegionCell = Tuple[int, int]
REGION_CELLS_PER_GRID_CELL = 8
GRID_CELL_SIZES_IN_REGION_CELLS = (4, 6, 8, 10, 12, 16)
EMPTY_MARK, ROUTE_MARK, TOWN_MARK = ".", "r", "t"
MARK_OF_BRUSH = {"route": ROUTE_MARK, "town": TOWN_MARK, "erase": EMPTY_MARK}
SMALLEST_WATER_SHARE_OF_A_SEA_GRID_CELL = 0.5
NEW_ROUTE_KEY = "new"
FEWEST_GRID_CELLS_OF_A_CITY = 3
STEPS: Tuple[GridCell, ...] = ((1, 0), (0, 1), (-1, 0), (0, -1))
PAINTED_LINK_KIND = "painted"


class MapGridProblem(ValueError):
    pass


def region_cells_per_grid_cell(w: World) -> int:
    stored_map_grid = w.overrides.get("map_grid") or {}
    size = int(stored_map_grid.get("cell") or REGION_CELLS_PER_GRID_CELL)
    return size


def grid_size_of_world(w: World) -> Tuple[int, int]:
    size = region_cells_per_grid_cell(w)
    grid_size = (w.W // size, w.H // size)
    return grid_size


def ordered_route_entries(assignment: Dict[GridCell, str]) -> List[dict]:
    keys = sorted(set(assignment.values()), key=route_key_order)
    entries = [{"key": key, "cells": [[cell[0], cell[1]] for cell in ordered_cells_of_route({cell for cell, owner in assignment.items() if owner == key})]}
               for key in keys]
    return entries


def map_grid_of_marks(marks: Dict[GridCell, str], grid_size: Tuple[int, int], size: int, assignment: Dict[GridCell, str]) -> dict:
    grid_width, grid_height = grid_size
    rows = ["".join(marks.get((column, row), EMPTY_MARK) for column in range(grid_width)) for row in range(grid_height)]
    map_grid = {"cell": size, "size": [grid_width, grid_height], "rows": rows, "routes": ordered_route_entries(assignment)}
    return map_grid


def empty_map_grid(w: World) -> dict:
    map_grid = map_grid_of_marks({}, grid_size_of_world(w), region_cells_per_grid_cell(w), {})
    return map_grid


def marks_of_map_grid(map_grid: dict) -> Dict[GridCell, str]:
    marks = {(column, row): mark for row, row_marks in enumerate(map_grid.get("rows") or [])
             for column, mark in enumerate(row_marks) if mark in (ROUTE_MARK, TOWN_MARK)}
    return marks


def assignment_of_map_grid(map_grid: dict) -> Dict[GridCell, str]:
    assignment = {(int(cell[0]), int(cell[1])): str(entry["key"]) for entry in map_grid.get("routes") or [] for cell in entry.get("cells") or []}
    return assignment


def route_key_order(key: str) -> Tuple[int, str]:
    digits = "".join(character for character in key if character.isdigit())
    order = (int(digits) if digits else 0, key)
    return order


def next_free_route_key(assignment: Dict[GridCell, str]) -> str:
    taken = set(assignment.values())
    number = 1
    while f"r{number}" in taken:
        number += 1
    free_key = f"r{number}"
    return free_key


def is_inside_grid(cell: GridCell, grid_size: Tuple[int, int]) -> bool:
    inside = 0 <= cell[0] < grid_size[0] and 0 <= cell[1] < grid_size[1]
    return inside


def mark_after_brush(current_mark: str, brush: str) -> str:
    if brush == "route" and current_mark == TOWN_MARK:
        return current_mark
    painted_mark = MARK_OF_BRUSH[brush]
    return painted_mark


def with_cells_painted(marks: Dict[GridCell, str], cells: Iterable[Sequence[int]], brush: str,
                       grid_size: Tuple[int, int]) -> Dict[GridCell, str]:
    if brush not in MARK_OF_BRUSH:
        raise MapGridProblem(f"unknown map brush {brush!r}: use route, town or erase")
    painted_marks = dict(marks)
    for column, row in cells:
        cell = (int(column), int(row))
        if not is_inside_grid(cell, grid_size):
            continue
        painted_marks[cell] = mark_after_brush(painted_marks.get(cell, EMPTY_MARK), brush)
    painted_marks = {cell: mark for cell, mark in painted_marks.items() if mark != EMPTY_MARK}
    return painted_marks


def target_route_key(route_key: Optional[str], first_cell: Optional[GridCell], assignment: Dict[GridCell, str]) -> str:
    if route_key and route_key != NEW_ROUTE_KEY:
        return route_key
    if not route_key and first_cell in assignment:
        return assignment[first_cell]
    fresh_key = next_free_route_key(assignment)
    return fresh_key


def may_take_route_cell(cell: GridCell, target_key: str, assignment: Dict[GridCell, str], takes_other_routes_cells: bool) -> bool:
    owner = assignment.get(cell)
    may_take = owner is None or owner == target_key or takes_other_routes_cells
    return may_take


def painted_map(marks: Dict[GridCell, str], assignment: Dict[GridCell, str], cells: Iterable[Sequence[int]], brush: str,
                grid_size: Tuple[int, int], route_key: Optional[str] = None) -> Tuple[Dict[GridCell, str], Dict[GridCell, str]]:
    inside_cells = [(int(column), int(row)) for column, row in cells if is_inside_grid((int(column), int(row)), grid_size)]
    if brush != "route":
        painted_marks = with_cells_painted(marks, inside_cells, brush, grid_size)
        kept_assignment = {cell: key for cell, key in assignment.items() if painted_marks.get(cell) == ROUTE_MARK}
        return painted_marks, kept_assignment
    target_key = target_route_key(route_key, inside_cells[0] if inside_cells else None, assignment)
    takes_other_routes_cells = bool(route_key)
    paintable_cells = [cell for cell in inside_cells if marks.get(cell) != TOWN_MARK and may_take_route_cell(cell, target_key, assignment, takes_other_routes_cells)]
    painted_marks = with_cells_painted(marks, paintable_cells, "route", grid_size)
    painted_assignment = dict(assignment)
    painted_assignment.update({cell: target_key for cell in paintable_cells})
    return painted_marks, painted_assignment


def with_pieces_split_off(assignment: Dict[GridCell, str]) -> Dict[GridCell, str]:
    split = dict(assignment)
    for key in sorted(set(assignment.values()), key=route_key_order):
        pieces = sorted(connected_groups(cell for cell, owner in assignment.items() if owner == key), key=lambda piece: (-len(piece), scan_order(piece[0])))
        for piece in pieces[1:]:
            fresh_key = next_free_route_key(split)
            split.update({cell: fresh_key for cell in piece})
    return split


def repaired_assignment(marks: Dict[GridCell, str], assignment: Dict[GridCell, str]) -> Dict[GridCell, str]:
    repaired = with_pieces_split_off({cell: key for cell, key in assignment.items() if marks.get(cell) == ROUTE_MARK})
    unassigned_cells = [cell for cell, mark in marks.items() if mark == ROUTE_MARK and cell not in repaired]
    for group in connected_groups(unassigned_cells):
        fresh_key = next_free_route_key(repaired)
        repaired.update({cell: fresh_key for cell in group})
    return repaired


def ordered_cells_of_route(cells: Set[GridCell]) -> List[GridCell]:
    chains = sorted(route_chains(cells), key=lambda chain: (-len(chain), scan_order(chain[0])))
    ordered_cells = [cell for chain in chains for cell in chain]
    return ordered_cells


def neighbours_of(cell: GridCell) -> List[GridCell]:
    neighbours = [(cell[0] + step_x, cell[1] + step_y) for step_x, step_y in STEPS]
    return neighbours


def scan_order(cell: GridCell) -> Tuple[int, int]:
    order = (cell[1], cell[0])
    return order


def connected_group_from(start: GridCell, cells: Set[GridCell]) -> List[GridCell]:
    group = {start}
    waiting = [start]
    while waiting:
        cell = waiting.pop()
        fresh_neighbours = [neighbour for neighbour in neighbours_of(cell) if neighbour in cells and neighbour not in group]
        group.update(fresh_neighbours)
        waiting.extend(fresh_neighbours)
    sorted_group = sorted(group, key=scan_order)
    return sorted_group


def connected_groups(cells: Iterable[GridCell]) -> List[List[GridCell]]:
    remaining = set(cells)
    groups = []
    while remaining:
        group = connected_group_from(min(remaining, key=scan_order), remaining)
        groups.append(group)
        remaining -= set(group)
    return groups


def chain_start_rank(cell: GridCell, remaining: Set[GridCell]) -> Tuple[int, int, int]:
    neighbour_count = sum(neighbour in remaining for neighbour in neighbours_of(cell))
    endpoint_rank = {1: 0, 0: 1}.get(neighbour_count, 2)
    rank = (endpoint_rank,) + scan_order(cell)
    return rank


def next_cell_of_walk(cell: GridCell, heading: Optional[GridCell], remaining: Set[GridCell]) -> Optional[GridCell]:
    steps_to_try = ([heading] if heading else []) + [step for step in STEPS if step != heading]
    for step_x, step_y in steps_to_try:
        candidate = (cell[0] + step_x, cell[1] + step_y)
        if candidate in remaining:
            return candidate
    return None


def walked_chain(start: GridCell, remaining: Set[GridCell]) -> List[GridCell]:
    chain = [start]
    unwalked = set(remaining) - {start}
    heading: Optional[GridCell] = None
    while True:
        following = next_cell_of_walk(chain[-1], heading, unwalked)
        if following is None:
            break
        heading = (following[0] - chain[-1][0], following[1] - chain[-1][1])
        chain.append(following)
        unwalked.discard(following)
    return chain


def route_chains(route_cells: Iterable[GridCell]) -> List[List[GridCell]]:
    remaining = set(route_cells)
    chains = []
    while remaining:
        start = min(remaining, key=lambda cell: chain_start_rank(cell, remaining))
        chain = walked_chain(start, remaining)
        chains.append(chain)
        remaining -= set(chain)
    return chains


def region_rect_of_grid_cells(cells: Sequence[GridCell], size: int) -> List[int]:
    columns = [cell[0] for cell in cells]
    rows = [cell[1] for cell in cells]
    region_rect = [min(columns) * size, min(rows) * size, (max(columns) + 1) * size, (max(rows) + 1) * size]
    return region_rect


def grid_cell_of_region_cell(region_cell: Sequence[float], size: int) -> GridCell:
    grid_cell = (int(region_cell[0]) // size, int(region_cell[1]) // size)
    return grid_cell


def centre_region_cell_of_grid_cell(cell: GridCell, size: int) -> RegionCell:
    centre = (cell[0] * size + size // 2, cell[1] * size + size // 2)
    return centre


def edge_region_cell_of_grid_cell(cell: GridCell, step: GridCell, size: int) -> RegionCell:
    centre_x, centre_y = centre_region_cell_of_grid_cell(cell, size)
    edge_x = {1: cell[0] * size + size - 1, -1: cell[0] * size}.get(step[0], centre_x)
    edge_y = {1: cell[1] * size + size - 1, -1: cell[1] * size}.get(step[1], centre_y)
    edge_cell = (edge_x, edge_y)
    return edge_cell


def water_share_of_grid_cell(w: World, cell: GridCell) -> float:
    left, top, right, bottom = region_rect_of_grid_cells([cell], region_cells_per_grid_cell(w))
    water_block = (~w.land | w.lake)[top:bottom, left:right]
    water_share = float(water_block.mean()) if water_block.size else 0.0
    return water_share


def is_sea_grid_cell(w: World, cell: GridCell) -> bool:
    is_sea = water_share_of_grid_cell(w, cell) >= SMALLEST_WATER_SHARE_OF_A_SEA_GRID_CELL
    return is_sea


def mode_of_route_cells(w: World, cells: Sequence[GridCell]) -> str:
    sea_cell_count = sum(is_sea_grid_cell(w, cell) for cell in cells)
    mode = "sea" if sea_cell_count * 2 >= len(cells) else "land"
    return mode


def town_groups_of(marks: Dict[GridCell, str]) -> List[List[GridCell]]:
    town_groups = connected_groups(cell for cell, mark in marks.items() if mark == TOWN_MARK)
    return town_groups


def painted_route_stretches(w: World, assignment: Dict[GridCell, str]) -> Tuple[List[Tuple[str, List[GridCell]]], List[str]]:
    keys = sorted(set(assignment.values()), key=route_key_order)
    ordered_cells_per_key = [ordered_cells_of_route({cell for cell, owner in assignment.items() if owner == key}) for key in keys]
    stretches = [(mode_of_route_cells(w, cells), cells) for cells in ordered_cells_per_key]
    return stretches, keys


def old_town_grid_cells(old_town: City, size: int) -> Set[GridCell]:
    if old_town.grid_cells:
        return {(int(cell[0]), int(cell[1])) for cell in old_town.grid_cells}
    centre_cells = {grid_cell_of_region_cell((old_town.cx, old_town.cy), size)}
    return centre_cells


def old_route_grid_cells(old_route: dict, size: int) -> Set[GridCell]:
    if old_route.get("grid_cells"):
        return {(int(cell[0]), int(cell[1])) for cell in old_route["grid_cells"]}
    path_cells = {grid_cell_of_region_cell(cell, size) for cell in old_route.get("path") or []}
    return path_cells


def matched_old_index_of_each_group(groups: Sequence[Sequence[GridCell]], old_cell_sets: Sequence[Set[GridCell]]) -> Dict[int, int]:
    overlaps = [(len(set(group) & old_cells), group_index, old_index)
                for group_index, group in enumerate(groups) for old_index, old_cells in enumerate(old_cell_sets)]
    matches: Dict[int, int] = {}
    for overlap, group_index, old_index in sorted(overlaps, key=lambda entry: (-entry[0], entry[1], entry[2])):
        if overlap == 0 or group_index in matches or old_index in matches.values():
            continue
        matches[group_index] = old_index
    return matches


def next_free_name(prefix: str, taken: Set[str]) -> str:
    number = 1
    while f"{prefix} {number}" in taken:
        number += 1
    free_name = f"{prefix} {number}"
    return free_name


def unique_town_id(name: str, taken_ids: Set[str]) -> str:
    base = network.ascii_id(name).lower() or "town"
    town_id, number = base, 2
    while town_id in taken_ids:
        town_id = f"{base}{number}"
        number += 1
    return town_id


def landmass_name_of_grid_cells(w: World, cells: Sequence[GridCell]) -> str:
    centre_x, centre_y = centre_region_cell_of_grid_cell(cells[0], region_cells_per_grid_cell(w))
    if w.landmass is None or not w.landmass[centre_y, centre_x]:
        return ""
    landmass_name = f"landmass {int(w.landmass[centre_y, centre_x])}"
    return landmass_name


def town_size_of(cells: Sequence[GridCell], size: int) -> str:
    smallest_city_area = FEWEST_GRID_CELLS_OF_A_CITY * REGION_CELLS_PER_GRID_CELL ** 2
    town_size = "city" if len(cells) * size ** 2 >= smallest_city_area else "town"
    return town_size


def town_of_group(w: World, cells: List[GridCell], old_town: Optional[City], name: str, town_id: str) -> City:
    grid_cells = [[cell[0], cell[1]] for cell in cells]
    rect = region_rect_of_grid_cells(cells, region_cells_per_grid_cell(w))
    if old_town is not None:
        kept_town = City(old_town.id, old_town.name, rect, old_town.gym, old_town.honours, landmass_name_of_grid_cells(w, cells),
                         town_size_of(cells, region_cells_per_grid_cell(w)), old_town.note, old_town.pinned, grid_cells)
        return kept_town
    new_town = City(town_id, name, rect, gym="", honours="", half=landmass_name_of_grid_cells(w, cells),
                    size=town_size_of(cells, region_cells_per_grid_cell(w)), grid_cells=grid_cells)
    return new_town


def towns_from_groups(w: World, town_groups: List[List[GridCell]], old_towns: Sequence[City]) -> List[City]:
    size = region_cells_per_grid_cell(w)
    matches = matched_old_index_of_each_group(town_groups, [old_town_grid_cells(town, size) for town in old_towns])
    taken_names = {old_towns[old_index].name for old_index in matches.values()}
    taken_ids = {old_towns[old_index].id for old_index in matches.values()}
    towns = []
    for group_index, cells in enumerate(town_groups):
        old_town = old_towns[matches[group_index]] if group_index in matches else None
        name = old_town.name if old_town else next_free_name("Town", taken_names)
        town_id = old_town.id if old_town else unique_town_id(name, taken_ids)
        taken_names.add(name)
        taken_ids.add(town_id)
        towns.append(town_of_group(w, cells, old_town, name, town_id))
    return towns


def names_of_merged_away_towns(old_towns: Sequence[City], towns: Sequence[City], size: int) -> Dict[str, str]:
    surviving_names = {town.name for town in towns}
    town_of_cell = {(cell[0], cell[1]): town.name for town in towns for cell in town.grid_cells}
    merged_away = {}
    for old_town in old_towns:
        if old_town.name in surviving_names:
            continue
        landing_names = [town_of_cell[cell] for cell in sorted(old_town_grid_cells(old_town, size)) if cell in town_of_cell]
        if landing_names:
            merged_away[old_town.name] = landing_names[0]
    return merged_away


def old_routes_by_key(keys: Sequence[str], old_routes: Sequence[dict]) -> Dict[int, dict]:
    old_route_of_key = {route.get("grid_key"): route for route in old_routes if route.get("grid_key")}
    kept = {stretch_index: old_route_of_key[key] for stretch_index, key in enumerate(keys) if key in old_route_of_key}
    return kept


def route_names_for_stretches(stretches: Sequence[Tuple[str, List[GridCell]]], keys: Sequence[str], old_routes: Sequence[dict],
                              taken_by_towns: Set[str], size: int) -> Tuple[List[str], Dict[int, dict]]:
    kept_old_routes = old_routes_by_key(keys, old_routes)
    unmatched_indices = [index for index in range(len(stretches)) if index not in kept_old_routes]
    unmatched_old_routes = [route for route in old_routes if not any(route is kept for kept in kept_old_routes.values()) and not route.get("grid_key")]
    matches = matched_old_index_of_each_group([stretches[index][1] for index in unmatched_indices], [old_route_grid_cells(route, size) for route in unmatched_old_routes])
    kept_old_routes.update({unmatched_indices[group_index]: unmatched_old_routes[old_index] for group_index, old_index in matches.items()})
    taken = set(taken_by_towns) | {route["name"] for route in kept_old_routes.values()}
    names = []
    for stretch_index in range(len(stretches)):
        name = kept_old_routes[stretch_index]["name"] if stretch_index in kept_old_routes else next_free_name("Route", taken)
        taken.add(name)
        names.append(name)
    return names, kept_old_routes


def connection_record(route_node: int, route_cell: GridCell, other_node: int, other_cell: GridCell, link_name: str, size: int) -> dict:
    step = (other_cell[0] - route_cell[0], other_cell[1] - route_cell[1])
    back_step = (-step[0], -step[1])
    side = network.SIDE_OF[step]
    record = dict(a=route_node, b=other_node, cell_a=edge_region_cell_of_grid_cell(route_cell, step, size),
                  cell_b=edge_region_cell_of_grid_cell(other_cell, back_step, size), side_a=side, side_b=network.OPP[side],
                  link=link_name)
    return record


def end_cells_of(cells: Sequence[GridCell]) -> List[GridCell]:
    end_cells = [cells[0]] if len(cells) == 1 else [cells[0], cells[-1]]
    return end_cells


def town_touches_of_stretch(cells: Sequence[GridCell], town_of_cell: Dict[GridCell, int]) -> List[Tuple[int, GridCell, GridCell, int]]:
    touches = []
    touched_pairs: Set[Tuple[int, int]] = set()
    end_cells = end_cells_of(cells)
    for end_rank, route_cell in [(rank, cell) for rank, cell in enumerate(end_cells)] + [(2, cell) for cell in cells]:
        for neighbour in neighbours_of(route_cell):
            town_index = town_of_cell.get(neighbour)
            pair_key = (town_index, end_rank if end_rank < 2 else -1)
            if town_index is None or pair_key in touched_pairs or (end_rank == 2 and any(pair[0] == town_index for pair in touched_pairs)):
                continue
            touched_pairs.add(pair_key)
            touches.append((end_rank, route_cell, neighbour, town_index))
    return touches


def route_touches_of_stretch(stretch_index: int, cells: Sequence[GridCell], route_of_cell: Dict[GridCell, int],
                             joined_pairs: Set[FrozenSet[int]]) -> List[Tuple[int, GridCell, GridCell, int]]:
    touches = []
    for end_rank, route_cell in list(enumerate(end_cells_of(cells))) + [(2, cell) for cell in cells]:
        for neighbour in neighbours_of(route_cell):
            other_index = route_of_cell.get(neighbour)
            if other_index is None or other_index == stretch_index or frozenset((stretch_index, other_index)) in joined_pairs:
                continue
            joined_pairs.add(frozenset((stretch_index, other_index)))
            touches.append((end_rank, route_cell, neighbour, other_index))
    return touches


def is_end_connected(touches: Sequence[Tuple[int, GridCell, GridCell, int]], end_rank: int) -> bool:
    connected = any(touch[0] == end_rank for touch in touches)
    return connected


def oriented_from_a_connected_end(cells: List[GridCell], touches: List[Tuple[int, GridCell, GridCell, int]]) -> Tuple[List[GridCell], List[Tuple[int, GridCell, GridCell, int]]]:
    if len(cells) == 1 or is_end_connected(touches, 0) or not is_end_connected(touches, 1):
        return cells, touches
    flipped_touches = [({0: 1, 1: 0}.get(touch[0], touch[0]),) + touch[1:] for touch in touches]
    return cells[::-1], flipped_touches


def region_cells_along_corners(corners: Sequence[RegionCell]) -> List[RegionCell]:
    cells = [tuple(corners[0])]
    for end_x, end_y in corners[1:]:
        x, y = cells[-1]
        while x != end_x:
            x += 1 if end_x > x else -1
            cells.append((x, y))
        while y != end_y:
            y += 1 if end_y > y else -1
            cells.append((x, y))
    return cells


def exit_region_cell_of_touch(touch: Tuple[int, GridCell, GridCell, int], size: int) -> RegionCell:
    _end_rank, route_cell, neighbour, _other = touch
    exit_cell = edge_region_cell_of_grid_cell(route_cell, (neighbour[0] - route_cell[0], neighbour[1] - route_cell[1]), size)
    return exit_cell


def through_touches_of_stretch(cells: Sequence[GridCell], touches: Sequence[Tuple[int, GridCell, GridCell, int]]) -> List[Tuple[int, GridCell, GridCell, int]]:
    end_touches = [touch for touch in touches if touch[0] < 2]
    if len(cells) == 1:
        return end_touches[:2]
    first_end_touches = [touch for touch in end_touches if touch[0] == 0][:1]
    last_end_touches = [touch for touch in end_touches if touch[0] == 1][:1]
    through_touches = first_end_touches + last_end_touches
    return through_touches


def is_dead_end_stretch(cells: Sequence[GridCell], touches: Sequence[Tuple[int, GridCell, GridCell, int]]) -> bool:
    through_touches = through_touches_of_stretch(cells, touches)
    dead_end = len(through_touches) < 2 if len(cells) == 1 else not is_end_connected(through_touches, 1)
    return dead_end


def shortest_steps_inside(start: GridCell, goal: GridCell, cells: Set[GridCell]) -> List[GridCell]:
    came_from: Dict[GridCell, Optional[GridCell]] = {start: None}
    waiting = [start]
    while waiting:
        cell = waiting.pop(0)
        if cell == goal:
            break
        for neighbour in neighbours_of(cell):
            if neighbour in cells and neighbour not in came_from:
                came_from[neighbour] = cell
                waiting.append(neighbour)
    if goal not in came_from:
        return [goal]
    steps = [goal]
    while came_from[steps[-1]] not in (None, start):
        steps.append(came_from[steps[-1]])
    forward_steps = steps[::-1]
    return forward_steps


def walk_through_route_cells(cells: Sequence[GridCell]) -> List[GridCell]:
    if not cells:
        return []
    route_cells = set(cells)
    walk = [cells[0]]
    for cell in cells[1:]:
        steps = [cell] if cell in neighbours_of(walk[-1]) else shortest_steps_inside(walk[-1], cell, route_cells)
        walk.extend(steps)
    return walk


def region_path_of_stretch(cells: Sequence[GridCell], touches: Sequence[Tuple[int, GridCell, GridCell, int]], size: int) -> List[List[int]]:
    through_touches = through_touches_of_stretch(cells, touches)
    first_end_touches = [touch for touch in through_touches if touch[0] == 0][:1]
    last_end_touches = [touch for touch in through_touches if touch not in first_end_touches][:1]
    lead_in = [exit_region_cell_of_touch(first_end_touches[0], size)] if first_end_touches else []
    lead_out = [exit_region_cell_of_touch(last_end_touches[0], size)] if last_end_touches else []
    corners = lead_in + [centre_region_cell_of_grid_cell(cell, size) for cell in walk_through_route_cells(cells)] + lead_out
    region_path = [[x, y] for x, y in region_cells_along_corners(corners)]
    return region_path


def route_node_record(w: World, node_index: int, name: str, mode: str, cells: List[GridCell],
                      touches: List[Tuple[int, GridCell, GridCell, int]], old_route: Optional[dict], key: str) -> dict:
    route_id = old_route["id"] if old_route else network.ascii_id(name)
    size = region_cells_per_grid_cell(w)
    rect = region_rect_of_grid_cells(cells, size)
    node = dict(kind="route", idx=node_index, rect=rect, paths=[[tuple(cell) for cell in region_path_of_stretch(cells, touches, size)]],
                mode=mode, link=name, kind_link=PAINTED_LINK_KIND, name=name, id=route_id,
                map=old_route["map"] if old_route else "MAP_" + route_id.upper(), keep=old_route,
                grid_cells=[[cell[0], cell[1]] for cell in cells], grid_key=key)
    if is_dead_end_stretch(cells, touches):
        node["goal"] = list(centre_region_cell_of_grid_cell(cells[-1], size))
    node["biome_code"] = network.dominant_biome_code_under_route(w, node)
    return node


def town_node_record(node_index: int, town: City) -> dict:
    node = dict(kind="city", idx=node_index, rect=list(town.rect), paths=[], name=town.name, id=town.id, map="MAP_" + town.id.upper())
    return node


def stretch_touches(stretches, town_of_cell, route_of_cell) -> List[List[Tuple[int, GridCell, GridCell, int]]]:
    joined_pairs: Set[FrozenSet[int]] = set()
    touches_per_stretch = []
    for stretch_index, (_mode, cells) in enumerate(stretches):
        town_touches = town_touches_of_stretch(cells, town_of_cell)
        route_touches = route_touches_of_stretch(stretch_index, cells, route_of_cell, joined_pairs)
        touches_per_stretch.append(sorted(town_touches + route_touches, key=lambda touch: touch[0]))
    return touches_per_stretch


def painted_network(w: World, towns: Sequence[City], stretches: Sequence[Tuple[str, List[GridCell]]], keys: Sequence[str],
                    names: Sequence[str], kept_old_routes: Dict[int, dict]) -> SimpleNamespace:
    town_of_cell = {(cell[0], cell[1]): town_index for town_index, town in enumerate(towns) for cell in town.grid_cells}
    route_of_cell = {cell: stretch_index for stretch_index, (_mode, cells) in enumerate(stretches) for cell in cells}
    touches_per_stretch = stretch_touches(stretches, town_of_cell, route_of_cell)
    nodes = [town_node_record(town_index, town) for town_index, town in enumerate(towns)]
    route_node_of_stretch = {stretch_index: len(towns) + stretch_index for stretch_index in range(len(stretches))}
    conns = []
    for stretch_index, (mode, cells) in enumerate(stretches):
        oriented_cells, touches = oriented_from_a_connected_end(list(cells), touches_per_stretch[stretch_index])
        node_index = route_node_of_stretch[stretch_index]
        nodes.append(route_node_record(w, node_index, names[stretch_index], mode, oriented_cells, touches, kept_old_routes.get(stretch_index), keys[stretch_index]))
        for touch in touches:
            _end_rank, route_cell, neighbour, other_index = touch
            other_node = other_index if neighbour in town_of_cell else route_node_of_stretch[other_index]
            conns.append(connection_record(node_index, route_cell, other_node, neighbour, names[stretch_index], region_cells_per_grid_cell(w)))
    painted = SimpleNamespace(nodes=nodes, conns=conns)
    return painted


def route_records_of(w: World, painted: SimpleNamespace) -> List[dict]:
    network.classify(w, painted)
    routes = []
    for node in painted.nodes:
        if node["kind"] != "route":
            continue
        manual_role = ((node.get("keep") or {}).get("manual") or {}).get("role")
        if manual_role:
            node["role"] = manual_role
        route = network.route_json(w, painted, node)
        route["biome_code"] = node["biome_code"]
        route["grid_cells"] = node["grid_cells"]
        route["grid_key"] = node["grid_key"]
        routes.append(route)
    return routes


def link_records_of(routes: Sequence[dict]) -> List[dict]:
    links = [dict(name=route["link"], kind=PAINTED_LINK_KIND, mode=route["mode"], routes=[route["name"]], landmark=None) for route in routes]
    return links


def map_from_marks(w: World, marks: Dict[GridCell, str], assignment: Dict[GridCell, str]) -> Dict[str, str]:
    old_towns = list(w.cities)
    towns = towns_from_groups(w, town_groups_of(marks), old_towns)
    route_assignment = repaired_assignment(marks, assignment)
    stretches, keys = painted_route_stretches(w, route_assignment)
    size = region_cells_per_grid_cell(w)
    names, kept_old_routes = route_names_for_stretches(stretches, keys, w.routes, {town.name for town in towns}, size)
    painted = painted_network(w, towns, stretches, keys, names, kept_old_routes)
    w.cities = list(towns)
    w.routes = route_records_of(w, painted)
    w.links = link_records_of(w.routes)
    w.overrides["map_grid"] = map_grid_of_marks(marks, grid_size_of_world(w), size, route_assignment)
    merged_away = names_of_merged_away_towns(old_towns, towns, size)
    return merged_away


def map_from_map_grid(w: World, map_grid: dict) -> Dict[str, str]:
    merged_away = map_from_marks(w, marks_of_map_grid(map_grid), assignment_of_map_grid(map_grid))
    return merged_away


def grid_cells_along_region_path(path: Sequence[Sequence[int]], size: int) -> List[GridCell]:
    grid_cells: List[GridCell] = []
    for region_cell in path:
        grid_cell = grid_cell_of_region_cell(region_cell, size)
        if not grid_cells or grid_cells[-1] != grid_cell:
            grid_cells.append(grid_cell)
    return grid_cells


def route_cells_of_place(route: dict, size: int) -> List[GridCell]:
    if route.get("grid_cells"):
        return [(int(cell[0]), int(cell[1])) for cell in route["grid_cells"]]
    path_cells = grid_cells_along_region_path(route.get("path") or [], size)
    return path_cells


def no_reaching_cells(route: dict) -> List[GridCell]:
    return []


def marks_and_assignment_of_places(w: World, route_cells_of, reaching_cells_of=no_reaching_cells) -> Tuple[Dict[GridCell, str], Dict[GridCell, str]]:
    grid_size = grid_size_of_world(w)
    size = region_cells_per_grid_cell(w)
    assignment: Dict[GridCell, str] = {}
    for route in w.routes:
        key = route.get("grid_key") or next_free_route_key(assignment)
        route["grid_key"] = key
        assignment.update({cell: key for cell in route_cells_of(route) if is_inside_grid(cell, grid_size) and cell not in assignment})
    for route in w.routes:
        assignment.update({cell: route["grid_key"] for cell in reaching_cells_of(route) if is_inside_grid(cell, grid_size) and cell not in assignment})
    town_cells = [cell for town in w.cities for cell in old_town_grid_cells(town, size)]
    marks = with_cells_painted(with_cells_painted({}, list(assignment), "route", grid_size), town_cells, "town", grid_size)
    route_assignment = {cell: key for cell, key in assignment.items() if marks.get(cell) == ROUTE_MARK}
    return marks, route_assignment


def map_grid_of_world(w: World) -> dict:
    stored_map_grid = w.overrides.get("map_grid")
    if stored_map_grid and stored_map_grid.get("rows") and list(stored_map_grid.get("size") or []) == list(grid_size_of_world(w)):
        return stored_map_grid
    size = region_cells_per_grid_cell(w)
    marks, assignment = marks_and_assignment_of_places(w, lambda route: route_cells_of_place(route, size))
    map_grid = map_grid_of_marks(marks, grid_size_of_world(w), size, assignment)
    return map_grid
