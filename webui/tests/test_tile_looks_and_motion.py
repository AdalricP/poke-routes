import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "webui"))

import tile_looks  # noqa: E402
from pokeroutes.idle_motion import (OFFSETS_IN_PIXELS_FOR_EACH_TICK_BY_MOTION, PropCells, TileMotion,  # noqa: E402
                                    atlas_sequence_for_motion, motions_with_overrides, object_frames, shifted_object)
from pokeroutes.ruletile import ArkTiler, fence_arm_key  # noqa: E402
from pokeroutes.tree_shade import floor_plan_for_shade, resolved_shade, shaded_metatile_pixels  # noqa: E402

GRASS_PIXEL = (100, 200, 100)
OBJECT_PIXEL = (200, 50, 50)


def plain_grass_pixels():
    grass_pixels = np.zeros((16, 16, 3), np.uint8)
    grass_pixels[...] = GRASS_PIXEL
    return grass_pixels


class FakeTilesetPair:
    def __init__(self, pixels_by_metatile):
        self.pixels_by_metatile = pixels_by_metatile

    def render_metatile(self, metatile_id, frame=0):
        return self.pixels_by_metatile[metatile_id].copy()


def test_bop_lifts_only_the_object_and_fills_the_gap_with_ground():
    prop_pixels = plain_grass_pixels()
    prop_pixels[10:14, 6:10] = OBJECT_PIXEL
    lifted_pixels = shifted_object(prop_pixels, plain_grass_pixels(), (0, -1))
    assert (lifted_pixels[9:13, 6:10] == OBJECT_PIXEL).all()
    assert (lifted_pixels[13, 6:10] == GRASS_PIXEL).all()
    assert (lifted_pixels[:, :6] == GRASS_PIXEL).all()


def test_a_two_cell_prop_moves_across_the_seam_between_its_cells():
    upper_cell, lower_cell = plain_grass_pixels(), plain_grass_pixels()
    upper_cell[12:16, 4:12] = OBJECT_PIXEL
    lower_cell[0:6, 4:12] = OBJECT_PIXEL
    tileset_pair = FakeTilesetPair({1: upper_cell, 2: lower_cell})
    frames = object_frames(tileset_pair, ((1,), (2,)), "bop", plain_grass_pixels())
    lifted_lower_cell = frames[2][(0, -1)]
    lifted_upper_cell = frames[1][(0, -1)]
    assert (lifted_lower_cell[0:5, 4:12] == OBJECT_PIXEL).all()
    assert (lifted_lower_cell[5, 4:12] == GRASS_PIXEL).all()
    assert (lifted_upper_cell[11:16, 4:12] == OBJECT_PIXEL).all()


def test_every_motion_starts_at_rest_so_the_atlas_cell_is_the_first_tick():
    for motion, offsets in OFFSETS_IN_PIXELS_FOR_EACH_TICK_BY_MOTION.items():
        assert offsets[0] == (0, 0), motion
        sequence = atlas_sequence_for_motion(7, {offset: 100 for offset in offsets if offset != (0, 0)}, motion)
        assert len(sequence) == len(offsets) - 1


def test_an_override_on_one_cell_moves_the_whole_prop():
    log_cells = PropCells(((713, 714),))
    motions = motions_with_overrides({713: TileMotion("roll"), 714: TileMotion("roll")}, {714: TileMotion("bop", 2.0)},
                                     {713: log_cells, 714: log_cells}, set())
    assert motions[713] == TileMotion("bop", 2.0)
    assert motions[714] == TileMotion("bop", 2.0)


def test_in_game_animated_tiles_can_only_play_or_stop():
    motions = motions_with_overrides({}, {5: TileMotion("dance"), 6: TileMotion("off")}, {}, {5, 6})
    assert motions[5].motion == "game"
    assert motions[6].motion == "off"


def test_route_shade_wins_then_tileset_then_green():
    assert resolved_shade("red", "green", "forest") == "red"
    assert resolved_shade("none", "yellow", "meadow") == "none"
    assert resolved_shade("", "yellow", "forest") == "yellow"
    assert resolved_shade("", "", "forest") == "green"
    assert resolved_shade("meadow", "", "plains") == "none"


