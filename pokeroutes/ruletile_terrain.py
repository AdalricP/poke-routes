from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

import numpy as np

ORTHOGONAL_OFFSETS = {"N": (0, -1), "E": (1, 0), "S": (0, 1), "W": (-1, 0)}
NEIGHBOUR_OFFSETS = {"N": (0, -1), "NE": (1, -1), "E": (1, 0), "SE": (1, 1),
                     "S": (0, 1), "SW": (-1, 1), "W": (-1, 0), "NW": (-1, -1)}
DIAGONAL_SIDES = {"NE": ("N", "E"), "SE": ("S", "E"), "SW": ("S", "W"), "NW": ("N", "W")}
HIGH_SIDE_OFFSET_BY_LEDGE = {"s": (0, -1), "j": (0, 1), "e": (-1, 0), "x": (1, 0)}
ROW_LEDGE_LETTERS = ("s", "j")
LEDGES_SHOWING_BOTH_SIDES = ("j",)
FULL_MASK_KEY = "255"
BRIDGE_UNDERLYING_LETTERS = "wbhaq"
GROUND_LETTER_BY_CLASS = {"p": "p", "d": "p", "q": "p", "t": "t"}
ROW_LEDGE_MASK_BY_ALONG_NEIGHBOURS = {(True, True): "255", (False, True): "31", (True, False): "241"}
COLUMN_LEDGE_MASK_BY_ALONG_NEIGHBOURS = {(True, True): "255", (False, True): "124", (True, False): "199"}

Cell = Tuple[int, int]


def is_inside(grid: List[str], cell_x: int, cell_y: int) -> bool:
    is_inside_grid = 0 <= cell_y < len(grid) and 0 <= cell_x < len(grid[0])
    return is_inside_grid


def class_at(grid: List[str], cell_x: int, cell_y: int) -> Optional[str]:
    if not is_inside(grid, cell_x, cell_y):
        return None
    letter = grid[cell_y][cell_x]
    return letter


def connected_cells(grid: List[str], start: Cell, letters: str) -> Set[Cell]:
    component = {start}
    frontier = [start]
    while frontier:
        cell_x, cell_y = frontier.pop()
        for step_x, step_y in ORTHOGONAL_OFFSETS.values():
            neighbour = (cell_x + step_x, cell_y + step_y)
            if neighbour in component or class_at(grid, *neighbour) is None:
                continue
            if grid[neighbour[1]][neighbour[0]] not in letters:
                continue
            component.add(neighbour)
            frontier.append(neighbour)
    return component


def components_of(grid: List[str], letter: str) -> List[Set[Cell]]:
    seen: Set[Cell] = set()
    components = []
    for cell_y, grid_row in enumerate(grid):
        for cell_x, cell_letter in enumerate(grid_row):
            if cell_letter != letter or (cell_x, cell_y) in seen:
                continue
            component = connected_cells(grid, (cell_x, cell_y), letter)
            seen |= component
            components.append(component)
    return components


def bounding_box_size(component: Set[Cell]) -> Tuple[int, int]:
    column_positions = [cell_x for cell_x, _ in component]
    row_positions = [cell_y for _, cell_y in component]
    width_in_cells = max(column_positions) - min(column_positions) + 1
    height_in_cells = max(row_positions) - min(row_positions) + 1
    width_and_height_in_cells = (width_in_cells, height_in_cells)
    return width_and_height_in_cells


def outside_neighbours_on_side(grid: List[str], component: Set[Cell], side: str) -> Set[Cell]:
    step_x, step_y = ORTHOGONAL_OFFSETS[side]
    neighbours = {(cell_x + step_x, cell_y + step_y) for cell_x, cell_y in component
                  if (cell_x + step_x, cell_y + step_y) not in component
                  and class_at(grid, cell_x + step_x, cell_y + step_y) is not None}
    return neighbours


def water_cells_beside(grid: List[str], component: Set[Cell], side: str, water_letters: str) -> Set[Cell]:
    neighbours = outside_neighbours_on_side(grid, component, side)
    water_neighbours = {cell for cell in neighbours if grid[cell[1]][cell[0]] in water_letters}
    return water_neighbours


