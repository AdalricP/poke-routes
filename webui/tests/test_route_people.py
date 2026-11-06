"""People on routes: Ark routes get the Arklarna route-people sprites (new OBJ_EVENT_GFX_ARK_ROUTE_*
ids) in the export and the Routes view; Emerald-tiled routes keep the stock sprites.

    python3 -m pytest -q webui/tests/test_route_people.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(ROOT))

import route_items  # noqa: E402
from pokeroutes import route_people  # noqa: E402
from pokeroutes.export import write_emerald  # noqa: E402
from pokeroutes.generate import generate  # noqa: E402
from pokeroutes.layout import Layout  # noqa: E402
from pokeroutes.spec import RouteSpec  # noqa: E402


def npc_graphics(tmp_path, ark_people):
    route = generate(RouteSpec.load(ROOT / "examples/bernsteinweg.yaml"))
    roles = {(e.x, e.y): e.data.get("role") for e in route.events if e.kind == "npc"}
    write_emerald(route, Layout.blank(route.w, route.h), tmp_path, "ark_outdoor", "ark_woodland", ark_people=ark_people)
    objects = json.loads((tmp_path / "data/maps" / route.spec.id / "map.json").read_text())["object_events"]
    return [(roles[(o["x"], o["y"])], o["graphics_id"]) for o in objects if (o["x"], o["y"]) in roles]


def test_export_uses_ark_route_people_only_when_asked(tmp_path):
    ark = npc_graphics(tmp_path / "ark", True)
    stock = npc_graphics(tmp_path / "stock", False)
    assert ark and stock
    for role, graphics_id in ark:
        assert graphics_id == route_people.ark_gfx(route_people.ROLE_SHEET[role])
    assert not any(graphics_id.startswith(route_people.ARK_GFX_PREFIX) for _role, graphics_id in stock)


def test_rom_has_ark_people_reads_the_constants(tmp_path):
    assert not route_people.rom_has_ark_people(tmp_path)
    header = tmp_path / "include/constants/event_objects.h"
    header.parent.mkdir(parents=True)
    header.write_text("    OBJ_EVENT_GFX_ARK_ROUTE_HIKER,\n")
    assert route_people.rom_has_ark_people(tmp_path)
    assert route_people.npc_gfx("hiker", False) == "OBJ_EVENT_GFX_HIKER"
    assert route_people.npc_gfx("story_blocker", True) == "OBJ_EVENT_GFX_ARK_ROUTE_BLOCKER"
    assert route_people.npc_gfx("nobody", True) == "OBJ_EVENT_GFX_MAN_3"


def test_routes_view_sprite_follows_the_tiler(tmp_path, monkeypatch):
    people = tmp_path / route_items.PICS / "people"
    (people / "arklarna/route").mkdir(parents=True)
    Image.new("P", (144, 32), 1).save(people / "arklarna/route/hiker.png")
    Image.new("P", (144, 32), 2).save(people / "hiker.png")
    monkeypatch.setattr(route_items, "ark_roots", lambda: [tmp_path])
    monkeypatch.setattr(route_items, "emerald_roots", lambda: [])
    assert route_items.sprite_file("npc_hiker", "ark")[0].name == "hiker.png"
    assert "arklarna" in str(route_items.sprite_file("npc_hiker", "ark")[0])
    assert "arklarna" not in str(route_items.sprite_file("npc_hiker", "learned")[0])
    assert {"npc_hiker", "trainer", "trainer_f", "story_blocker"} <= set(route_items.SPRITE_SOURCES)
