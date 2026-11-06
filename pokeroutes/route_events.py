"""Events: "when <trigger> then <effects>" rules across the routes of a region, and how
one route's share of them becomes pokeemerald-expansion flags, map scripts and object
visibility.

    {"id": "e1", "when": {"type": "defeat_trainer", "route": "Route 3", "person": "p1"},
     "then": [{"type": "remove_object", "route": "Route 4", "x": 12, "y": 4}]}

Every rule owns one flag, FLAG_PR_EVENT_<ID>, set once when the trigger happens. Effects
read it: objects get a visibility flag set by the map's ON_TRANSITION script, tile changes
are applied by its ON_LOAD script, and when the trigger is on the same map the effect
also happens on the spot (removeobject / addobject / setmetatile)."""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

TRIGGER_FIELDS = {
    "defeat_trainer": ("route", "person"),
    "talk_to": ("route", "person"),
    "step_on": ("route", "x", "y"),
    "press_button": ("route", "x", "y"),
    "field_move": ("route", "x", "y", "move"),
    "story_beat": ("beat",),
}
EFFECT_FIELDS = {
    "remove_object": ("route", "target"),
    "show_object": ("route", "target"),
    "add_objects": ("route", "cells", "object"),
    "change_tiles": ("route", "cells", "to"),
    "set_flag": ("flag",),
    "set_var": ("var", "value"),
}
FIELD_MOVE_CONSTANTS = {
    "cut": "FIELD_MOVE_CUT", "surf": "FIELD_MOVE_SURF", "strength": "FIELD_MOVE_STRENGTH",
    "rock_smash": "FIELD_MOVE_ROCK_SMASH", "waterfall": "FIELD_MOVE_WATERFALL", "dive": "FIELD_MOVE_DIVE",
    "rock_climb": "FIELD_MOVE_ROCK_CLIMB", "flash": "FIELD_MOVE_FLASH", "dig": "FIELD_MOVE_DIG", "defog": "FIELD_MOVE_DEFOG",
}
OTHER_MOVE_CONSTANTS = {"earthquake": "MOVE_EARTHQUAKE", "headbutt": "MOVE_HEADBUTT", "dynamic_punch": "MOVE_DYNAMIC_PUNCH"}
ADDED_OBJECT_KINDS = {
    "boulder": ("OBJ_EVENT_GFX_PUSHABLE_BOULDER", "EventScript_StrengthBoulder"),
    "rock": ("OBJ_EVENT_GFX_BREAKABLE_ROCK", "EventScript_RockSmash"),
    "cut_tree": ("OBJ_EVENT_GFX_CUTTABLE_TREE", "EventScript_CutTree"),
}
TILE_TARGET_CLASSES = {"ground": "g", "path": "p", "tall_grass": "t", "rock": "r", "tree": "f", "water": "w", "sand": "d"}
MAXIMUM_CELLS_PER_EFFECT = 24
MAXIMUM_EFFECTS_PER_RULE = 8
RULE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,15}$")
FLAG_PATTERN = re.compile(r"^FLAG_[A-Z0-9_]+$")
VAR_PATTERN = re.compile(r"^VAR_[A-Z0-9_]+$")
MOVE_PATTERN = re.compile(r"^MOVE_[A-Z0-9_]+$")
TEMPORARY_FLAG_NAMES = [f"FLAG_TEMP_{flag_number:X}" for flag_number in range(0x1, 0x20)]
PARTY_KNOWS_MOVE_SPECIAL = "PokeRoutes_PartyKnowsMove"
STEP_TRIGGER_VAR = "VAR_TEMP_0"


class RuleRejected(ValueError):
    pass


def constant_case(text: str) -> str:
    split_camel_case = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(text))
    constant_name = re.sub(r"[^A-Z0-9]+", "_", split_camel_case.upper()).strip("_")
    return constant_name


def same_route_name(first_route_name, second_route_name) -> bool:
    is_same_route = str(first_route_name or "").strip().casefold() == str(second_route_name or "").strip().casefold()
    return is_same_route


def whole_number(raw_value, what: str) -> int:
    try:
        number = int(raw_value)
    except (TypeError, ValueError):
        raise RuleRejected(f"{what} must be a whole number, got {raw_value!r}") from None
    return number


def required_text(raw_value, what: str) -> str:
    text = str(raw_value or "").strip()
    if not text:
        raise RuleRejected(f"pick {what}")
    return text


def picked_cell(raw_object: dict) -> dict:
    if raw_object.get("x") is None or raw_object.get("y") is None:
        raise RuleRejected("pick a cell")
    cell = {"x": whole_number(raw_object.get("x"), "x"), "y": whole_number(raw_object.get("y"), "y")}
    return cell


