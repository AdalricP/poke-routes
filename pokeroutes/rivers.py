"""Rivers on a route: smooth, wide water that flows along a curved course (so it
can run diagonally), at least two metatiles across everywhere, and stays whole.

A course is a list of points (y, x) in metatiles. It is rounded off (Chaikin
corner cutting), and every cell within the river's half width of the rounded
line is river. Where a walkable lane crosses it the lane becomes a bridge; trees
and rock under it become water. The river's cells are remembered on the route,
and once the route is otherwise finished they are put back (``restore_rivers``),
so no later stage can fill a river in or chop it into stubs: anything walkable
standing in it then is a crossing (a bridge), anything else is water.

Riverland routes without region rivers get one or two rivers of their own, from
one map edge to the opposite one, wandering across the route at a slant."""
from __future__ import annotations

from typing import List, Sequence, Tuple

import numpy as np
from scipy.ndimage import binary_dilation, distance_transform_edt

from .generate import WALKABLE, Route, Zone

Point = Tuple[float, float]

ROUNDING_PASSES = 4
SAMPLES_PER_METATILE_ALONG_THE_COURSE = 3
SMALLEST_RIVER_HALF_WIDTH_IN_METATILES = 1.1
RIVER_HALF_WIDTH_BY_REGION_WIDTH = {1: 1.2, 2: 1.7, 3: 2.3}
RIVER_CELLS_KEY = "_river_cells"
WATER_CLASS = "w"
WATERFALL_CLASS = "a"
CLEAR_DISTANCE_FROM_EXIT_MOUTHS_IN_METATILES = 4
OWN_RIVER_HALF_WIDTH_RANGE_IN_METATILES = (1.3, 2.1)
OWN_RIVER_WANDER_SHARE_OF_CROSS_DISTANCE = 0.22
OWN_RIVER_SLANT_SHARE_OF_CROSS_DISTANCE = 0.55
OWN_RIVER_CONTROL_POINT_COUNT = 4
OWN_RIVER_ATTEMPTS = 12
SMALLEST_SEPARATION_BETWEEN_OWN_RIVERS_IN_METATILES = 14


def rounded_course(course: Sequence[Point]) -> List[Point]:
    rounded = [tuple(map(float, point)) for point in course]
    for _pass in range(ROUNDING_PASSES):
        if len(rounded) < 3:
            break
        cut_corners = [rounded[0]]
        for start, end in zip(rounded, rounded[1:]):
            cut_corners.append((0.75 * start[0] + 0.25 * end[0], 0.75 * start[1] + 0.25 * end[1]))
            cut_corners.append((0.25 * start[0] + 0.75 * end[0], 0.25 * start[1] + 0.75 * end[1]))
        cut_corners.append(rounded[-1])
        rounded = cut_corners
    return rounded


def densely_sampled_course(course: Sequence[Point]) -> List[Point]:
    samples = []
    for start, end in zip(course, course[1:]):
        length_in_metatiles = max(abs(end[0] - start[0]), abs(end[1] - start[1]))
        sample_count = max(1, int(np.ceil(length_in_metatiles * SAMPLES_PER_METATILE_ALONG_THE_COURSE)))
        for sample_index in range(sample_count):
            share = sample_index / sample_count
            samples.append((start[0] + (end[0] - start[0]) * share, start[1] + (end[1] - start[1]) * share))
    samples.append(tuple(course[-1]))
    return samples


def river_mask_along_course(shape: Tuple[int, int], course: Sequence[Point], half_width_in_metatiles: float) -> np.ndarray:
    height, width = shape
    centre_line = np.zeros(shape, dtype=bool)
    for point_y, point_x in densely_sampled_course(rounded_course(course)):
        cell_y, cell_x = int(round(point_y)), int(round(point_x))
        if 0 <= cell_y < height and 0 <= cell_x < width:
            centre_line[cell_y, cell_x] = True
    if not centre_line.any():
        return centre_line
    half_width = max(SMALLEST_RIVER_HALF_WIDTH_IN_METATILES, half_width_in_metatiles)
    river_mask = distance_transform_edt(~centre_line) <= half_width
    return river_mask


def exit_mouth_clearance_mask(route: Route) -> np.ndarray:
    mouths = exit_mouth_mask(route)
    if not mouths.any():
        return mouths
    clearance = binary_dilation(mouths, iterations=CLEAR_DISTANCE_FROM_EXIT_MOUTHS_IN_METATILES)
    return clearance


def special_area_mask(route: Route) -> np.ndarray:
    special = np.zeros(route.cls.shape, dtype=bool)
    for cell in route.zone_cells("special"):
        special[cell] = True
    return special


def remembered_river_mask(route: Route) -> np.ndarray:
    remembered = route.__dict__.get(RIVER_CELLS_KEY)
    if remembered is None:
        remembered = np.zeros(route.cls.shape, dtype=bool)
        route.__dict__[RIVER_CELLS_KEY] = remembered
    return remembered


def exit_mouth_mask(route: Route) -> np.ndarray:
    mouths = np.zeros(route.cls.shape, dtype=bool)
    for _exit, cells in route.exits:
        for cell in cells:
            if route.inside(*cell):
                mouths[cell] = True
    return mouths


