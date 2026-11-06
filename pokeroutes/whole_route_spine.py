from collections import deque
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

Cell = Tuple[int, int]
SquareRect = Tuple[int, int, int, int]


def square_rects_of_spec(spec) -> List[SquareRect]:
    region_block = spec.region if isinstance(getattr(spec, "region", None), dict) else {}
    whole_route_block = region_block.get("whole_route") or {}
    square_rects = [tuple(int(edge) for edge in square) for square in whole_route_block.get("squares") or []]
    return square_rects


def mask_of_squares(square_rects: Sequence[SquareRect], height_in_metatiles: int, width_in_metatiles: int) -> np.ndarray:
    mask_inside_route = np.zeros((height_in_metatiles, width_in_metatiles), dtype=bool)
    for left, top, right, bottom in square_rects:
        mask_inside_route[top:bottom, left:right] = True
    return mask_inside_route


def square_index_holding(square_rects: Sequence[SquareRect], cell: Cell) -> int:
    cell_row, cell_column = cell
    distances_to_squares = [max(left - cell_column, cell_column - right + 1, 0) + max(top - cell_row, cell_row - bottom + 1, 0)
                            for left, top, right, bottom in square_rects]
    nearest_square_index = distances_to_squares.index(min(distances_to_squares))
    return nearest_square_index


def are_squares_touching(first_square: SquareRect, second_square: SquareRect) -> bool:
    first_left, first_top, first_right, first_bottom = first_square
    second_left, second_top, second_right, second_bottom = second_square
    touches_sideways = (first_right == second_left or second_right == first_left) and first_top < second_bottom and second_top < first_bottom
    touches_up_or_down = (first_bottom == second_top or second_bottom == first_top) and first_left < second_right and second_left < first_right
    is_touching = touches_sideways or touches_up_or_down
    return is_touching


def square_path_between(square_rects: Sequence[SquareRect], start_index: int, end_index: int) -> List[int]:
    came_from: Dict[int, Optional[int]] = {start_index: None}
    frontier = deque([start_index])
    while frontier:
        square_index = frontier.popleft()
        for neighbour_index, neighbour_square in enumerate(square_rects):
            if neighbour_index in came_from or not are_squares_touching(square_rects[square_index], neighbour_square):
                continue
            came_from[neighbour_index] = square_index
            frontier.append(neighbour_index)
    if end_index not in came_from:
        return [start_index]
    square_path = [end_index]
    while came_from[square_path[-1]] is not None:
        square_path.append(came_from[square_path[-1]])
    square_path.reverse()
    return square_path


def centre_of_square(square: SquareRect) -> Cell:
    left, top, right, bottom = square
    centre = ((top + bottom) // 2, (left + right) // 2)
    return centre


def corner_between(first_cell: Cell, second_cell: Cell, first_direction: Tuple[int, int]) -> Cell:
    is_moving_vertically_first = first_direction[0] != 0
    corner = (second_cell[0], first_cell[1]) if is_moving_vertically_first else (first_cell[0], second_cell[1])
    return corner


def direction_between(first_cell: Cell, second_cell: Cell) -> Tuple[int, int]:
    direction = (int(np.sign(second_cell[0] - first_cell[0])), int(np.sign(second_cell[1] - first_cell[1])))
    return direction


def orthogonal_points_through(waypoints: Sequence[Cell], entry_direction: Tuple[int, int]) -> List[Cell]:
    orthogonal_points = [waypoints[0]]
    current_direction = entry_direction
    for next_waypoint in waypoints[1:]:
        last_point = orthogonal_points[-1]
        if last_point[0] != next_waypoint[0] and last_point[1] != next_waypoint[1]:
            corner = corner_between(last_point, next_waypoint, current_direction)
            orthogonal_points.append(corner)
            current_direction = direction_between(corner, next_waypoint)
        orthogonal_points.append(next_waypoint)
    return orthogonal_points


def straight_runs_of_points(points: Sequence[Cell]) -> List[Tuple[Cell, Tuple[int, int], int]]:
    straight_runs: List[Tuple[Cell, Tuple[int, int], int]] = []
    for start_point, end_point in zip(points, points[1:]):
        length_in_metatiles = abs(end_point[0] - start_point[0]) + abs(end_point[1] - start_point[1])
        if length_in_metatiles == 0:
            continue
        direction = direction_between(start_point, end_point)
        if straight_runs and straight_runs[-1][1] == direction:
            run_start, _, run_length = straight_runs[-1]
            straight_runs[-1] = (run_start, direction, run_length + length_in_metatiles)
            continue
        straight_runs.append((start_point, direction, length_in_metatiles))
    return straight_runs


def spine_waypoints(square_rects: Sequence[SquareRect], entry_cell: Cell, exit_cell: Cell) -> List[Cell]:
    square_path = square_path_between(square_rects, square_index_holding(square_rects, entry_cell),
                                      square_index_holding(square_rects, exit_cell))
    waypoints = [entry_cell] + [centre_of_square(square_rects[square_index]) for square_index in square_path] + [exit_cell]
    return waypoints


def lateral_band_inside_mask(mask_inside_route: np.ndarray, run_start: Cell, direction: Tuple[int, int], length_in_metatiles: int,
                             margin_in_metatiles: int) -> Tuple[int, int]:
    lateral_direction = (abs(direction[1]), abs(direction[0]))
    height_in_metatiles, width_in_metatiles = mask_inside_route.shape

    def is_lateral_offset_inside(lateral_offset: int) -> bool:
        cells_along_run = [(run_start[0] + along * direction[0] + lateral_offset * lateral_direction[0],
                            run_start[1] + along * direction[1] + lateral_offset * lateral_direction[1])
                           for along in range(length_in_metatiles + 1)]
        is_inside = all(0 <= row < height_in_metatiles and 0 <= column < width_in_metatiles and mask_inside_route[row, column]
                        for row, column in cells_along_run[1:-1] or cells_along_run)
        return is_inside

    lowest_offset = 0
    while is_lateral_offset_inside(lowest_offset - 1):
        lowest_offset -= 1
    highest_offset = 0
    while is_lateral_offset_inside(highest_offset + 1):
        highest_offset += 1
    band = (min(0, lowest_offset + margin_in_metatiles), max(0, highest_offset - margin_in_metatiles))
    return band
