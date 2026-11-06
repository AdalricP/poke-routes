from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

TESTS_DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIRECTORY.parent))
sys.path.insert(0, str(TESTS_DIRECTORY.parent.parent))

from pokeroutes import tile_coalesce, tile_editing, tileset_overlay  # noqa: E402
from pokeroutes.ruletile import PRIMARY, SECONDARY, ArkTiler  # noqa: E402
from pokeroutes.tileset import Tileset, TilesetPair, load_pair  # noqa: E402

SOLID_ONE_TILE, SOLID_TWO_TILE, SHARED_PATTERN_TILE = 1, 2, 3
SECONDARY_SOLID_TILE = 512
GRASS_METATILE, ROCK_METATILE, PATTERN_METATILE, SECONDARY_METATILE = 0, 1, 2, 512
PRIMARY_TILE_COUNT = 16
TOP_BLANK_ENTRIES = [0, 0, 0, 0]


def solid_tile(colour_index: int) -> np.ndarray:
    tile_pixels = np.full((8, 8), colour_index, dtype=np.uint8)
    return tile_pixels


def pattern_tile() -> np.ndarray:
    tile_pixels = np.zeros((8, 8), dtype=np.uint8)
    tile_pixels[0, :] = 5
    tile_pixels[:, 0] = 6
    return tile_pixels


def entry(tile_index: int, palette_slot: int = 0, flip_horizontal: bool = False) -> int:
    packed_entry = tile_editing.encode_tile_entry(tile_editing.TileEntry(tile_index, flip_horizontal, False, palette_slot))
    return packed_entry


def distinct_palettes() -> np.ndarray:
    palettes = np.zeros((16, 16, 3), dtype=np.uint8)
    for palette_slot in range(16):
        for colour_index in range(16):
            palettes[palette_slot, colour_index] = (colour_index * 16, palette_slot * 15, 200 - colour_index * 8)
    return palettes


def small_tileset_pair(root: Path = Path(".")) -> TilesetPair:
    primary_tiles = np.zeros((PRIMARY_TILE_COUNT, 8, 8), dtype=np.uint8)
    primary_tiles[SOLID_ONE_TILE] = solid_tile(1)
    primary_tiles[SOLID_TWO_TILE] = solid_tile(2)
    primary_tiles[SHARED_PATTERN_TILE] = pattern_tile()
    primary_metatiles = np.array([
        [entry(SOLID_ONE_TILE)] * 4 + TOP_BLANK_ENTRIES,
        [entry(SOLID_TWO_TILE)] * 4 + TOP_BLANK_ENTRIES,
        [entry(SHARED_PATTERN_TILE, flip_horizontal=True)] + [entry(SHARED_PATTERN_TILE)] * 3 + TOP_BLANK_ENTRIES,
    ], dtype=np.uint16)
    secondary_tiles = np.stack([solid_tile(3), solid_tile(0)])
    secondary_metatiles = np.array([[entry(SECONDARY_SOLID_TILE, palette_slot=6)] * 4 + TOP_BLANK_ENTRIES], dtype=np.uint16)
    primary = Tileset(PRIMARY, root / "primary" / PRIMARY, primary_tiles, distinct_palettes(), primary_metatiles,
                      np.array([0x1000, 0x1000, 0x1000], dtype=np.uint16))
    secondary = Tileset(SECONDARY, root / "secondary" / SECONDARY, secondary_tiles, distinct_palettes(), secondary_metatiles,
                        np.array([0x1000], dtype=np.uint16))
    tileset_pair = TilesetPair(primary, secondary)
    return tileset_pair


def write_small_rom_tree(rom_root: Path) -> Path:
    tilesets_directory = rom_root / "data" / "tilesets"
    pair = small_tileset_pair(tilesets_directory)
    for tileset in (pair.primary, pair.secondary):
        tileset.path.mkdir(parents=True, exist_ok=True)
        blank_sheet = Image.new("P", (128, 8))
        blank_sheet.putpalette([value for colour_index in range(16) for value in (colour_index * 16,) * 3])
        blank_sheet.save(tileset.path / "tiles.png")
        tileset_overlay.write_one_tileset_in_pokeemerald_format(tileset, tileset, tileset.path)
    class_table = {"mask_bits": ["N", "NE", "E", "SE", "S", "SW", "W", "NW"],
                   "classes": {"g": {"name": "grass", "base": [GRASS_METATILE]},
                               "r": {"name": "rock", "base": [ROCK_METATILE], "collision": 1},
                               "f": {"name": "forest", "base": [ROCK_METATILE]}},
                   "props": {}, "class_of_metatile": {"0": "g", "1": "r"}}
    (tilesets_directory / "metatile_classes_ark.json").write_text(json.dumps(class_table))
    return rom_root


