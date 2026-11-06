"""People, signs and events for the Routes view and the Events panel.

    GET  /api/people-options[?tiler=ark|learned]  sprites a person can wear (OBJ_EVENT_GFX_*, only ones with art
                                                  on this machine), facings, movements, trainer classes, species,
                                                  and the event trigger / effect vocabulary
    GET  /api/sprite.png?kind=gfx_<OBJ_EVENT_GFX_*>[&facing=down|up|left|right][&tiler=]
                                                  that sprite's standing frame (other kinds: route_items.py)
    POST /api/route-people {id, entries}          replace the hand-placed people and signs of a generated route;
                                                  answers {placed, skipped, generated}: generated is the route's
                                                  own people and signs with the text they export with
    POST /api/route-events {id, rules}            the region's event rules, kept on the route for its export;
                                                  answers {rules, rejected, descriptions}"""
from __future__ import annotations

import io
import re
from functools import lru_cache
from pathlib import Path

import route_items
from pokeroutes import hand_authored_export, people_and_signs, route_events, route_people

PICS_DIRECTORY = Path("graphics/object_events/pics")
SPRITE_KIND_PREFIX = "gfx_"
FRAME_WIDTH_IN_PIXELS = 16
FRAME_INDEX_FOR_FACING = {"down": 0, "up": 1, "left": 2, "right": 2}
ARK_ROUTE_SHEETS = ("hiker", "fisherman", "bug_catcher", "picnicker", "old_man", "villager", "researcher",
                    "woodcutter", "blocker", "youngster", "lass")
STOCK_PEOPLE = ("YOUNGSTER", "LASS", "BOY_1", "GIRL_1", "LITTLE_BOY", "LITTLE_GIRL", "MAN_1", "MAN_3", "WOMAN_2",
                "WOMAN_5", "OLD_MAN", "OLD_WOMAN", "HIKER", "CAMPER", "PICNICKER", "BUG_CATCHER", "FISHERMAN",
                "SCIENTIST_1", "COOLTRAINER_M", "COOLTRAINER_F", "BLACK_BELT", "BEAUTY", "GENTLEMAN", "SAILOR",
                "PSYCHIC_M", "HEX_MANIAC", "NINJA_BOY", "TWIN", "EXPERT_M", "EXPERT_F", "RICH_BOY", "POKEFAN_M",
                "POKEFAN_F", "SCHOOL_KID_M", "WORKER_M")
GYM_LEADERS = ("ROXANNE", "BRAWLY", "WATTSON", "FLANNERY", "NORMAN", "WINONA", "TATE", "LIZA", "JUAN")
TRAINER_CLASS_DEFINITION = re.compile(r"^\s*(TRAINER_CLASS_[A-Z0-9_]+)\s*[,=]", re.M)
SPECIES_DEFINITION = re.compile(r"^\s*(?:#define\s+)?(SPECIES_[A-Z0-9_]+)\s*=?\s*(\d+)\s*,?\s*$", re.M)
HIGHEST_NATIONAL_DEX_NUMBER = 1025
BUILTIN_TRAINER_CLASSES = ["TRAINER_CLASS_YOUNGSTER", "TRAINER_CLASS_LASS", "TRAINER_CLASS_HIKER", "TRAINER_CLASS_BUG_CATCHER",
                           "TRAINER_CLASS_PICNICKER", "TRAINER_CLASS_CAMPER", "TRAINER_CLASS_FISHERMAN",
                           "TRAINER_CLASS_COOLTRAINER", "TRAINER_CLASS_BLACK_BELT", "TRAINER_CLASS_LEADER"]
BUILTIN_SPECIES = ["SPECIES_ZIGZAGOON", "SPECIES_POOCHYENA", "SPECIES_WURMPLE", "SPECIES_TAILLOW", "SPECIES_GEODUDE",
                   "SPECIES_MAKUHITA", "SPECIES_MARILL", "SPECIES_NOSEPASS", "SPECIES_ONIX", "SPECIES_RALTS"]
ADDED_OBJECT_LABELS = {"boulder": "Strength boulder", "rock": "Smashable rock", "cut_tree": "Cut tree"}


def sprite_label(graphics_constant: str) -> str:
    label = graphics_constant.replace("OBJ_EVENT_GFX_", "").replace("ARK_ROUTE_", "").replace("_", " ").title()
    return label


