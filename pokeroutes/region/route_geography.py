"""Route spec fields read off the geography under a route's rectangle: the
biome most of its land has (and whether it crosses into another one), how wooded
it is, which edge the sea lies along, how much the ground falls along the route
and how mountainous it is. Pure functions over the ``region`` block that
``export.route_region`` writes, so they can be tested on a made-up block."""
from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional, Tuple

from .world import BIOME_NAMES, SPEC_BIOME

WATER_BIOME_CODES = "~=o"
WOODED_BIOME_CODES = "fFgB"
MOUNTAIN_BIOME_CODES = "MS"
SEA_CHARACTERS_IN_WATER_GRID = "~="
TALL_GRASS_BY_SPEC_BIOME = {"meadow": 0.4, "plains": 0.45, "forest": 0.3, "riverland": 0.35, "beach": 0.2,
                            "desert": 0.1, "mountain": 0.15, "volcanic": 0.08}
MAXIMUM_WALKABLE_SHARE_BY_WOODLAND = {"deep forest": 0.30, "forest": 0.34, "meadow": 0.40, "plains": 0.45}
MOST_WOODED_SHARE_THAT_STILL_COUNTS_AS_PLAINS = 0.12
SMALLEST_SECOND_BIOME_SHARE_THAT_MAKES_A_MIX = 0.25
SMALLEST_MOUNTAIN_SHARE_THAT_MAKES_A_PASS = 0.2
SMALLEST_EDGE_SEA_SHARE_THAT_MAKES_A_COAST = 0.2
SMALLEST_SEA_DEPTH_SHARE_THAT_MAKES_A_COAST = 0.06
EXTRA_SEA_SHARE_KEPT_FOR_THE_SHORE = 0.06
SMALLEST_SEA_SHARE_KEPT, LARGEST_SEA_SHARE_KEPT = 0.12, 0.5
LEDGE_CHANCE_ON_FLAT_GROUND = 0.2
LEDGE_CHANCE_PER_HEIGHT_STEP_ALONG_THE_PATH = 0.08
LARGEST_LEDGE_CHANCE = 0.75
MEANDER_THROUGH_A_MOUNTAIN_PASS = 0.4


def land_biome_codes(biome_grid_rows: List[str]) -> List[str]:
    codes = [code for row in biome_grid_rows for code in row if code not in WATER_BIOME_CODES]
    return codes


def biome_shares(biome_grid_rows: List[str]) -> Dict[str, float]:
    codes = land_biome_codes(biome_grid_rows)
    if not codes:
        return {}
    counts = Counter(codes)
    total = sum(counts.values())
    shares = {code: count / total for code, count in counts.most_common()}
    return shares


def share_of(shares: Dict[str, float], codes: str) -> float:
    total_share = sum(share for code, share in shares.items() if code in codes)
    return total_share


def looks_like_open_plains(shares: Dict[str, float]) -> bool:
    is_open_meadow = share_of(shares, "m") >= 0.5 and share_of(shares, WOODED_BIOME_CODES) < MOST_WOODED_SHARE_THAT_STILL_COUNTS_AS_PLAINS
    return is_open_meadow


def majority_spec_biome(shares: Dict[str, float]) -> Optional[str]:
    if not shares:
        return None
    majority_code = next(iter(shares))
    spec_biome = SPEC_BIOME.get(majority_code, "meadow")
    if spec_biome == "meadow" and looks_like_open_plains(shares):
        return "plains"
    return spec_biome


def woodland_kind(shares: Dict[str, float], spec_biome: str) -> str:
    if spec_biome == "plains":
        return "plains"
    if share_of(shares, "F") >= 0.4:
        return "deep forest"
    if spec_biome == "forest" or share_of(shares, WOODED_BIOME_CODES) >= 0.5:
        return "forest"
    return "meadow"


def biome_mix_note(shares: Dict[str, float], gradient: dict) -> str:
    if gradient:
        return f"transition: {gradient['from']} to {gradient['to']} along the {gradient['along']} ({gradient['axis']})"
    ranked = list(shares.items())
    if len(ranked) < 2 or ranked[1][1] < SMALLEST_SECOND_BIOME_SHARE_THAT_MAKES_A_MIX:
        return ""
    (first_code, first_share), (second_code, second_share) = ranked[0], ranked[1]
    note = (f"mixed: {BIOME_NAMES.get(first_code, first_code)} {first_share:.0%}, "
            f"{BIOME_NAMES.get(second_code, second_code)} {second_share:.0%}")
    return note


def sea_depth_share_from_side(water_grid_rows: List[str], side: str) -> Tuple[float, float]:
    is_sea = [[character in SEA_CHARACTERS_IN_WATER_GRID for character in row] for row in water_grid_rows]
    lines_from_edge = lines_running_in_from(is_sea, side)
    if not lines_from_edge:
        return 0.0, 0.0
    depths = [sea_run_length_from_start(line) for line in lines_from_edge]
    edge_sea_share = sum(1 for line in lines_from_edge if line and line[0]) / len(lines_from_edge)
    depth_share = sum(depths) / len(depths) / max(1, len(lines_from_edge[0]))
    shares = (edge_sea_share, depth_share)
    return shares