@pytest.fixture
def overlay_directory(tmp_path, monkeypatch):
    overlay_root = tmp_path / "overrides"
    monkeypatch.setenv(tileset_overlay.OVERLAY_DIRECTORY_ENVIRONMENT_VARIABLE, str(overlay_root))
    return overlay_root


@pytest.fixture
def small_rom(tmp_path, monkeypatch, overlay_directory):
    rom_root = write_small_rom_tree(tmp_path / "rom")
    monkeypatch.setenv("ARK_ROM", str(rom_root))
    return rom_root



def test_missing_overlay_reads_as_empty(overlay_directory):
    overlay = tileset_overlay.read_overlay_for_tileset_pair("ark")
    assert overlay["revision"] == 0
    assert not tileset_overlay.overlay_has_changes(overlay)


def test_overlay_save_load_round_trip_bumps_revision_and_keeps_previous(overlay_directory):
    first_overlay = tileset_overlay.empty_overlay()
    first_overlay["palettes"]["3"] = ["#000000"] * 16
    stored_first = tileset_overlay.write_overlay_for_tileset_pair("ark", first_overlay, expected_revision=0)
    second_overlay = dict(stored_first, palettes={"3": ["#ffffff"] * 16})
    stored_second = tileset_overlay.write_overlay_for_tileset_pair("ark", second_overlay, expected_revision=1)

    reloaded_overlay = tileset_overlay.read_overlay_for_tileset_pair("ark")
    previous_overlay = json.loads((overlay_directory / "ark" / tileset_overlay.PREVIOUS_OVERLAY_FILE_NAME).read_text())

    assert (stored_first["revision"], stored_second["revision"], reloaded_overlay["revision"]) == (1, 2, 2)
    assert reloaded_overlay["palettes"]["3"][0] == "#ffffff"
    assert previous_overlay["palettes"]["3"][0] == "#000000"
    assert not list((overlay_directory / "ark").glob("*.tmp"))


def test_overlay_save_refuses_a_stale_revision(overlay_directory):
    tileset_overlay.write_overlay_for_tileset_pair("ark", tileset_overlay.empty_overlay(), expected_revision=0)
    with pytest.raises(tileset_overlay.OverlayRevisionConflict):
        tileset_overlay.write_overlay_for_tileset_pair("ark", tileset_overlay.empty_overlay(), expected_revision=0)


def test_overlay_names_cannot_escape_the_overlay_folder(overlay_directory):
    with pytest.raises(tileset_overlay.InvalidOverlay):
        tileset_overlay.overlay_file_path_for_tileset_pair("../rom")



def test_overlay_replaces_and_appends_tiles_palettes_and_metatiles():
    base_pair = small_tileset_pair()
    overlay = tileset_overlay.empty_overlay()
    overlay["tiles"][str(SOLID_ONE_TILE)] = "7" * 64
    overlay["tiles"]["514"] = "9" * 64
    overlay["palettes"]["7"] = ["#102030"] * 16
    overlay["metatiles"]["513"] = [entry(514, palette_slot=7)] * 4 + TOP_BLANK_ENTRIES
    overlay["metatile_attributes"]["513"] = 0x1021

    overlaid_pair = tileset_overlay.apply_overlay_to_tileset_pair(base_pair, overlay)

    assert overlaid_pair.primary.tiles[SOLID_ONE_TILE][0, 0] == 7
    assert len(overlaid_pair.secondary.tiles) == 3 and overlaid_pair.secondary.tiles[2][3, 3] == 9
    assert overlaid_pair.metatile_count() == (3, 2)
    assert overlaid_pair.behavior(513) == 0x21
    assert tuple(overlaid_pair.render_metatile(513)[0, 0]) == (0x10, 0x20, 0x30)
    assert base_pair.primary.tiles[SOLID_ONE_TILE][0, 0] == 1


def test_overlay_rejects_a_metatile_gap():
    overlay = tileset_overlay.empty_overlay()
    overlay["metatiles"]["520"] = [0] * 8
    with pytest.raises(tileset_overlay.InvalidOverlay):
        tileset_overlay.apply_overlay_to_tileset_pair(small_tileset_pair(), overlay)


