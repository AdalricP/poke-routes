"""The Ark rule tiler joins ledges of different directions with corner pieces when its
class table has them, and keeps the straight pieces when it does not. North ledges
('j') tile as rows and are one-way going north.

    python3 -m pytest -q webui/tests/test_ledge_corners.py
"""
from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np

from pokeroutes.classify import CLASS_NAMES, LEDGE_CLASSES
from pokeroutes.generate import can_leave, reachable
from pokeroutes.ruletile import ArkTiler, ledge_arm_key

GRASS_ID = 1
STRAIGHT_PARTS = {
    "s": {"left": 10, "mid": 11, "right": 12, "single": 13},
    "e": {"top": 20, "mid": 21, "bottom": 22, "single": 23},
    "x": {"top": 30, "mid": 31, "bottom": 32, "single": 33},
    "j": {"left": 40, "mid": 41, "right": 42, "single": 43},
}
ROW_LETTERS = ("s", "j")
STRAIGHT_KEYS = {
    "s": {".s.s": "mid", "...s": "right", ".s..": "left", "....": "single"},
    "j": {".j.j": "mid", "...j": "right", ".j..": "left", "....": "single"},
    "e": {"e.e.": "mid", "..e.": "top", "e...": "bottom", "....": "single"},
    "x": {"x.x.": "mid", "..x.": "top", "x...": "bottom", "....": "single"},
}


def corner_keys(direction):
    if direction in ROW_LETTERS:
        vertical_options, horizontal_options = ".ex", "." + direction
    else:
        vertical_options, horizontal_options = "." + direction, ".sj"
    return ["".join(arms) for arms in itertools.product(
        vertical_options, horizontal_options, vertical_options, horizontal_options)]


def corner_id(direction, key):
    if key in STRAIGHT_KEYS[direction]:
        return STRAIGHT_PARTS[direction][STRAIGHT_KEYS[direction][key]]
    return 1000 + 100 * "sexj".index(direction) + corner_keys(direction).index(key)


def class_table(with_corners: bool, letters="sexj") -> dict:
    classes = {"g": {"name": "grass", "base": [GRASS_ID], "collision": 0}}
    for direction in letters:
        kind = "row" if direction in ROW_LETTERS else "column"
        classes[direction] = {"autotile": kind, "parts": STRAIGHT_PARTS[direction], "collision": 0}
        if with_corners:
            classes[direction]["corners"] = {key: corner_id(direction, key) for key in corner_keys(direction)}
    return {"mask_bits": ["N", "NE", "E", "SE", "S", "SW", "W", "NW"], "classes": classes}


def grid_of(rows):
    return np.array([list(row) for row in rows])


L_SHAPED_LEDGE = grid_of((
    "gggggg",
    "gsssgg",
    "gxgggg",
    "gxgggg",
    "gggggg",
))


def test_l_shaped_ledge_gets_corner_ids_not_ground():
    layout = ArkTiler(class_table(with_corners=True), Path(".")).tile(L_SHAPED_LEDGE)
    assert layout.metatile[1, 1] == corner_id("s", ".sx.")
    assert layout.metatile[2, 1] == corner_id("x", "x.x.")
    assert layout.metatile[1, 2] == corner_id("s", ".s.s")
    assert layout.metatile[3, 1] == corner_id("x", "x...")
    assert GRASS_ID not in {int(layout.metatile[y, x]) for y, x in zip(*np.nonzero(L_SHAPED_LEDGE != "g"))}


def test_arm_keys_of_a_t_junction():
    grid = ["gegg",
            "sesg",
            "gegg"]
    assert ledge_arm_key(grid, 1, 1) == "eses"
    assert ledge_arm_key(grid, 0, 1) == ".s.."
    assert ledge_arm_key(grid, 2, 1) == "...s"
    assert ledge_arm_key(grid, 1, 0) == "..e."


def test_parallel_runs_do_not_join():
    stacked_rows = ["ss",
                    "ss"]
    side_by_side_columns = ["ex",
                            "ex"]
    assert ledge_arm_key(stacked_rows, 0, 0) == ".s.."
    assert ledge_arm_key(side_by_side_columns, 0, 0) == "..e."
    assert ledge_arm_key(side_by_side_columns, 1, 1) == "x..."


def test_north_ledge_arms_name_the_row_it_belongs_to():
    plateau_corner = ["jjj",
                      "xgg"]
    assert ledge_arm_key(plateau_corner, 0, 0) == ".jx."
    assert ledge_arm_key(plateau_corner, 0, 1) == "x..."
    assert ledge_arm_key(["ej"], 0, 0) == ".j.."
    assert ledge_arm_key(["sj"], 0, 0) == "...."


def test_painted_north_ledge_run_gets_north_ledge_ids():
    grid = grid_of(("ggggg",
                    "gjjjg",
                    "ggggg"))
    layout = ArkTiler(class_table(with_corners=True), Path(".")).tile(grid)
    assert [int(metatile_id) for metatile_id in layout.metatile[1, 1:4]] == [
        STRAIGHT_PARTS["j"]["left"], STRAIGHT_PARTS["j"]["mid"], STRAIGHT_PARTS["j"]["right"]]
    assert "j" in LEDGE_CLASSES and CLASS_NAMES["j"] == "ledge (north)"


def test_north_ledge_is_one_way_north():
    grid = grid_of(("ggg",
                    "jjj",
                    "ggg"))
    from_south = reachable(grid, [(2, 1)])
    from_north = reachable(grid, [(0, 1)])
    assert from_south[0, 1]
    assert not from_north[2, 1]
    assert can_leave(grid, [(0, 1)])[2, 1]
    assert not can_leave(grid, [(2, 1)])[0, 1]


def test_without_corner_data_the_straight_pieces_stay():
    tiler = ArkTiler(class_table(with_corners=False), Path("."))
    assert not tiler.joins_ledge_corners()
    layout = tiler.tile(L_SHAPED_LEDGE)
    assert layout.metatile[1, 1] == STRAIGHT_PARTS["s"]["left"]
    assert layout.metatile[2, 1] == STRAIGHT_PARTS["x"]["top"]


def test_a_table_without_north_ledges_draws_them_as_south_ledges():
    tiler = ArkTiler(class_table(with_corners=True, letters="sex"), Path("."))
    assert tiler.joins_ledge_corners()
    layout = tiler.tile(grid_of(("gjjg",)))
    assert [int(metatile_id) for metatile_id in layout.metatile[0, 1:3]] == [
        STRAIGHT_PARTS["s"]["left"], STRAIGHT_PARTS["s"]["right"]]
