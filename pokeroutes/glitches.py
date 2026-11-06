from __future__ import annotations

from typing import Dict, List, Set, Tuple

import numpy as np
from scipy.ndimage import label

Cell = Tuple[int, int]

WATER_CLASSES = "wbh"
BRIDGE_CLASS = "q"
WALKABLE_LAND_CLASSES = "gptdhyvzn"
WIDEST_BRIDGE_IN_METATILES = 3
SMALLEST_ISLET_SIDE_IN_METATILES = 2
EDGE_NOTCH_DEPTH_IN_METATILES = 2
SPIKE_WATER_SIDES = 3
SHORE_GROUND_CLASSES = "gptdyv"
LONGEST_SPAN_OVER_A_CHANNEL_IN_METATILES = 3
FLOW_WINDOW_REACH_IN_METATILES = 5
SMALLEST_ELONGATION_OF_A_FLOWING_RIVER = 2.0
COSINE_OF_LARGEST_ANGLE_TO_THE_FLOW_THAT_RUNS_ALONG_IT = 0.87
ORTHOGONAL_STEPS = ((0, 1), (0, -1), (1, 0), (-1, 0))


def cells_of_components(mask: np.ndarray) -> List[List[Cell]]:
    labelled, component_count = label(mask)
    components = [[(int(y), int(x)) for y, x in zip(*np.nonzero(labelled == component_number))]
                  for component_number in range(1, component_count + 1)]
    return components


def is_inside(class_grid: np.ndarray, y: int, x: int) -> bool:
    is_inside_grid = 0 <= y < class_grid.shape[0] and 0 <= x < class_grid.shape[1]
    return is_inside_grid


def is_water_or_outside(class_grid: np.ndarray, y: int, x: int) -> bool:
    is_water = not is_inside(class_grid, y, x) or str(class_grid[y, x]) in WATER_CLASSES
    return is_water


def is_walkable_land_or_outside(class_grid: np.ndarray, y: int, x: int) -> bool:
    is_land = not is_inside(class_grid, y, x) or str(class_grid[y, x]) in WALKABLE_LAND_CLASSES
    return is_land


def middle_of_span(positions_along: range) -> range:
    inner_positions = positions_along[1:-1] if len(positions_along) > 2 else positions_along[:0]
    return inner_positions


def is_straight_span(class_grid: np.ndarray, rows: range, columns: range, runs_east_west: bool) -> bool:
    across_cells = len(rows) if runs_east_west else len(columns)
    if across_cells > WIDEST_BRIDGE_IN_METATILES:
        return False
    if runs_east_west:
        flanks_are_water = all(is_water_or_outside(class_grid, y, x) for x in middle_of_span(columns) for y in (rows[0] - 1, rows[-1] + 1))
        ends_are_land = all(is_walkable_land_or_outside(class_grid, y, x) for y in rows for x in (columns[0] - 1, columns[-1] + 1))
        spans_water = any(is_water_or_outside(class_grid, y, x) for x in columns for y in (rows[0] - 1, rows[-1] + 1))
        return flanks_are_water and ends_are_land and spans_water
    flanks_are_water = all(is_water_or_outside(class_grid, y, x) for y in middle_of_span(rows) for x in (columns[0] - 1, columns[-1] + 1))
    ends_are_land = all(is_walkable_land_or_outside(class_grid, y, x) for x in columns for y in (rows[0] - 1, rows[-1] + 1))
    spans_water = any(is_water_or_outside(class_grid, y, x) for y in rows for x in (columns[0] - 1, columns[-1] + 1))
    return flanks_are_water and ends_are_land and spans_water


def flow_direction_near(class_grid: np.ndarray, rows: range, columns: range) -> Tuple[np.ndarray, float]:
    reach = FLOW_WINDOW_REACH_IN_METATILES
    top, bottom = max(0, rows[0] - reach), min(class_grid.shape[0], rows[-1] + reach + 1)
    left, right = max(0, columns[0] - reach), min(class_grid.shape[1], columns[-1] + reach + 1)
    water_positions = np.argwhere(np.isin(class_grid[top:bottom, left:right], list(WATER_CLASSES))).astype(float)
    if len(water_positions) < 3:
        return np.zeros(2), 1.0
    eigenvalues, eigenvectors = np.linalg.eigh(np.cov(water_positions.T))
    elongation = float(eigenvalues[1] / max(eigenvalues[0], 1e-6))
    principal_direction = eigenvectors[:, 1]
    return principal_direction, elongation