def sprite_candidates() -> list:
    ark_candidates = [(route_people.ark_gfx(sheet), f"{route_people.ARK_SHEET_DIR}/{sheet}.png", "Route people")
                      for sheet in ARK_ROUTE_SHEETS]
    stock_candidates = [(f"OBJ_EVENT_GFX_{name}", f"people/{name.lower()}.png", "People") for name in STOCK_PEOPLE]
    leader_candidates = [(f"OBJ_EVENT_GFX_{name}", f"people/gym_leaders/{name.lower()}.png", "Gym leaders") for name in GYM_LEADERS]
    candidates = ark_candidates + stock_candidates + leader_candidates
    return candidates


def is_ark_only(graphics_constant: str) -> bool:
    return graphics_constant.startswith(route_people.ARK_GFX_PREFIX)


def sprite_sheet_path(graphics_constant: str, tiler: str):
    relative_paths = {constant: relative_path for constant, relative_path, _group in sprite_candidates()}
    relative_path = relative_paths.get(graphics_constant)
    if relative_path is None:
        return None
    if tiler == "learned" and is_ark_only(graphics_constant):
        return None
    for graphics_root in route_items.graphics_roots(tiler):
        sheet_path = graphics_root / PICS_DIRECTORY / relative_path
        if sheet_path.is_file():
            return sheet_path
    return None


@lru_cache(maxsize=512)
def standing_frame_png(graphics_constant: str, facing: str, tiler: str):
    sheet_path = sprite_sheet_path(graphics_constant, tiler)
    if sheet_path is None:
        return None
    frame = route_items.first_frame(sheet_path, FRAME_INDEX_FOR_FACING.get(facing, 0))
    if facing == "right":
        from PIL import ImageOps

        frame = ImageOps.mirror(frame)
    buffer = io.BytesIO()
    frame.save(buffer, "PNG")
    frame_png = buffer.getvalue()
    return frame_png


def sprite_options(tiler: str) -> list:
    options = [{"id": constant, "label": sprite_label(constant), "group": group}
               for constant, _relative_path, group in sprite_candidates() if sprite_sheet_path(constant, tiler) is not None]
    return options


def first_rom_file(relative_path: str):
    for graphics_root in route_items.graphics_roots():
        candidate = graphics_root / relative_path
        if candidate.is_file():
            return candidate
    return None


@lru_cache(maxsize=1)
def trainer_classes() -> list:
    header = first_rom_file("include/constants/trainers.h")
    if header is None:
        return BUILTIN_TRAINER_CLASSES
    names = [name for name in dict.fromkeys(TRAINER_CLASS_DEFINITION.findall(header.read_text(errors="replace")))
             if not name.endswith("_COUNT") and "PKMN_TRAINER" not in name]
    return names or BUILTIN_TRAINER_CLASSES


@lru_cache(maxsize=1)
def species_names() -> list:
    header = first_rom_file("include/constants/species.h")
    if header is None:
        return BUILTIN_SPECIES
    names = [name for name, number in SPECIES_DEFINITION.findall(header.read_text(errors="replace"))
             if 0 < int(number) <= HIGHEST_NATIONAL_DEX_NUMBER]
    return names or BUILTIN_SPECIES


def people_options(tiler: str) -> dict:
    options = {
        "sprites": sprite_options(tiler),
        "facings": list(people_and_signs.FACING_MOVEMENT_TYPES),
        "movements": list(people_and_signs.MOVEMENT_STYLES),
        "trainer_classes": trainer_classes(),
        "species": species_names(),
        "default_sprite": people_and_signs.DEFAULT_PERSON_SPRITE,
        "events": {
            "triggers": list(route_events.TRIGGER_FIELDS),
            "effects": list(route_events.EFFECT_FIELDS),
            "field_moves": list(route_events.FIELD_MOVE_CONSTANTS),
            "other_moves": list(route_events.OTHER_MOVE_CONSTANTS),
            "added_objects": ADDED_OBJECT_LABELS,
            "tile_targets": list(route_events.TILE_TARGET_CLASSES),
        },
    }
    return options


def route_tiler_name(srv, route_id: str) -> str:
    tiler_name = "ark" if route_id in getattr(srv, "_ark_ids", set()) else "learned"
    return tiler_name


