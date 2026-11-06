"""Overworld art for the people the generator puts on routes (npc roles, trainers).

Ark routes use the Arklarna route people: new graphics ids OBJ_EVENT_GFX_ARK_ROUTE_* with
sheets under graphics/object_events/pics/people/arklarna/route/ (ROM branch ark-trainers,
HGSS-in-FR-style overworlds by Delta231; credits sit next to the sheets). They are only used
when the Arklarna ROM tree defines them, so an export never names a constant the ROM lacks.
Emerald-tiled routes keep the stock sprites."""
from functools import lru_cache
from pathlib import Path

ARK_GFX_PREFIX = "OBJ_EVENT_GFX_ARK_ROUTE_"
ARK_SHEET_DIR = "people/arklarna/route"
# npc role -> Ark route-people sheet
ROLE_SHEET = {
    "hiker": "hiker", "fisherman": "fisherman", "bug_catcher": "bug_catcher", "picnicker": "picnicker",
    "old_man": "old_man", "villager": "villager", "researcher": "researcher", "woodcutter": "woodcutter",
    "blocker": "blocker", "story_blocker": "blocker",
}
# trainer events carry no class yet: alternate the two trainer sheets by position
TRAINER_SHEETS = ("youngster", "lass")
# npc role -> stock graphics id (Emerald-tiled routes, or an Ark ROM without the route people)
STOCK_NPC_GFX = {
    "researcher": "OBJ_EVENT_GFX_SCIENTIST_1", "old_man": "OBJ_EVENT_GFX_OLD_MAN",
    "villager": "OBJ_EVENT_GFX_WOMAN_2", "hiker": "OBJ_EVENT_GFX_HIKER",
    "bug_catcher": "OBJ_EVENT_GFX_BUG_CATCHER", "blocker": "OBJ_EVENT_GFX_MAN_3",
    "fisherman": "OBJ_EVENT_GFX_FISHERMAN", "picnicker": "OBJ_EVENT_GFX_PICNICKER",
}
STOCK_DEFAULT_GFX = "OBJ_EVENT_GFX_MAN_3"
# sheet -> stock overworld file, for the Routes view when a ROM has no Ark sheet
STOCK_SHEET_FILE = {
    "hiker": "people/hiker.png", "fisherman": "people/fisherman.png", "bug_catcher": "people/bug_catcher.png",
    "picnicker": "people/picnicker.png", "old_man": "people/old_man.png", "villager": "people/woman_2.png",
    "researcher": "people/scientist_1.png", "woodcutter": "people/man_3.png", "blocker": "people/man_3.png",
    "youngster": "people/youngster.png", "lass": "people/lass.png",
}


def ark_gfx(sheet: str) -> str:
    return ARK_GFX_PREFIX + sheet.upper()


def trainer_sheet(x: int, y: int) -> str:
    return TRAINER_SHEETS[(int(x) + int(y)) % len(TRAINER_SHEETS)]


def npc_gfx(role: str, ark_people: bool) -> str:
    if ark_people and role in ROLE_SHEET:
        return ark_gfx(ROLE_SHEET[role])
    return STOCK_NPC_GFX.get(role, STOCK_DEFAULT_GFX)


def rom_has_ark_people(rom) -> bool:
    header = Path(rom) / "include/constants/event_objects.h"
    return header.is_file() and ark_gfx("hiker") in header.read_text(errors="replace")


@lru_cache(maxsize=1)
def ark_people_available() -> bool:
    """True when the Arklarna ROM tree in use (ruletile.find_rom) defines the route people."""
    try:
        from .ruletile import find_rom

        return rom_has_ark_people(find_rom())
    except (FileNotFoundError, ImportError):
        return False


def use_ark_people(primary: str) -> bool:
    return str(primary).startswith("ark") and ark_people_available()
