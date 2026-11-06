"""Routes drawn by hand (pokeroutes/region/drawn.py) through the edit module and the
editor API: laying, sea stretches, refusals, recompute, save and load.

    python3 -m pytest -q webui/tests/test_region_drawn.py
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import pytest

from pokeroutes.region import drawn, edit
from pokeroutes.region.world import City, World

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import editor_api  # noqa: E402


def flat_world(width: int = 90, height: int = 60) -> World:
    w = World(seed=1, W=width, H=height, preset="generic", geography="test")
    w.land = np.ones((height, width), dtype=bool)
    w.land[:, 56:64] = False
    w.height = np.where(w.land, 0.1, -0.2)
    w.landmass = np.where(w.land, 1, 0).astype(np.int8)
    w.landmass[:, 64:] = 2
    w.sea = ~w.land
    w.lake = np.zeros((height, width), dtype=bool)
    w.lake[40:46, 20:27] = True
    w.river = np.zeros((height, width), dtype=bool)
    w.flow = np.zeros((height, width))
    w.recv = np.full(height * width, -1)
    w.temp = np.zeros((height, width))
    w.moist = np.zeros((height, width))
    w.biome = np.where(w.land, "m", "~").astype("<U1")
    w.biome[w.lake] = "o"
    w.tiers = np.zeros((height, width), dtype=np.int8)
    w.lagoon = np.zeros((height, width), dtype=bool)
    w.cities = [
        City("ahlen", "Ahlen", [6, 20, 16, 29], "", "", "landmass 1", "town"),
        City("bertsch", "Bertsch", [40, 20, 50, 29], "", "", "landmass 1", "town"),
        City("corve", "Corve", [72, 20, 82, 29], "", "", "landmass 2", "town"),
        City("dorn", "Dorn", [6, 48, 16, 57], "", "", "landmass 1", "town"),
    ]
    return w


def flat_doc() -> dict:
    doc = edit.world_to_doc(flat_world())
    doc["routes"] = []
    return doc


def draw(doc, waypoints, legs, source, target, replace=None):
    return edit.draw_route(doc, waypoints, legs, source, target, replace)


def routes_of_line(doc, line_name):
    return [r for r in doc["routes"] if r.get("link") == line_name]


def test_corners_follow_the_legs():
    assert drawn.corners_of_line([[0, 0], [5, 3]], ["h"]) == [(0, 0), (5, 0), (5, 3)]
    assert drawn.corners_of_line([[0, 0], [5, 3]], ["v"]) == [(0, 0), (0, 3), (5, 3)]
    assert drawn.corners_of_line([[0, 0], [4, 0], [4, 0]], ["h", "h"]) == [(0, 0), (4, 0)]


def test_cells_of_corners_are_four_connected():
    cells = drawn.cells_of_corners([(0, 0), (3, 0), (3, -2)])
    assert cells == [(0, 0), (1, 0), (2, 0), (3, 0), (3, -1), (3, -2)]


def test_short_inner_sea_stretch_counts_as_land():
    w = flat_world()
    w.land[30, 30:32] = False
    cells = [(x, 30) for x in range(26, 36)]
    assert drawn.modes_along(w, cells) == ["land"] * len(cells)


def test_a_straight_line_between_two_towns_becomes_pinned_routes_with_specs():
    out = draw(flat_doc(), [[11, 24], [45, 24]], ["h"], {"city": "ahlen"}, {"city": "bertsch"})
    routes = routes_of_line(out, out["line"])
    assert routes and [r["name"] for r in routes] == out["added"]
    assert all(r["pinned"] and r["drawn"] for r in routes)
    exits_to = {e["to"] for r in routes for e in r["exits"]}
    assert {"Ahlen", "Bertsch"} <= exits_to
    assert all("error" not in r["spec"] for r in routes)
    assert all(r["path"] and all(y == 24 for _, y in r["path"]) for r in routes)
    line = next(l for l in out["links"] if l["name"] == out["line"])
    assert line["kind"] == "drawn" and line["waypoints"] == [[11, 24], [45, 24]]
    spec = edit.full_spec(out, routes[0]["id"])
    assert spec["exits"] and spec["region"]


def test_an_l_shaped_line_bends_once():
    out = draw(flat_doc(), [[11, 24], [11, 52]], ["v"], {"city": "ahlen"}, {"city": "dorn"})
    path = [tuple(c) for r in routes_of_line(out, out["line"]) for c in r["path"]]
    assert all(x == 11 for x, _ in path)
    bent = draw(flat_doc(), [[11, 24], [30, 33], [11, 52]], ["h", "v"], {"city": "ahlen"}, {"city": "dorn"})
    assert len({tuple(c) for r in routes_of_line(bent, bent["line"]) for c in r["path"]}) > len(path)


def test_a_line_over_the_sea_gets_a_sea_route():
    out = draw(flat_doc(), [[45, 24], [77, 24]], ["h"], {"city": "bertsch"}, {"city": "corve"})
    modes = {r["mode"] for r in routes_of_line(out, out["line"])}
    assert "sea" in modes


def test_a_line_through_a_lake_is_refused():
    with pytest.raises(ValueError, match="lake"):
        draw(flat_doc(), [[11, 24], [25, 52], [11, 52]], ["h", "h"], {"city": "ahlen"}, {"city": "dorn"})


def test_a_line_through_another_town_is_refused():
    with pytest.raises(ValueError, match="Bertsch"):
        draw(flat_doc(), [[11, 24], [77, 24]], ["h"], {"city": "ahlen"}, {"city": "corve"})


def test_a_line_can_end_on_a_route():
    first = draw(flat_doc(), [[11, 24], [45, 24]], ["h"], {"city": "ahlen"}, {"city": "bertsch"})
    route = routes_of_line(first, first["line"])[0]
    x0, y0, x1, y1 = route["rect"]
    middle_x = (x0 + x1) // 2
    out = draw(first, [[11, 52], [middle_x, 52], [middle_x, (y0 + y1) // 2]], ["h", "v"],
               {"city": "dorn"}, {"route": route["name"]})
    joined = next(r for r in out["routes"] if r["name"] == route["name"])
    assert any(e["to"] in out["added"] for e in joined["exits"])


def test_redrawing_a_line_replaces_its_routes():
    first = draw(flat_doc(), [[11, 24], [45, 24]], ["h"], {"city": "ahlen"}, {"city": "bertsch"})
    again = draw(first, [[11, 24], [28, 12], [45, 24]], ["v", "v"], {"city": "ahlen"}, {"city": "bertsch"},
                 replace=first["line"])
    assert again["line"] == first["line"]
    assert len([l for l in again["links"] if l["name"] == first["line"]]) == 1
    assert all(r["path"][0][1] != 24 or r["path"][-1][1] != 24 for r in routes_of_line(again, first["line"]))


def test_recompute_keeps_drawn_routes_and_lays_no_second_road_between_the_same_towns():
    out = draw(flat_doc(), [[11, 24], [45, 24]], ["h"], {"city": "ahlen"}, {"city": "bertsch"})
    again = edit.recompute(out, ["routes"])
    kept = routes_of_line(again, out["line"])
    assert [r["rect"] for r in kept] == [r["rect"] for r in routes_of_line(out, out["line"])]
    assert not any(l["name"] in ("Ahlen-Bertsch", "Bertsch-Ahlen") for l in again["links"])
    assert any(l["name"] == out["line"] and l["kind"] == "drawn" for l in again["links"])


class FakeRequest:
    def __init__(self, body: dict):
        raw = json.dumps(body).encode()
        self.headers = {"Content-Type": "application/json", "Content-Length": str(len(raw))}
        self.rfile = io.BytesIO(raw)
        self.reply = None

    def _json(self, value, status=200):
        self.reply = (status, value)


def call_api(path: str, body: dict):
    request = FakeRequest(body)
    editor_api.post(request, urlparse(path), None)
    status, value = request.reply
    assert status == 200, value
    return value


def test_drawn_route_saves_and_loads_through_the_editor_api(tmp_path, monkeypatch):
    monkeypatch.setattr(editor_api, "OUT", tmp_path)
    out = call_api("/api/editor/draw-route", {"doc": flat_doc(), "waypoints": [[11, 24], [45, 24]], "legs": ["h"],
                                              "src": {"city": "ahlen"}, "dst": {"city": "bertsch"}})
    call_api("/api/editor/save", {"doc": out, "name": "drawn-test"})
    assert list((tmp_path / "drawn-test" / "routes").glob("*.yaml"))
    loaded = call_api("/api/editor/load", {"name": "drawn-test"})
    line = next(l for l in loaded["links"] if l["name"] == out["line"])
    assert line["waypoints"] == [[11, 24], [45, 24]]
    assert [r["rect"] for r in routes_of_line(loaded, out["line"])] == [r["rect"] for r in routes_of_line(out, out["line"])]
    assert all(r.get("drawn") for r in routes_of_line(loaded, out["line"]))


def test_drawn_route_problems_come_back_as_api_errors():
    request = FakeRequest({"doc": flat_doc(), "waypoints": [[11, 24], [25, 52], [11, 52]], "legs": ["h", "h"],
                           "src": {"city": "ahlen"}, "dst": {"city": "dorn"}})
    editor_api.post(request, urlparse("/api/editor/draw-route"), None)
    status, value = request.reply
    assert status == 400 and "lake" in value["error"]