def normalized_cells(raw_cells) -> List[List[int]]:
    if not isinstance(raw_cells, list) or not raw_cells:
        raise RuleRejected("pick at least one cell")
    if len(raw_cells) > MAXIMUM_CELLS_PER_EFFECT:
        raise RuleRejected(f"at most {MAXIMUM_CELLS_PER_EFFECT} cells per effect")
    cells = []
    for raw_cell in raw_cells:
        if not isinstance(raw_cell, (list, tuple)) or len(raw_cell) != 2:
            raise RuleRejected(f"a cell must be [x, y], got {raw_cell!r}")
        cell = [whole_number(raw_cell[0], "x"), whole_number(raw_cell[1], "y")]
        if cell not in cells:
            cells.append(cell)
    return cells


def normalized_target(raw_effect: dict) -> dict:
    person_id = str(raw_effect.get("person") or "").strip()
    if person_id:
        return {"person": person_id}
    target = picked_cell(raw_effect)
    return target


def normalized_move(raw_move) -> str:
    move = str(raw_move or "").strip().lower().replace(" ", "_")
    if move in FIELD_MOVE_CONSTANTS or move in OTHER_MOVE_CONSTANTS:
        return move
    move_constant = str(raw_move or "").strip().upper()
    if MOVE_PATTERN.match(move_constant):
        return move_constant
    raise RuleRejected(f"unknown move {raw_move!r}; use one of {', '.join([*FIELD_MOVE_CONSTANTS, *OTHER_MOVE_CONSTANTS])} or a MOVE_* constant")


def normalized_trigger(raw_trigger) -> dict:
    if not isinstance(raw_trigger, dict):
        raise RuleRejected("when must be an object {type, ...}")
    trigger_type = raw_trigger.get("type")
    if trigger_type not in TRIGGER_FIELDS:
        raise RuleRejected(f"unknown trigger {trigger_type!r}; expected one of {', '.join(TRIGGER_FIELDS)}")
    if trigger_type == "story_beat":
        return {"type": trigger_type, "beat": required_text(raw_trigger.get("beat"), "a name for the story beat")}
    trigger = {"type": trigger_type, "route": required_text(raw_trigger.get("route"), "the trigger's route")}
    if trigger_type in ("defeat_trainer", "talk_to"):
        trigger["person"] = str(raw_trigger.get("person") or "").strip()
        if not trigger["person"]:
            raise RuleRejected("pick a person")
        return trigger
    trigger.update(picked_cell(raw_trigger))
    if trigger_type == "field_move":
        trigger["move"] = normalized_move(raw_trigger.get("move"))
    return trigger


def normalized_effect(raw_effect) -> dict:
    if not isinstance(raw_effect, dict):
        raise RuleRejected("an effect must be an object {type, ...}")
    effect_type = raw_effect.get("type")
    if effect_type not in EFFECT_FIELDS:
        raise RuleRejected(f"unknown effect {effect_type!r}; expected one of {', '.join(EFFECT_FIELDS)}")
    if effect_type == "set_flag":
        flag = str(raw_effect.get("flag") or "").strip().upper()
        if not FLAG_PATTERN.match(flag):
            raise RuleRejected(f"{flag!r} is not a FLAG_* constant")
        return {"type": effect_type, "flag": flag}
    if effect_type == "set_var":
        var = str(raw_effect.get("var") or "").strip().upper()
        if not VAR_PATTERN.match(var):
            raise RuleRejected(f"{var!r} is not a VAR_* constant")
        value = whole_number(raw_effect.get("value", 1), "value")
        if not 0 <= value <= 0xFFFF:
            raise RuleRejected("value must be 0-65535")
        return {"type": effect_type, "var": var, "value": value}
    effect = {"type": effect_type, "route": required_text(raw_effect.get("route"), "the effect's route")}
    if effect_type in ("remove_object", "show_object"):
        return {**effect, **normalized_target(raw_effect)}
    effect["cells"] = normalized_cells(raw_effect.get("cells"))
    if effect_type == "add_objects":
        object_kind = str(raw_effect.get("object") or "rock")
        if object_kind not in ADDED_OBJECT_KINDS:
            raise RuleRejected(f"object must be one of {', '.join(ADDED_OBJECT_KINDS)}")
        effect["object"] = object_kind
        return effect
    tile_target = str(raw_effect.get("to") or "ground")
    if tile_target not in TILE_TARGET_CLASSES:
        raise RuleRejected(f"to must be one of {', '.join(TILE_TARGET_CLASSES)}")
    effect["to"] = tile_target
    return effect


