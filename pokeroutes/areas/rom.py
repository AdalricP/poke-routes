"""pokeemerald-expansion output for special areas: object events, bg events,
coord events, warps, and the scripts behind them.

Overworld Pokémon use the expansion's species object graphics,
``OBJ_EVENT_GFX_SPECIES(NAME)`` (needs ``OW_POKEMON_OBJECT_EVENTS TRUE`` in
include/config/overworld.h, which Arklarna has on). Talking to one plays its
cry, shows a line, and starts a wild battle with that species through
``setwildbattle`` / ``dowildbattle`` (the Kecleon / Sudowoodo pattern). The
command takes literal species and level, so every Pokémon gets a short stub
script. Caught or beaten, the object is removed; ordinary colony members use a
temporary flag (FLAG_TEMP_*) so the colony is back next time you enter the map,
unique ones (leaders, legendaries) get a permanent flag.

All script/text labels contain the placeholder ``{MAP}``; AreaResult.render_scripts
substitutes the map's id (e.g. ``Route3``).
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# behaviour words in recipes -> (movement type, range, faces player when talked to?)
MOVES = {
    "wander": ("MOVEMENT_TYPE_WANDER_AROUND", 2, True),
    "wander_far": ("MOVEMENT_TYPE_WANDER_AROUND", 4, True),
    "wander_slow": ("MOVEMENT_TYPE_WANDER_AROUND_SLOWER", 1, True),
    "look_around": ("MOVEMENT_TYPE_LOOK_AROUND", 0, True),
    "sleep": ("MOVEMENT_TYPE_FACE_DOWN", 0, False),
    "pace_h": ("MOVEMENT_TYPE_WALK_LEFT_AND_RIGHT", 2, True),
    "pace_v": ("MOVEMENT_TYPE_WALK_UP_AND_DOWN", 2, True),
    "circle": ("MOVEMENT_TYPE_WALK_SEQUENCE_UP_RIGHT_DOWN_LEFT", 1, True),
    "spin": ("MOVEMENT_TYPE_ROTATE_CLOCKWISE", 0, True),
    "tree_disguise": ("MOVEMENT_TYPE_TREE_DISGUISE", 0, True),
    "rock_disguise": ("MOVEMENT_TYPE_MOUNTAIN_DISGUISE", 0, True),
    "buried": ("MOVEMENT_TYPE_BURIED", 0, True),
    "hidden": ("MOVEMENT_TYPE_INVISIBLE", 0, True),
    "copy_player": ("MOVEMENT_TYPE_COPY_PLAYER", 1, True),
}
FACE = {"up": "UP", "down": "DOWN", "left": "LEFT", "right": "RIGHT"}


def face_towards(src: Tuple[int, int], dst: Tuple[int, int]) -> str:
    dy, dx = dst[0] - src[0], dst[1] - src[1]
    if abs(dy) >= abs(dx):
        return "down" if dy > 0 else "up"
    return "right" if dx > 0 else "left"


def movement(move: str, pos: Tuple[int, int], target: Optional[Tuple[int, int]] = None) -> Tuple[str, int, bool]:
    """Recipe behaviour -> (MOVEMENT_TYPE_*, range, faceplayer)."""
    if move in MOVES:
        return MOVES[move]
    if move.startswith("face"):  # face / face:<dir> / face_point (needs target)
        d = move.split(":", 1)[1] if ":" in move else (face_towards(pos, target) if target else "down")
        return f"MOVEMENT_TYPE_FACE_{FACE[d]}", 0, True
    if move.startswith("spar"):  # jog in place facing a point: sparring, displaying, digging
        d = move.split(":", 1)[1] if ":" in move else (face_towards(pos, target) if target else "down")
        return f"MOVEMENT_TYPE_JOG_IN_PLACE_{FACE[d]}", 0, True
    if move.startswith("walk_in_place"):
        d = move.split(":", 1)[1] if ":" in move else (face_towards(pos, target) if target else "down")
        return f"MOVEMENT_TYPE_WALK_SLOWLY_IN_PLACE_{FACE[d]}", 0, True
    return "MOVEMENT_TYPE_LOOK_AROUND", 0, True


def const_name(s: str) -> str:
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", s)
    return re.sub(r"[^A-Z0-9]+", "_", s.upper()).strip("_")


def label(s: str) -> str:
    return "".join(w.capitalize() for w in re.split(r"[^A-Za-z0-9]+", s) if w)


def species_const(name: str) -> str:
    """'Mr. Mime' -> MR_MIME, 'Ho-Oh' -> HO_OH, 'Farfetch'd' -> FARFETCHD."""
    n = name.replace("'", "").replace("’", "").replace(".", "")
    return const_name(n)