def test_shade_recolours_only_the_floor_pixels():
    grass_pixels = plain_grass_pixels()
    grass_pixels[0, :8] = (120, 220, 120)
    tree_pixels = np.zeros((16, 16, 3), np.uint8)
    floor_rows = [0b11] + [0] * 15
    shaded_pixels = shaded_metatile_pixels(tree_pixels, floor_rows, grass_pixels, "red")
    assert tuple(shaded_pixels[0, 0]) != (0, 0, 0)
    assert (shaded_pixels[0, 2:] == 0).all()
    meadow_pixels = shaded_metatile_pixels(tree_pixels, floor_rows, grass_pixels, "none")
    assert tuple(meadow_pixels[0, 0]) == (120, 220, 120)


def test_floor_plan_skips_trees_already_drawn_in_that_shade():
    class_table = {"classes": {"f": {"floor_shade": "green", "floor_masks": {"10": [1] * 16}},
                               "R": {"floor_shade": "yellow", "floor_masks": {"20": [1] * 16}}}}
    green_plan = floor_plan_for_shade(class_table, "green")
    assert set(green_plan) == {20}
    assert green_plan[20][1] == "green"
    assert set(floor_plan_for_shade(class_table, "none")) == {10, 20}


def fence_class_table():
    arm_keys = [n + e + s + w for n in "N-" for e in "E-" for s in "S-" for w in "W-"]
    by_arms = {arm_key: 100 + position for position, arm_key in enumerate(arm_keys)}
    class_table = {"mask_bits": ["N", "NE", "E", "SE", "S", "SW", "W", "NW"],
                   "classes": {"g": {"base": [1], "collision": 0},
                               "c": {"autotile": "fence4", "joins": "c", "by_arms": by_arms, "base": [by_arms["----"]], "collision": 1}}}
    return class_table


def test_fence_pieces_join_in_both_directions_and_a_lone_fence_is_a_post():
    grid = ["ccc", "gcg", "gcg", "ggg"]
    assert fence_arm_key(grid, 0, 0, {"c"}) == "-E--"
    assert fence_arm_key(grid, 1, 0, {"c"}) == "-ESW"
    assert fence_arm_key(grid, 1, 2, {"c"}) == "N---"
    tiler = ArkTiler(fence_class_table(), Path("."))
    layout = tiler.tile(np.array([list("gggg"), list("gcgg"), list("gggg")]))
    by_arms = tiler.C["c"]["by_arms"]
    assert layout.metatile[1, 1] == by_arms["----"]
    assert layout.collision[1, 1] == 1


def test_tile_looks_are_stored_per_tileset_and_bump_the_version(tmp_path, monkeypatch):
    monkeypatch.setattr(tile_looks, "TILE_LOOKS_PATH", tmp_path / "tile-looks.json")
    monkeypatch.setitem(tile_looks._state, "looks", None)
    version_before = tile_looks.version()
    log_cells = PropCells(((713, 714),))
    tile_looks.set_motion("ark", [713], "dance", 1.9, {713: log_cells, 714: log_cells})
    assert tile_looks.version() > version_before
    assert tile_looks.motion_overrides("ark") == {713: TileMotion("dance", 2.0), 714: TileMotion("dance", 2.0)}
    assert tile_looks.motion_overrides("general_fortree") == {}
    tile_looks.set_motion("ark", [713], "default", 1, {713: log_cells, 714: log_cells})
    assert tile_looks.motion_overrides("ark") == {}
    tile_looks.set_tree_shade("ark", "red")
    monkeypatch.setitem(tile_looks._state, "looks", None)
    assert tile_looks.tree_shade_of_tileset("ark") == "red"


class FakeServer:
    @staticmethod
    def is_known_tile_looks_key(tileset_key):
        return tileset_key == "ark"


def test_tile_look_changes_are_checked_before_they_are_stored():
    assert tile_looks.change_problem({"tileset": "nope"}, FakeServer) == "unknown tileset"
    assert "tree_shade" in tile_looks.change_problem({"tileset": "ark", "tree_shade": "purple"}, FakeServer)
    assert "metatiles" in tile_looks.change_problem({"tileset": "ark", "metatiles": [], "motion": "bop"}, FakeServer)
    assert "motion" in tile_looks.change_problem({"tileset": "ark", "metatiles": [3], "motion": "spin"}, FakeServer)
    assert tile_looks.change_problem({"tileset": "ark", "metatiles": [3], "motion": "bop", "speed": 2}, FakeServer) == ""


def test_heavy_props_stand_still_and_light_ones_keep_their_idle_motion():
    from pokeroutes.idle_motion import default_prop_motion
    heavy_prop_motions = [default_prop_motion(prop_name) for prop_name in ("boulder", "pebbles", "log", "fallen_log_0")]
    assert heavy_prop_motions == ["", "", "", ""]
    assert default_prop_motion("mushroom_red") == "bop"
