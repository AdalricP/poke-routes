import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(ROOT))

import playtest_api  # noqa: E402
from pokeroutes import export  # noqa: E402
from pokeroutes.generate import Event, generate  # noqa: E402
from pokeroutes.layout import Layout  # noqa: E402
from pokeroutes.spec import RouteSpec  # noqa: E402


def generated_route_with_sign_and_person():
    route = generate(RouteSpec.load(ROOT / "examples/bernsteinweg.yaml"))
    route.events.append(Event("sign", 1, 1, {"type": "ruin"}))
    route.events.append(Event("npc", 2, 1, {"role": "hiker"}))
    return route


def fake_server_holding(route, layout):
    fake_server = SimpleNamespace(_results={"route-id": (route, layout)}, G=lambda: SimpleNamespace(export=export))
    return fake_server


def test_route_data_carries_collision_rows_and_export_texts():
    route = generated_route_with_sign_and_person()
    layout = Layout.blank(route.w, route.h)
    layout.collision[0, 0] = 1
    playtest_data = playtest_api.playtest_route_data(fake_server_holding(route, layout), "route-id")

    assert len(playtest_data["collision"]) == route.h
    assert playtest_data["collision"][0][0] == "1"
    assert set("".join(playtest_data["collision"])) <= {"0", "1"}
    texts_by_cell = {(text_entry["x"], text_entry["y"]): text_entry["text"] for text_entry in playtest_data["texts"]}
    assert texts_by_cell[(1, 1)] == export.LORE_TEXT["ruin"]
    assert texts_by_cell[(1, 2)] == export.NPC_TEXT["hiker"]


def test_unknown_route_id_has_no_data():
    route = generated_route_with_sign_and_person()
    assert playtest_api.playtest_route_data(fake_server_holding(route, Layout.blank(route.w, route.h)), "other-id") is None


def test_unknown_pose_has_no_sheet():
    assert playtest_api.sheet_png_for_pose("dancing", "learned") is None


def test_collision_rows_follow_layout_shape():
    layout = Layout.blank(3, 2)
    layout.collision[:] = np.array([[0, 1, 0], [1, 1, 0]])
    assert playtest_api.collision_rows_of_layout(layout) == ["010", "110"]