def water_body_size(grid: List[str], starting_cells: Set[Cell], water_letters: str) -> int:
    body: Set[Cell] = set()
    for cell in starting_cells:
        if cell not in body:
            body |= connected_cells(grid, cell, water_letters)
    return len(body)


def is_sideways_waterfall(grid: List[str], component: Set[Cell], water_letters: str) -> bool:
    water_contact = {side: len(water_cells_beside(grid, component, side, water_letters)) for side in ORTHOGONAL_OFFSETS}
    flows_between_west_and_east = water_contact["W"] > 0 and water_contact["E"] > 0
    has_more_water_west_and_east = water_contact["W"] + water_contact["E"] > water_contact["N"] + water_contact["S"]
    is_sideways = flows_between_west_and_east and has_more_water_west_and_east
    return is_sideways


def sideways_flow_direction(grid: List[str], component: Set[Cell], water_letters: str) -> str:
    west_body_size = water_body_size(grid, water_cells_beside(grid, component, "W", water_letters), water_letters)
    east_body_size = water_body_size(grid, water_cells_beside(grid, component, "E", water_letters), water_letters)
    flow_direction = "west" if west_body_size > east_body_size else "east"
    return flow_direction


def sideways_row(component: Set[Cell], cell_x: int, cell_y: int) -> str:
    has_fall_above = (cell_x, cell_y - 1) in component
    has_fall_below = (cell_x, cell_y + 1) in component
    if has_fall_above and not has_fall_below:
        return "south"
    if has_fall_below and not has_fall_above:
        return "north"
    return "middle"


def sideways_stage(component: Set[Cell], cell_x: int, cell_y: int, flow_direction: str) -> str:
    downstream_step = 1 if flow_direction == "east" else -1
    has_fall_upstream = (cell_x - downstream_step, cell_y) in component
    has_fall_downstream = (cell_x + downstream_step, cell_y) in component
    if has_fall_upstream and has_fall_downstream:
        return "fall"
    if has_fall_downstream:
        return "lip"
    return "foot"


def tile_sideways_waterfalls(grid: List[str], metatile_ids: np.ndarray, waterfall_class: dict,
                             water_letters: str) -> None:
    sideways_parts = waterfall_class.get("sideways_parts")
    if not sideways_parts:
        return
    for component in components_of(grid, "a"):
        if not is_sideways_waterfall(grid, component, water_letters):
            continue
        flow_direction = sideways_flow_direction(grid, component, water_letters)
        for cell_x, cell_y in component:
            row_name = sideways_row(component, cell_x, cell_y)
            stage_name = sideways_stage(component, cell_x, cell_y, flow_direction)
            part_name = f"{flow_direction}_{row_name}_{stage_name}"
            metatile_ids[cell_y, cell_x] = sideways_parts[part_name]


def bridge_part_name(component: Set[Cell], cell_x: int, cell_y: int, runs_east_west: bool) -> str:
    has = {side: (cell_x + step_x, cell_y + step_y) in component
           for side, (step_x, step_y) in ORTHOGONAL_OFFSETS.items()}
    across_before, across_after, along_before, along_after = (
        ("N", "S", "W", "E") if runs_east_west else ("W", "E", "N", "S"))
    is_single_row = not has[across_before] and not has[across_after]
    if is_single_row:
        return bridge_single_part_name(has[along_before], has[along_after], runs_east_west)
    if not has[across_before]:
        return "horizontal_top" if runs_east_west else "vertical_left"
    if not has[across_after]:
        return "horizontal_bottom" if runs_east_west else "vertical_right"
    return "horizontal_middle" if runs_east_west else "vertical_middle"


def bridge_single_part_name(has_bridge_before: bool, has_bridge_after: bool, runs_east_west: bool) -> str:
    orientation = "horizontal" if runs_east_west else "vertical"
    before_end, after_end = ("west", "east") if runs_east_west else ("north", "south")
    if has_bridge_after and not has_bridge_before:
        return f"{orientation}_single_{before_end}_end"
    if has_bridge_before and not has_bridge_after:
        return f"{orientation}_single_{after_end}_end"
    return f"{orientation}_single"