def crosses_the_water(class_grid: np.ndarray, rows: range, columns: range, runs_east_west: bool) -> bool:
    principal_direction, elongation = flow_direction_near(class_grid, rows, columns)
    bridge_direction = np.array([0.0, 1.0]) if runs_east_west else np.array([1.0, 0.0])
    runs_along_the_flow = elongation >= SMALLEST_ELONGATION_OF_A_FLOWING_RIVER and abs(float(principal_direction @ bridge_direction)) >= COSINE_OF_LARGEST_ANGLE_TO_THE_FLOW_THAT_RUNS_ALONG_IT
    span_length = len(columns) if runs_east_west else len(rows)
    crosses = span_length <= LONGEST_SPAN_OVER_A_CHANNEL_IN_METATILES or not runs_along_the_flow
    return crosses


def is_clean_crossing(class_grid: np.ndarray, rows: range, columns: range, runs_east_west: bool) -> bool:
    is_clean = is_straight_span(class_grid, rows, columns, runs_east_west) and crosses_the_water(class_grid, rows, columns, runs_east_west)
    return is_clean


def is_clean_bridge(class_grid: np.ndarray, bridge_cells: List[Cell]) -> bool:
    rows = range(min(y for y, _x in bridge_cells), max(y for y, _x in bridge_cells) + 1)
    columns = range(min(x for _y, x in bridge_cells), max(x for _y, x in bridge_cells) + 1)
    if len(bridge_cells) != len(rows) * len(columns):
        return False
    is_clean = is_clean_crossing(class_grid, rows, columns, True) or is_clean_crossing(class_grid, rows, columns, False)
    return is_clean


def crooked_bridges(class_grid: np.ndarray) -> List[Cell]:
    bridges = cells_of_components(class_grid == BRIDGE_CLASS)
    first_cells_of_crooked_bridges = [bridge_cells[0] for bridge_cells in bridges if not is_clean_bridge(class_grid, bridge_cells)]
    return first_cells_of_crooked_bridges


def has_square_of_side(cells: List[Cell], side_in_metatiles: int) -> bool:
    cell_set = set(cells)
    has_square = any(all((y + dy, x + dx) in cell_set for dy in range(side_in_metatiles) for dx in range(side_in_metatiles))
                     for y, x in cells)
    return has_square


def touches_map_edge(class_grid: np.ndarray, cells: List[Cell]) -> bool:
    height, width = class_grid.shape
    touches = any(y in (0, height - 1) or x in (0, width - 1) for y, x in cells)
    return touches


def is_surrounded_by_water(class_grid: np.ndarray, cells: List[Cell]) -> bool:
    cell_set = set(cells)
    neighbours = {(y + dy, x + dx) for y, x in cells for dy, dx in ORTHOGONAL_STEPS} - cell_set
    surrounded = all(is_water_or_outside(class_grid, y, x) for y, x in neighbours)
    return surrounded


def tiny_islets(class_grid: np.ndarray) -> List[Cell]:
    land = ~np.isin(class_grid, list(WATER_CLASSES + BRIDGE_CLASS))
    islets = [cells for cells in cells_of_components(land)
              if not touches_map_edge(class_grid, cells) and is_surrounded_by_water(class_grid, cells)]
    first_cells_of_tiny_islets = [cells[0] for cells in islets if not has_square_of_side(cells, SMALLEST_ISLET_SIDE_IN_METATILES)]
    return first_cells_of_tiny_islets


def is_ground_spike_into_water(class_grid: np.ndarray, cell: Cell) -> bool:
    y, x = cell
    water_neighbours = sum(1 for dy, dx in ORTHOGONAL_STEPS if is_water_or_outside(class_grid, y + dy, x + dx)
                           and is_inside(class_grid, y + dy, x + dx))
    bridge_neighbours = sum(1 for dy, dx in ORTHOGONAL_STEPS if is_inside(class_grid, y + dy, x + dx) and class_grid[y + dy, x + dx] == BRIDGE_CLASS)
    is_spike = water_neighbours >= SPIKE_WATER_SIDES and bridge_neighbours == 0
    return is_spike


