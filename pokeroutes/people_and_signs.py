"""People and signs placed by hand in the Routes view, and how they become Porymap object
events, bg_events, scripts and a trainers.party stub.

An entry is one of:

    {"kind": "person", "id": "p1", "x", "y", "name", "sprite": "OBJ_EVENT_GFX_*",
     "facing": "down|up|left|right", "movement": "still|look_around|wander", "text",
     "trainer": null | {"trainer_class": "TRAINER_CLASS_*", "team": [{"species", "level"}],
                        "defeat_text", "after_text"}}
    {"kind": "sign", "id": "s1", "x", "y", "text"}
    {"kind": "removed", "x", "y"}      the generated person or sign on that cell is gone

A person or sign on the cell of a generated one replaces it."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

PERSON_KIND = "person"
SIGN_KIND = "sign"
REMOVED_KIND = "removed"
ENTRY_KINDS = (PERSON_KIND, SIGN_KIND, REMOVED_KIND)
GENERATED_KINDS_THAT_HAND_ENTRIES_REPLACE = ("npc", "sign")
WALKABLE_CLASS_CODES = set("gptdhyvzqn")
FACING_MOVEMENT_TYPES = {
    "down": "MOVEMENT_TYPE_FACE_DOWN", "up": "MOVEMENT_TYPE_FACE_UP",
    "left": "MOVEMENT_TYPE_FACE_LEFT", "right": "MOVEMENT_TYPE_FACE_RIGHT",
}
MOVEMENT_STYLES = ("still", "look_around", "wander")
MOVEMENT_TYPE_FOR_STYLE = {"look_around": "MOVEMENT_TYPE_LOOK_AROUND", "wander": "MOVEMENT_TYPE_WANDER_AROUND"}
WANDER_RANGE_IN_CELLS = 2
DEFAULT_PERSON_SPRITE = "OBJ_EVENT_GFX_MAN_3"
DEFAULT_TRAINER_CLASS = "TRAINER_CLASS_YOUNGSTER"
TRAINER_SIGHT_IN_CELLS = 3
MAXIMUM_TEAM_SIZE = 6
MAXIMUM_TEXT_LENGTH_IN_CHARACTERS = 600
MAXIMUM_NAME_LENGTH_IN_CHARACTERS = 24
TEXT_WIDTH_IN_CHARACTERS = 30
SPRITE_CONSTANT_PATTERN = re.compile(r"^OBJ_EVENT_GFX_[A-Z0-9_]+$")
TRAINER_CLASS_PATTERN = re.compile(r"^TRAINER_CLASS_[A-Z0-9_]+$")
ENTRY_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,15}$")
DEFAULT_PERSON_TEXT = "Hello there!"
DEFAULT_SIGN_TEXT = "..."
DEFAULT_DEFEAT_TEXT = "I lost..."


class EntryRejected(ValueError):
    pass


def constant_case(text: str) -> str:
    split_camel_case = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(text))
    constant_name = re.sub(r"[^A-Z0-9]+", "_", split_camel_case.upper()).strip("_")
    return constant_name


def whole_number(raw_value, what: str) -> int:
    try:
        number = int(raw_value)
    except (TypeError, ValueError):
        raise EntryRejected(f"{what} must be a whole number, got {raw_value!r}") from None
    return number


def cleaned_text(raw_text, fallback: str) -> str:
    text = str(raw_text if raw_text is not None else "").replace("\r\n", "\n").replace("$", "").strip()
    if len(text) > MAXIMUM_TEXT_LENGTH_IN_CHARACTERS:
        raise EntryRejected(f"text is longer than {MAXIMUM_TEXT_LENGTH_IN_CHARACTERS} characters")
    return text or fallback


def species_constant(raw_species) -> str:
    species_name = constant_case(raw_species)
    if not species_name:
        raise EntryRejected("a team member needs a species")
    species = species_name if species_name.startswith("SPECIES_") else "SPECIES_" + species_name
    return species


def normalized_team_member(raw_member) -> dict:
    if not isinstance(raw_member, dict):
        raise EntryRejected("a team member must be an object {species, level}")
    level = whole_number(raw_member.get("level", 5), "level")
    if not 1 <= level <= 100:
        raise EntryRejected(f"level {level} is outside 1-100")
    team_member = {"species": species_constant(raw_member.get("species")), "level": level}
    return team_member


def normalized_trainer(raw_trainer) -> Optional[dict]:
    if not raw_trainer:
        return None
    if not isinstance(raw_trainer, dict):
        raise EntryRejected("trainer must be an object or null")
    trainer_class = str(raw_trainer.get("trainer_class") or DEFAULT_TRAINER_CLASS).upper()
    if not TRAINER_CLASS_PATTERN.match(trainer_class):
        raise EntryRejected(f"{trainer_class!r} is not a TRAINER_CLASS_* constant")
    raw_team = raw_trainer.get("team") or []
    if not isinstance(raw_team, list) or len(raw_team) > MAXIMUM_TEAM_SIZE:
        raise EntryRejected(f"team must be a list of at most {MAXIMUM_TEAM_SIZE} Pokémon")
    trainer = {
        "trainer_class": trainer_class,
        "team": [normalized_team_member(raw_member) for raw_member in raw_team],
        "defeat_text": cleaned_text(raw_trainer.get("defeat_text"), DEFAULT_DEFEAT_TEXT),
        "after_text": cleaned_text(raw_trainer.get("after_text"), ""),
    }
    return trainer


def normalized_entry_id(raw_id, kind: str) -> str:
    entry_id = str(raw_id or "").strip().lower()
    if not entry_id:
        return ""
    if not ENTRY_ID_PATTERN.match(entry_id):
        raise EntryRejected(f"{kind} id {raw_id!r} must be lowercase letters, digits or _ (max 16)")
    return entry_id


def normalized_person(raw_entry: dict) -> dict:
    sprite = str(raw_entry.get("sprite") or DEFAULT_PERSON_SPRITE).upper()
    if not SPRITE_CONSTANT_PATTERN.match(sprite):
        raise EntryRejected(f"{sprite!r} is not an OBJ_EVENT_GFX_* constant")
    facing = str(raw_entry.get("facing") or "down")
    if facing not in FACING_MOVEMENT_TYPES:
        raise EntryRejected(f"facing must be one of {', '.join(FACING_MOVEMENT_TYPES)}")
    movement = str(raw_entry.get("movement") or "still")
    if movement not in MOVEMENT_STYLES:
        raise EntryRejected(f"movement must be one of {', '.join(MOVEMENT_STYLES)}")
    person = {
        "kind": PERSON_KIND, "id": normalized_entry_id(raw_entry.get("id"), PERSON_KIND),
        "x": whole_number(raw_entry.get("x"), "x"), "y": whole_number(raw_entry.get("y"), "y"),
        "name": str(raw_entry.get("name") or "").strip()[:MAXIMUM_NAME_LENGTH_IN_CHARACTERS],
        "sprite": sprite, "facing": facing, "movement": movement,
        "text": cleaned_text(raw_entry.get("text"), DEFAULT_PERSON_TEXT),
        "trainer": normalized_trainer(raw_entry.get("trainer")),
    }
    return person


def normalized_sign(raw_entry: dict) -> dict:
    sign = {
        "kind": SIGN_KIND, "id": normalized_entry_id(raw_entry.get("id"), SIGN_KIND),
        "x": whole_number(raw_entry.get("x"), "x"), "y": whole_number(raw_entry.get("y"), "y"),
        "text": cleaned_text(raw_entry.get("text"), DEFAULT_SIGN_TEXT),
    }
    return sign


def normalized_removal(raw_entry: dict) -> dict:
    removal = {"kind": REMOVED_KIND, "x": whole_number(raw_entry.get("x"), "x"), "y": whole_number(raw_entry.get("y"), "y")}
    return removal


def normalized_entry(raw_entry) -> dict:
    if not isinstance(raw_entry, dict):
        raise EntryRejected("an entry must be an object")
    kind = raw_entry.get("kind")
    if kind == PERSON_KIND:
        return normalized_person(raw_entry)
    if kind == SIGN_KIND:
        return normalized_sign(raw_entry)
    if kind == REMOVED_KIND:
        return normalized_removal(raw_entry)
    raise EntryRejected(f"unknown kind {kind!r}; expected one of {', '.join(ENTRY_KINDS)}")


def next_free_entry_id(kind: str, taken_ids: set) -> str:
    prefix = "p" if kind == PERSON_KIND else "s"
    entry_number = 1
    while f"{prefix}{entry_number}" in taken_ids:
        entry_number += 1
    free_entry_id = f"{prefix}{entry_number}"
    return free_entry_id


def entries_with_ids(entries: List[dict]) -> List[dict]:
    taken_ids = {entry["id"] for entry in entries if entry.get("id")}
    entries_with_unique_ids = []
    for entry in entries:
        if entry["kind"] == REMOVED_KIND or entry.get("id"):
            entries_with_unique_ids.append(entry)
            continue
        new_id = next_free_entry_id(entry["kind"], taken_ids)
        taken_ids.add(new_id)
        entries_with_unique_ids.append({**entry, "id": new_id})
    return entries_with_unique_ids


def cell_problem(entry: dict, class_grid, generated_cells: set, cells_taken_by_other_objects: set) -> Optional[str]:
    map_height, map_width = len(class_grid), len(class_grid[0]) if len(class_grid) else 0
    x, y = entry["x"], entry["y"]
    if not (0 <= x < map_width and 0 <= y < map_height):
        return "outside the map"
    if entry["kind"] == REMOVED_KIND:
        return None if (y, x) in generated_cells else "no generated person or sign there"
    if entry["kind"] == PERSON_KIND and str(class_grid[y][x]) not in WALKABLE_CLASS_CODES:
        return f"cell is not walkable (class '{class_grid[y][x]}')"
    if (y, x) in cells_taken_by_other_objects:
        return "another object is already there"
    return None


@dataclass
class PlacementResult:
    placed: List[dict] = field(default_factory=list)
    skipped: List[dict] = field(default_factory=list)


def place_entries(raw_entries: list, class_grid, generated_cells: set, cells_taken_by_other_objects: set) -> PlacementResult:
    placement = PlacementResult()
    taken_cells = set(cells_taken_by_other_objects)
    accepted_entries = []
    for raw_entry in raw_entries:
        try:
            entry = normalized_entry(raw_entry)
        except EntryRejected as rejection:
            placement.skipped.append({"entry": raw_entry, "reason": str(rejection)})
            continue
        problem = cell_problem(entry, class_grid, generated_cells, taken_cells)
        if problem:
            placement.skipped.append({"entry": raw_entry, "reason": problem})
            continue
        if entry["kind"] != REMOVED_KIND:
            taken_cells.add((entry["y"], entry["x"]))
        accepted_entries.append(entry)
    placement.placed = entries_with_ids(accepted_entries)
    return placement


def cells_replaced_by_entries(entries: List[dict]) -> set:
    replaced_cells = {(entry["y"], entry["x"]) for entry in entries}
    return replaced_cells


def events_without_replaced_generated_people(events: list, entries: List[dict]) -> list:
    replaced_cells = cells_replaced_by_entries(entries)
    kept_events = [event for event in events
                   if not (event.kind in GENERATED_KINDS_THAT_HAND_ENTRIES_REPLACE and (int(event.y), int(event.x)) in replaced_cells)]
    return kept_events


def people_in(entries: List[dict]) -> List[dict]:
    people = [entry for entry in entries if entry["kind"] == PERSON_KIND]
    return people


def signs_in(entries: List[dict]) -> List[dict]:
    signs = [entry for entry in entries if entry["kind"] == SIGN_KIND]
    return signs


def wrapped_lines(paragraph: str, width_in_characters: int) -> List[str]:
    lines, current_line = [], ""
    for word in paragraph.split():
        if current_line and len(current_line) + 1 + len(word) > width_in_characters:
            lines.append(current_line)
            current_line = word
            continue
        current_line = f"{current_line} {word}" if current_line else word
    if current_line:
        lines.append(current_line)
    return lines


def text_boxes(text: str, width_in_characters: int) -> List[List[str]]:
    paragraphs = [paragraph for paragraph in re.split(r"\n\s*\n", text) if paragraph.strip()]
    boxes = []
    for paragraph in paragraphs:
        paragraph_lines = [line for written_line in paragraph.split("\n") for line in wrapped_lines(written_line, width_in_characters)]
        boxes += [paragraph_lines[line_index:line_index + 2] for line_index in range(0, len(paragraph_lines), 2)]
    return boxes or [[""]]


def escaped_game_text(line: str) -> str:
    escaped_line = line.replace("\\", "").replace('"', "'")
    return escaped_line


def multiline_text_block(label: str, text: str, width_in_characters: int = TEXT_WIDTH_IN_CHARACTERS) -> str:
    string_lines = []
    boxes = text_boxes(text, width_in_characters)
    for box_index, box_lines in enumerate(boxes):
        for line_index, line in enumerate(box_lines):
            is_last_line_of_box = line_index == len(box_lines) - 1
            is_last_box = box_index == len(boxes) - 1
            ending = "$" if is_last_box and is_last_line_of_box else "\\p" if is_last_line_of_box else "\\n"
            string_lines.append(f'\t.string "{escaped_game_text(line)}{ending}"')
    block = f"{label}:\n" + "\n".join(string_lines) + "\n\n"
    return block


def person_script_label(map_id: str, person: dict) -> str:
    return f"{map_id}_EventScript_Person_{constant_case(person['id']).title().replace('_', '')}"


def person_text_label(map_id: str, person: dict, part: str) -> str:
    return f"{map_id}_Text_Person_{constant_case(person['id']).title().replace('_', '')}_{part}"


def sign_script_label(map_id: str, sign: dict) -> str:
    return f"{map_id}_EventScript_HandSign_{constant_case(sign['id']).title().replace('_', '')}"


def trainer_constant(map_constant: str, person: dict) -> str:
    person_part = constant_case(person.get("name") or person["id"]) or constant_case(person["id"])
    trainer_name = f"TRAINER_PR_{map_constant}_{person_part}"
    return trainer_name


def movement_type_and_range(person: dict) -> tuple:
    if person["movement"] == "still":
        return FACING_MOVEMENT_TYPES[person["facing"]], 0
    movement_range_in_cells = WANDER_RANGE_IN_CELLS if person["movement"] == "wander" else 0
    return MOVEMENT_TYPE_FOR_STYLE[person["movement"]], movement_range_in_cells


def person_object_event(person: dict, map_id: str, elevation: int) -> dict:
    movement_type, movement_range_in_cells = movement_type_and_range(person)
    is_trainer = bool(person.get("trainer"))
    object_event = {
        "graphics_id": person["sprite"], "x": person["x"], "y": person["y"], "elevation": elevation,
        "movement_type": movement_type, "movement_range_x": movement_range_in_cells, "movement_range_y": movement_range_in_cells,
        "trainer_type": "TRAINER_TYPE_NORMAL" if is_trainer else "TRAINER_TYPE_NONE",
        "trainer_sight_or_berry_tree_id": str(TRAINER_SIGHT_IN_CELLS if is_trainer else 0),
        "script": person_script_label(map_id, person), "flag": "0",
    }
    return object_event


def sign_bg_event(sign: dict, map_id: str) -> dict:
    bg_event = {"type": "sign", "x": sign["x"], "y": sign["y"], "elevation": 0,
                "player_facing_dir": "BG_EVENT_PLAYER_FACING_ANY", "script": sign_script_label(map_id, sign)}
    return bg_event


def indented_script_lines(lines: List[str]) -> str:
    indented = "".join(f"\t{line}\n" for line in lines)
    return indented


def plain_person_script(person: dict, map_id: str, lines_after_talking: List[str]) -> str:
    script_label = person_script_label(map_id, person)
    text_label = person_text_label(map_id, person, "Talk")
    script = (f"{script_label}::\n\tlock\n\tfaceplayer\n\tmsgbox {text_label}, MSGBOX_DEFAULT\n"
              + indented_script_lines(lines_after_talking) + "\trelease\n\tend\n\n"
              + multiline_text_block(text_label, person["text"]))
    return script


def trainer_person_script(person: dict, map_id: str, map_constant: str, lines_after_talking: List[str],
                          lines_after_defeat: List[str]) -> str:
    script_label = person_script_label(map_id, person)
    defeated_label = f"{script_label}_Defeated"
    intro_label = person_text_label(map_id, person, "Intro")
    defeat_label = person_text_label(map_id, person, "Defeat")
    after_label = person_text_label(map_id, person, "After")
    trainer = person["trainer"]
    script = (f"{script_label}::\n"
              f"\ttrainerbattle_single {trainer_constant(map_constant, person)}, {intro_label}, {defeat_label}, {defeated_label}\n"
              + indented_script_lines(lines_after_defeat + lines_after_talking)
              + f"\tmsgbox {after_label}, MSGBOX_AUTOCLOSE\n\tend\n\n"
              f"{defeated_label}::\n"
              + indented_script_lines(lines_after_defeat) + "\trelease\n\tend\n\n"
              + multiline_text_block(intro_label, person["text"])
              + multiline_text_block(defeat_label, trainer["defeat_text"])
              + multiline_text_block(after_label, trainer["after_text"] or trainer["defeat_text"]))
    return script


def sign_script(sign: dict, map_id: str) -> str:
    script_label = sign_script_label(map_id, sign)
    text_label = f"{script_label}_Text"
    script = f"{script_label}::\n\tmsgbox {text_label}, MSGBOX_SIGN\n\tend\n\n" + multiline_text_block(text_label, sign["text"])
    return script


def trainer_pic_for_class(trainer_class: str) -> str:
    trainer_pic = "TRAINER_PIC_" + trainer_class.replace("TRAINER_CLASS_", "")
    return trainer_pic


def trainer_party_entry(person: dict, map_constant: str) -> str:
    trainer = person["trainer"]
    shown_name = (person.get("name") or person["id"]).split()[-1].upper()[:10]
    header = (f"=== {trainer_constant(map_constant, person)} ===\nName: {shown_name}\nClass: {trainer['trainer_class']}\n"
              f"Pic: {trainer_pic_for_class(trainer['trainer_class'])}\n"
              f"Gender: Male\nMusic: Male\nDouble Battle: No\nAI: Check Bad Move\n")
    team_members = trainer["team"] or [{"species": "SPECIES_ZIGZAGOON", "level": 5}]
    members = "".join(f"\n{member['species']}\nLevel: {member['level']}\n" for member in team_members)
    party_entry = header + members + "\n"
    return party_entry


@dataclass
class ExportedPeopleAndSigns:
    object_events: List[dict]
    bg_events: List[dict]
    trainer_party_text: str
    trainer_constants: List[str]
    render_scripts: Callable[[Dict[str, List[str]], Dict[str, List[str]]], str]


def export_people_and_signs(entries: List[dict], map_id: str, map_constant: str,
                            elevation_at: Callable[[int, int], int]) -> ExportedPeopleAndSigns:
    people = people_in(entries)
    signs = signs_in(entries)
    trainers = [person for person in people if person.get("trainer")]

    def render_scripts(lines_after_talking_by_person: Dict[str, List[str]], lines_after_defeat_by_person: Dict[str, List[str]]) -> str:
        person_scripts = [trainer_person_script(person, map_id, map_constant, lines_after_talking_by_person.get(person["id"], []),
                                                lines_after_defeat_by_person.get(person["id"], []))
                          if person.get("trainer") else plain_person_script(person, map_id, lines_after_talking_by_person.get(person["id"], []))
                          for person in people]
        all_scripts = "".join(person_scripts) + "".join(sign_script(sign, map_id) for sign in signs)
        return all_scripts

    exported = ExportedPeopleAndSigns(
        object_events=[person_object_event(person, map_id, elevation_at(person["y"], person["x"])) for person in people],
        bg_events=[sign_bg_event(sign, map_id) for sign in signs],
        trainer_party_text="".join(trainer_party_entry(person, map_constant) for person in trainers),
        trainer_constants=[trainer_constant(map_constant, person) for person in trainers],
        render_scripts=render_scripts,
    )
    return exported
