"""The Tiles tab's paint palette (webui/paint_palette.py) and the exact-tile path of
/api/retile (webui/editor_api.py).

    python3 -m pytest -q webui/tests/test_paint_palette.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent))

import editor_api  # noqa: E402
import paint_palette  # noqa: E402
from fasttile import FastTiler  # noqa: E402
from pokeroutes.autotile import Autotiler  # noqa: E402
from pokeroutes.classify import CLASS_NAMES  # noqa: E402
from pokeroutes.layout import Layout  # noqa: E402
from pokeroutes.ruletile import ArkTiler  # noqa: E402

MODELS = HERE.parent.parent / "pokeroutes" / "models"
GRASS_ID = 1


class LearnedServer:
    def tiler(self, tileset_name):
        return FastTiler(Autotiler.load(MODELS / f"{tileset_name}.json"))


def small_ark_table() -> dict:
    classes = {
        "g": {"name": "grass", "base": [GRASS_ID], "collision": 0},
        "r": {"name": "rock / cliff", "base": [40], "collision": 1},
        "s": {"name": "ledge (jump south)", "autotile": "row", "parts": {"left": 10, "mid": 11, "right": 12, "single": 13}},
        "e": {"name": "ledge (jump east)", "autotile": "column", "parts": {"top": 20, "mid": 21, "bottom": 22, "single": 23}},
        "w": {"name": "water", "base": [50], "collision": 0},
    }
    props = {
        "stump": {"cells": [[70, 71], [72, 73]], "collision": 1},
        "flowers0": {"cells": [[80]], "collision": 0},
        "split_oak": {"cells": [[90, 91], [92, 93]], "collision": 1, "blocked": [[0, 1], [1, 1]]},
    }
    class_of_metatile = {"1": "g", "40": "r", "70": "o", "80": "o", "99": "o"}
    return {"mask_bits": ["N", "NE", "E", "SE", "S", "SW", "W", "NW"], "classes": classes, "props": props,
            "class_of_metatile": class_of_metatile}


def test_learned_palette_lists_every_drawable_class_with_a_real_sample():
    palette = paint_palette.learned_palette(LearnedServer(), "general_fortree")
    codes = [entry["code"] for entry in palette["terrain"]]
    assert "f" in codes and "g" in codes and "o" not in codes
    assert set(codes) | {entry["code"] for entry in palette["unavailable"]} == set(CLASS_NAMES) - {"o"}
    for entry in palette["terrain"]:
        assert len(entry["sample"]) == 4 and all(isinstance(metatile_id, int) for metatile_id in entry["sample"])
        assert entry["name"] != entry["code"]
    ledge_codes = [entry["code"] for entry in palette["terrain"] if entry["group"] == "ledges"]
    assert ledge_codes[0] == "s" and codes[-len(ledge_codes):] == ledge_codes
    assert all(entry["reason"] for entry in palette["unavailable"])
    assert {entry["id"] for entry in palette["objects"]} >= {"tree", "pine", "round_tree", "flowers"}


def test_ark_palette_reads_classes_props_and_collision_from_the_class_table(monkeypatch):
    tiler = ArkTiler(small_ark_table(), Path("."))
    monkeypatch.setattr(paint_palette, "ark_tiler", lambda: tiler)
    palette = paint_palette.ark_palette()
    by_code = {entry["code"]: entry for entry in palette["terrain"]}
    assert set(by_code) == {"g", "r", "s", "e", "w"}
    assert by_code["s"]["group"] == by_code["e"]["group"] == "ledges"
    assert by_code["s"]["sample"][:2] == [11, 11]
    assert by_code["r"]["group"] == "rock" and by_code["w"]["two_wide"]
    assert any(entry["code"] == "d" for entry in palette["unavailable"])
    objects = {entry["id"]: entry for entry in palette["objects"]}
    assert objects["stump"]["classes"] == [["o", "o"], ["o", "o"]]
    assert objects["flowers0"]["classes"] == [["g"]] and objects["flowers0"]["name"] == "flowers 1"
    assert objects["split_oak"]["classes"] == [["g", "o"], ["o", "o"]]
    assert palette["tiles"]["70"] == "o" and palette["tiles"]["80"] == "g" and palette["tiles"]["99"] == "g"
    assert palette["tiles"]["40"] == "r"


def test_object_class_is_only_taken_together_with_an_exact_metatile():
    painted = [[1, 1, "o"], [1, 1, "#70"], [2, 2, "o"], [3, 3, "F"], [4, 4, "?"], [9, 9, "g"]]
    class_cells, metatile_cells = editor_api.split_painted_cells_into_classes_and_metatiles(painted, (5, 5), {"g", "F"})
    assert class_cells == [(1, 1, "o"), (3, 3, "F")]
    assert metatile_cells == [(1, 1, 70)]


def test_exact_metatile_on_a_blocked_class_blocks_the_cell():
    layout = Layout(np.zeros((2, 2), np.uint16), np.zeros((2, 2), np.uint8), np.full((2, 2), 3, np.uint8))
    editor_api.apply_exact_metatile(layout, 0, 0, 70, is_blocked=True)
    editor_api.apply_exact_metatile(layout, 1, 0, 80, is_blocked=False)
    assert layout.metatile[0, 0] == 70 and layout.collision[0, 0] == 1 and layout.elevation[0, 0] == 0
    assert layout.metatile[0, 1] == 80 and layout.collision[0, 1] == 0 and layout.elevation[0, 1] == 3
