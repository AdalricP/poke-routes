import numpy as np

from pokeroutes.ruletile import ArkTiler
from pokeroutes.ruletile_secondaries import (class_table_for_secondary, cliff_face_metatile, sea_band_grid,
                                             secondary_for_spec)
from pokeroutes.spec import RouteSpec

ALL_BLOB_MASKS = [str(mask) for mask in range(256)]
MASK_BITS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def blob_table(metatile_id):
    by_mask = {mask: metatile_id for mask in ALL_BLOB_MASKS}
    return by_mask


def class_table_with_coast():
    woodland_crowns = {"F": {"0,0,0,0": 0x220}}
    table = {
        "tileset": {"primary": "gTileset_ArkOutdoor", "secondary": "gTileset_ArkWoodland"},
        "mask_bits": MASK_BITS,
        "classes": {
            "g": {"name": "grass", "base": [1], "collision": 0},
            "w": {"name": "water", "autotile": "blob47", "by_mask": blob_table(2), "base": [2], "collision": 0},
            "f": {"name": "forest", "autotile": "tree2x2", "base": [3], "collision": 1, "crowns_from_below": woodland_crowns,
                  "by_context": {}, "overlays": {"g": {}, "bush": {}, "p": {}}, "bush": 4},
            "T": {"name": "autumn tall grass", "autotile": "blob47", "by_mask": blob_table(0x210), "base": [0x210]},
        },
        "props": {"boulder": {"cells": [[5]], "collision": 1}, "leaves": {"cells": [[0x230]], "collision": 0}},
        "class_of_metatile": {"1": "g", "560": "t"},
        "secondary_tilesets": {"ark_coast": {
            "symbol": "gTileset_ArkCoast", "biomes": ["beach", "sea"],
            "classes": {"b": {"name": "sea", "autotile": "sea_depth", "surf": True, "collision": 0,
                              "band_starts_in_metatiles": [0, 2, 4], "depth_water": "bDu",
                              "bands": [blob_table(0x200), blob_table(0x201), blob_table(0x202)], "base": [0x201]},
                        "D": {"name": "dive", "autotile": "blob47", "surf": True, "by_mask": blob_table(0x203),
                              "base": [0x203], "collision": 0}},
            "props": {}, "class_of_metatile": {"512": "b"}}},
    }
    return table


def test_a_terrain_secondary_keeps_primary_classes_and_drops_woodland_ones():
    table = class_table_for_secondary(class_table_with_coast(), "ark_coast")

    assert set(table["classes"]) == {"g", "w", "f", "b", "D"}
    assert "crowns_from_below" not in table["classes"]["f"]
    assert set(table["props"]) == {"boulder"}
    assert table["tileset"]["secondary"] == "gTileset_ArkCoast"


def test_sea_bands_get_darker_away_from_land_and_dive_water_is_surfable():
    tiler = ArkTiler(class_table_with_coast(), rom=None, secondary="ark_coast")
    grid = np.full((9, 9), "b")
    grid[:, 0] = "g"
    grid[4, 7] = "D"

    layout = tiler.tile(grid)
    bands = sea_band_grid(["".join(row) for row in grid], tiler.C["b"])

    assert bands[4, 1] == 0 and bands[4, 6] == 2
    assert layout.metatile[4, 1] == 0x200 and layout.metatile[4, 5] == 0x202 and layout.metatile[4, 7] == 0x203
    assert layout.elevation[4, 7] == 1 and not layout.collision[4, 7]


def test_cliff_faces_pick_upper_and_lower_rows_and_their_ends():
    parts = {f"{row}_{column}": index for index, (row, column) in enumerate(
        (row, column) for row in ("upper", "lower") for column in ("left", "mid", "right", "single"))}
    face_class = {"parts": parts, "base": [0], "joins": "k"}
    grid = ["rrrr", "kkkk", "kkkk", "vvvv"]

    assert cliff_face_metatile(grid, 0, 1, face_class) == parts["upper_left"]
    assert cliff_face_metatile(grid, 1, 2, face_class) == parts["lower_mid"]
    assert cliff_face_metatile(grid, 3, 2, face_class) == parts["lower_right"]


def test_open_sea_routes_and_beach_biomes_pick_the_coast_secondary():
    table = class_table_with_coast()

    assert secondary_for_spec(table, RouteSpec(name="Sea", biome="forest", region={"mode": "sea"})) == "ark_coast"
    assert secondary_for_spec(table, RouteSpec(name="Beach", biome="beach")) == "ark_coast"
    assert secondary_for_spec(table, RouteSpec(name="Wood", biome="forest")) == "ark_woodland"