def normalized_rule(raw_rule, rule_id: str) -> dict:
    if not isinstance(raw_rule, dict):
        raise RuleRejected("a rule must be an object {when, then}")
    raw_effects = raw_rule.get("then")
    if isinstance(raw_effects, dict):
        raw_effects = [raw_effects]
    if not isinstance(raw_effects, list) or not raw_effects:
        raise RuleRejected("then must list at least one effect")
    if len(raw_effects) > MAXIMUM_EFFECTS_PER_RULE:
        raise RuleRejected(f"at most {MAXIMUM_EFFECTS_PER_RULE} effects per rule")
    rule = {"id": rule_id, "when": normalized_trigger(raw_rule.get("when")),
            "then": [normalized_effect(raw_effect) for raw_effect in raw_effects]}
    if raw_rule.get("note"):
        rule["note"] = str(raw_rule["note"]).strip()[:120]
    return rule


def next_free_rule_id(rules: List[dict]) -> str:
    taken_rule_ids = {rule.get("id") for rule in rules}
    rule_number = 1
    while f"e{rule_number}" in taken_rule_ids:
        rule_number += 1
    free_rule_id = f"e{rule_number}"
    return free_rule_id


def checked_rule_id(raw_rule_id) -> str:
    rule_id = str(raw_rule_id or "").strip().lower()
    if not RULE_ID_PATTERN.match(rule_id):
        raise RuleRejected(f"rule id {raw_rule_id!r} must be lowercase letters, digits or _ (max 16)")
    return rule_id


def normalized_rules(raw_rules) -> Tuple[List[dict], List[dict]]:
    if not isinstance(raw_rules, list):
        raise RuleRejected("rules must be a list")
    rules, rejected = [], []
    for raw_rule in raw_rules:
        try:
            wanted_rule_id = raw_rule.get("id") if isinstance(raw_rule, dict) else None
            taken_rule_ids = {rule["id"] for rule in rules}
            rule_id = checked_rule_id(wanted_rule_id) if wanted_rule_id and wanted_rule_id not in taken_rule_ids else next_free_rule_id(rules)
            rules.append(normalized_rule(raw_rule, rule_id))
        except RuleRejected as rejection:
            rejected.append({"rule": raw_rule, "reason": str(rejection)})
    return rules, rejected


def rules_with_rule_added(rules: List[dict], raw_rule: dict) -> Tuple[List[dict], dict]:
    wanted_rule_id = raw_rule.get("id") if isinstance(raw_rule, dict) else None
    if wanted_rule_id and any(rule["id"] == wanted_rule_id for rule in rules):
        raise RuleRejected(f"there is already a rule {wanted_rule_id!r}")
    rule_id = checked_rule_id(wanted_rule_id) if wanted_rule_id else next_free_rule_id(rules)
    new_rule = normalized_rule(raw_rule, rule_id)
    rules_after = [*copy.deepcopy(rules), new_rule]
    return rules_after, new_rule


def rules_with_rule_replaced(rules: List[dict], rule_id: str, raw_rule: dict) -> Tuple[List[dict], dict]:
    if not any(rule["id"] == rule_id for rule in rules):
        raise RuleRejected(f"no rule {rule_id!r}")
    replacement_rule = normalized_rule(raw_rule, rule_id)
    rules_after = [replacement_rule if rule["id"] == rule_id else copy.deepcopy(rule) for rule in rules]
    return rules_after, replacement_rule


def rules_without_rule(rules: List[dict], rule_id: str) -> List[dict]:
    if not any(rule["id"] == rule_id for rule in rules):
        raise RuleRejected(f"no rule {rule_id!r}")
    rules_after = [copy.deepcopy(rule) for rule in rules if rule["id"] != rule_id]
    return rules_after


def move_label(move: str) -> str:
    return move.replace("MOVE_", "").replace("_", " ").title()


def target_words(effect: dict, person_names: Dict[Tuple[str, str], str]) -> str:
    if "person" in effect:
        person_name = person_names.get((str(effect["route"]).casefold(), effect["person"])) or effect["person"]
        return person_name
    return f"whatever is at {effect['x']},{effect['y']}"


