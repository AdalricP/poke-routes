from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from ..seams import SEAM_BAND_DEPTH_IN_METATILES
from .world import CELL, SPEC_BIOME

EXIT_OPENING_HALF_WIDTH_IN_METATILES = 1
ROCK_WALLED_SPEC_BIOMES = ("desert", "mountain", "snow", "volcanic")
ROAD_CLASS_OF_SPEC_BIOME = {"forest": "g", "meadow": "p", "plains": "p", "riverland": "p", "beach": "d",
                            "desert": "d", "mountain": "p", "snow": "p", "volcanic": "p"}
SEA_BAND_CLASS = "b"
FRESH_WATER_BAND_CLASS = "w"
BRIDGE_BAND_CLASS = "q"
TREE_BAND_CLASS = "f"
ROCK_BAND_CLASS = "r"
TOWN_SIDE_BIOME = "meadow"
RAISED_GRASS_BAND_CLASS = "o"
WOODED_REGION_BIOME_CODES = "fFgB"
OPEN_LAND_REGION_BIOME_CODES = "mbw"


@dataclass(frozen=True)
class SharedEdge:
    side_of_first: str
    start_along_in_region_cells: int
    end_along_in_region_cells: int


def shared_edge_between(rect: Sequence[int], neighbour_rect: Sequence[int]) -> Optional[SharedEdge]:
    x0, y0, x1, y1 = rect
    nx0, ny0, nx1, ny1 = neighbour_rect
    candidates = [("east", x1 == nx0, max(y0, ny0), min(y1, ny1)), ("west", x0 == nx1, max(y0, ny0), min(y1, ny1)),
                  ("south", y1 == ny0, max(x0, nx0), min(x1, nx1)), ("north", y0 == ny1, max(x0, nx0), min(x1, nx1))]
    for side, touches, start_along, end_along in candidates:
        if touches and end_along > start_along:
            shared_edge = SharedEdge(side, int(start_along), int(end_along))
            return shared_edge
    return None


def region_cells_either_side(rect: Sequence[int], shared_edge: SharedEdge, along_in_region_cells: int):
    side = shared_edge.side_of_first
    if side == "east":
        return (rect[2] - 1, along_in_region_cells), (rect[2], along_in_region_cells)
    if side == "west":
        return (rect[0], along_in_region_cells), (rect[0] - 1, along_in_region_cells)
    if side == "south":
        return (along_in_region_cells, rect[3] - 1), (along_in_region_cells, rect[3])
    cells_either_side = ((along_in_region_cells, rect[1]), (along_in_region_cells, rect[1] - 1))
    return cells_either_side


def water_class_of_region_cell(world, region_cell) -> str:
    x, y = int(region_cell[0]), int(region_cell[1])
    if not (0 <= x < world.W and 0 <= y < world.H):
        return ""
    if not world.land[y, x]:
        return SEA_BAND_CLASS
    if world.lake[y, x] or world.river[y, x]:
        return FRESH_WATER_BAND_CLASS
    return ""


def water_class_on_one_side(world, rect: Sequence[int], shared_edge: SharedEdge, side_index: int, along_in_region_cells: int) -> str:
    region_cell = region_cells_either_side(rect, shared_edge, along_in_region_cells)[side_index]
    water_class = water_class_of_region_cell(world, region_cell)
    return water_class


def water_class_across_edge(world, rect: Sequence[int], shared_edge: SharedEdge, along_in_region_cells: int) -> str:
    this_side = water_class_on_one_side(world, rect, shared_edge, 0, along_in_region_cells)
    other_side = water_class_on_one_side(world, rect, shared_edge, 1, along_in_region_cells)
    crosses_here = bool(this_side and other_side)
    if not crosses_here:
        return ""
    water_class = SEA_BAND_CLASS if SEA_BAND_CLASS in (this_side, other_side) else FRESH_WATER_BAND_CLASS
    return water_class


def spec_biome_of_map(chain_map: Optional[dict]) -> str:
    if chain_map is None:
        return TOWN_SIDE_BIOME
    spec_biome = "beach" if chain_map.get("mode") == "sea" else SPEC_BIOME.get(chain_map.get("biome_code") or "m", "forest")
    return spec_biome


