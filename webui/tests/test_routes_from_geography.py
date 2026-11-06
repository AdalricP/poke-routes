"""Routes take their look from the geography under them, and "Generate routes"
lays a different network on every press.

    python3 -m pytest -q webui/tests/test_routes_from_geography.py
"""
from __future__ import annotations

import numpy as np
import pytest

from pokeroutes.generate import generate
from pokeroutes.region import edit, map_grid
from pokeroutes.region.route_geography import geography_spec_fields
from pokeroutes.spec import Exit, RouteSpec

REGION_WIDTH_IN_CELLS, REGION_HEIGHT_IN_CELLS = 16, 18


def synthetic_region_block(water_rows, biome_rows, rivers=(), path=None):
    flat_rows = ["0" * REGION_WIDTH_IN_CELLS] * REGION_HEIGHT_IN_CELLS
    region_block = {
        "cell": 4, "origin": [0, 0],
        "water": {"grid": water_rows},
        "biome": {"grid": biome_rows, "gradient": {}},
        "elevation": {"tiers": flat_rows, "fine": flat_rows},
        "rivers": list(rivers),
        "coast": {"sides": []},
        "path": path or [[REGION_WIDTH_IN_CELLS // 2, row] for row in range(REGION_HEIGHT_IN_CELLS)],
    }
    return region_block


def dry_land_rows():
    return ["." * REGION_WIDTH_IN_CELLS for _ in range(REGION_HEIGHT_IN_CELLS)]


def river_across_the_middle():
    middle_row = REGION_HEIGHT_IN_CELLS // 2
    river = {"cells": [[column, middle_row] for column in range(REGION_WIDTH_IN_CELLS)], "flow": 200.0, "width": 2}
    water_rows = dry_land_rows()
    water_rows[middle_row] = "r" * REGION_WIDTH_IN_CELLS
    return river, water_rows


def sea_along_the_east_edge(sea_columns=5):
    water_rows = ["." * (REGION_WIDTH_IN_CELLS - sea_columns) + "~" * sea_columns for _ in range(REGION_HEIGHT_IN_CELLS)]
    return water_rows


def route_spec_on(region_block, biome="meadow", seed=3, **fields):
    spec = RouteSpec(name="Geography Test", size=[REGION_WIDTH_IN_CELLS * 4, REGION_HEIGHT_IN_CELLS * 4], seed=seed,
                     biome=biome, exits=[Exit("north", 0.45), Exit("south", 0.45)], region=region_block,
                     special_areas=[], hidden_paths=0, **fields)
    return spec


def test_majority_biome_decides_the_route_biome_and_a_mix_is_noted():
    biome_rows = ["f" * 10 + "m" * 6 for _ in range(REGION_HEIGHT_IN_CELLS)]
    region_block = synthetic_region_block(dry_land_rows(), biome_rows)
    fields = geography_spec_fields(region_block, is_sea_route=False)
    assert fields["biome"] == "forest"
    assert fields["max_walkable"] < 0.4
    from pokeroutes.region.route_geography import biome_mix_note, biome_shares

    assert "forest" in biome_mix_note(biome_shares(biome_rows), {}) and "meadow" in biome_mix_note(biome_shares(biome_rows), {})


def test_open_meadow_without_woods_becomes_plains():
    region_block = synthetic_region_block(dry_land_rows(), ["m" * REGION_WIDTH_IN_CELLS] * REGION_HEIGHT_IN_CELLS)
    assert geography_spec_fields(region_block, is_sea_route=False)["biome"] == "plains"


def test_sea_on_the_east_edge_puts_the_coast_east():
    region_block = synthetic_region_block(sea_along_the_east_edge(), ["m" * REGION_WIDTH_IN_CELLS] * REGION_HEIGHT_IN_CELLS)
    fields = geography_spec_fields(region_block, is_sea_route=False)
    assert fields["coast_side"] == "east"
    assert 0.2 < fields["sea_share"] <= 0.5


def test_sea_on_the_east_edge_is_drawn_as_sea_along_the_east_of_the_route():
    region_block = synthetic_region_block(sea_along_the_east_edge(), ["f" * REGION_WIDTH_IN_CELLS] * REGION_HEIGHT_IN_CELLS,
                                          path=[[5, row] for row in range(REGION_HEIGHT_IN_CELLS)])
    fields = geography_spec_fields(region_block, is_sea_route=False)
    route = generate(route_spec_on(region_block, biome="forest", coast_side=fields["coast_side"], sea_share=fields["sea_share"]))
    sea_class = "b"
    east_quarter = route.cls[:, -route.w // 5:]
    west_half = route.cls[:, :route.w // 2]
    assert (east_quarter == sea_class).mean() > 0.4
    assert (west_half == sea_class).mean() < 0.02


def test_river_through_the_route_is_drawn_with_a_bridge_where_the_road_crosses():
    river, water_rows = river_across_the_middle()
    region_block = synthetic_region_block(water_rows, ["m" * REGION_WIDTH_IN_CELLS] * REGION_HEIGHT_IN_CELLS, rivers=[river])
    route = generate(route_spec_on(region_block, biome="meadow"))
    river_zones = [zone for zone in route.zones if zone.meta.get("region_river")]
    assert river_zones, route.log
    middle_rows = route.cls[REGION_HEIGHT_IN_CELLS * 2 - 3:REGION_HEIGHT_IN_CELLS * 2 + 4]
    assert (middle_rows == "w").sum() > route.w
    assert (middle_rows == "q").any()


def test_lake_in_the_region_becomes_water_in_the_route():
    water_rows = dry_land_rows()
    for row in range(3, 7):
        water_rows[row] = "." * 2 + "o" * 4 + "." * (REGION_WIDTH_IN_CELLS - 6)
    region_block = synthetic_region_block(water_rows, ["f" * REGION_WIDTH_IN_CELLS] * REGION_HEIGHT_IN_CELLS)
    route = generate(route_spec_on(region_block, biome="forest"))
    lake_window = route.cls[12:28, 8:24]
    assert (lake_window == "w").mean() > 0.3


def generic_doc_with_towns(seed=3, town_count=4):
    from pokeroutes.region.town_sites import pick_town_sites

    doc = edit.generate_doc(seed, "generic")
    sites = pick_town_sites(edit.doc_to_world(doc), 0, [])[:town_count]
    doc = edit.paint_map(doc, [list(map_grid.grid_cell_of_region_cell(site, map_grid.REGION_CELLS_PER_GRID_CELL)) for site in sites], "town")
    return doc


def route_layout(doc):
    return tuple((route["name"], tuple(route["rect"])) for route in doc["routes"])


def test_recomputing_routes_keeps_the_painted_map():
    doc = edit.generate_map(edit.generate_doc(3, "generic"))
    again = edit.recompute(doc, ["routes"])
    assert route_layout(again) == route_layout(doc)
    assert again["map_grid"] == doc["map_grid"]


def test_generate_map_places_towns_and_keeps_pinned_ones():
    doc = generic_doc_with_towns(town_count=1)
    doc["cities"][0]["pinned"] = True
    pinned_name = doc["cities"][0]["name"]
    generated = edit.generate_map(doc)
    names = [town["name"] for town in generated["cities"]]
    assert pinned_name in names and 4 <= len(names) <= 14
    assert generated["routes"] and generated["message"]
    again = edit.generate_map(generated)
    assert route_layout(again) != route_layout(generated)


def landmasses_with_towns(doc):
    world = edit.doc_to_world(doc)
    labels = {int(world.landmass[(town["rect"][1] + town["rect"][3]) // 2, (town["rect"][0] + town["rect"][2]) // 2]) for town in doc["cities"]}
    return labels


def test_generate_map_scales_towns_with_the_land_and_spreads_them_over_islands():
    big_coast = edit.generate_map(edit.generate_doc(8, geography_kind="coast"))
    archipelago = edit.generate_map(edit.generate_doc(5, geography_kind="archipelago"))
    assert len(big_coast["cities"]) >= 8
    assert len(landmasses_with_towns(archipelago)) >= 4


def route_field_move_needed(route):
    from pokeroutes.region.gates import route_field_move_needed as field_move_of_route

    return field_move_of_route(route)


def test_towns_on_islands_far_apart_are_joined_by_surf_routes():
    archipelago = edit.generate_map(edit.generate_doc(5, geography_kind="archipelago"))
    surf_routes = [route for route in archipelago["routes"] if route["mode"] == "sea"]
    assert surf_routes
    assert all(route["role"] == "sea" and route_field_move_needed(route) == "surf" for route in surf_routes)
