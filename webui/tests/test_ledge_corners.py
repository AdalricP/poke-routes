"""The Ark rule tiler joins ledges of different directions with corner pieces when its
class table has them, and keeps the straight pieces when it does not.

    python3 -m pytest -q webui/tests/test_ledge_corners.py
"""
from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np

from pokeroutes.ruletile import ArkTiler, ledge_arm_key

GRASS_ID = 1
STRAIGHT_PARTS = {
    "s": {"left": 10, "mid": 11, "right": 12, "single": 13},
    "e": {"top": 20, "mid": 21, "bottom": 22, "single": 23},
    "x": {"top": 30, "mid": 31, "bottom": 32, "single": 33},
}


def corner_keys(direction):
    vertical_options = ".ex" if direction == "s" else "." + direction
    return ["".join(arms) for arms in itertools.product(vertical_options, ".s", vertical_options, ".s")]


def corner_id(direction, key):
    return 1000 + 100 * "sex".index(direction) + corner_keys(direction).index(key)


def class_table(with_corners: bool) -> dict:
    classes = {"g": {"name": "grass", "base": [GRASS_ID], "collision": 0}}
    for direction, kind in (("s", "row"), ("e", "column"), ("x", "column")):
        classes[direction] = {"autotile": kind, "parts": STRAIGHT_PARTS[direction], "collision": 0}
        if with_corners:
            classes[direction]["corners"] = {key: corner_id(direction, key) for key in corner_keys(direction)}
    return {"mask_bits": ["N", "NE", "E", "SE", "S", "SW", "W", "NW"], "classes": classes}


L_SHAPED_LEDGE = np.array([list(row) for row in (
    "gggggg",
    "gsssgg",
    "gxgggg",
    "gxgggg",
    "gggggg",
)])


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


def test_without_corner_data_the_straight_pieces_stay():
    tiler = ArkTiler(class_table(with_corners=False), Path("."))
    assert not tiler.joins_ledge_corners()
    layout = tiler.tile(L_SHAPED_LEDGE)
    assert layout.metatile[1, 1] == STRAIGHT_PARTS["s"]["left"]
    assert layout.metatile[2, 1] == STRAIGHT_PARTS["x"]["top"]