def lines_running_in_from(is_sea: List[List[bool]], side: str) -> List[List[bool]]:
    if not is_sea:
        return []
    columns = [list(column) for column in zip(*is_sea)]
    lines_by_side = {"west": [list(row) for row in is_sea], "east": [list(reversed(row)) for row in is_sea],
                     "north": columns, "south": [list(reversed(column)) for column in columns]}
    lines = lines_by_side[side]
    return lines


def sea_run_length_from_start(line: List[bool]) -> int:
    run_length = 0
    for is_sea_cell in line:
        if not is_sea_cell:
            break
        run_length += 1
    return run_length


def coast_along_an_edge(water_grid_rows: List[str]) -> Tuple[str, float]:
    best_side, best_depth_share, best_score = "", 0.0, 0.0
    for side in ("north", "east", "south", "west"):
        edge_sea_share, depth_share = sea_depth_share_from_side(water_grid_rows, side)
        if edge_sea_share < SMALLEST_EDGE_SEA_SHARE_THAT_MAKES_A_COAST:
            continue
        score_of_side = edge_sea_share * depth_share
        if score_of_side > best_score:
            best_side, best_depth_share, best_score = side, depth_share, score_of_side
    if not best_side or best_depth_share < SMALLEST_SEA_DEPTH_SHARE_THAT_MAKES_A_COAST:
        return "", 0.0
    sea_share = min(LARGEST_SEA_SHARE_KEPT, max(SMALLEST_SEA_SHARE_KEPT, best_depth_share + EXTRA_SEA_SHARE_KEPT_FOR_THE_SHORE))
    coast = (best_side, round(sea_share, 3))
    return coast


def height_steps_along_path(fine_height_rows: List[str], path: List[List[int]]) -> int:
    heights = [int(fine_height_rows[y][x]) for x, y in path
               if 0 <= y < len(fine_height_rows) and 0 <= x < len(fine_height_rows[y])]
    if not heights:
        return 0
    height_range = max(heights) - min(heights)
    return height_range


def ledge_chance_for_height_steps(height_steps: int) -> float:
    ledge_chance = min(LARGEST_LEDGE_CHANCE, LEDGE_CHANCE_ON_FLAT_GROUND + LEDGE_CHANCE_PER_HEIGHT_STEP_ALONG_THE_PATH * height_steps)
    return round(ledge_chance, 3)


def geography_spec_fields(region_block: dict, is_sea_route: bool) -> dict:
    biome_block = region_block.get("biome") or {}
    shares = biome_shares(biome_block.get("grid") or [])
    spec_biome = "beach" if is_sea_route else (majority_spec_biome(shares) or "meadow")
    elevation = region_block.get("elevation") or {}
    height_steps = height_steps_along_path(elevation.get("fine") or [], region_block.get("path") or [])
    fields = {"biome": spec_biome, "tall_grass": TALL_GRASS_BY_SPEC_BIOME.get(spec_biome, 0.3),
              "ledge_chance": ledge_chance_for_height_steps(height_steps)}
    woodland = woodland_kind(shares, spec_biome)
    if woodland in MAXIMUM_WALKABLE_SHARE_BY_WOODLAND and spec_biome in ("forest", "meadow", "plains", "riverland"):
        fields["max_walkable"] = MAXIMUM_WALKABLE_SHARE_BY_WOODLAND[woodland]
    if share_of(shares, MOUNTAIN_BIOME_CODES) >= SMALLEST_MOUNTAIN_SHARE_THAT_MAKES_A_PASS and spec_biome != "mountain":
        fields["meander"] = MEANDER_THROUGH_A_MOUNTAIN_PASS
        fields["ledge_chance"] = round(min(LARGEST_LEDGE_CHANCE, fields["ledge_chance"] + 0.15), 3)
    coast_side, sea_share = ("", 0.0) if is_sea_route else coast_along_an_edge((region_block.get("water") or {}).get("grid") or [])
    if coast_side:
        fields["coast_side"], fields["sea_share"] = coast_side, sea_share
    return fields


def geography_summary(region_block: dict, fields: dict) -> dict:
    biome_block = region_block.get("biome") or {}
    shares = biome_shares(biome_block.get("grid") or [])
    water_rows = (region_block.get("water") or {}).get("grid") or []
    summary = {"biome": fields.get("biome"), "woodland": woodland_kind(shares, fields.get("biome", "")),
               "note": biome_mix_note(shares, biome_block.get("gradient") or {}),
               "rivers": len(region_block.get("rivers") or []),
               "lake_cells": sum(row.count("o") for row in water_rows),
               "coast_side": fields.get("coast_side", ""),
               "mountain_share": round(share_of(shares, MOUNTAIN_BIOME_CODES), 3)}
    return summary