def test_exported_tileset_files_load_back_with_the_overlay(tmp_path):
    base_pair = small_tileset_pair(write_small_rom_tree(tmp_path / "rom") / "data" / "tilesets")
    overlay = tileset_editing_overlay_with_a_red_pixel(base_pair)

    tileset_overlay.write_overlaid_tileset_pair_files(base_pair, overlay, tmp_path / "export")
    exported_pair = load_pair(tmp_path / "export", PRIMARY, SECONDARY)

    expected_rgb = tileset_overlay.apply_overlay_to_tileset_pair(base_pair, overlay).render_metatile(GRASS_METATILE)
    assert np.array_equal(exported_pair.render_metatile(GRASS_METATILE), expected_rgb)


def tileset_editing_overlay_with_a_red_pixel(base_pair: TilesetPair) -> dict:
    overlay = tile_editing.write_layer_pixels(base_pair, tileset_overlay.empty_overlay(), GRASS_METATILE, "bottom", [(3, 3, 9)])
    overlay = tile_editing.set_palette_colours(base_pair, overlay, 0, {"9": "#ff0000"})
    return overlay



def test_drawing_on_a_shared_tile_copies_it_first():
    base_pair = small_tileset_pair()
    overlay = tile_editing.write_layer_pixels(base_pair, tileset_overlay.empty_overlay(), PATTERN_METATILE, "bottom",
                                              [(9, 1, 4)])
    edited_pair = tileset_overlay.apply_overlay_to_tileset_pair(base_pair, overlay)

    edited_entry = tile_editing.decode_tile_entry(edited_pair.primary.metatiles[PATTERN_METATILE][1])
    assert edited_entry.tile_index != SHARED_PATTERN_TILE
    assert edited_pair.primary.tiles[edited_entry.tile_index][1, 1] == 4
    assert np.array_equal(edited_pair.primary.tiles[SHARED_PATTERN_TILE], pattern_tile())


def test_drawing_on_a_private_tile_edits_it_in_place():
    base_pair = small_tileset_pair()
    overlay = tile_editing.write_layer_pixels(base_pair, tileset_overlay.empty_overlay(), SECONDARY_METATILE, "bottom",
                                              [(0, 0, 8)], edit_shared_tiles=True)
    edited_pair = tileset_overlay.apply_overlay_to_tileset_pair(base_pair, overlay)

    assert list(overlay["tiles"]) == [str(SECONDARY_SOLID_TILE)]
    assert edited_pair.secondary.tiles[0][0, 0] == 8


def test_drawing_respects_the_entry_flip():
    base_pair = small_tileset_pair()
    overlay = tile_editing.write_layer_pixels(base_pair, tileset_overlay.empty_overlay(), PATTERN_METATILE, "bottom",
                                              [(0, 7, 1)])
    edited_pair = tileset_overlay.apply_overlay_to_tileset_pair(base_pair, overlay)

    shown_pixels = tile_editing.layer_pixels_as_displayed(edited_pair, PATTERN_METATILE, 0)
    stored_tile = edited_pair.primary.tiles[tile_editing.decode_tile_entry(edited_pair.primary.metatiles[PATTERN_METATILE][0]).tile_index]
    assert shown_pixels[7, 0] == 1
    assert stored_tile[7, 7] == 1


def test_flip_and_rotate_move_pixels_and_palettes():
    base_pair = small_tileset_pair()
    overlay = tile_editing.set_quadrant_palette(base_pair, tileset_overlay.empty_overlay(), GRASS_METATILE, "bottom", 0, 4)
    overlay = tile_editing.write_layer_pixels(base_pair, overlay, GRASS_METATILE, "bottom", [(0, 0, 9)])

    flipped_overlay = tile_editing.transform_layers(base_pair, overlay, GRASS_METATILE, ["bottom"], "flip_horizontal")
    rotated_overlay = tile_editing.transform_layers(base_pair, overlay, GRASS_METATILE, ["bottom"], "rotate_clockwise")

    flipped_pair = tileset_overlay.apply_overlay_to_tileset_pair(base_pair, flipped_overlay)
    rotated_pair = tileset_overlay.apply_overlay_to_tileset_pair(base_pair, rotated_overlay)
    assert tile_editing.layer_pixels_as_displayed(flipped_pair, GRASS_METATILE, 0)[0, 15] == 9
    assert tile_editing.layer_palette_slots(flipped_pair, GRASS_METATILE, 0) == [0, 4, 0, 0]
    assert tile_editing.layer_pixels_as_displayed(rotated_pair, GRASS_METATILE, 0)[0, 15] == 9
    assert tile_editing.layer_palette_slots(rotated_pair, GRASS_METATILE, 0) == [0, 4, 0, 0]


