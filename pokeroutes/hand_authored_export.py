"""The hand-authored layer of a route's Porymap export: people and signs placed in the
Routes view (people_and_signs.py) and the region's event rules (route_events.py), laid on
top of the generated events that export.build_events wrote."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import people_and_signs, route_events

HAND_ENTRIES_KEY = "hand_people_and_signs"
EVENT_RULES_KEY = "event_rules"
FALLBACK_UNUSED_FLAG_NAMES = [f"FLAG_UNUSED_0x{flag_number:03X}" for flag_number in range(0x20, 0x50)]
FLAGS_HEADER_PATH = "include/constants/poke_routes_event_flags.h"
TRAINERS_PARTY_PATH = "trainers_poke_routes.party"
EVENTS_README_PATH = "EVENTS_README.txt"


@dataclass
class HandAuthoredExport:
    object_events: List[dict]
    bg_events: List[dict]
    coord_events: List[dict]
    flags: List[str]
    map_scripts_header: str
    scripts: str
    extra_files: Dict[str, str] = field(default_factory=dict)


def hand_entries_of(route) -> List[dict]:
    return list(route.__dict__.get(HAND_ENTRIES_KEY) or [])


def event_rules_of(route) -> List[dict]:
    return list(route.__dict__.get(EVENT_RULES_KEY) or [])


def generated_events_kept(route) -> list:
    kept_events = people_and_signs.events_without_replaced_generated_people(route.events, hand_entries_of(route))
    return kept_events


def most_common_metatile_finder(route, layout) -> Callable[[str], Optional[Tuple[int, bool]]]:
    def metatile_for_class(class_code: str) -> Optional[Tuple[int, bool]]:
        cells_of_class = list(zip(*(route.cls == class_code).nonzero()))
        if not cells_of_class:
            return None
        metatile_counts = Counter((int(layout.metatile[y, x]), bool(layout.collision[y, x])) for y, x in cells_of_class)
        most_common_metatile = metatile_counts.most_common(1)[0][0]
        return most_common_metatile

    return metatile_for_class


def rom_unused_flag_names() -> List[str]:
    try:
        from .ruletile import find_rom

        flags_header = Path(find_rom()) / "include/constants/flags.h"
    except (FileNotFoundError, ImportError):
        return FALLBACK_UNUSED_FLAG_NAMES
    if not flags_header.is_file():
        return FALLBACK_UNUSED_FLAG_NAMES
    unused_flag_names = route_events.unused_flag_names_from_header(flags_header.read_text(errors="replace"))
    return unused_flag_names or FALLBACK_UNUSED_FLAG_NAMES


def extra_files_for(rules: List[dict], notes: List[str], people_export) -> Dict[str, str]:
    if not rules and not people_export.trainer_constants:
        return {}
    flags_header, flags_without_slot = route_events.event_flag_defines(rules, rom_unused_flag_names())
    extra_files = {EVENTS_README_PATH: route_events.events_readme(rules, notes, people_export.trainer_constants, flags_without_slot)}
    if rules:
        extra_files[FLAGS_HEADER_PATH] = flags_header
    if people_export.trainer_party_text:
        extra_files[TRAINERS_PARTY_PATH] = people_export.trainer_party_text
    return extra_files


def export_hand_authored_layer(route, layout, elevation_at, map_id: str, map_constant: str,
                               generated_object_events: List[dict]) -> HandAuthoredExport:
    entries = hand_entries_of(route)
    rules = event_rules_of(route)
    people_export = people_and_signs.export_people_and_signs(entries, map_id, map_constant, elevation_at)
    people_by_id = {person["id"]: person for person in people_and_signs.people_in(entries)}
    events_export = route_events.export_route_events(
        rules, route.spec.name, map_id, generated_object_events + people_export.object_events, people_by_id,
        most_common_metatile_finder(route, layout), elevation_at)
    person_scripts = people_export.render_scripts(events_export.lines_after_talking_by_person, events_export.lines_after_defeat_by_person)
    hand_authored_export = HandAuthoredExport(
        object_events=events_export.object_events,
        bg_events=people_export.bg_events + events_export.bg_events,
        coord_events=events_export.coord_events,
        flags=events_export.flags,
        map_scripts_header=events_export.map_scripts_header,
        scripts=person_scripts + events_export.scripts,
        extra_files=extra_files_for(rules, events_export.notes, people_export),
    )
    return hand_authored_export


def write_extra_files(out: Path, extra_files: Dict[str, str]) -> None:
    for relative_path, text in extra_files.items():
        file_path = Path(out) / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(text)
