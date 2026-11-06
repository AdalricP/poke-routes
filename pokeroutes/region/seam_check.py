from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .seam_bands import shared_edge_between
from .world import CELL


@dataclass
class GeneratedMap:
    spec: dict
    route: object

    @property
    def name(self) -> str:
        return self.spec["name"]

    @property
    def region_rect(self) -> List[int]:
        return list(self.spec["region"]["rect"])


@dataclass
class SeamCheck:
    first_map_name: str
    second_map_name: str
    side_of_first_map: str
    length_in_metatiles: int
    mismatches: List = field(default_factory=list)

    @property
    def matches(self) -> bool:
        return not self.mismatches


def route_spec_object_from_dict(spec_dict: dict):
    from ..spec import Exit, RouteSpec

    spec_fields = {key: value for key, value in spec_dict.items() if key in RouteSpec.__dataclass_fields__}
    spec_fields["exits"] = [Exit(**{key: value for key, value in spec_exit.items() if key in Exit.__dataclass_fields__})
                            for spec_exit in spec_dict["exits"]]
    route_spec_object = RouteSpec(**spec_fields)
    return route_spec_object


def generate_sealed_map(spec_dict: dict) -> GeneratedMap:
    from ..export import seal_edges
    from ..generate import generate

    route = generate(route_spec_object_from_dict(spec_dict))
    seal_edges(route)
    generated_map = GeneratedMap(spec_dict, route)
    return generated_map


def chain_specs_of_routes(world, route_names: Sequence[str]) -> List[dict]:
    from .export import chain_map_specs

    chain_specs = [spec for spec in chain_map_specs(world) if spec["region"]["chain"]["route"] in route_names]
    return chain_specs


def generate_maps_of_routes(world, route_names: Sequence[str]) -> List[GeneratedMap]:
    generated_maps = [generate_sealed_map(spec) for spec in chain_specs_of_routes(world, route_names)]
    return generated_maps


def seam_check_between(first_map: GeneratedMap, second_map: GeneratedMap) -> Optional[SeamCheck]:
    from ..seams import seam_mismatches

    shared_edge = shared_edge_between(first_map.region_rect, second_map.region_rect)
    if shared_edge is None:
        return None
    along_axis = 1 if shared_edge.side_of_first in ("east", "west") else 0
    start_along_first = (shared_edge.start_along_in_region_cells - first_map.region_rect[along_axis]) * CELL
    start_along_second = (shared_edge.start_along_in_region_cells - second_map.region_rect[along_axis]) * CELL
    length_in_metatiles = (shared_edge.end_along_in_region_cells - shared_edge.start_along_in_region_cells) * CELL
    mismatches = seam_mismatches(first_map.route.cls, shared_edge.side_of_first, start_along_first,
                                 second_map.route.cls, start_along_second, length_in_metatiles)
    seam_check = SeamCheck(first_map.name, second_map.name, shared_edge.side_of_first, length_in_metatiles, mismatches)
    return seam_check


def seam_checks_of_maps(generated_maps: Sequence[GeneratedMap]) -> List[SeamCheck]:
    seam_checks = [seam_check for first_index, first_map in enumerate(generated_maps)
                   for second_map in generated_maps[first_index + 1:]
                   for seam_check in [seam_check_between(first_map, second_map)] if seam_check is not None]
    return seam_checks


def world_origin_in_metatiles(generated_map: GeneratedMap) -> Tuple[int, int]:
    x0, y0 = generated_map.region_rect[0], generated_map.region_rect[1]
    origin_in_metatiles = (x0 * CELL, y0 * CELL)
    return origin_in_metatiles


def stitched_class_grid(generated_maps: Sequence[GeneratedMap], empty_class: str = " ") -> Tuple[np.ndarray, Tuple[int, int]]:
    origins = [world_origin_in_metatiles(generated_map) for generated_map in generated_maps]
    left = min(origin[0] for origin in origins)
    top = min(origin[1] for origin in origins)
    right = max(origin[0] + generated_map.route.w for origin, generated_map in zip(origins, generated_maps))
    bottom = max(origin[1] + generated_map.route.h for origin, generated_map in zip(origins, generated_maps))
    stitched = np.full((bottom - top, right - left), empty_class, dtype="<U1")
    for (origin_x, origin_y), generated_map in zip(origins, generated_maps):
        stitched[origin_y - top:origin_y - top + generated_map.route.h, origin_x - left:origin_x - left + generated_map.route.w] = generated_map.route.cls
    return stitched, (left, top)


def describe_seam_checks(seam_checks: Sequence[SeamCheck]) -> List[str]:
    from ..seams import describe_seam_mismatches

    descriptions = []
    for seam_check in seam_checks:
        verdict = "match" if seam_check.matches else f"{len(seam_check.mismatches)} mismatches"
        mismatch_text = "; ".join(describe_seam_mismatches(seam_check.mismatches[:6]))
        descriptions.append(f"{seam_check.first_map_name} | {seam_check.second_map_name} ({seam_check.side_of_first_map}, "
                            f"{seam_check.length_in_metatiles} tiles): {verdict}{' - ' + mismatch_text if mismatch_text else ''}")
    return descriptions