INWARD_STEP_FROM_EXIT_SIDE = {"west": (0, 1), "east": (0, -1), "north": (1, 0), "south": (-1, 0)}
EXIT_LANE_DEPTH_IN_METATILES = 6


def exit_lane_mask(route: Route) -> np.ndarray:
    lanes = np.zeros(route.cls.shape, dtype=bool)
    for route_exit, cells in route.exits:
        step_y, step_x = INWARD_STEP_FROM_EXIT_SIDE.get(getattr(route_exit, "side", ""), (0, 0))
        for depth in range(EXIT_LANE_DEPTH_IN_METATILES):
            lane_cells = [(cell[0] + step_y * depth, cell[1] + step_x * depth) for cell in cells]
            for lane_cell in lane_cells:
                if route.inside(*lane_cell):
                    lanes[lane_cell] = True
    return lanes


def paint_river(route: Route, river_mask: np.ndarray, name: str, extra_meta: dict) -> Tuple[int, int]:
    from .stages import code

    drawable = river_mask & ~special_area_mask(route)
    crossing_cells = drawable & (np.isin(route.cls, list(WALKABLE)) | exit_lane_mask(route))
    water_cells = drawable & ~crossing_cells & (route.cls != WATERFALL_CLASS)
    route.cls[water_cells] = WATER_CLASS
    route.cls[crossing_cells] = code("BRIDGE", "q")
    route.protect[crossing_cells] = True
    remembered_river_mask(route)[drawable] = True
    wet_cells = [tuple(map(int, cell)) for cell in zip(*np.nonzero(water_cells))]
    route.zones.append(Zone("lake", wet_cells, name, {"river": True, "crossings": int(crossing_cells.sum()), **extra_meta}))
    painted_counts = (int(water_cells.sum()), int(crossing_cells.sum()))
    return painted_counts


def restore_rivers(route: Route) -> int:
    from .stages import code

    remembered = route.__dict__.get(RIVER_CELLS_KEY)
    if remembered is None or not remembered.any():
        return 0
    bridge = code("BRIDGE", "q")
    walkable_in_river = remembered & np.isin(route.cls, list(WALKABLE) + ["s", "e", "x", "j"]) & (route.cls != bridge)
    filled_in_river = remembered & ~np.isin(route.cls, list(WALKABLE) + ["s", "e", "x", "j", WATER_CLASS, WATERFALL_CLASS, bridge])
    route.cls[walkable_in_river] = bridge
    route.cls[filled_in_river] = WATER_CLASS
    restored_count = int(walkable_in_river.sum() + filled_in_river.sum())
    if restored_count:
        route.log.append(f"rivers: {int(filled_in_river.sum())} cells filled in by later stages turned back to water, "
                         f"{int(walkable_in_river.sum())} walkable cells in the river made bridge")
    return restored_count


def own_river_course(route: Route, flows_across_rows: bool, rng: np.random.Generator) -> List[Point]:
    height, width = route.h, route.w
    cross_length, along_length = (height, width) if flows_across_rows else (width, height)
    slant = rng.uniform(-1, 1) * OWN_RIVER_SLANT_SHARE_OF_CROSS_DISTANCE * cross_length
    start_along = rng.uniform(0.2, 0.8) * along_length
    end_along = float(np.clip(start_along + slant, 0.1 * along_length, 0.9 * along_length))
    wander = OWN_RIVER_WANDER_SHARE_OF_CROSS_DISTANCE * cross_length
    course_along_cross = [(-2.0, start_along)]
    for control_index in range(1, OWN_RIVER_CONTROL_POINT_COUNT + 1):
        share = control_index / (OWN_RIVER_CONTROL_POINT_COUNT + 1)
        along = start_along + (end_along - start_along) * share + rng.uniform(-1, 1) * wander
        course_along_cross.append((share * cross_length, float(np.clip(along, 3, along_length - 4))))
    course_along_cross.append((cross_length + 1.0, end_along))
    course = [(cross, along) if flows_across_rows else (along, cross) for cross, along in course_along_cross]
    return course


def paint_own_rivers(route: Route, river_count: int) -> int:
    rng = route.rng
    painted_count = 0
    painted_centres: List[float] = []
    flows_across_rows = route.w >= route.h
    for _attempt in range(OWN_RIVER_ATTEMPTS):
        if painted_count >= river_count:
            break
        course = own_river_course(route, flows_across_rows, rng)
        centre_along = float(np.mean([point[1] if flows_across_rows else point[0] for point in course]))
        if any(abs(centre_along - other) < SMALLEST_SEPARATION_BETWEEN_OWN_RIVERS_IN_METATILES for other in painted_centres):
            continue
        half_width = rng.uniform(*OWN_RIVER_HALF_WIDTH_RANGE_IN_METATILES)
        river_mask = river_mask_along_course(route.cls.shape, course, half_width)
        if (river_mask & (special_area_mask(route) | exit_mouth_clearance_mask(route))).any():
            continue
        water_cells, bridge_cells = paint_river(route, river_mask, f"river {painted_count + 1}", {})
        route.log.append(f"river {painted_count + 1}: {water_cells} water cells, {bridge_cells} bridge cells, edge to edge")
        painted_centres.append(centre_along)
        painted_count += 1
    return painted_count