def wall_class_between(spec_biome: str, neighbour_spec_biome: str) -> str:
    both_rock_walled = spec_biome in ROCK_WALLED_SPEC_BIOMES and neighbour_spec_biome in ROCK_WALLED_SPEC_BIOMES
    wall_class = ROCK_BAND_CLASS if both_rock_walled else TREE_BAND_CLASS
    return wall_class


def road_class_between(spec_biome: str, neighbour_spec_biome: str) -> str:
    road_class = ROAD_CLASS_OF_SPEC_BIOME.get(spec_biome, "g")
    neighbour_road_class = ROAD_CLASS_OF_SPEC_BIOME.get(neighbour_spec_biome, "g")
    shared_road_class = road_class if road_class == neighbour_road_class else "g"
    return shared_road_class


def region_biome_code(world, region_cell) -> str:
    x, y = int(region_cell[0]), int(region_cell[1])
    if not (0 <= x < world.W and 0 <= y < world.H):
        return ""
    biome_code = str(world.biome[y, x])
    return biome_code


def is_open_land_on_both_sides(world, rect: Sequence[int], shared_edge: SharedEdge, along_in_region_cells: int) -> bool:
    biome_codes = [region_biome_code(world, region_cell)
                   for step in (-1, 0, 1)
                   for region_cell in region_cells_either_side(rect, shared_edge, along_in_region_cells + step)]
    is_open_land = all(biome_code in OPEN_LAND_REGION_BIOME_CODES for biome_code in biome_codes if biome_code) and any(biome_codes)
    return is_open_land


def wall_class_along_edge(world, rect: Sequence[int], shared_edge: SharedEdge, along_in_region_cells: int, wall_class: str) -> str:
    if wall_class != TREE_BAND_CLASS or not is_open_land_on_both_sides(world, rect, shared_edge, along_in_region_cells):
        return wall_class
    return RAISED_GRASS_BAND_CLASS


def opening_class(road_class: str, water_class: str, is_sea_crossing: bool) -> str:
    if is_sea_crossing:
        return SEA_BAND_CLASS
    opening_class_over_ground = BRIDGE_BAND_CLASS if water_class else road_class
    return opening_class_over_ground


def openings_along_edge_in_metatiles(chain_map: dict, neighbour_rect: Sequence[int], shared_edge: SharedEdge) -> List[int]:
    from .route_chain import STEP_TOWARD_SIDE, is_inside_rect

    along_axis = 1 if shared_edge.side_of_first in ("east", "west") else 0
    opening_positions = []
    for map_exit in chain_map.get("exits") or []:
        step_x, step_y = STEP_TOWARD_SIDE[map_exit["side"]]
        target_cell = (map_exit["cell"][0] + step_x, map_exit["cell"][1] + step_y)
        if map_exit["side"] != shared_edge.side_of_first or not is_inside_rect(target_cell, neighbour_rect):
            continue
        middle_in_metatiles = int(map_exit["cell"][along_axis]) * CELL + CELL // 2
        half_width = EXIT_OPENING_HALF_WIDTH_IN_METATILES
        opening_positions.extend(range(middle_in_metatiles - half_width, middle_in_metatiles + half_width + 1))
    return opening_positions


def without_single_wall_cells(band_classes: List[str], wall_class: str) -> List[str]:
    smoothed = list(band_classes)
    for band_index, band_class in enumerate(band_classes):
        if band_class != wall_class:
            continue
        before = band_classes[band_index - 1] if band_index > 0 else wall_class
        after = band_classes[band_index + 1] if band_index + 1 < len(band_classes) else wall_class
        if before != wall_class and after != wall_class:
            smoothed[band_index] = before if before in (SEA_BAND_CLASS, FRESH_WATER_BAND_CLASS) else after
    return smoothed