def generated_people_and_signs(route, uses_ark_people: bool) -> list:
    from pokeroutes import export

    generated_entries = []
    sign_number = 0
    for event in route.events:
        if event.kind == "sign":
            sign_number += 1
            generated_entries.append({"kind": "sign", "x": int(event.x), "y": int(event.y),
                                      "text": export._sign_text(route, event, sign_number)})
        if event.kind == "npc":
            generated_entries.append({"kind": "person", "x": int(event.x), "y": int(event.y), "name": "",
                                      "sprite": route_people.npc_gfx(event.data.get("role"), uses_ark_people),
                                      "sprite_kind": f"npc_{event.data.get('role', 'villager')}",
                                      "facing": "down", "movement": "still", "text": export._npc_text(route, event), "trainer": None})
    return generated_entries


def place_people_and_signs(route, raw_entries: list, uses_ark_people: bool) -> dict:
    replaceable_kinds = people_and_signs.GENERATED_KINDS_THAT_HAND_ENTRIES_REPLACE
    generated_cells = {(int(event.y), int(event.x)) for event in route.events if event.kind in replaceable_kinds}
    cells_of_other_objects = {(int(event.y), int(event.x)) for event in route.events if event.kind not in replaceable_kinds}
    placement = people_and_signs.place_entries(raw_entries, route.cls, generated_cells, cells_of_other_objects)
    route.__dict__[hand_authored_export.HAND_ENTRIES_KEY] = placement.placed
    answer = {"placed": placement.placed, "skipped": placement.skipped,
              "generated": generated_people_and_signs(route, uses_ark_people)}
    return answer


def keep_event_rules(route, raw_rules: list) -> dict:
    rules, rejected = route_events.normalized_rules(raw_rules)
    route.__dict__[hand_authored_export.EVENT_RULES_KEY] = rules
    person_names = {(route.spec.name.casefold(), person["id"]): person.get("name") or person["id"]
                    for person in people_and_signs.people_in(hand_authored_export.hand_entries_of(route))}
    answer = {"rules": rules, "rejected": rejected,
              "descriptions": {rule["id"]: route_events.describe_rule(rule, person_names) for rule in rules}}
    return answer


def query_value(query, name: str, default: str = "") -> str:
    return query.get(name, [default])[0]


def handle_get(h, u, query) -> bool:
    if u.path == "/api/people-options":
        return h._json(people_options(route_items.query_tiler(query))) or True
    if u.path == "/api/sprite.png" and query_value(query, "kind").startswith(SPRITE_KIND_PREFIX):
        graphics_constant = query_value(query, "kind")[len(SPRITE_KIND_PREFIX):]
        frame_png = standing_frame_png(graphics_constant, query_value(query, "facing", "down"), route_items.query_tiler(query))
        if frame_png is None:
            return h._json({"error": f"no art for {graphics_constant} in any ROM tree on this machine"}, 404) or True
        return h._send(200, frame_png, "image/png") or True
    return False


def generated_route_or_answer(h, srv, body: dict):
    route_id = str(body.get("id", ""))
    if route_id not in srv._results:
        h._json({"error": "unknown or expired route id; generate again"}, 404)
        return None, route_id
    route, _final_layout = srv._results[route_id]
    return route, route_id


def handle_post(h, u, srv) -> bool:
    if u.path not in ("/api/route-people", "/api/route-events"):
        return False
    try:
        body = route_items.read_body(h)
    except ValueError as error:
        return h._json({"error": f"bad request: {error}"}, 400) or True
    route, route_id = generated_route_or_answer(h, srv, body)
    if route is None:
        return True
    if u.path == "/api/route-people":
        entries = body.get("entries")
        if not isinstance(entries, list):
            return h._json({"error": "expected {id, entries: [...]}"}, 400) or True
        uses_ark_people = route_tiler_name(srv, route_id) == "ark" and route_people.ark_people_available()
        return h._json(place_people_and_signs(route, entries, uses_ark_people)) or True
    rules = body.get("rules")
    if not isinstance(rules, list):
        return h._json({"error": "expected {id, rules: [...]}"}, 400) or True
    return h._json(keep_event_rules(route, rules)) or True


def handle(h, method: str, u, query, srv) -> bool:
    if method == "GET":
        return handle_get(h, u, query)
    if method == "POST":
        return handle_post(h, u, srv)
    return False