def text_block(lbl: str, text: str, width: int = 36) -> str:
    """Emerald text: wrap into lines, \\n between lines, \\p every two lines."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + (1 if cur else 0) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    if cur:
        lines.append(cur)
    out = []
    for i, ln in enumerate(lines):
        ln = ln.replace('"', "'")
        if i == len(lines) - 1:
            out.append(f'\t.string "{ln}$"')
        else:
            sep = "\\p" if i % 2 == 1 else "\\n"
            out.append(f'\t.string "{ln}{sep}"')
    return f"{lbl}:\n" + "\n".join(out or ['\t.string "$"']) + "\n"


class ScriptBook:
    """Collects object/bg/coord events and their scripts for one area."""

    def __init__(self, area_id: str, elevation=lambda y, x: 3):
        self.area = label(area_id)
        self.elev = elevation
        self.objects: List[dict] = []
        self.bgs: List[dict] = []
        self.coords: List[dict] = []
        self.warps: List[dict] = []
        self.scripts: List[str] = []
        self.texts: List[str] = []
        self.flags: List[str] = []           # permanent flags to allocate
        self.temp_flags: List[str] = []      # placeholders {TEMPFLAG:n}
        self.temp_vars: List[str] = []
        self.mons: List[dict] = []
        self.n = 0

    def _id(self, kind: str) -> str:
        self.n += 1
        return f"{{MAP}}_EventScript_{self.area}_{kind}{self.n}"

    def _text(self, kind: str, text: str) -> str:
        lbl = f"{{MAP}}_Text_{self.area}_{kind}{self.n}"
        self.texts.append(text_block(lbl, text))
        return lbl

    # -- overworld Pokémon -------------------------------------------------
    def mon(self, species: str, y: int, x: int, level: int, move: str = "wander", target=None,
            text: str = "", unique: bool = False, shiny: bool = False, note: str = "", flag: str = "") -> dict:
        sp = species_const(species)
        mtype, rng, faces = movement(move, (y, x), target)
        sid = self._id("Mon")
        lbl = self._text("Mon", text or f"{species.upper()}!")
        if unique:
            flag = flag or f"FLAG_ARK_{const_name(self.area)}_{sp}_{self.n}"
            self.flags.append(flag)
        else:
            flag = "{TEMPFLAG}"
        gfx = f"OBJ_EVENT_GFX_SPECIES{'_SHINY' if shiny else ''}({sp})"
        ev = {"graphics_id": gfx, "x": x, "y": y, "elevation": self.elev(y, x), "movement_type": mtype,
              "movement_range_x": rng, "movement_range_y": rng, "trainer_type": "TRAINER_TYPE_NONE",
              "trainer_sight_or_berry_tree_id": "0", "script": sid, "flag": flag}
        self.objects.append(ev)
        lines = [f"{sid}::", "\tlock"]
        if faces:
            lines.append("\tfaceplayer")
        lines += [f"\tplaymoncry SPECIES_{sp}, CRY_MODE_ENCOUNTER",
                  f"\tmsgbox {lbl}, MSGBOX_DEFAULT", "\twaitmoncry", "\tclosemessage",
                  f"\tsetwildbattle SPECIES_{sp}, {int(level)}", "\tdowildbattle",
                  "\tspecialvar VAR_RESULT, GetBattleOutcome",
                  f"\tgoto_if_eq VAR_RESULT, B_OUTCOME_CAUGHT, {sid}_Gone",
                  f"\tgoto_if_eq VAR_RESULT, B_OUTCOME_WON, {sid}_Gone",
                  "\trelease", "\tend", "", f"{sid}_Gone::", "\tremoveobject VAR_LAST_TALKED", "\trelease", "\tend", ""]
        self.scripts.append("\n".join(lines))
        m = {"species": species, "const": f"SPECIES_{sp}", "graphics_id": gfx, "x": x, "y": y, "level": int(level),
             "movement": mtype, "behaviour": move, "unique": unique, "note": note, "script": sid}
        self.mons.append(m)
        return m

    # -- people, signs, items ----------------------------------------------
    def npc(self, gfx: str, y: int, x: int, text: str, move: str = "look_around", target=None,
            give: Optional[str] = None, flag: str = "0") -> None:
        mtype, rng, _ = movement(move, (y, x), target)
        sid = self._id("Npc")
        lbl = self._text("Npc", text)
        g = gfx if gfx.startswith("OBJ_EVENT_GFX_") else f"OBJ_EVENT_GFX_{gfx}"
        self.objects.append({"graphics_id": g, "x": x, "y": y, "elevation": self.elev(y, x), "movement_type": mtype,
                             "movement_range_x": rng, "movement_range_y": rng, "trainer_type": "TRAINER_TYPE_NONE",
                             "trainer_sight_or_berry_tree_id": "0", "script": sid, "flag": flag})
        if give:
            gflag = f"FLAG_ARK_{const_name(self.area)}_GIFT_{self.n}"
            self.flags.append(gflag)
            self.scripts.append("\n".join([
                f"{sid}::", "\tlock", "\tfaceplayer", f"\tmsgbox {lbl}, MSGBOX_DEFAULT",
                f"\tgoto_if_set {gflag}, {sid}_Done", f"\tgiveitem {give}",
                f"\tgoto_if_eq VAR_RESULT, FALSE, Common_EventScript_ShowBagIsFull", f"\tsetflag {gflag}",
                f"{sid}_Done::", "\trelease", "\tend", ""]))
        else:
            self.scripts.append(f"{sid}::\n\tmsgbox {lbl}, MSGBOX_NPC\n\tend\n")

    def sign(self, y: int, x: int, text: str) -> None:
        sid = self._id("Sign")
        lbl = self._text("Sign", text)
        self.bgs.append({"type": "sign", "x": x, "y": y, "elevation": self.elev(y, x),
                         "player_facing_dir": "BG_EVENT_PLAYER_FACING_ANY", "script": sid})
        self.scripts.append(f"{sid}::\n\tmsgbox {lbl}, MSGBOX_SIGN\n\tend\n")

    def hidden_item(self, y: int, x: int, item: str, when: Optional[str] = None, text: str = "") -> None:
        flag = f"FLAG_ARK_{const_name(self.area)}_ITEM_{self.n + 1}"
        self.flags.append(flag)
        if not when:
            self.n += 1
            self.bgs.append({"type": "hidden_item", "x": x, "y": y, "elevation": self.elev(y, x), "item": item,
                             "flag": flag, "quantity": 1, "underfoot": False})
            return
        # time-gated: a bg 'sign' that checks the clock (gettimeofday -> VAR_RESULT = TIME_*)
        sid = self._id("Find")
        lbl = self._text("Find", text or "There's nothing here. Not now, anyway.")
        tod = {"night": "TIME_NIGHT", "morning": "TIME_MORNING", "day": "TIME_DAY", "evening": "TIME_EVENING"}[when]
        self.bgs.append({"type": "sign", "x": x, "y": y, "elevation": self.elev(y, x),
                         "player_facing_dir": "BG_EVENT_PLAYER_FACING_ANY", "script": sid})
        self.scripts.append("\n".join([
            f"{sid}::", "\tlockall", f"\tgoto_if_set {flag}, {sid}_None", "\tgettimeofday",
            f"\tgoto_if_ne VAR_RESULT, {tod}, {sid}_None", f"\tfinditem {item}", f"\tsetflag {flag}",
            "\treleaseall", "\tend", f"{sid}_None::", f"\tmsgbox {lbl}, MSGBOX_DEFAULT", "\treleaseall", "\tend", ""]))

    def item_ball(self, y: int, x: int, item: str) -> None:
        sid = self._id("Item")
        flag = f"FLAG_ARK_{const_name(self.area)}_BALL_{self.n}"
        self.flags.append(flag)
        self.objects.append({"graphics_id": "OBJ_EVENT_GFX_ITEM_BALL", "x": x, "y": y, "elevation": self.elev(y, x),
                             "movement_type": "MOVEMENT_TYPE_LOOK_AROUND", "movement_range_x": 1, "movement_range_y": 1,
                             "trainer_type": "TRAINER_TYPE_NONE", "trainer_sight_or_berry_tree_id": "0",
                             "script": sid, "flag": flag})
        self.scripts.append(f"{sid}::\n\tfinditem {item}\n\tend\n")

    def prop_object(self, kind: str, y: int, x: int) -> None:
        gfx, script = {
            "breakable_rock": ("OBJ_EVENT_GFX_BREAKABLE_ROCK", "EventScript_RockSmash"),
            "cuttable_tree": ("OBJ_EVENT_GFX_CUTTABLE_TREE", "EventScript_CutTree"),
            "pushable_boulder": ("OBJ_EVENT_GFX_PUSHABLE_BOULDER", "EventScript_StrengthBoulder"),
        }[kind]
        self.objects.append({"graphics_id": gfx, "x": x, "y": y, "elevation": self.elev(y, x),
                             "movement_type": "MOVEMENT_TYPE_LOOK_AROUND", "movement_range_x": 1,
                             "movement_range_y": 1, "trainer_type": "TRAINER_TYPE_NONE",
                             "trainer_sight_or_berry_tree_id": "0", "script": script, "flag": "0"})

    def weather(self, cells_in: List[Tuple[int, int]], cells_out: List[Tuple[int, int]], inside: str, outside: str) -> None:
        for (y, x) in cells_in:
            self.coords.append({"type": "weather", "x": x, "y": y, "elevation": self.elev(y, x), "weather": inside})
        for (y, x) in cells_out:
            self.coords.append({"type": "weather", "x": x, "y": y, "elevation": self.elev(y, x), "weather": outside})

    def on_enter(self, cells: List[Tuple[int, int]], text: str, cry: Optional[str] = None) -> None:
        """A once-per-visit coord trigger at the way in: a cry, a line of what you notice."""
        sid = self._id("Enter")
        lbl = self._text("Enter", text)
        var = "{TEMPVAR}"
        for (y, x) in cells:
            self.coords.append({"type": "trigger", "x": x, "y": y, "elevation": self.elev(y, x),
                                "var": var, "var_value": "0", "script": sid})
        lines = [f"{sid}::", "\tlockall"]
        if cry:
            lines += [f"\tplaymoncry SPECIES_{species_const(cry)}, CRY_MODE_NORMAL"]
        lines += [f"\tmsgbox {lbl}, MSGBOX_DEFAULT"]
        if cry:
            lines += ["\twaitmoncry"]
        lines += [f"\tsetvar {var}, 1", "\treleaseall", "\tend", ""]
        self.scripts.append("\n".join(lines))

    def ambush(self, cells: List[Tuple[int, int]], species: str, level: int, text: str = "") -> None:
        """Territorial residents: stepping onto their stretch of lane starts a fight, once per visit."""
        sp = species_const(species)
        sid = self._id("Ambush")
        lbl = self._text("Ambush", text or f"A furious {species.upper()} charges out of cover!")
        for (y, x) in cells:
            self.coords.append({"type": "trigger", "x": x, "y": y, "elevation": self.elev(y, x),
                                "var": "{TEMPVAR}", "var_value": "0", "script": sid})
        self.scripts.append("\n".join([
            f"{sid}::", "\tlockall", f"\tplaymoncry SPECIES_{sp}, CRY_MODE_ENCOUNTER",
            f"\tmsgbox {lbl}, MSGBOX_DEFAULT", "\twaitmoncry", "\tclosemessage", "\tsetvar {TEMPVAR}, 1",
            f"\tsetwildbattle SPECIES_{sp}, {int(level)}", "\tdowildbattle", "\treleaseall", "\tend", ""]))

    def warp(self, y: int, x: int, dest_map: str, dest_warp: str) -> None:
        self.warps.append({"x": x, "y": y, "elevation": self.elev(y, x), "dest_map": dest_map, "dest_warp_id": dest_warp})


def render(text: str, map_id: str, temp_flags: List[str], temp_var: str) -> str:
    return text.replace("{MAP}", map_id)


def assign_temps(objs: List[dict], coords: List[dict], scripts: str, flag_pool: List[str], var: str) -> Tuple[List[dict], List[dict], str]:
    """Replace {TEMPFLAG} with FLAG_TEMP_* (one per object, from the pool; falls back
    to '0' = always present once the pool runs out) and {TEMPVAR} with ``var``."""
    out = []
    for o in objs:
        o = dict(o)
        if o.get("flag") == "{TEMPFLAG}":
            o["flag"] = flag_pool.pop(0) if flag_pool else "0"
        out.append(o)
    cs = [dict(c, var=var) if c.get("var") == "{TEMPVAR}" else dict(c) for c in coords]
    return out, cs, scripts.replace("{TEMPVAR}", var)


TEMP_FLAGS = [f"FLAG_TEMP_{i:X}" for i in range(0x11, 0x20)]
TEMP_VARS = [f"VAR_TEMP_{i:X}" for i in range(1, 10)]
