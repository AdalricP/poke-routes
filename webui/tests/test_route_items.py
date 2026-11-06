"""Items placed by hand in the Routes view reach the Porymap export: a visible ball as an
item-ball object event, a hidden item as a hidden_item bg_event; scene.json keeps both.

    python3 -m pytest -q webui/tests/test_route_items.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(ROOT))

import glitter  # noqa: E402
import route_items  # noqa: E402
from pokeroutes.export import scene_dict, write_emerald  # noqa: E402
from pokeroutes.generate import generate  # noqa: E402
from pokeroutes.layout import Layout  # noqa: E402
from pokeroutes.spec import RouteSpec  # noqa: E402


def small_route():
    return generate(RouteSpec.load(ROOT / "examples/bernsteinweg.yaml"))


def free_walkable_cells(route, count):
    taken = {(event.y, event.x) for event in route.events}
    cells = [(int(y), int(x)) for y, x in zip(*np.nonzero(np.isin(route.cls, list("gp")))) if (y, x) not in taken]
    return cells[len(cells) // 2: len(cells) // 2 + count]


def blocked_cell(route):
    y, x = np.argwhere(route.cls == "f")[0]
    return int(y), int(x)


def exported_map(route, out: Path) -> dict:
    write_emerald(route, Layout.blank(route.w, route.h), out, "general", "fortree")
    return json.loads((out / "data/maps" / route.spec.id / "map.json").read_text())


def test_hand_placed_items_are_exported(tmp_path):
    route = small_route()
    (ball_y, ball_x), (hidden_y, hidden_x) = free_walkable_cells(route, 2)
    tree_y, tree_x = blocked_cell(route)
    result = route_items.place_hand_items(route, [
        {"kind": "item_ball", "x": ball_x, "y": ball_y, "item": "ITEM_RARE_CANDY"},
        {"kind": "hidden_item", "x": hidden_x, "y": hidden_y, "item": "ITEM_NUGGET"},
        {"kind": "item_ball", "x": tree_x, "y": tree_y, "item": "ITEM_POTION"},
    ])
    assert len(result["placed"]) == 2
    assert "not walkable" in result["skipped"][0]["reason"]

    exported = exported_map(route, tmp_path)
    balls = [event for event in exported["object_events"]
             if event["graphics_id"] == "OBJ_EVENT_GFX_ITEM_BALL" and (event["x"], event["y"]) == (ball_x, ball_y)]
    assert len(balls) == 1
    hidden = [event for event in exported["bg_events"] if event["type"] == "hidden_item" and event["item"] == "ITEM_NUGGET"]
    assert (hidden_x, hidden_y) in [(event["x"], event["y"]) for event in hidden]
    scripts = (tmp_path / "data/maps" / route.spec.id / "scripts.inc").read_text()
    assert "finditem ITEM_RARE_CANDY" in scripts

    hidden_cell_objects = [event for event in exported["object_events"] if (event["x"], event["y"]) == (hidden_x, hidden_y)]
    assert hidden_cell_objects == []

    hand_events = [event for event in scene_dict(route)["events"] if event.get("hand")]
    assert {(event["kind"], event["item"]) for event in hand_events} == {("item_ball", "ITEM_RARE_CANDY"), ("hidden_item", "ITEM_NUGGET")}


def test_placing_again_replaces_the_hand_items_and_keeps_generated_ones():
    route = small_route()
    generated_count = len(route.events)
    (y, x), = free_walkable_cells(route, 1)
    route_items.place_hand_items(route, [{"kind": "item_ball", "x": x, "y": y, "item": "ITEM_POTION"}])
    route_items.place_hand_items(route, [{"kind": "hidden_item", "x": x, "y": y, "item": "ITEM_ETHER"}])
    assert len(route.events) == generated_count + 1
    assert route.events[-1].kind == "hidden_item"
    result = route_items.place_hand_items(route, [{"kind": "item_ball", "x": x, "y": y, "item": "potion"}])
    assert result["placed"] == [] and len(route.events) == generated_count


def test_glitter_frames_are_small_star_sprites():
    strip = glitter.glitter_strip()
    assert strip.size == (16 * len(glitter.FRAME_SHAPES), 16)
    for frame_index in range(len(glitter.FRAME_SHAPES)):
        frame = strip.crop((frame_index * 16, 0, frame_index * 16 + 16, 16))
        assert frame.getpixel((glitter.CENTRE, glitter.CENTRE)) == glitter.CORE_WHITE
        assert frame.getpixel((0, 0))[3] == 0


def test_hidden_items_leave_nothing_visible_in_the_map(tmp_path):
    route = small_route()
    layout = Layout.blank(route.w, route.h)
    write_emerald(route, layout, tmp_path / "before", "general", "fortree")
    (y, x), = free_walkable_cells(route, 1)
    route_items.place_hand_items(route, [{"kind": "hidden_item", "x": x, "y": y, "item": "ITEM_NUGGET"}])
    write_emerald(route, layout, tmp_path / "after", "general", "fortree")
    map_dir, layout_dir = Path("data/maps") / route.spec.id, Path("data/layouts") / route.spec.id
    before = json.loads((tmp_path / "before" / map_dir / "map.json").read_text())
    after = json.loads((tmp_path / "after" / map_dir / "map.json").read_text())
    assert after["object_events"] == before["object_events"]
    added = [event for event in after["bg_events"] if event not in before["bg_events"]]
    assert [(event["type"], event["x"], event["y"]) for event in added] == [("hidden_item", x, y)]
    for name in ("map.bin", "border.bin"):
        assert (tmp_path / "after" / layout_dir / name).read_bytes() == (tmp_path / "before" / layout_dir / name).read_bytes()
