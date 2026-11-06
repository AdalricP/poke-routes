from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

import numpy as np

SEAM_BAND_DEPTH_IN_METATILES = 2
FARTHEST_INWARD_REACH_TO_JOIN_BAND_WATER_IN_METATILES = 8
WATER_CLASSES_A_BAND_JOINS = "wbha"
BLOCKED_CLASSES_BAND_WATER_MAY_REPLACE = "fr"


@dataclass(frozen=True)
class SeamMismatch:
    position_along_edge_in_metatiles: int
    class_on_this_side: str
    class_on_other_side: str


def seam_bands_of_route(route) -> List[dict]:
    region_block = route.spec.region if isinstance(getattr(route.spec, "region", None), dict) else {}
    seam_bands = list(region_block.get("seams") or [])
    return seam_bands


def band_cell(route, side: str, position_along_edge: int, depth_from_edge: int):
    if side == "north":
        return depth_from_edge, position_along_edge
    if side == "south":
        return route.h - 1 - depth_from_edge, position_along_edge
    if side == "west":
        return position_along_edge, depth_from_edge
    band_position = (position_along_edge, route.w - 1 - depth_from_edge)
    return band_position


def edge_length_of_side(route, side: str) -> int:
    edge_length = route.w if side in ("north", "south") else route.h
    return edge_length


def stamp_seam_band(route, seam_band: dict) -> None:
    side, start_along_edge, band_classes = seam_band["side"], int(seam_band["start"]), seam_band["classes"]
    edge_length = edge_length_of_side(route, side)
    for band_index, band_class in enumerate(band_classes):
        position_along_edge = start_along_edge + band_index
        if not 0 <= position_along_edge < edge_length:
            continue
        for depth_from_edge in range(SEAM_BAND_DEPTH_IN_METATILES):
            route.cls[band_cell(route, side, position_along_edge, depth_from_edge)] = band_class


def inward_cells_from_band(route, side: str, position_along_edge: int) -> List[tuple]:
    first_depth = SEAM_BAND_DEPTH_IN_METATILES
    last_depth = SEAM_BAND_DEPTH_IN_METATILES + FARTHEST_INWARD_REACH_TO_JOIN_BAND_WATER_IN_METATILES
    deepest_depth = (route.h if side in ("north", "south") else route.w) - 1
    inward_cells = [band_cell(route, side, position_along_edge, depth_from_edge)
                    for depth_from_edge in range(first_depth, min(last_depth, deepest_depth))]
    return inward_cells


def join_band_water_cell_inward(route, side: str, position_along_edge: int, band_water_class: str) -> None:
    inward_cells = inward_cells_from_band(route, side, position_along_edge)
    for reach_index, inward_cell in enumerate(inward_cells):
        inward_class = str(route.cls[inward_cell])
        if inward_class in WATER_CLASSES_A_BAND_JOINS:
            for blocked_cell in inward_cells[:reach_index]:
                route.cls[blocked_cell] = band_water_class
            return
        if inward_class not in BLOCKED_CLASSES_BAND_WATER_MAY_REPLACE:
            return


def join_band_water_inward(route, seam_band: dict) -> None:
    side, start_along_edge = seam_band["side"], int(seam_band["start"])
    edge_length = edge_length_of_side(route, side)
    for band_index, band_class in enumerate(seam_band["classes"]):
        position_along_edge = start_along_edge + band_index
        if band_class in WATER_CLASSES_A_BAND_JOINS and 0 <= position_along_edge < edge_length:
            join_band_water_cell_inward(route, side, position_along_edge, band_class)


def stage_seams(route) -> None:
    seam_bands = seam_bands_of_route(route)
    if not seam_bands:
        return
    for seam_band in seam_bands:
        join_band_water_inward(route, seam_band)
        stamp_seam_band(route, seam_band)
    route.snap("seams")


def edge_classes(class_grid: np.ndarray, side: str) -> np.ndarray:
    if side == "north":
        return class_grid[0, :]
    if side == "south":
        return class_grid[-1, :]
    if side == "west":
        return class_grid[:, 0]
    east_edge = class_grid[:, -1]
    return east_edge


def seam_mismatches(class_grid: np.ndarray, side: str, start_along_this_edge: int,
                    other_class_grid: np.ndarray, start_along_other_edge: int, length_in_metatiles: int) -> List[SeamMismatch]:
    opposite_side = {"north": "south", "south": "north", "east": "west", "west": "east"}[side]
    this_edge = edge_classes(class_grid, side)
    other_edge = edge_classes(other_class_grid, opposite_side)
    mismatches = [SeamMismatch(offset, str(this_edge[start_along_this_edge + offset]), str(other_edge[start_along_other_edge + offset]))
                  for offset in range(length_in_metatiles)
                  if this_edge[start_along_this_edge + offset] != other_edge[start_along_other_edge + offset]]
    return mismatches


def describe_seam_mismatches(mismatches: Sequence[SeamMismatch]) -> List[str]:
    descriptions = [f"at {mismatch.position_along_edge_in_metatiles}: {mismatch.class_on_this_side} vs {mismatch.class_on_other_side}"
                    for mismatch in mismatches]
    return descriptions