def trigger_words(trigger: dict, person_names: Dict[Tuple[str, str], str]) -> str:
    trigger_type = trigger["type"]
    if trigger_type == "story_beat":
        return f"the story reaches \"{trigger['beat']}\""
    route = trigger["route"]
    if trigger_type in ("defeat_trainer", "talk_to"):
        person_name = person_names.get((route.casefold(), trigger["person"])) or trigger["person"]
        verb = "is beaten" if trigger_type == "defeat_trainer" else "is talked to"
        return f"{person_name} on {route} {verb}"
    if trigger_type == "step_on":
        return f"the player steps on {trigger['x']},{trigger['y']} on {route}"
    if trigger_type == "press_button":
        return f"the player presses the button at {trigger['x']},{trigger['y']} on {route}"
    return f"{move_label(trigger['move'])} is used at {trigger['x']},{trigger['y']} on {route}"


def effect_words(effect: dict, person_names: Dict[Tuple[str, str], str]) -> str:
    effect_type = effect["type"]
    if effect_type == "set_flag":
        return f"set {effect['flag']}"
    if effect_type == "set_var":
        return f"set {effect['var']} to {effect['value']}"
    route = effect["route"]
    if effect_type == "remove_object":
        return f"remove {target_words(effect, person_names)} on {route}"
    if effect_type == "show_object":
        return f"show {target_words(effect, person_names)} on {route}"
    cell_count = len(effect["cells"])
    if effect_type == "add_objects":
        return f"add {cell_count} {effect['object'].replace('_', ' ')}{'s' if cell_count != 1 else ''} on {route}"
    return f"turn {cell_count} cell{'s' if cell_count != 1 else ''} on {route} into {effect['to'].replace('_', ' ')}"


def describe_rule(rule: dict, person_names: Optional[Dict[Tuple[str, str], str]] = None) -> str:
    names = person_names or {}
    description = f"When {trigger_words(rule['when'], names)}: " + ", ".join(effect_words(effect, names) for effect in rule["then"])
    return description


def rule_event_flag(rule: dict) -> str:
    return f"FLAG_PR_EVENT_{constant_case(rule['id'])}"


def shown_object_flag(rule: dict, effect_index: int) -> str:
    return f"{rule_event_flag(rule)}_SHOW_{effect_index + 1}"


def all_rule_flags(rules: List[dict]) -> List[str]:
    flags = []
    for rule in rules:
        flags.append(rule_event_flag(rule))
        flags += [shown_object_flag(rule, effect_index) for effect_index, effect in enumerate(rule["then"]) if effect["type"] == "show_object"]
    return flags


def event_flag_defines(rules: List[dict], unused_flag_names: List[str]) -> Tuple[str, List[str]]:
    flags = all_rule_flags(rules)
    define_lines = [f"#define {flag} {unused_flag}" for flag, unused_flag in zip(flags, unused_flag_names)]
    flags_without_slot = flags[len(unused_flag_names):]
    header = ("#ifndef GUARD_POKE_ROUTES_EVENT_FLAGS_H\n#define GUARD_POKE_ROUTES_EVENT_FLAGS_H\n\n"
              + "\n".join(define_lines) + "\n\n#endif\n")
    return header, flags_without_slot


def unused_flag_names_from_header(flags_header_text: str) -> List[str]:
    unused_flag_names = re.findall(r"^#define\s+(FLAG_UNUSED_0x[0-9A-Fa-f]+)\s", flags_header_text, re.M)
    return unused_flag_names


def label_part(rule: dict) -> str:
    return constant_case(rule["id"]).title().replace("_", "")


def fire_label(map_id: str, rule: dict) -> str:
    return f"{map_id}_EventScript_PrEvent{label_part(rule)}"


@dataclass
class RouteEventExport:
    object_events: List[dict]
    bg_events: List[dict] = field(default_factory=list)
    coord_events: List[dict] = field(default_factory=list)
    map_scripts_header: str = ""
    scripts: str = ""
    lines_after_talking_by_person: Dict[str, List[str]] = field(default_factory=dict)
    lines_after_defeat_by_person: Dict[str, List[str]] = field(default_factory=dict)
    flags: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


@dataclass
class EffectsOnThisMap:
    transition_lines: List[str] = field(default_factory=list)
    load_lines: List[str] = field(default_factory=list)
    subroutines: List[str] = field(default_factory=list)
    immediate_lines_by_rule: Dict[str, List[str]] = field(default_factory=dict)
    redraws_map_by_rule: Dict[str, bool] = field(default_factory=dict)


class TemporaryFlagPool:
    def __init__(self, object_events: List[dict]):
        used_flags = {str(object_event.get("flag")) for object_event in object_events}
        self.free_flags = [flag for flag in reversed(TEMPORARY_FLAG_NAMES) if flag not in used_flags]

    def take(self) -> Optional[str]:
        if not self.free_flags:
            return None
        return self.free_flags.pop(0)


