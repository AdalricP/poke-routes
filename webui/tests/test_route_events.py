"""People, signs and events: the rule model, and what reaches the Porymap export
(map.json object/bg/coord events, scripts.inc map scripts, the flags header).

    python3 -m pytest -q webui/tests/test_route_events.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(ROOT))

from pokeroutes import hand_authored_export, people_and_signs, route_events  # noqa: E402
from pokeroutes.export import write_emerald  # noqa: E402
from pokeroutes.generate import generate  # noqa: E402
from pokeroutes.layout import Layout  # noqa: E402
from pokeroutes.spec import RouteSpec  # noqa: E402

GYM_LEADER = {"kind": "person", "id": "p1", "name": "Leader Brock", "sprite": "OBJ_EVENT_GFX_ARK_ROUTE_HIKER",
              "facing": "down", "movement": "still", "text": "I'm the toughest here.\n\nShow me what you've got!",
              "trainer": {"trainer_class": "TRAINER_CLASS_LEADER", "team": [{"species": "Geodude", "level": 12},
                                                                             {"species": "SPECIES_ONIX", "level": 14}],
                          "defeat_text": "You win.", "after_text": "The boulder up north is gone now."}}


def generated_route(example_name: str):
    route = generate(RouteSpec.load(ROOT / f"examples/{example_name}.yaml"))
    return route


def free_walkable_cells(route, count):
    taken = {(int(event.y), int(event.x)) for event in route.events}
    cells = [(int(x), int(y)) for y, x in zip(*np.nonzero(np.isin(route.cls, list("gp")))) if (y, x) not in taken]
    chosen_cells = cells[len(cells) // 3: len(cells) // 3 + count]
    return chosen_cells


def exported_files(route, out: Path) -> tuple:
    write_emerald(route, Layout.blank(route.w, route.h), out, "general", "fortree", ark_people=False)
    map_json = json.loads((out / "data/maps" / route.spec.id / "map.json").read_text())
    scripts = (out / "data/maps" / route.spec.id / "scripts.inc").read_text()
    return map_json, scripts


def placed(route, raw_entries):
    generated_cells = {(int(event.y), int(event.x)) for event in route.events if event.kind in ("npc", "sign")}
    other_cells = {(int(event.y), int(event.x)) for event in route.events if event.kind not in ("npc", "sign")}
    placement = people_and_signs.place_entries(raw_entries, route.cls, generated_cells, other_cells)
    route.__dict__[hand_authored_export.HAND_ENTRIES_KEY] = placement.placed
    return placement


def test_rule_model_normalises_and_rejects():
    rules, rejected = route_events.normalized_rules([
        {"when": {"type": "defeat_trainer", "route": "Route 3", "person": "p1"},
         "then": {"type": "remove_object", "route": "Route 4", "x": "5", "y": 6}},
        {"when": {"type": "field_move", "route": "Route 4", "x": 1, "y": 2, "move": "Rock Smash"},
         "then": [{"type": "add_objects", "route": "Route 5", "cells": [[1, 1], [1, 1], [2, 1]], "object": "boulder"},
                  {"type": "set_var", "var": "var_story", "value": 3}]},
        {"when": {"type": "teleport"}, "then": [{"type": "set_flag", "flag": "FLAG_X"}]},
        {"when": {"type": "step_on", "route": "Route 4", "x": 1, "y": 1}, "then": []},
    ])
    assert [rule["id"] for rule in rules] == ["e1", "e2"]
    assert rules[0]["then"][0] == {"type": "remove_object", "route": "Route 4", "x": 5, "y": 6}
    assert rules[1]["when"]["move"] == "rock_smash"
    assert rules[1]["then"][0]["cells"] == [[1, 1], [2, 1]]
    assert rules[1]["then"][1]["var"] == "VAR_STORY"
    assert "unknown trigger" in rejected[0]["reason"] and "at least one effect" in rejected[1]["reason"]
    assert route_events.describe_rule(rules[0], {("route 3", "p1"): "Brock"}) == \
        "When Brock on Route 3 is beaten: remove whatever is at 5,6 on Route 4"


def test_rule_list_edits_are_pure():
    rules, first_rule = route_events.rules_with_rule_added([], {"when": {"type": "story_beat", "beat": "Met the professor"},
                                                               "then": [{"type": "set_flag", "flag": "FLAG_MET_PROF"}]})
    rules_after_edit, edited_rule = route_events.rules_with_rule_replaced(
        rules, first_rule["id"], {"when": {"type": "story_beat", "beat": "Left town"}, "then": [{"type": "set_flag", "flag": "FLAG_LEFT"}]})
    assert rules[0]["when"]["beat"] == "Met the professor" and edited_rule["when"]["beat"] == "Left town"
    assert route_events.rules_without_rule(rules_after_edit, "e1") == []
    with pytest.raises(route_events.RuleRejected):
        route_events.rules_without_rule(rules_after_edit, "e9")


def test_people_and_signs_are_validated_and_exported(tmp_path):
    route = generated_route("bernsteinweg")
    (person_x, person_y), (sign_x, sign_y) = free_walkable_cells(route, 2)
    generated_sign = next(event for event in route.events if event.kind == "sign")
    tree_y, tree_x = map(int, np.argwhere(route.cls == "f")[0])
    placement = placed(route, [
        {**GYM_LEADER, "x": person_x, "y": person_y},
        {"kind": "sign", "x": sign_x, "y": sign_y, "text": "BERNSTEINWEG\nNorth: the lake"},
        {"kind": "sign", "x": int(generated_sign.x), "y": int(generated_sign.y), "text": "Edited generated sign"},
        {"kind": "person", "x": tree_x, "y": tree_y},
        {"kind": "person", "x": person_x, "y": person_y + 1, "sprite": "not a sprite"},
    ])
    assert len(placement.placed) == 3
    assert "not walkable" in placement.skipped[0]["reason"] and "OBJ_EVENT_GFX" in placement.skipped[1]["reason"]

    map_json, scripts = exported_files(route, tmp_path)
    trainer = next(obj for obj in map_json["object_events"] if (obj["x"], obj["y"]) == (person_x, person_y))
    assert trainer["trainer_type"] == "TRAINER_TYPE_NORMAL" and trainer["graphics_id"] == "OBJ_EVENT_GFX_ARK_ROUTE_HIKER"
    assert "trainerbattle_single TRAINER_PR_BERNSTEINWEG_LEADER_BROCK" in scripts
    assert '.string "Show me what you\'ve got!$"' in scripts
    sign_scripts = [bg["script"] for bg in map_json["bg_events"] if (bg["x"], bg["y"]) == (int(generated_sign.x), int(generated_sign.y))]
    assert len(sign_scripts) == 1 and "Edited generated sign" in scripts
    party = (tmp_path / "trainers_poke_routes.party").read_text()
    assert "=== TRAINER_PR_BERNSTEINWEG_LEADER_BROCK ===" in party and "SPECIES_GEODUDE\nLevel: 12" in party


def demo_rules(route_a, route_b, route_x, route_y, boulder, button, rock_cells):
    rules, rejected = route_events.normalized_rules([
        {"when": {"type": "defeat_trainer", "route": route_a, "person": "p1"},
         "then": [{"type": "remove_object", "route": route_b, "x": boulder[0], "y": boulder[1]}]},
        {"when": {"type": "press_button", "route": route_x, "x": button[0], "y": button[1]},
         "then": [{"type": "add_objects", "route": route_y, "cells": rock_cells, "object": "rock"}]},
    ])
    assert not rejected
    return rules


def test_demo_gym_leader_opens_a_boulder_and_a_button_drops_rocks(tmp_path):
    route_a, route_b, route_x = generated_route("bernsteinweg"), generated_route("erlenbruch"), generated_route("kiefernheide")
    route_y = route_a
    leader_x, leader_y = free_walkable_cells(route_a, 1)[0]
    placed(route_a, [{**GYM_LEADER, "x": leader_x, "y": leader_y}])
    boulder = next(event for event in route_b.events if event.kind == "strength_boulder")
    button_x, button_y = free_walkable_cells(route_x, 1)[0]
    rock_cells = [list(cell) for cell in free_walkable_cells(route_y, 6)[2:5]]
    rules = demo_rules(route_a.spec.name, route_b.spec.name, route_x.spec.name, route_y.spec.name,
                       (int(boulder.x), int(boulder.y)), (button_x, button_y), rock_cells)
    for route in (route_a, route_b, route_x):
        route.__dict__[hand_authored_export.EVENT_RULES_KEY] = rules

    map_a, scripts_a = exported_files(route_a, tmp_path / "a")
    map_b, scripts_b = exported_files(route_b, tmp_path / "b")
    map_x, scripts_x = exported_files(route_x, tmp_path / "x")

    assert "Bernsteinweg_EventScript_Person_P1_Defeated::\n\tcall Bernsteinweg_EventScript_PrEventE1\n" in scripts_a
    assert "setflag FLAG_PR_EVENT_E1" in scripts_a

    boulder_object = next(obj for obj in map_b["object_events"] if (obj["x"], obj["y"]) == (int(boulder.x), int(boulder.y)))
    assert boulder_object["flag"].startswith("FLAG_TEMP_")
    assert "map_script MAP_SCRIPT_ON_TRANSITION, Erlenbruch_OnTransition" in scripts_b
    assert "call_if_set FLAG_PR_EVENT_E1, Erlenbruch_EventScript_PrEventE1Objects1" in scripts_b
    assert f"Erlenbruch_EventScript_PrEventE1Objects1:\n\tsetflag {boulder_object['flag']}\n\treturn" in scripts_b

    button_events = [bg for bg in map_x["bg_events"] if (bg["x"], bg["y"]) == (button_x, button_y)]
    assert button_events and button_events[0]["script"] == "Kiefernheide_EventScript_PrEventE2_Button"
    assert "setflag FLAG_PR_EVENT_E2" in scripts_x

    rocks = [obj for obj in map_a["object_events"] if [obj["x"], obj["y"]] in rock_cells]
    assert len(rocks) == 3 and all(rock["graphics_id"] == "OBJ_EVENT_GFX_BREAKABLE_ROCK" for rock in rocks)
    assert "call_if_unset FLAG_PR_EVENT_E2, Bernsteinweg_EventScript_PrEventE2Objects1" in scripts_a

    flags_header = (tmp_path / "a" / hand_authored_export.FLAGS_HEADER_PATH).read_text()
    assert "#define FLAG_PR_EVENT_E1 FLAG_UNUSED_0x" in flags_header and "#define FLAG_PR_EVENT_E2 FLAG_UNUSED_0x" in flags_header
    assert "FLAG_PR_EVENT_E1" in json.loads((tmp_path / "b" / "flags.json").read_text())


def test_same_map_effects_happen_on_the_spot(tmp_path):
    route = generated_route("erlenbruch")
    boulder = next(event for event in route.events if event.kind == "strength_boulder")
    step_x, step_y = free_walkable_cells(route, 1)[0]
    rules, _ = route_events.normalized_rules([
        {"when": {"type": "step_on", "route": route.spec.name, "x": step_x, "y": step_y},
         "then": [{"type": "remove_object", "route": route.spec.name, "x": int(boulder.x), "y": int(boulder.y)},
                  {"type": "change_tiles", "route": route.spec.name, "cells": [[step_x + 1, step_y]], "to": "path"}]},
    ])
    route.__dict__[hand_authored_export.EVENT_RULES_KEY] = rules
    map_json, scripts = exported_files(route, tmp_path)
    boulder_local_id = next(index + 1 for index, obj in enumerate(map_json["object_events"])
                            if (obj["x"], obj["y"]) == (int(boulder.x), int(boulder.y)))
    fire_script = scripts.split("Erlenbruch_EventScript_PrEventE1::")[1].split("_Done:")[0]
    assert f"removeobject {boulder_local_id}" in fire_script
    assert f"setmetatile {step_x + 1}, {step_y}," in fire_script and "special DrawWholeMapView" in fire_script
    assert "map_script MAP_SCRIPT_ON_LOAD, Erlenbruch_OnLoad" in scripts
    assert map_json["coord_events"][-1]["script"] == "Erlenbruch_EventScript_PrEventE1_Step"


def test_agent_tools_edit_events_and_people(tmp_path):
    import route_tools

    events_file = f"out/agent/test-events-{tmp_path.name}.json"
    generated = route_tools.generate_route({"spec": "examples/erlenbruch.yaml"})
    route_id = generated["id"]
    _spec, route, _tiler = route_tools._routes[route_id]
    boulder = next(event for event in route.events if event.kind == "strength_boulder")
    (leader_x, leader_y), = free_walkable_cells(route, 1)
    placed_people = route_tools.place_people_and_signs({"id": route_id, "entries": [{**GYM_LEADER, "x": leader_x, "y": leader_y}]})
    assert placed_people["placed"][0]["id"] == "p1"
    saved = route_tools.save_event({"file": events_file, "rule": {
        "when": {"type": "defeat_trainer", "route": route.spec.name, "person": "p1"},
        "then": [{"type": "remove_object", "route": route.spec.name, "x": int(boulder.x), "y": int(boulder.y)}]}})
    assert saved["saved"]["id"] == "e1" and saved["count"] == 1
    assert route_tools.list_events({"file": events_file})["events"][0]["description"].startswith("When p1 on Erlenbruch is beaten")
    applied = route_tools.apply_events({"id": route_id, "file": events_file})
    scripts = Path(applied["scripts"]).read_text()
    assert "call_if_set FLAG_PR_EVENT_E1" in scripts and "removeobject" in scripts
    assert route_tools.delete_event({"file": events_file, "id": "e1"})["count"] == 0
    (ROOT / events_file).unlink()


def test_multiline_text_boxes():
    block = people_and_signs.multiline_text_block("Label", "First line\nsecond line\nthird\n\nNew box")
    assert block == ('Label:\n\t.string "First line\\n"\n\t.string "second line\\p"\n\t.string "third\\p"\n'
                     '\t.string "New box$"\n\n')