def test_palette_colours_snap_to_gba_15_bit():
    overlay = tile_editing.set_palette_colours(small_tileset_pair(), tileset_overlay.empty_overlay(), 2, {"5": "#ff8107"})
    assert overlay["palettes"]["2"][5] == "#f88000"


def test_new_tiles_append_after_the_secondary_set_and_carry_their_properties():
    base_pair = small_tileset_pair()
    overlay, blank_metatile_id = tile_editing.create_metatile(base_pair, tileset_overlay.empty_overlay(), name="flowers")
    overlay, copied_metatile_id = tile_editing.create_metatile(base_pair, overlay, copy_from_metatile_id=ROCK_METATILE,
                                                               class_letter="r", has_collision=True)
    overlay = tile_editing.set_metatile_properties(base_pair, overlay, blank_metatile_id, {"behaviour": 0x02, "class": "t"})
    edited_pair = tileset_overlay.apply_overlay_to_tileset_pair(base_pair, overlay)

    assert (blank_metatile_id, copied_metatile_id) == (513, 514)
    assert edited_pair.behavior(blank_metatile_id) == 0x02
    assert np.array_equal(edited_pair.render_metatile(copied_metatile_id), edited_pair.render_metatile(ROCK_METATILE))
    assert overlay["custom_metatiles"][str(blank_metatile_id)] == {"name": "flowers", "class": "t", "collision": False}


def test_custom_tiles_join_the_ark_paint_palette_and_class_table():
    base_pair = small_tileset_pair()
    overlay, new_metatile_id = tile_editing.create_metatile(base_pair, tileset_overlay.empty_overlay(), name="stone",
                                                            class_letter="g", has_collision=True)
    class_table = {"mask_bits": [], "classes": {"g": {"base": [0]}}, "props": {}, "class_of_metatile": {}}

    overlaid_table = tileset_overlay.apply_overlay_to_ark_class_table(class_table, overlay)

    custom_prop = overlaid_table["props"][tileset_overlay.custom_prop_name(new_metatile_id)]
    assert custom_prop == {"cells": [[new_metatile_id]], "collision": 1, "name": "stone"}
    assert overlaid_table["class_of_metatile"][str(new_metatile_id)] == "o"
    assert class_table["props"] == {}



def test_coalesce_makes_every_blob_variant_and_a_tiler_class():
    base_pair = small_tileset_pair()
    overlay, metatile_id_by_mask = tile_coalesce.coalesce_edge_variants(
        base_pair, tileset_overlay.empty_overlay(), ROCK_METATILE, GRASS_METATILE, "d", "sand",
        existing_class_letters=["g", "r"])
    edited_pair = tileset_overlay.apply_overlay_to_tileset_pair(base_pair, overlay)

    assert len(metatile_id_by_mask) == 47
    assert metatile_id_by_mask["255"] == ROCK_METATILE
    assert len(set(metatile_id_by_mask.values())) == 47
    assert len(overlay["tiles"]) <= 16
    lonely_sand_rgb = edited_pair.render_metatile(metatile_id_by_mask["0"])
    assert tuple(lonely_sand_rgb[0, 0]) == tuple(edited_pair.render_metatile(GRASS_METATILE)[0, 0])
    assert tuple(lonely_sand_rgb[8, 8]) == tuple(edited_pair.render_metatile(ROCK_METATILE)[8, 8])
    assert overlay["classes"]["d"]["autotile"] == "blob47"


def test_coalesce_refuses_a_letter_the_tileset_already_draws():
    with pytest.raises(tileset_overlay.InvalidOverlay):
        tile_coalesce.coalesce_edge_variants(small_tileset_pair(), tileset_overlay.empty_overlay(), ROCK_METATILE,
                                             GRASS_METATILE, "g", existing_class_letters=["g"])


def test_the_ark_tiler_draws_a_coalesced_class(small_rom):
    from pokeroutes.tile_operations import apply_tile_operation

    tiler = ArkTiler.load()
    overlay, outcome = apply_tile_operation(tiler.rom_pair(), tiler.overlay,
                                            {"kind": "coalesce", "inner": ROCK_METATILE, "outer": GRASS_METATILE, "class": "d"},
                                            tiler.c)
    tileset_overlay.write_overlay_for_tileset_pair("ark", overlay, 0)
    grid = np.full((5, 5), "g", dtype="<U1")
    grid[1:4, 1:4] = "d"

    tiled = ArkTiler.load().tile(grid).metatile

    assert tiled[2, 2] == ROCK_METATILE
    assert tiled[1, 1] == outcome["by_mask"]["28"]
    assert tiled[0, 0] == GRASS_METATILE