def is_temporary_flag(flag) -> bool:
    return str(flag).startswith("FLAG_TEMP_")


def has_no_flag(flag) -> bool:
    return str(flag) in ("0", "", "None")


def object_index_at(object_events: List[dict], x: int, y: int) -> Optional[int]:
    for object_index, object_event in enumerate(object_events):
        if int(object_event["x"]) == x and int(object_event["y"]) == y:
            return object_index
    return None


def target_cell(effect: dict, people_by_id: Dict[str, dict]) -> Optional[Tuple[int, int]]:
    if "person" not in effect:
        return effect["x"], effect["y"]
    person = people_by_id.get(effect["person"])
    if person is None:
        return None
    return person["x"], person["y"]


def subroutine(label: str, lines: List[str]) -> str:
    body = "".join(f"\t{line}\n" for line in lines)
    subroutine_text = f"{label}:\n{body}\treturn\n\n"
    return subroutine_text


def visibility_subroutine_label(map_id: str, rule: dict, effect_index: int) -> str:
    return f"{map_id}_EventScript_PrEvent{label_part(rule)}Objects{effect_index + 1}"


def tiles_subroutine_label(map_id: str, rule: dict, effect_index: int) -> str:
    return f"{map_id}_EventScript_PrEvent{label_part(rule)}Tiles{effect_index + 1}"


def metatile_lines(cells: List[List[int]], metatile_id: int, is_impassable: bool) -> List[str]:
    impassable_word = "TRUE" if is_impassable else "FALSE"
    lines = [f"setmetatile {x}, {y}, 0x{metatile_id:03X}, {impassable_word}" for x, y in cells]
    return lines