def land_cells_beside(grid: List[str], component: Set[Cell], side: str) -> int:
    land_cell_count = sum(1 for cell_x, cell_y in outside_neighbours_on_side(grid, component, side)
                          if grid[cell_y][cell_x] not in BRIDGE_UNDERLYING_LETTERS)
    return land_cell_count


def bridge_runs_east_west(grid: List[str], component: Set[Cell]) -> bool:
    land_at_west_and_east = land_cells_beside(grid, component, "W") + land_cells_beside(grid, component, "E")
    land_at_north_and_south = land_cells_beside(grid, component, "N") + land_cells_beside(grid, component, "S")
    if land_at_west_and_east != land_at_north_and_south:
        return land_at_west_and_east > land_at_north_and_south
    width_in_cells, height_in_cells = bounding_box_size(component)
    runs_east_west = width_in_cells >= height_in_cells
    return runs_east_west


def tile_bridges(grid: List[str], metatile_ids: np.ndarray, bridge_class: dict) -> None:
    for component in components_of(grid, "q"):
        runs_east_west = bridge_runs_east_west(grid, component)
        for cell_x, cell_y in component:
            part_name = bridge_part_name(component, cell_x, cell_y, runs_east_west)
            metatile_ids[cell_y, cell_x] = bridge_class["parts"][part_name]


def is_water_at(grid: List[str], cell_x: int, cell_y: int, water_letters: str) -> bool:
    letter = class_at(grid, cell_x, cell_y)
    is_water = letter is not None and letter in water_letters
    return is_water


def water_neighbour_sides(grid: List[str], cell_x: int, cell_y: int, water_letters: str) -> Set[str]:
    water_sides = {side for side, (step_x, step_y) in NEIGHBOUR_OFFSETS.items()
                   if is_water_at(grid, cell_x + step_x, cell_y + step_y, water_letters)}
    return water_sides


def shore_mask(water_sides: Set[str], mask_bits: List[str]) -> int:
    counted_sides = {side for side in water_sides
                     if side not in DIAGONAL_SIDES or not set(DIAGONAL_SIDES[side]) & water_sides}
    mask = sum(1 << index for index, side in enumerate(mask_bits) if side in counted_sides)
    return mask


def orthogonal_part_of_mask(mask: int, mask_bits: List[str]) -> int:
    orthogonal_mask = sum(1 << index for index, side in enumerate(mask_bits)
                          if side in ORTHOGONAL_OFFSETS and mask >> index & 1)
    return orthogonal_mask


def shore_metatile(grid: List[str], cell_x: int, cell_y: int, sand_class: dict, mask_bits: List[str]) -> Optional[int]:
    water_sides = water_neighbour_sides(grid, cell_x, cell_y, sand_class.get("water", "wb"))
    mask = shore_mask(water_sides, mask_bits)
    if mask == 0:
        return None
    by_mask = sand_class["by_mask"]
    metatile_id = by_mask.get(str(mask)) or by_mask.get(str(orthogonal_part_of_mask(mask, mask_bits)))
    return metatile_id


def ground_letter_at(grid: List[str], cell_x: int, cell_y: int) -> Optional[str]:
    ground_letter = GROUND_LETTER_BY_CLASS.get(class_at(grid, cell_x, cell_y) or "")
    return ground_letter


def ledge_ground(grid: List[str], cell_x: int, cell_y: int) -> Optional[str]:
    letter = class_at(grid, cell_x, cell_y)
    if letter not in HIGH_SIDE_OFFSET_BY_LEDGE:
        return ground_letter_at(grid, cell_x, cell_y)
    step_x, step_y = HIGH_SIDE_OFFSET_BY_LEDGE[letter]
    ground_letter = ground_letter_at(grid, cell_x + step_x, cell_y + step_y)
    low_side_ground_letter = ground_letter_at(grid, cell_x - step_x, cell_y - step_y)
    if letter in LEDGES_SHOWING_BOTH_SIDES and low_side_ground_letter != ground_letter:
        return None
    return ground_letter