def ground_spikes_into_water(class_grid: np.ndarray) -> List[Cell]:
    spikes = [(int(y), int(x)) for y, x in zip(*np.nonzero(np.isin(class_grid, list(SHORE_GROUND_CLASSES))))
              if is_ground_spike_into_water(class_grid, (int(y), int(x)))]
    return spikes


def signs_in_water(route) -> List[Cell]:
    wet_signs = [(event.y, event.x) for event in route.events
                 if event.kind == "sign" and str(route.cls[event.y, event.x]) in WATER_CLASSES + BRIDGE_CLASS]
    return wet_signs


def edge_water_notches(class_grid: np.ndarray) -> List[Cell]:
    height, width = class_grid.shape
    depth = EDGE_NOTCH_DEPTH_IN_METATILES
    water_bodies = cells_of_components(np.isin(class_grid, list(WATER_CLASSES)))
    first_cells_of_notches = [cells[0] for cells in water_bodies
                              if all(y < depth or x < depth or y >= height - depth or x >= width - depth for y, x in cells)]
    return first_cells_of_notches


def open_river_ends(route) -> List[Cell]:
    from .river_ends import walkable_river_ends

    open_ends = walkable_river_ends(route)
    return open_ends


TREE_CLASS = "f"


def half_tree_block_stub_cells(class_grid: np.ndarray) -> List[Cell]:
    from .tree_blocks import half_tree_blocks

    stub_cells = [(int(y), int(x)) for block_cells in half_tree_blocks(class_grid)
                  for y, x in block_cells if class_grid[y, x] == TREE_CLASS]
    return stub_cells


def orphan_crown_fragments(class_grid: np.ndarray) -> List[Cell]:
    stub_cells = half_tree_block_stub_cells(class_grid)
    stub_cell_set = set(stub_cells)
    orphan_cells = [(y, x) for y, x in stub_cells
                    if not any(is_inside(class_grid, y + dy, x + dx) and class_grid[y + dy, x + dx] == TREE_CLASS
                              and (y + dy, x + dx) not in stub_cell_set
                              for dy in (-1, 0, 1) for dx in (-1, 0, 1))]
    return orphan_cells


def straight_bush_rows_hugging_tree_blocks(class_grid: np.ndarray) -> List[Cell]:
    stub_cell_set = set(half_tree_block_stub_cells(class_grid))
    row_start_cells = [(y, x) for y, x in stub_cell_set
                       if ((y, x - 1) not in stub_cell_set and (y, x + 1) in stub_cell_set and (y, x + 2) in stub_cell_set)
                       or ((y - 1, x) not in stub_cell_set and (y + 1, x) in stub_cell_set and (y + 2, x) in stub_cell_set)]
    return row_start_cells


def shade_cells_without_a_tree_above(class_grid: np.ndarray) -> List[Cell]:
    stub_cells = half_tree_block_stub_cells(class_grid)
    shaded_ground_cells = [(y, x + 1) for y, x in stub_cells
                           if is_inside(class_grid, y, x + 1) and str(class_grid[y, x + 1]) in WALKABLE_LAND_CLASSES]
    return shaded_ground_cells


def glitches_of_route(route, tiler) -> Dict[str, List[Cell]]:
    class_grid = np.asarray(route.cls).astype(str)
    clipped_crown_cells_by_kind = tiler.clipped_crown_cells(class_grid)
    glitches = {
        **{kind: [(y, x) for x, y in cells] for kind, cells in clipped_crown_cells_by_kind.items()},
        "crooked bridges": crooked_bridges(class_grid),
        "open river ends": open_river_ends(route),
        "tiny islets": tiny_islets(class_grid),
        "ground spikes into water": ground_spikes_into_water(class_grid),
        "signs in water": signs_in_water(route),
        "edge water notches": edge_water_notches(class_grid),
        "orphan crown fragments": orphan_crown_fragments(class_grid),
        "single-cell bush stubs": half_tree_block_stub_cells(class_grid),
        "straight bush rows hugging tree blocks": straight_bush_rows_hugging_tree_blocks(class_grid),
        "shade cells without a tree above": shade_cells_without_a_tree_above(class_grid),
    }
    return glitches
