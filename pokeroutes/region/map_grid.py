from __future__ import annotations

from types import SimpleNamespace
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np

from . import network
from .world import City, World

GridCell = Tuple[int, int]
RegionCell = Tuple[int, int]
REGION_CELLS_PER_GRID_CELL = 8
EMPTY_MARK, ROUTE_MARK, TOWN_MARK = ".", "r", "t"
MARK_OF_BRUSH = {"route": ROUTE_MARK, "town": TOWN_MARK, "erase": EMPTY_MARK}
SMALLEST_WATER_SHARE_OF_A_SEA_GRID_CELL = 0.5
SHORTEST_STRETCH_WITH_A_ROUTE_OF_ITS_OWN_IN_GRID_CELLS = 2
FEWEST_GRID_CELLS_OF_A_CITY = 3
STEPS: Tuple[GridCell, ...] = ((1, 0), (0, 1), (-1, 0), (0, -1))
PAINTED_LINK_KIND = "painted"


class MapGridProblem(ValueError):
    pass


def grid_size_of_world(w: World) -> Tuple[int, int]:
    grid_size = (w.W // REGION_CELLS_PER_GRID_CELL, w.H // REGION_CELLS_PER_GRID_CELL)
    return grid_size


def map_grid_of_marks(marks: Dict[GridCell, str], grid_size: Tuple[int, int]) -> dict:
    grid_width, grid_height = grid_size
    rows = ["".join(marks.get((column, row), EMPTY_MARK) for column in range(grid_width)) for row in range(grid_height)]
    map_grid = {"cell": REGION_CELLS_PER_GRID_CELL, "size": [grid_width, grid_height], "rows": rows}
    return map_grid


def empty_map_grid(w: World) -> dict:
    map_grid = map_grid_of_marks({}, grid_size_of_world(w))
    return map_grid


def marks_of_map_grid(map_grid: dict) -> Dict[GridCell, str]:
    marks = {(column, row): mark for row, row_marks in enumerate(map_grid.get("rows") or [])
             for column, mark in enumerate(row_marks) if mark in (ROUTE_MARK, TOWN_MARK)}
    return marks


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


def region_rect_of_grid_cells(cells: Sequence[GridCell]) -> List[int]:
    size = REGION_CELLS_PER_GRID_CELL
    columns = [cell[0] for cell in cells]
    rows = [cell[1] for cell in cells]
    region_rect = [min(columns) * size, min(rows) * size, (max(columns) + 1) * size, (max(rows) + 1) * size]
    return region_rect


def grid_cell_of_region_cell(region_cell: Sequence[float]) -> GridCell:
    grid_cell = (int(region_cell[0]) // REGION_CELLS_PER_GRID_CELL, int(region_cell[1]) // REGION_CELLS_PER_GRID_CELL)
    return grid_cell


def centre_region_cell_of_grid_cell(cell: GridCell) -> RegionCell:
    size = REGION_CELLS_PER_GRID_CELL
    centre = (cell[0] * size + size // 2, cell[1] * size + size // 2)
    return centre


def edge_region_cell_of_grid_cell(cell: GridCell, step: GridCell) -> RegionCell:
    size = REGION_CELLS_PER_GRID_CELL
    centre_x, centre_y = centre_region_cell_of_grid_cell(cell)
    edge_x = {1: cell[0] * size + size - 1, -1: cell[0] * size}.get(step[0], centre_x)
    edge_y = {1: cell[1] * size + size - 1, -1: cell[1] * size}.get(step[1], centre_y)
    edge_cell = (edge_x, edge_y)
    return edge_cell


def water_share_of_grid_cell(w: World, cell: GridCell) -> float:
    left, top, right, bottom = region_rect_of_grid_cells([cell])
    water_block = (~w.land | w.lake)[top:bottom, left:right]
    water_share = float(water_block.mean()) if water_block.size else 0.0
    return water_share


def is_sea_grid_cell(w: World, cell: GridCell) -> bool:
    is_sea = water_share_of_grid_cell(w, cell) >= SMALLEST_WATER_SHARE_OF_A_SEA_GRID_CELL
    return is_sea


def mode_runs(modes: Sequence[str]) -> List[List[int]]:
    runs: List[List[int]] = []
    for index, mode in enumerate(modes):
        if runs and modes[runs[-1][0]] == mode:
            runs[-1].append(index)
            continue
        runs.append([index])
    return runs


def with_short_runs_absorbed(modes: Sequence[str]) -> List[str]:
    absorbed = list(modes)
    while True:
        runs = mode_runs(absorbed)
        short_runs = [run for run in runs if len(run) < SHORTEST_STRETCH_WITH_A_ROUTE_OF_ITS_OWN_IN_GRID_CELLS]
        if len(runs) < 2 or not short_runs:
            return absorbed
        run = short_runs[0]
        neighbour_mode = absorbed[run[0] - 1] if run[0] > 0 else absorbed[run[-1] + 1]
        for index in run:
            absorbed[index] = neighbour_mode


def chain_stretches(chain: Sequence[GridCell], sea_cells: Set[GridCell]) -> List[Tuple[str, List[GridCell]]]:
    modes = with_short_runs_absorbed(["sea" if cell in sea_cells else "land" for cell in chain])
    stretches = [(modes[run[0]], [chain[index] for index in run]) for run in mode_runs(modes)]
    return stretches


def town_groups_of(marks: Dict[GridCell, str]) -> List[List[GridCell]]:
    town_groups = connected_groups(cell for cell, mark in marks.items() if mark == TOWN_MARK)
    return town_groups


def painted_route_stretches(w: World, marks: Dict[GridCell, str]) -> List[Tuple[str, List[GridCell]]]:
    route_cells = [cell for cell, mark in marks.items() if mark == ROUTE_MARK]
    sea_cells = {cell for cell in route_cells if is_sea_grid_cell(w, cell)}
    stretches = [stretch for chain in route_chains(route_cells) for stretch in chain_stretches(chain, sea_cells)]
    return stretches


def old_town_grid_cells(old_town: City) -> Set[GridCell]:
    if old_town.grid_cells:
        return {(int(cell[0]), int(cell[1])) for cell in old_town.grid_cells}
    centre_cells = {grid_cell_of_region_cell((old_town.cx, old_town.cy))}
    return centre_cells


def old_route_grid_cells(old_route: dict) -> Set[GridCell]:
    if old_route.get("grid_cells"):
        return {(int(cell[0]), int(cell[1])) for cell in old_route["grid_cells"]}
    path_cells = {grid_cell_of_region_cell(cell) for cell in old_route.get("path") or []}
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
    centre_x, centre_y = centre_region_cell_of_grid_cell(cells[0])
    if w.landmass is None or not w.landmass[centre_y, centre_x]:
        return ""
    landmass_name = f"landmass {int(w.landmass[centre_y, centre_x])}"
    return landmass_name


def town_size_of(cells: Sequence[GridCell]) -> str:
    town_size = "city" if len(cells) >= FEWEST_GRID_CELLS_OF_A_CITY else "town"
    return town_size


def town_of_group(w: World, cells: List[GridCell], old_town: Optional[City], name: str, town_id: str) -> City:
    grid_cells = [[cell[0], cell[1]] for cell in cells]
    rect = region_rect_of_grid_cells(cells)
    if old_town is not None:
        kept_town = City(old_town.id, old_town.name, rect, old_town.gym, old_town.honours, landmass_name_of_grid_cells(w, cells),
                         town_size_of(cells), old_town.note, old_town.pinned, grid_cells)
        return kept_town
    new_town = City(town_id, name, rect, gym="", honours="", half=landmass_name_of_grid_cells(w, cells),
                    size=town_size_of(cells), grid_cells=grid_cells)
    return new_town


def towns_from_groups(w: World, town_groups: List[List[GridCell]], old_towns: Sequence[City]) -> List[City]:
    matches = matched_old_index_of_each_group(town_groups, [old_town_grid_cells(town) for town in old_towns])
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


def names_of_merged_away_towns(old_towns: Sequence[City], towns: Sequence[City]) -> Dict[str, str]:
    surviving_names = {town.name for town in towns}
    town_of_cell = {(cell[0], cell[1]): town.name for town in towns for cell in town.grid_cells}
    merged_away = {}
    for old_town in old_towns:
        if old_town.name in surviving_names:
            continue
        landing_names = [town_of_cell[cell] for cell in sorted(old_town_grid_cells(old_town)) if cell in town_of_cell]
        if landing_names:
            merged_away[old_town.name] = landing_names[0]
    return merged_away


def route_names_for_stretches(stretches: Sequence[Tuple[str, List[GridCell]]], old_routes: Sequence[dict],
                              taken_by_towns: Set[str]) -> Tuple[List[str], Dict[int, dict]]:
    matches = matched_old_index_of_each_group([cells for _mode, cells in stretches], [old_route_grid_cells(route) for route in old_routes])
    kept_old_routes = {stretch_index: old_routes[old_index] for stretch_index, old_index in matches.items()}
    taken = set(taken_by_towns) | {route["name"] for route in kept_old_routes.values()}
    names = []
    for stretch_index in range(len(stretches)):
        name = kept_old_routes[stretch_index]["name"] if stretch_index in kept_old_routes else next_free_name("Route", taken)
        taken.add(name)
        names.append(name)
    return names, kept_old_routes


def connection_record(route_node: int, route_cell: GridCell, other_node: int, other_cell: GridCell, link_name: str) -> dict:
    step = (other_cell[0] - route_cell[0], other_cell[1] - route_cell[1])
    back_step = (-step[0], -step[1])
    side = network.SIDE_OF[step]
    record = dict(a=route_node, b=other_node, cell_a=edge_region_cell_of_grid_cell(route_cell, step),
                  cell_b=edge_region_cell_of_grid_cell(other_cell, back_step), side_a=side, side_b=network.OPP[side],
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
    for end_rank, route_cell in enumerate(end_cells_of(cells)):
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


def exit_region_cell_of_touch(touch: Tuple[int, GridCell, GridCell, int]) -> RegionCell:
    _end_rank, route_cell, neighbour, _other = touch
    exit_cell = edge_region_cell_of_grid_cell(route_cell, (neighbour[0] - route_cell[0], neighbour[1] - route_cell[1]))
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


def region_path_of_stretch(cells: Sequence[GridCell], touches: Sequence[Tuple[int, GridCell, GridCell, int]]) -> List[List[int]]:
    through_touches = through_touches_of_stretch(cells, touches)
    first_end_touches = [touch for touch in through_touches if touch[0] == 0][:1]
    last_end_touches = [touch for touch in through_touches if touch not in first_end_touches][:1]
    lead_in = [exit_region_cell_of_touch(first_end_touches[0])] if first_end_touches else []
    lead_out = [exit_region_cell_of_touch(last_end_touches[0])] if last_end_touches else []
    corners = lead_in + [centre_region_cell_of_grid_cell(cell) for cell in cells] + lead_out
    region_path = [[x, y] for x, y in region_cells_along_corners(corners)]
    return region_path


def route_node_record(w: World, node_index: int, name: str, mode: str, cells: List[GridCell],
                      touches: List[Tuple[int, GridCell, GridCell, int]], old_route: Optional[dict]) -> dict:
    route_id = old_route["id"] if old_route else network.ascii_id(name)
    rect = region_rect_of_grid_cells(cells)
    node = dict(kind="route", idx=node_index, rect=rect, paths=[[tuple(cell) for cell in region_path_of_stretch(cells, touches)]],
                mode=mode, link=name, kind_link=PAINTED_LINK_KIND, name=name, id=route_id,
                map=old_route["map"] if old_route else "MAP_" + route_id.upper(), keep=old_route,
                grid_cells=[[cell[0], cell[1]] for cell in cells])
    if is_dead_end_stretch(cells, touches):
        node["goal"] = list(centre_region_cell_of_grid_cell(cells[-1]))
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


def painted_network(w: World, towns: Sequence[City], stretches: Sequence[Tuple[str, List[GridCell]]],
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
        nodes.append(route_node_record(w, node_index, names[stretch_index], mode, oriented_cells, touches, kept_old_routes.get(stretch_index)))
        for touch in touches:
            _end_rank, route_cell, neighbour, other_index = touch
            other_node = other_index if neighbour in town_of_cell else route_node_of_stretch[other_index]
            conns.append(connection_record(node_index, route_cell, other_node, neighbour, names[stretch_index]))
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
        routes.append(route)
    return routes


def link_records_of(routes: Sequence[dict]) -> List[dict]:
    links = [dict(name=route["link"], kind=PAINTED_LINK_KIND, mode=route["mode"], routes=[route["name"]], landmark=None) for route in routes]
    return links


def map_from_marks(w: World, marks: Dict[GridCell, str]) -> Dict[str, str]:
    old_towns = list(w.cities)
    towns = towns_from_groups(w, town_groups_of(marks), old_towns)
    stretches = painted_route_stretches(w, marks)
    names, kept_old_routes = route_names_for_stretches(stretches, w.routes, {town.name for town in towns})
    painted = painted_network(w, towns, stretches, names, kept_old_routes)
    w.cities = list(towns)
    w.routes = route_records_of(w, painted)
    w.links = link_records_of(w.routes)
    w.overrides["map_grid"] = map_grid_of_marks(marks, grid_size_of_world(w))
    merged_away = names_of_merged_away_towns(old_towns, towns)
    return merged_away


def grid_cells_along_region_path(path: Sequence[Sequence[int]]) -> List[GridCell]:
    grid_cells: List[GridCell] = []
    for region_cell in path:
        grid_cell = grid_cell_of_region_cell(region_cell)
        if not grid_cells or grid_cells[-1] != grid_cell:
            grid_cells.append(grid_cell)
    return grid_cells


def marks_of_places(w: World) -> Dict[GridCell, str]:
    grid_size = grid_size_of_world(w)
    route_cells = [cell for route in w.routes for cell in grid_cells_along_region_path(route.get("path") or [])]
    town_cells = [cell for town in w.cities for cell in old_town_grid_cells(town)]
    marks = with_cells_painted({}, route_cells, "route", grid_size)
    marks = with_cells_painted(marks, town_cells, "town", grid_size)
    return marks


def map_grid_of_world(w: World) -> dict:
    stored_map_grid = w.overrides.get("map_grid")
    if stored_map_grid and list(stored_map_grid.get("size") or []) == list(grid_size_of_world(w)):
        return stored_map_grid
    map_grid = map_grid_of_marks(marks_of_places(w), grid_size_of_world(w))
    return map_grid