class RouteEventExporter:
    def __init__(self, rules, route_name, map_id, object_events, people_by_id, metatile_for_class, elevation_at):
        self.rules = rules
        self.route_name = route_name
        self.map_id = map_id
        self.object_events = copy.deepcopy(object_events)
        self.people_by_id = people_by_id
        self.metatile_for_class = metatile_for_class
        self.elevation_at = elevation_at
        self.temporary_flags = TemporaryFlagPool(self.object_events)
        self.effects = EffectsOnThisMap()
        self.exported = RouteEventExport(object_events=self.object_events)

    def is_this_route(self, route_name) -> bool:
        return same_route_name(route_name, self.route_name)

    def note(self, rule: dict, message: str) -> None:
        self.exported.notes.append(f"{self.map_id} {rule['id']}: {message}")

    def immediate(self, rule: dict, line: str) -> None:
        self.effects.immediate_lines_by_rule.setdefault(rule["id"], []).append(line)

    def immediate_for_object(self, rule: dict, command: str, object_index: int) -> None:
        self.effects.immediate_lines_by_rule.setdefault(rule["id"], []).append(f"{command} {object_index + 1}")

    def visibility_flag_for(self, rule: dict, object_index: int) -> Optional[str]:
        object_event = self.object_events[object_index]
        if not has_no_flag(object_event.get("flag")):
            return str(object_event["flag"])
        temporary_flag = self.temporary_flags.take()
        if temporary_flag is None:
            self.note(rule, "no FLAG_TEMP_* left for another object; it stays as it is")
            return None
        object_event["flag"] = temporary_flag
        return temporary_flag

    def hide_when(self, condition: str, rule: dict, effect_index: int, flags_to_set: List[str]) -> None:
        label = visibility_subroutine_label(self.map_id, rule, effect_index)
        self.effects.transition_lines.append(f"{condition} {rule_event_flag(rule)}, {label}")
        self.effects.subroutines.append(subroutine(label, [f"setflag {flag}" for flag in flags_to_set]))

    def export_remove_object(self, rule: dict, effect_index: int, effect: dict) -> None:
        cell = target_cell(effect, self.people_by_id)
        object_index = object_index_at(self.object_events, *cell) if cell else None
        if object_index is None:
            self.note(rule, f"nothing to remove at {cell or effect.get('person')}")
            return
        visibility_flag = self.visibility_flag_for(rule, object_index)
        if visibility_flag is None:
            return
        self.hide_when("call_if_set", rule, effect_index, [visibility_flag])
        self.immediate_for_object(rule, "removeobject", object_index)

    def export_show_object(self, rule: dict, effect_index: int, effect: dict) -> None:
        cell = target_cell(effect, self.people_by_id)
        object_index = object_index_at(self.object_events, *cell) if cell else None
        if object_index is None:
            self.note(rule, f"nothing to show at {cell or effect.get('person')}")
            return
        object_event = self.object_events[object_index]
        if not has_no_flag(object_event.get("flag")) and not is_temporary_flag(object_event["flag"]):
            object_event["flag"] = shown_object_flag(rule, effect_index)
        visibility_flag = self.visibility_flag_for(rule, object_index)
        if visibility_flag is None:
            return
        self.hide_when("call_if_unset", rule, effect_index, [visibility_flag])
        if is_temporary_flag(visibility_flag):
            self.immediate(rule, f"clearflag {visibility_flag}")
        self.immediate_for_object(rule, "addobject", object_index)

    def export_add_objects(self, rule: dict, effect_index: int, effect: dict) -> None:
        graphics_id, script = ADDED_OBJECT_KINDS[effect["object"]]
        added_flags = []
        for x, y in effect["cells"]:
            if object_index_at(self.object_events, x, y) is not None:
                self.note(rule, f"{x},{y} already has an object; skipped")
                continue
            temporary_flag = self.temporary_flags.take()
            if temporary_flag is None:
                self.note(rule, "no FLAG_TEMP_* left for more objects")
                break
            self.object_events.append({
                "graphics_id": graphics_id, "x": x, "y": y, "elevation": self.elevation_at(y, x),
                "movement_type": "MOVEMENT_TYPE_LOOK_AROUND", "movement_range_x": 0, "movement_range_y": 0,
                "trainer_type": "TRAINER_TYPE_NONE", "trainer_sight_or_berry_tree_id": "0", "script": script, "flag": temporary_flag})
            added_flags.append(temporary_flag)
            self.immediate(rule, f"clearflag {temporary_flag}")
            self.immediate_for_object(rule, "addobject", len(self.object_events) - 1)
        if added_flags:
            self.hide_when("call_if_unset", rule, effect_index, added_flags)

    def export_change_tiles(self, rule: dict, effect_index: int, effect: dict) -> None:
        class_code = TILE_TARGET_CLASSES[effect["to"]]
        metatile = self.metatile_for_class(class_code)
        if metatile is None:
            self.note(rule, f"this map has no {effect['to'].replace('_', ' ')} tiles to copy; tile change skipped")
            return
        metatile_id, is_impassable = metatile
        lines = metatile_lines(effect["cells"], metatile_id, is_impassable)
        label = tiles_subroutine_label(self.map_id, rule, effect_index)
        self.effects.load_lines.append(f"call_if_set {rule_event_flag(rule)}, {label}")
        self.effects.subroutines.append(subroutine(label, lines))
        for line in lines:
            self.immediate(rule, line)
        self.effects.redraws_map_by_rule[rule["id"]] = True

    def export_effects_on_this_map(self) -> None:
        exporters = {"remove_object": self.export_remove_object, "show_object": self.export_show_object,
                     "add_objects": self.export_add_objects, "change_tiles": self.export_change_tiles}
        for rule in self.rules:
            for effect_index, effect in enumerate(rule["then"]):
                if effect["type"] in exporters and self.is_this_route(effect["route"]):
                    exporters[effect["type"]](rule, effect_index, effect)
                    self.exported.flags.append(rule_event_flag(rule))

    def fire_script(self, rule: dict) -> str:
        label = fire_label(self.map_id, rule)
        event_flag = rule_event_flag(rule)
        lines = [f"goto_if_set {event_flag}, {label}_Done", f"setflag {event_flag}"]
        for effect_index, effect in enumerate(rule["then"]):
            if effect["type"] == "show_object":
                lines.append(f"clearflag {shown_object_flag(rule, effect_index)}")
            if effect["type"] == "set_flag":
                lines.append(f"setflag {effect['flag']}")
            if effect["type"] == "set_var":
                lines.append(f"setvar {effect['var']}, {effect['value']}")
        lines += self.effects.immediate_lines_by_rule.get(rule["id"], [])
        if self.effects.redraws_map_by_rule.get(rule["id"]):
            lines.append("special DrawWholeMapView")
        body = "".join(f"\t{line}\n" for line in lines)
        script = f"{label}::\n{body}{label}_Done:\n\treturn\n\n"
        return script

    def person_hook(self, rule: dict, lines_by_person: Dict[str, List[str]]) -> bool:
        person = self.people_by_id.get(rule["when"]["person"])
        if person is None:
            self.note(rule, f"no person {rule['when']['person']!r} on this map")
            return False
        if rule["when"]["type"] == "defeat_trainer" and not person.get("trainer"):
            self.note(rule, f"{person.get('name') or person['id']} is not a trainer; the rule fires after talking instead")
            self.exported.lines_after_talking_by_person.setdefault(person["id"], []).append(f"call {fire_label(self.map_id, rule)}")
            return True
        lines_by_person.setdefault(person["id"], []).append(f"call {fire_label(self.map_id, rule)}")
        return True

    def step_trigger(self, rule: dict) -> str:
        trigger = rule["when"]
        label = f"{fire_label(self.map_id, rule)}_Step"
        self.exported.coord_events.append({"type": "trigger", "x": trigger["x"], "y": trigger["y"],
                                           "elevation": self.elevation_at(trigger["y"], trigger["x"]),
                                           "var": STEP_TRIGGER_VAR, "var_value": "0", "script": label})
        script = f"{label}::\n\tcall {fire_label(self.map_id, rule)}\n\tend\n\n"
        return script

    def sign_like_bg_event(self, trigger: dict, label: str) -> None:
        self.exported.bg_events.append({"type": "sign", "x": trigger["x"], "y": trigger["y"], "elevation": 0,
                                        "player_facing_dir": "BG_EVENT_PLAYER_FACING_ANY", "script": label})

    def button_trigger(self, rule: dict) -> str:
        from .people_and_signs import multiline_text_block

        label = f"{fire_label(self.map_id, rule)}_Button"
        self.sign_like_bg_event(rule["when"], label)
        script = (f"{label}::\n\tlockall\n\tgoto_if_set {rule_event_flag(rule)}, {label}_Already\n"
                  f"\tmsgbox {label}_Text, MSGBOX_DEFAULT\n\tplayse SE_SWITCH\n\twaitse\n\tcall {fire_label(self.map_id, rule)}\n"
                  f"\treleaseall\n\tend\n{label}_Already:\n\tmsgbox {label}_TextAlready, MSGBOX_DEFAULT\n\treleaseall\n\tend\n\n"
                  + multiline_text_block(f"{label}_Text", "There's a switch here. You pressed it!")
                  + multiline_text_block(f"{label}_TextAlready", "The switch is already pressed down."))
        return script

    def party_check_lines(self, move: str) -> List[str]:
        if move in FIELD_MOVE_CONSTANTS:
            return [f"checkfieldmove {FIELD_MOVE_CONSTANTS[move]}, TRUE"]
        move_constant = OTHER_MOVE_CONSTANTS.get(move, move)
        return [f"setvar VAR_0x8004, {move_constant}", f"specialvar VAR_RESULT, {PARTY_KNOWS_MOVE_SPECIAL}"]

    def field_move_trigger(self, rule: dict) -> str:
        from .people_and_signs import multiline_text_block

        move = rule["when"]["move"]
        move_name = move_label(move).upper()
        label = f"{fire_label(self.map_id, rule)}_Move"
        self.sign_like_bg_event(rule["when"], label)
        if move not in FIELD_MOVE_CONSTANTS:
            self.note(rule, f"{move_label(move)} is not a field move: add the {PARTY_KNOWS_MOVE_SPECIAL} special (see EVENTS_README.txt)")
        check_lines = "".join(f"\t{line}\n" for line in self.party_check_lines(move))
        script = (f"{label}::\n\tlockall\n\tgoto_if_set {rule_event_flag(rule)}, {label}_Done\n{check_lines}"
                  f"\tgoto_if_eq VAR_RESULT, PARTY_SIZE, {label}_Cannot\n"
                  f"\tmsgbox {label}_TextAsk, MSGBOX_YESNO\n\tgoto_if_eq VAR_RESULT, NO, {label}_Done\n"
                  f"\tcall {fire_label(self.map_id, rule)}\n{label}_Done:\n\treleaseall\n\tend\n"
                  f"{label}_Cannot:\n\tmsgbox {label}_TextCannot, MSGBOX_DEFAULT\n\treleaseall\n\tend\n\n"
                  + multiline_text_block(f"{label}_TextAsk", f"Something could happen here with {move_name}. Use it?")
                  + multiline_text_block(f"{label}_TextCannot", f"Something could happen here with {move_name}."))
        return script

    def export_triggers_on_this_map(self) -> List[str]:
        scripts = []
        for rule in self.rules:
            trigger = rule["when"]
            if trigger["type"] == "story_beat":
                continue
            if not self.is_this_route(trigger["route"]):
                continue
            has_trigger = True
            if trigger["type"] == "defeat_trainer":
                has_trigger = self.person_hook(rule, self.exported.lines_after_defeat_by_person)
            if trigger["type"] == "talk_to":
                has_trigger = self.person_hook(rule, self.exported.lines_after_talking_by_person)
            if trigger["type"] == "step_on":
                scripts.append(self.step_trigger(rule))
            if trigger["type"] == "press_button":
                scripts.append(self.button_trigger(rule))
            if trigger["type"] == "field_move":
                scripts.append(self.field_move_trigger(rule))
            if not has_trigger:
                continue
            scripts.append(self.fire_script(rule))
            self.exported.flags.append(rule_event_flag(rule))
        return scripts

    def map_scripts(self) -> Tuple[str, str]:
        header_lines, bodies = [], []
        if self.effects.load_lines:
            header_lines.append(f"\tmap_script MAP_SCRIPT_ON_LOAD, {self.map_id}_OnLoad")
            bodies.append(f"{self.map_id}_OnLoad:\n" + "".join(f"\t{line}\n" for line in self.effects.load_lines) + "\tend\n\n")
        if self.effects.transition_lines:
            header_lines.append(f"\tmap_script MAP_SCRIPT_ON_TRANSITION, {self.map_id}_OnTransition")
            bodies.append(f"{self.map_id}_OnTransition:\n" + "".join(f"\t{line}\n" for line in self.effects.transition_lines) + "\tend\n\n")
        header = f"{self.map_id}_MapScripts::\n" + "".join(f"{line}\n" for line in header_lines) + "\t.byte 0\n\n"
        map_script_bodies = "".join(bodies) + "".join(self.effects.subroutines)
        return header, map_script_bodies

    def export(self) -> RouteEventExport:
        self.export_effects_on_this_map()
        trigger_scripts = self.export_triggers_on_this_map()
        header, map_script_bodies = self.map_scripts()
        self.exported.map_scripts_header = header
        self.exported.scripts = map_script_bodies + "".join(trigger_scripts)
        self.exported.flags = list(dict.fromkeys(self.exported.flags))
        return self.exported