def has_same_ground(grid: List[str], cell_x: int, cell_y: int, ground_letter: str) -> bool:
    if not is_inside(grid, cell_x, cell_y):
        return True
    has_same = ledge_ground(grid, cell_x, cell_y) == ground_letter
    return has_same


def ledge_ground_mask_key(grid: List[str], cell_x: int, cell_y: int, ground_letter: str) -> Optional[str]:
    is_row = grid[cell_y][cell_x] in ROW_LEDGE_LETTERS
    before = (cell_x - 1, cell_y) if is_row else (cell_x, cell_y - 1)
    after = (cell_x + 1, cell_y) if is_row else (cell_x, cell_y + 1)
    along_neighbours = (has_same_ground(grid, *before, ground_letter), has_same_ground(grid, *after, ground_letter))
    mask_table = ROW_LEDGE_MASK_BY_ALONG_NEIGHBOURS if is_row else COLUMN_LEDGE_MASK_BY_ALONG_NEIGHBOURS
    mask_key = mask_table.get(along_neighbours)
    return mask_key


def ledge_over_ground_metatile(grid: List[str], cell_x: int, cell_y: int, ledge_class: dict) -> Optional[int]:
    ground_letter = ledge_ground(grid, cell_x, cell_y)
    over_ground = ledge_class.get("over_ground", {}).get(ground_letter or "")
    if not over_ground:
        return None
    if ground_letter == "t":
        return over_ground.get("255")
    mask_key = ledge_ground_mask_key(grid, cell_x, cell_y, ground_letter)
    if mask_key is None:
        return None
    metatile_id = over_ground.get(mask_key)
    return metatile_id


def ledge_part_name(ledge_class: dict, metatile_id: int) -> Optional[str]:
    part_names = [name for name, part_id in ledge_class["parts"].items() if part_id == metatile_id]
    part_name = part_names[0] if part_names else None
    return part_name


def ledge_end_over_ground_metatile(grid: List[str], cell_x: int, cell_y: int, ledge_class: dict,
                                   part_name: str) -> Optional[int]:
    ground_letter = ledge_ground(grid, cell_x, cell_y)
    ends = ledge_class.get("over_ground_ends", {}).get(ground_letter or "", {})
    if part_name not in ends or ledge_ground_mask_key(grid, cell_x, cell_y, ground_letter) != FULL_MASK_KEY:
        return None
    metatile_id = ends[part_name]
    return metatile_id


def tile_ledges_over_ground(grid: List[str], metatile_ids: np.ndarray,
                            class_table: Dict[str, dict]) -> Dict[Cell, str]:
    ground_letter_by_ledge_cell: Dict[Cell, str] = {}
    for cell_y, grid_row in enumerate(grid):
        for cell_x, letter in enumerate(grid_row):
            ledge_class = class_table.get(letter) if letter in HIGH_SIDE_OFFSET_BY_LEDGE else None
            part_name = ledge_part_name(ledge_class, metatile_ids[cell_y, cell_x]) if ledge_class else None
            if part_name is None:
                continue
            metatile_id = (ledge_over_ground_metatile(grid, cell_x, cell_y, ledge_class) if part_name == "mid"
                           else ledge_end_over_ground_metatile(grid, cell_x, cell_y, ledge_class, part_name))
            if metatile_id is None:
                continue
            metatile_ids[cell_y, cell_x] = metatile_id
            ground_letter_by_ledge_cell[(cell_x, cell_y)] = ledge_ground(grid, cell_x, cell_y)
    return ground_letter_by_ledge_cell


def grid_with_ledge_grounds(grid: List[str], ground_letter_by_ledge_cell: Dict[Cell, str]) -> List[str]:
    rows = [list(grid_row) for grid_row in grid]
    for (cell_x, cell_y), ground_letter in ground_letter_by_ledge_cell.items():
        rows[cell_y][cell_x] = ground_letter
    grid_seen_through_ledges = ["".join(grid_row) for grid_row in rows]
    return grid_seen_through_ledges