class FakeRequest:
    def __init__(self, request_body=None):
        encoded_body = json.dumps(request_body or {}).encode()
        self.headers = {"Content-Length": str(len(encoded_body))}
        self.rfile = io.BytesIO(encoded_body)
        self.answers = []

    def _json(self, response_body, status_code=200):
        self.answers.append((status_code, response_body))


def fake_server(tmp_path) -> SimpleNamespace:
    fake = SimpleNamespace(EMERALD=tmp_path / "no-emerald", MODELS=tmp_path / "no-models", pair=lambda tileset_name: None)
    return fake


def call_tile_editor_api(tmp_path, method: str, path: str, request_body=None, query=None):
    import tile_overrides
    from urllib.parse import urlparse

    request = FakeRequest(request_body)
    tile_overrides.handle(request, method, urlparse(path), query or {}, fake_server(tmp_path))
    return request.answers[-1]


def test_the_http_api_edits_a_draft_then_saves_it(small_rom, tmp_path):
    status_code, listing = call_tile_editor_api(tmp_path, "GET", "/api/tile-editor/tilesets")
    assert status_code == 200 and listing["tilesets"][0]["id"] == "ark"
    status_code, model = call_tile_editor_api(tmp_path, "GET", "/api/tile-editor/tileset", query={"id": ["ark"]})
    assert status_code == 200 and len(model["base"]["primary_metatiles"]) == 3

    status_code, edited = call_tile_editor_api(tmp_path, "POST", "/api/tile-editor/edit", {
        "id": "ark", "overlay": model["overlay"],
        "operation": {"kind": "write_pixels", "metatile_id": GRASS_METATILE, "layer": "bottom", "pixels": [[1, 1, 3]]}})
    assert status_code == 200 and edited["overlay"]["tiles"]
    assert tileset_overlay.read_overlay_for_tileset_pair("ark")["revision"] == 0

    status_code, saved = call_tile_editor_api(tmp_path, "POST", "/api/tile-editor/save",
                                              {"id": "ark", "overlay": edited["overlay"], "revision": 0})
    assert (status_code, saved["revision"]) == (200, 1)
    status_code, conflict = call_tile_editor_api(tmp_path, "POST", "/api/tile-editor/save",
                                                 {"id": "ark", "overlay": edited["overlay"], "revision": 0})
    assert status_code == 409 and conflict["conflict"]
    status_code, refused = call_tile_editor_api(tmp_path, "POST", "/api/tile-editor/edit",
                                                {"id": "ark", "overlay": {}, "operation": {"kind": "explode"}})
    assert status_code == 400


def test_agent_tools_read_draw_create_and_coalesce(small_rom, tmp_path):
    import tile_agent_tools

    tools = tile_agent_tools.tile_tools(lambda: None, tmp_path / "previews")
    run_tool = lambda tool_name, arguments: tools[tool_name][0](arguments)  # noqa: E731

    description = run_tool("tile_read", {"metatile_id": GRASS_METATILE})
    drawn = run_tool("tile_edit", {"operation": {"kind": "write_pixels", "metatile_id": GRASS_METATILE,
                                                 "rows": ["2" * 16] * 16}})
    created = run_tool("tile_edit", {"operation": {"kind": "create", "copy_from": GRASS_METATILE, "name": "copy"}})
    coalesced = run_tool("tile_coalesce", {"inner": ROCK_METATILE, "outer": GRASS_METATILE, "class": "d"})
    sheet = run_tool("tile_sheet", {"which": "secondary"})

    assert description["layers"][0]["quadrants"][0]["rows_as_shown"][0] == "1" * 8
    assert Path(description["preview"]).is_file()
    assert drawn["saved_revision"] == 1
    assert created["metatile_id"] == 513 and created["saved_revision"] == 2
    assert coalesced["saved_revision"] == 3 and len(coalesced["by_mask"]) == 47
    assert Path(coalesced["sample"]).is_file() and Path(sheet["image"]).is_file()
    assert "d" in sheet["coalesced_classes"]
    saved_pair = ArkTiler.load().pair()
    assert tile_editing.layer_pixels_as_displayed(saved_pair, GRASS_METATILE, 0).max() == 2