def band_classes_along_shared_edge(world, chain_map: dict, neighbour_rect: Sequence[int], neighbour_map: Optional[dict],
                                   shared_edge: SharedEdge) -> str:
    spec_biome, neighbour_spec_biome = spec_biome_of_map(chain_map), spec_biome_of_map(neighbour_map)
    wall_class = wall_class_between(spec_biome, neighbour_spec_biome)
    road_class = road_class_between(spec_biome, neighbour_spec_biome)
    is_sea_crossing = chain_map.get("mode") == "sea" and (neighbour_map or {}).get("mode") == "sea"
    opening_positions = set(openings_along_edge_in_metatiles(chain_map, neighbour_rect, shared_edge))
    band_classes = []
    for along_in_region_cells in range(shared_edge.start_along_in_region_cells, shared_edge.end_along_in_region_cells):
        water_class = water_class_across_edge(world, chain_map["rect"], shared_edge, along_in_region_cells)
        for metatile_in_cell in range(CELL):
            position_in_metatiles = along_in_region_cells * CELL + metatile_in_cell
            if position_in_metatiles in opening_positions:
                band_classes.append(opening_class(road_class, water_class, is_sea_crossing))
                continue
            band_classes.append(water_class or wall_class_along_edge(world, chain_map["rect"], shared_edge, along_in_region_cells, wall_class))
    corner_positions = positions_near_map_corners(chain_map["rect"], neighbour_rect, shared_edge)
    band_classes_with_walled_corners = [TREE_BAND_CLASS if band_index + shared_edge.start_along_in_region_cells * CELL in corner_positions
                                        else band_class for band_index, band_class in enumerate(band_classes)]
    trees_beside_raised_grass_widened = without_single_trees_beside_raised_grass(band_classes_with_walled_corners)
    without_single_trees = without_single_wall_cells(trees_beside_raised_grass_widened, wall_class)
    shared_band_classes = "".join(without_single_wall_cells(without_single_trees, RAISED_GRASS_BAND_CLASS))
    return shared_band_classes


def without_single_trees_beside_raised_grass(band_classes: List[str]) -> List[str]:
    smoothed = list(band_classes)
    for band_index, band_class in enumerate(band_classes):
        before = band_classes[band_index - 1] if band_index > 0 else TREE_BAND_CLASS
        after = band_classes[band_index + 1] if band_index + 1 < len(band_classes) else TREE_BAND_CLASS
        is_single_tree = band_class == TREE_BAND_CLASS and before != TREE_BAND_CLASS and after != TREE_BAND_CLASS
        if is_single_tree and RAISED_GRASS_BAND_CLASS in (before, after):
            smoothed[band_index] = RAISED_GRASS_BAND_CLASS
    return smoothed


def positions_near_map_corners(rect: Sequence[int], neighbour_rect: Sequence[int], shared_edge: SharedEdge) -> set:
    along_axis = 1 if shared_edge.side_of_first in ("east", "west") else 0
    map_ends_in_metatiles = [int(edge_rect[along_axis + offset]) * CELL for edge_rect in (rect, neighbour_rect) for offset in (0, 2)]
    corner_positions = {map_end + step for map_end in map_ends_in_metatiles
                        for step in range(-SEAM_BAND_DEPTH_IN_METATILES, SEAM_BAND_DEPTH_IN_METATILES)}
    return corner_positions


def seam_band_for_neighbour(world, chain_map: dict, neighbour_rect: Sequence[int], neighbour_map: Optional[dict],
                            neighbour_name: str) -> Optional[dict]:
    shared_edge = shared_edge_between(chain_map["rect"], neighbour_rect)
    if shared_edge is None:
        return None
    along_axis = 1 if shared_edge.side_of_first in ("east", "west") else 0
    start_in_map_metatiles = (shared_edge.start_along_in_region_cells - int(chain_map["rect"][along_axis])) * CELL
    seam_band = {"side": shared_edge.side_of_first, "start": start_in_map_metatiles, "to": neighbour_name,
                 "classes": band_classes_along_shared_edge(world, chain_map, neighbour_rect, neighbour_map, shared_edge)}
    return seam_band


def seam_bands_of_chain_map(world, chain_map: dict, chain_maps: Sequence[dict]) -> List[dict]:
    neighbours = [(other["rect"], other, other["name"]) for other in chain_maps if other is not chain_map]
    neighbours += [(town.rect, None, town.name) for town in world.cities]
    seam_bands = [seam_band for seam_band in (seam_band_for_neighbour(world, chain_map, rect, neighbour_map, name)
                                              for rect, neighbour_map, name in neighbours) if seam_band is not None]
    return seam_bands


def attach_seam_bands(world, chain_maps: Sequence[dict]) -> None:
    for chain_map in chain_maps:
        chain_map["seams"] = seam_bands_of_chain_map(world, chain_map, chain_maps)

