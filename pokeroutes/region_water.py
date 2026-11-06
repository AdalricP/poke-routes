"""Water from the region under a route: the region's rivers, lakes and sea drawn
onto the route grid at metatile scale, so a route opened from a region map shows
the water the map shows under it.

The region block (``spec.region``, written by ``pokeroutes.region.export``) holds
one character per region cell in ``water.grid`` (``~`` sea, ``=`` deep sea, ``o``
lake, ``r`` river), the rivers as downstream chains of cells, and the region path.
Water only ever replaces blocked ground (trees, rock): a lane under water becomes
a bridge, so nothing walkable is cut off. Where the region path itself crosses
water the lanes bridge it; elsewhere sea and lakes draw back from the lanes,
leaving a shore.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import numpy as np
from scipy.ndimage import binary_dilation, gaussian_filter

from .generate import WALKABLE, Route, Zone

Cell = Tuple[int, int]

SEA_CHARACTERS_IN_WATER_GRID = "~="
LAKE_CHARACTER_IN_WATER_GRID = "o"
RIVER_CHARACTER_IN_WATER_GRID = "r"
MOUNTAIN_BIOME_CODES_IN_BIOME_GRID = "MS"
SHORE_GAP_BETWEEN_LANE_AND_SEA_IN_METATILES = 3
SHORE_GAP_BETWEEN_LANE_AND_LAKE_IN_METATILES = 1
BEACH_WIDTH_IN_METATILES = 4
GROUND_CLASSES_THE_BEACH_COVERS = "gtp"
WATER_OUTLINE_SMOOTHING_IN_METATILES = 1.6
WATER_OUTLINE_NOISE_STRENGTH = 0.18
CROSSING_REACH_AROUND_REGION_PATH_IN_REGION_CELLS = 1
SMALLEST_PATH_CELL_COUNT_ON_WATER_THAT_MAKES_A_CROSSING = 2
SURF_CROSSING_KEY = "_surf_crossing"


def region_block_of(route: Route) -> dict:
    region_block = route.spec.region if isinstance(getattr(route.spec, "region", None), dict) else {}
    return region_block


def region_cell_size_in_metatiles(region_block: dict) -> int:
    cell_size_in_metatiles = int(region_block.get("cell", 4) or 4)
    return cell_size_in_metatiles


def has_region_rivers(route: Route) -> bool:
    has_rivers = bool(region_block_of(route).get("rivers"))
    return has_rivers


def has_region_sea(route: Route) -> bool:
    has_sea = bool(region_water_mask(route, SEA_CHARACTERS_IN_WATER_GRID).any())
    return has_sea


def region_water_grid_rows(region_block: dict) -> List[str]:
    water_grid_rows = list((region_block.get("water") or {}).get("grid") or [])
    return water_grid_rows


def upsample_region_mask_to_route(region_mask: np.ndarray, cell_size_in_metatiles: int, route_shape) -> np.ndarray:
    route_height, route_width = route_shape
    upsampled = np.kron(region_mask.astype(np.uint8), np.ones((cell_size_in_metatiles, cell_size_in_metatiles), np.uint8))
    upsampled = upsampled[:route_height, :route_width]
    padded = np.pad(upsampled, ((0, route_height - upsampled.shape[0]), (0, route_width - upsampled.shape[1])), mode="edge")
    route_mask = padded.astype(bool)
    return route_mask


def region_water_mask(route: Route, characters: str) -> np.ndarray:
    region_block = region_block_of(route)
    rows = region_water_grid_rows(region_block)
    if not rows:
        return np.zeros(route.cls.shape, dtype=bool)
    region_mask = np.array([[character in characters for character in row] for row in rows], dtype=bool)
    route_mask = upsample_region_mask_to_route(region_mask, region_cell_size_in_metatiles(region_block), route.cls.shape)
    return route_mask


def soften_water_outline(blocky_mask: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    if not blocky_mask.any():
        return blocky_mask
    smoothed = gaussian_filter(blocky_mask.astype(float), WATER_OUTLINE_SMOOTHING_IN_METATILES, mode="nearest")
    wobble = gaussian_filter(rng.random(blocky_mask.shape), 2.0, mode="nearest")
    wobble = (wobble - wobble.mean()) / max(1e-6, wobble.std()) * WATER_OUTLINE_NOISE_STRENGTH * 0.5
    softened_mask = (smoothed + wobble) > 0.5
    return softened_mask


def region_path_crossing_mask(route: Route) -> np.ndarray:
    region_block = region_block_of(route)
    rows = region_water_grid_rows(region_block)
    path = region_block.get("path") or []
    if not rows or not path:
        return np.zeros(route.cls.shape, dtype=bool)
    region_height, region_width = len(rows), len(rows[0])
    crossing_cells = np.zeros((region_height, region_width), dtype=bool)
    for region_x, region_y in path:
        if 0 <= region_y < region_height and 0 <= region_x < region_width and rows[region_y][region_x] != ".":
            crossing_cells[region_y, region_x] = True
    reach = CROSSING_REACH_AROUND_REGION_PATH_IN_REGION_CELLS
    crossing_cells = binary_dilation(crossing_cells, iterations=reach) if crossing_cells.any() else crossing_cells
    route_mask = upsample_region_mask_to_route(crossing_cells, region_cell_size_in_metatiles(region_block), route.cls.shape)
    return route_mask


def special_area_mask(route: Route) -> np.ndarray:
    special = np.zeros(route.cls.shape, dtype=bool)
    for cell in route.zone_cells("special"):
        special[cell] = True
    return special


def exit_mouth_mask(route: Route) -> np.ndarray:
    mouths = np.zeros(route.cls.shape, dtype=bool)
    for _exit, cells in route.exits:
        for cell in cells:
            mouths[cell] = True
    mouths = binary_dilation(mouths, iterations=3)
    return mouths


def drawable_water_mask(route: Route, wanted_water: np.ndarray) -> np.ndarray:
    blocked_ground = ~np.isin(route.cls, list(WALKABLE)) & (route.cls != "w")
    drawable = wanted_water & blocked_ground & ~special_area_mask(route) & ~exit_mouth_mask(route)
    return drawable


def lanes_to_bridge(route: Route, wanted_water: np.ndarray) -> np.ndarray:
    bridged_lanes = wanted_water & np.isin(route.cls, list(WALKABLE)) & ~special_area_mask(route) & ~exit_mouth_mask(route)
    return bridged_lanes


def bridge_class() -> str:
    from .stages import code

    bridge = code("BRIDGE", "p")
    return bridge


def paint_water_with_bridges(route: Route, wanted_water: np.ndarray, water_class: str,
                             longest_bridge_in_metatiles: int = 0) -> Tuple[int, int]:
    water_cells = drawable_water_mask(route, wanted_water)
    bridge_cells = lanes_to_bridge(route, wanted_water)
    if longest_bridge_in_metatiles:
        bridge_cells = crossings_no_longer_than(bridge_cells, longest_bridge_in_metatiles)
    route.cls[water_cells] = water_class
    route.cls[bridge_cells] = bridge_class()
    route.protect[bridge_cells] = True
    painted_counts = (int(water_cells.sum()), int(bridge_cells.sum()))
    return painted_counts


def crossings_no_longer_than(bridge_cells: np.ndarray, longest_bridge_in_metatiles: int) -> np.ndarray:
    from scipy.ndimage import find_objects, label

    labelled, _count = label(bridge_cells)
    short_crossings = np.zeros(bridge_cells.shape, dtype=bool)
    for component_number, bounding_box in enumerate(find_objects(labelled), start=1):
        rows, columns = bounding_box
        span_in_metatiles = max(rows.stop - rows.start, columns.stop - columns.start)
        if span_in_metatiles <= longest_bridge_in_metatiles:
            short_crossings[bounding_box] |= labelled[bounding_box] == component_number
    return short_crossings


def main_road_mask(route: Route) -> np.ndarray:
    critical = np.zeros(route.cls.shape, dtype=bool)
    for cell in route.critical_path:
        critical[cell] = True
    main_road = binary_dilation(critical, iterations=1) & np.isin(route.cls, list(WALKABLE)) if critical.any() else critical
    return main_road


def water_drawn_back_from_main_road(route: Route, wanted_water: np.ndarray, gap_in_metatiles: int) -> np.ndarray:
    crossing = region_path_crossing_mask(route)
    main_road_off_the_crossing = main_road_mask(route) & ~crossing
    if not main_road_off_the_crossing.any():
        return wanted_water
    shore = binary_dilation(main_road_off_the_crossing, iterations=gap_in_metatiles)
    receded_water = wanted_water & ~shore
    return receded_water


def flood_side_lanes(route: Route, wanted_water: np.ndarray, water_class: str) -> int:
    side_lanes = np.isin(route.cls, list(WALKABLE)) & ~main_road_mask(route) & ~region_path_crossing_mask(route)
    flooded = wanted_water & side_lanes & ~special_area_mask(route) & ~exit_mouth_mask(route)
    route.cls[flooded] = water_class
    route.protect[flooded] = False
    flooded_cell_count = int(flooded.sum())
    return flooded_cell_count


def region_path_cells_on_water(route: Route, characters: str) -> int:
    region_block = region_block_of(route)
    rows = region_water_grid_rows(region_block)
    path = region_block.get("path") or []
    if not rows:
        return 0
    on_water_count = sum(1 for region_x, region_y in path
                         if 0 <= region_y < len(rows) and 0 <= region_x < len(rows[region_y])
                         and rows[region_y][region_x] in characters)
    return on_water_count


def route_crosses_map_water(route: Route, characters: str) -> bool:
    crosses = region_path_cells_on_water(route, characters) >= SMALLEST_PATH_CELL_COUNT_ON_WATER_THAT_MAKES_A_CROSSING
    return crosses


def water_for_route(route: Route, softened_water: np.ndarray, characters: str, shore_gap_in_metatiles: int) -> np.ndarray:
    if route_crosses_map_water(route, characters):
        return softened_water
    receded_water = water_drawn_back_from_main_road(route, softened_water, shore_gap_in_metatiles)
    return receded_water


def remember_surf_crossing(route: Route, crossing_cells: np.ndarray, water_class: str, characters: str) -> None:
    if not route_crosses_map_water(route, characters) or not crossing_cells.any():
        return
    remembered = route.__dict__.setdefault(SURF_CROSSING_KEY, {})
    remembered[water_class] = remembered.get(water_class, np.zeros(route.cls.shape, dtype=bool)) | crossing_cells


def paint_region_sea(route: Route) -> int:
    from .stages import code

    blocky_sea = region_water_mask(route, SEA_CHARACTERS_IN_WATER_GRID)
    if not blocky_sea.any():
        return 0
    sea = water_for_route(route, soften_water_outline(blocky_sea, route.rng), SEA_CHARACTERS_IN_WATER_GRID,
                          SHORE_GAP_BETWEEN_LANE_AND_SEA_IN_METATILES)
    flood_side_lanes(route, sea, code("SEA", "w"))
    remember_surf_crossing(route, lanes_to_bridge(route, sea), code("SEA", "w"), SEA_CHARACTERS_IN_WATER_GRID)
    sea_cells, bridge_cells = paint_water_with_bridges(route, sea, code("SEA", "w"))
    beach_cells = paint_beach_along_sea(route, route.cls == code("SEA", "w"))
    route.zones.append(Zone("lake", [tuple(map(int, cell)) for cell in zip(*np.nonzero(sea & (route.cls == code("SEA", "w"))))],
                            "region sea", {"sea": True, "crossings": bridge_cells}))
    route.log.append(f"region sea: {sea_cells} sea cells, {beach_cells} beach cells, {bridge_cells} bridge cells")
    return sea_cells


def paint_beach_along_sea(route: Route, sea: np.ndarray) -> int:
    from .stages import code

    if not sea.any():
        return 0
    beach_band = binary_dilation(sea, iterations=BEACH_WIDTH_IN_METATILES) & ~sea
    becomes_sand = ~np.isin(route.cls, list(WALKABLE)) | np.isin(route.cls, list(GROUND_CLASSES_THE_BEACH_COVERS))
    beach_band &= becomes_sand & (route.cls != "w") & ~special_area_mask(route)
    beach_band &= away_from_map_border(route)
    route.cls[beach_band] = code("SAND", "p")
    beach_cell_count = int(beach_band.sum())
    return beach_cell_count


def away_from_map_border(route: Route) -> np.ndarray:
    inner_cells = np.zeros(route.cls.shape, dtype=bool)
    inner_cells[2:-2, 2:-2] = True
    return inner_cells


def paint_region_lakes(route: Route) -> int:
    blocky_lakes = region_water_mask(route, LAKE_CHARACTER_IN_WATER_GRID)
    if not blocky_lakes.any():
        return 0
    lakes = water_for_route(route, soften_water_outline(blocky_lakes, route.rng), LAKE_CHARACTER_IN_WATER_GRID,
                            SHORE_GAP_BETWEEN_LANE_AND_LAKE_IN_METATILES)
    flood_side_lanes(route, lakes, "w")
    remember_surf_crossing(route, lanes_to_bridge(route, lakes), "w", LAKE_CHARACTER_IN_WATER_GRID)
    lake_cells, bridge_cells = paint_water_with_bridges(route, lakes, "w")
    painted = [tuple(map(int, cell)) for cell in zip(*np.nonzero(lakes & (route.cls == "w")))]
    if painted:
        route.zones.append(Zone("lake", painted, "region lake", {"region_lake": True, "crossings": bridge_cells}))
    route.log.append(f"region lakes: {lake_cells} water cells, {bridge_cells} bridge cells")
    return lake_cells


def region_cell_centre_in_metatiles(region_cell: Sequence[int], cell_size_in_metatiles: int) -> Cell:
    region_x, region_y = int(region_cell[0]), int(region_cell[1])
    centre = (region_y * cell_size_in_metatiles + cell_size_in_metatiles // 2,
              region_x * cell_size_in_metatiles + cell_size_in_metatiles // 2)
    return centre


def edge_point_beyond(region_cell: Sequence[int], region_shape, cell_size_in_metatiles: int) -> Optional[Cell]:
    region_height, region_width = region_shape
    region_x, region_y = int(region_cell[0]), int(region_cell[1])
    centre_y, centre_x = region_cell_centre_in_metatiles(region_cell, cell_size_in_metatiles)
    distances_to_edges = {"west": region_x, "east": region_width - 1 - region_x,
                          "north": region_y, "south": region_height - 1 - region_y}
    nearest_side = min(distances_to_edges, key=distances_to_edges.get)
    if distances_to_edges[nearest_side] > 0:
        return None
    edge_points = {"west": (centre_y, 0), "east": (centre_y, region_width * cell_size_in_metatiles - 1),
                   "north": (0, centre_x), "south": (region_height * cell_size_in_metatiles - 1, centre_x)}
    edge_point = edge_points[nearest_side]
    return edge_point


def river_course_in_metatiles(river: dict, region_shape, cell_size_in_metatiles: int) -> List[Cell]:
    region_cells = river.get("cells") or []
    if len(region_cells) < 2:
        return []
    course = [region_cell_centre_in_metatiles(cell, cell_size_in_metatiles) for cell in region_cells]
    upstream_edge = edge_point_beyond(region_cells[0], region_shape, cell_size_in_metatiles)
    downstream_edge = edge_point_beyond(region_cells[-1], region_shape, cell_size_in_metatiles)
    course_with_edges = ([upstream_edge] if upstream_edge else []) + course + ([downstream_edge] if downstream_edge else [])
    return course_with_edges


def paint_region_rivers(route: Route) -> int:
    from .rivers import RIVER_HALF_WIDTH_BY_REGION_WIDTH, paint_river, river_mask_along_course

    region_block = region_block_of(route)
    rivers = region_block.get("rivers") or []
    rows = region_water_grid_rows(region_block)
    if not rivers or not rows:
        return 0
    region_shape = (len(rows), len(rows[0]))
    cell_size_in_metatiles = region_cell_size_in_metatiles(region_block)
    painted_rivers = 0
    for river_number, river in enumerate(rivers, start=1):
        course = river_course_in_metatiles(river, region_shape, cell_size_in_metatiles)
        if not course:
            continue
        half_width = RIVER_HALF_WIDTH_BY_REGION_WIDTH.get(int(river.get("width", 1)), 1.2)
        river_mask = river_mask_along_course(route.cls.shape, course, half_width)
        if not river_mask.any():
            continue
        water_cells, bridge_cells = paint_river(route, river_mask, f"region river {river_number}", {"region_river": True})
        route.log.append(f"region river {river_number}: {water_cells} water cells, {bridge_cells} bridge cells")
        painted_rivers += 1
    return painted_rivers


def region_biome_mask(route: Route, biome_codes: str) -> np.ndarray:
    region_block = region_block_of(route)
    rows = list((region_block.get("biome") or {}).get("grid") or [])
    if not rows:
        return np.zeros(route.cls.shape, dtype=bool)
    region_mask = np.array([[code in biome_codes for code in row] for row in rows], dtype=bool)
    route_mask = upsample_region_mask_to_route(region_mask, region_cell_size_in_metatiles(region_block), route.cls.shape)
    return route_mask


def paint_region_mountain_rock(route: Route) -> int:
    from .stages import code

    if route.spec.biome in ("mountain", "volcanic", "desert"):
        return 0
    blocky_mountain = region_biome_mask(route, MOUNTAIN_BIOME_CODES_IN_BIOME_GRID)
    if not blocky_mountain.any():
        return 0
    mountain = soften_water_outline(blocky_mountain, route.rng)
    rock = mountain & (route.cls == "f") & ~route.protect & ~special_area_mask(route)
    route.cls[rock] = code("CLIFF", "r")
    rock_cell_count = int(rock.sum())
    route.log.append(f"region mountains: {rock_cell_count} rock cells")
    return rock_cell_count


def paint_region_water(route: Route) -> dict:
    painted = {"mountain": paint_region_mountain_rock(route), "sea": paint_region_sea(route),
               "lakes": paint_region_lakes(route), "rivers": paint_region_rivers(route)}
    return painted