def export_route_events(rules: List[dict], route_name: str, map_id: str, object_events: List[dict],
                        people_by_id: Dict[str, dict], metatile_for_class: Callable[[str], Optional[Tuple[int, bool]]],
                        elevation_at: Callable[[int, int], int]) -> RouteEventExport:
    exporter = RouteEventExporter(rules, route_name, map_id, object_events, people_by_id, metatile_for_class, elevation_at)
    route_event_export = exporter.export()
    return route_event_export


def story_beat_notes(rules: List[dict]) -> List[str]:
    notes = [f"story beat \"{rule['when']['beat']}\": put `setflag {rule_event_flag(rule)}` in the script where it happens"
             for rule in rules if rule["when"]["type"] == "story_beat"]
    return notes


def readme_wiring_steps(rules: List[dict], trainer_constants: List[str], flags_without_slot: List[str]) -> List[str]:
    steps = ["Flags. include/constants/poke_routes_event_flags.h aliases every rule flag onto an unused flag of the ROM\n"
             "   (FLAG_UNUSED_0x...). #include it at the end of include/constants/flags.h, and check nothing else uses those flags."]
    if flags_without_slot:
        steps.append("These flags found no unused slot and need defining by hand: " + ", ".join(flags_without_slot))
    if trainer_constants:
        steps.append("Trainers. Add these to include/constants/opponents.h (before TRAINERS_COUNT, raising it) and append\n"
                     "   trainers_poke_routes.party to src/data/trainers.party: " + ", ".join(trainer_constants))
    if any(rule["when"]["type"] == "field_move" and rule["when"]["move"] not in FIELD_MOVE_CONSTANTS for rule in rules):
        steps.append(f"Moves that are not field moves (Earthquake...) call the special {PARTY_KNOWS_MOVE_SPECIAL}: add it to\n"
                     "   data/specials.inc (def_special) and src/field_specials.c, returning the party slot of the first Pokemon\n"
                     "   that knows the move in gSpecialVar_0x8004, or PARTY_SIZE.")
    if any(rule["when"]["type"] == "press_button" for rule in rules):
        steps.append("Buttons are bg_events: paint a switch or button tile on their cell so the player can find them.")
    if rules:
        steps.append("Effects on another route than their trigger show the next time the player enters that route.")
    return steps


def events_readme(rules: List[dict], notes: List[str], trainer_constants: List[str], flags_without_slot: List[str]) -> str:
    steps = readme_wiring_steps(rules, trainer_constants, flags_without_slot)
    numbered_steps = "".join(f"{step_number}. {step}\n" for step_number, step in enumerate(steps, start=1))
    all_notes = story_beat_notes(rules) + notes
    notes_section = ("\nNotes:\n" + "".join(f"- {note}\n" for note in all_notes)) if all_notes else ""
    readme = "poke-routes events: what still needs wiring by hand in pokeemerald-expansion\n\n" + numbered_steps + notes_section
    return readme
