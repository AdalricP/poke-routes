"""Write a generated route out: pokeemerald layout/map files, a scene file for
the viewer, and debug images."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw

from .classify import CLASS_COLORS
from . import route_people
from .route_effects import effect_summary_for_route, emerald_weather_for_route
from .gates import class_grid_with_route_gates_open
from .generate import WALKABLE, Route, reachable

ZONE_COLORS = {
    "main": (240, 200, 90), "branch": (120, 200, 255), "loop": (255, 140, 200), "dead_end": (200, 160, 255),
    "hidden": (255, 80, 80), "trail": (255, 255, 255), "clearing": (140, 230, 140), "special": (255, 170, 60),
    "lake": (90, 150, 255),
}


BIOME_HABITATS = {
    "forest": {"forest", "deep_forest", "tall_grass", "meadow"},
    "meadow": {"meadow", "tall_grass", "flower_field", "farmland"},
    "plains": {"meadow", "tall_grass", "farmland"},
    "riverland": {"riverbank", "lake", "marsh", "meadow"},
}
RARITY_WEIGHT = {"common": 40, "uncommon": 14, "rare": 4, "very_rare": 1}


def grade_forest(im: Image.Image) -> Image.Image:
    """Forest reads too close to meadow at the same brightness: darken it under the
    canopy, cool the shadows a touch, and fall off further at the edges (more light
    reaches a clearing than the deep treeline) so it feels like standing under trees."""
    a = np.asarray(im.convert("RGB")).astype(np.float32)
    h, w = a.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.hypot((yy - h / 2) / (h / 2), (xx - w / 2) / (w / 2))
    vignette = 1.0 - 0.15 * np.clip(r - 0.4, 0, 1)
    a *= (0.78 * vignette)[..., None]
    a[..., 2] *= 1.05  # cooler shade
    a[..., 1] *= 0.98
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


SNOW_GRADE_SATURATION_KEPT = 0.22
SNOW_GRADE_BRIGHTNESS_FLOOR = 0.5
SNOW_GRADE_COOL_TINT_RGB = (0.94, 0.97, 1.06)


def grade_snow(im: Image.Image) -> Image.Image:
    """Emerald has no snow: a snow route is tiled with Route 113's ash set, then
    washed toward white (bright ground, blue-grey rock) for the preview until the
    tileset has snow art. Preview only: the ROM keeps the ash tiles."""
    rgb = np.asarray(im.convert("RGB")).astype(np.float32) / 255.0
    grey = rgb.mean(axis=2, keepdims=True)
    washed = grey + (rgb - grey) * SNOW_GRADE_SATURATION_KEPT
    brightened = SNOW_GRADE_BRIGHTNESS_FLOOR + (1 - SNOW_GRADE_BRIGHTNESS_FLOOR) * washed
    cooled = brightened * np.array(SNOW_GRADE_COOL_TINT_RGB, dtype=np.float32)
    graded = Image.fromarray(np.clip(cooled * 255, 0, 255).astype(np.uint8))
    return graded


def grade_for_biome(im: Image.Image, biome: str) -> Image.Image:
    if biome == "forest":
        return grade_forest(im)
    if biome == "snow":
        return grade_snow(im)
    return im


def encounters(route: Route) -> dict:
    """Weighted wild tables: one for the route's tall grass, one per special area."""
    path = route.spec.species_file
    if not path or not Path(path).exists():
        return {}
    dex = json.loads(Path(path).read_text())
    habs = BIOME_HABITATS.get(route.spec.biome, set())
    table = []
    for sp in dex:
        if not sp.get("wild_level") or sp.get("rarity") not in RARITY_WEIGHT:
            continue
        if habs & set(sp.get("habitats", [])):
            table.append({"name": sp["name"], "types": sp.get("types", []), "levels": sp["wild_level"],
                          "weight": RARITY_WEIGHT[sp["rarity"]], "rarity": sp["rarity"]})
    areas = {}
    by_name = {sp["name"]: sp for sp in dex}
    for z in route.zones:
        a = z.meta.get("area") if z.kind == "special" else None
        if a and a.get("species") in by_name:
            sp = by_name[a["species"]]
            lv = sp.get("wild_level") or [20, 30]
            areas[a["id"]] = [{"name": sp["name"], "types": sp.get("types", []), "levels": lv, "weight": 30,
                               "rarity": sp.get("rarity"), "shiny_boost": a.get("shiny_boost", 1)}] + \
                             [dict(t, weight=max(1, t["weight"] // 4)) for t in table[:6]]
    return {"route": table, "areas": areas}


def scene_dict(route: Route) -> dict:
    s = route.spec
    return {
        "name": s.name,
        "id": s.id,
        "seed": s.seed,
        "biome": s.biome,
        **effect_summary_for_route(s),
        "width": route.w,
        "height": route.h,
        "grid": ["".join(row) for row in route.cls],
        "zones": [
            {"kind": z.kind, "name": z.name, "cells": [[int(y), int(x)] for (y, x) in z.cells],
             "meta": {k: v for k, v in z.meta.items() if k in ("center", "end", "ledge", "purpose", "where")} |
                     ({"area": {k: z.meta["area"].get(k) for k in ("id", "name", "description", "species", "terrain_marks", "shiny_boost")}}
                      if "area" in z.meta else {})}
            for z in route.zones
        ],
        "events": [{"kind": e.kind, "y": e.y, "x": e.x, **e.data} for e in route.events],
        "props": route.props,
        "purposes": route.__dict__.get("purposes", []),
        "encounters": encounters(route),
        "exits": [{"side": e.side, "to": e.to, "cells": [[int(y), int(x)] for (y, x) in cells]} for e, cells in route.exits],
        "gates": route.gates,
    }


def write_scene(route: Route, path: Path) -> None:
    Path(path).write_text(json.dumps(scene_dict(route), separators=(",", ":"), default=int))


def _const(name: str) -> str:
    import re

    return re.sub(r"[^A-Z0-9]+", "_", re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).upper()).strip("_")


# --------------------------------------------------------------- GBA limits / helpers

MAX_MAP_DATA_SIZE = 10240  # include/fieldmap.h: (width + 15) * (height + 14) must fit
DIRECTION = {"north": "up", "south": "down", "east": "right", "west": "left"}
OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}


def check_size(w: int, h: int) -> None:
    if (w + 15) * (h + 14) > MAX_MAP_DATA_SIZE:
        raise ValueError(f"{w}x{h} is too big for the GBA map buffer: (w+15)*(h+14) must be <= {MAX_MAP_DATA_SIZE}")


def exit_span(route: Route, e) -> tuple:
    """(first cell along the shared edge, width) of an exit's opening."""
    cells = next(c for x, c in route.exits if x is e)
    along = [x for (_, x) in cells] if e.side in ("north", "south") else [y for (y, _) in cells]
    return min(along), len(along)


def connection_offset(my_start: int, their_start: int) -> int:
    """pokeemerald connection offset: position of the connected map along the shared
    edge, relative to this map. Lining up both openings gives mine - theirs."""
    return my_start - their_start


WATER_THAT_RUNS_OFF_THE_MAP_EDGE = "wba"
SEAL_DEPTH = 2  # GBA trees are 2x2 metatiles; a 1-deep ring tiles as half trees


def seal_edges(route: Route) -> int:
    """Close the outer ring of the map (2 deep) except at the exits.

    Carved paths and clearings can run into the map edge. On the GBA the edge is a
    connection seam: an opening there that the neighbouring map doesn't line up with
    reads as a road into nothing and is a wall in-game. Walkable pockets that were only
    reachable along the edge are filled with trees (cliff on a desert / volcanic route,
    whatever the biome walls its lanes with). Returns the number of cells changed."""
    from .stages import wall_class

    h, w, d = route.h, route.w, SEAL_DEPTH
    wall = wall_class(route)
    keep = np.zeros((h, w), dtype=bool)
    keep[d:-d, d:-d] = True
    for e, cells in route.exits:
        for (y, x) in cells:
            for k in range(d):
                yy = y + k if e.side == "north" else y - k if e.side == "south" else y
                xx = x + k if e.side == "west" else x - k if e.side == "east" else x
                keep[yy, xx] = True
    before = route.cls.copy()
    # rock-mass tops and trees (a plains verge's tree line) are wall already
    route.cls[~keep & ~np.isin(route.cls, [wall, "r", "f"] + list(WATER_THAT_RUNS_OFF_THE_MAP_EDGE))] = wall
    seen = reachable(class_grid_with_route_gates_open(route), route.exits[0][1])
    orphan = np.isin(route.cls, list("gpt")) & ~seen
    route.cls[orphan] = wall
    drop_obstacles_left_standing_in_walls(route)
    return int((before != route.cls).sum())


def drop_obstacles_left_standing_in_walls(route: Route) -> None:
    from .generate import WALKABLE
    from .obstacles import FIELD_MOVE_OBSTACLE_KINDS

    route.events = [event for event in route.events
                    if event.kind not in FIELD_MOVE_OBSTACLE_KINDS or str(route.cls[event.y, event.x]) in WALKABLE]


def exits_dict(route: Route) -> list:
    """Each exit's opening: side, target map, first cell along the edge and width."""
    out = []
    for e, _ in route.exits:
        start, width = exit_span(route, e)
        out.append({"side": e.side, "to": e.to, "start": int(start), "width": int(width)})
    return out


def edge_class(route: Route) -> str:
    ring = np.concatenate([route.cls[0, :], route.cls[-1, :], route.cls[:, 0], route.cls[:, -1]])
    vals, counts = np.unique(ring, return_counts=True)
    return str(vals[int(np.argmax(counts))])


def write_emerald(route: Route, layout, out: Path, primary: str, secondary: str,
                  offsets: Optional[dict] = None, borders: Optional[dict] = None,
                  ark_people: Optional[bool] = None) -> None:
    """Files for pokeemerald-expansion (Porymap format):
    data/layouts/<Id>/{map.bin,border.bin,layouts_entry.json}, data/maps/<Id>/{map.json,scripts.inc},
    plus region_map_section.json, flags.json and exits.json for an installer to merge.

    ``offsets`` maps exit index -> connection offset (from :func:`region_offsets`) for exits
    into other routes generated in the same run; the rest are left at 0 for the installer.

    ``ark_people`` picks the Arklarna route-people sprites (OBJ_EVENT_GFX_ARK_ROUTE_*) for npcs;
    by default they are used on Ark-tiled maps when the Arklarna ROM defines them."""
    s = route.spec
    check_size(route.w, route.h)
    out = Path(out)
    lay_dir = out / "data/layouts" / s.id
    map_dir = out / "data/maps" / s.id
    lay_dir.mkdir(parents=True, exist_ok=True)
    map_dir.mkdir(parents=True, exist_ok=True)
    (lay_dir / "map.bin").write_bytes(layout.to_blockdata())
    border = (borders or {}).get(edge_class(route)) or [0x1D4, 0x1D5, 0x1DC, 0x1DD]
    (lay_dir / "border.bin").write_bytes(np.array(border, dtype="<u2").tobytes())
    cname = _const(s.id)
    entry = {
        "id": f"LAYOUT_{cname}", "name": f"{s.id}_Layout", "width": route.w, "height": route.h,
        "primary_tileset": f"gTileset_{primary.title()}", "secondary_tileset": f"gTileset_{secondary.title()}",
        "border_filepath": f"data/layouts/{s.id}/border.bin", "blockdata_filepath": f"data/layouts/{s.id}/map.bin",
        "layout_version": "emerald",
    }
    (lay_dir / "layouts_entry.json").write_text(json.dumps(entry, indent=2))

    def elev(y, x):
        return int(layout.elevation[y, x])

    seen = reachable(class_grid_with_route_gates_open(route), route.exits[0][1]) & np.isin(route.cls, list(WALKABLE))
    taken = set()

    def settle(y, x):
        """Later stages (cleanup) can grow trees over a hidden item's pocket; move such
        items to the nearest reachable ground so they can still be found."""
        if seen[y, x] and (y, x) not in taken:
            return y, x
        ys, xs = np.nonzero(seen)
        order = np.argsort((ys - y) ** 2 + (xs - x) ** 2)
        for k in order:
            c = (int(ys[k]), int(xs[k]))
            if c not in taken:
                return c
        return y, x

    if ark_people is None:
        ark_people = route_people.use_ark_people(primary)
    objs, bgs, coords, warps, flags, scripts = build_events(route, elev, settle, taken, cname, ark_people)
    conns = []
    for i, (e, _) in enumerate(route.exits):
        if not e.to:
            continue
        # offsets to maps outside this run are filled in by the ROM installer, which can
        # read where the target map's opening is (it writes them from exits.json)
        conns.append({"map": e.to, "offset": int((offsets or {}).get(i, 0)), "direction": DIRECTION[e.side]})
    mapsec = f"MAPSEC_{cname}"
    m = {
        "id": f"MAP_{cname}", "name": s.id, "layout": f"LAYOUT_{cname}", "music": s.music,
        "region": "REGION_HOENN", "region_map_section": mapsec, "requires_flash": False, "weather": emerald_weather_for_route(s),
        "map_type": "MAP_TYPE_ROUTE", "allow_cycling": True, "allow_escaping": False, "allow_running": True,
        "show_map_name": True, "battle_scene": "MAP_BATTLE_SCENE_NORMAL",
        "connections": conns or None,
        "object_events": objs, "warp_events": warps, "coord_events": coords, "bg_events": bgs,
    }
    (map_dir / "map.json").write_text(json.dumps(m, indent=2))
    (map_dir / "scripts.inc").write_text(f"@ generated by poke-routes (export.py); regenerate instead of editing\n"
                                          f"{s.id}_MapScripts::\n\t.byte 0\n\n" + scripts)
    (out / "region_map_section.json").write_text(json.dumps({"id": mapsec, "name": s.name.upper()}, indent=2))
    (out / "flags.json").write_text(json.dumps(flags, indent=2))
    (out / "exits.json").write_text(json.dumps(exits_dict(route), indent=2))


# --------------------------------------------------------------- events + scripts

NPC_TEXT = {
    "old_man": "These woods were here long before the road. Stay on it after dark and you'll be fine.",
    "villager": "We come up here for firewood. The wild POKéMON keep to the long grass, mostly.",
    "hiker": "The ground gets rockier further on. Good boots, that's all you need.",
    "bug_catcher": "Every one of these trees has a bug POKéMON in it. I'm going to find them all!",
    "blocker": "Hold on. Nobody's going through here just now.",
}
ACCESS_HINT = {
    "cut_tree": "A thin tree blocks the way in. You'd need CUT.",
    "rock_smash": "A cracked rock blocks the way in. A strong POKéMON could break it.",
    "strength_boulder": "There's a heavy boulder in the gap. You'd need STRENGTH to move it.",
    "hidden_gap": "Look for a gap in the trees behind the tall grass.",
    "ledge": "You get in from the high side and hop down.",
    "npc_block": "Someone stands in the way most days.",
    "none": "Just follow your nose.",
}
VIEW_TEXT = [
    "The trees open up here. Far off, chimney smoke rises over the canopy.",
    "A good spot to rest. The wind smells of pine resin.",
    "From here you can hear water somewhere below the trees.",
    "Someone carved initials into the bark here. They're old and grown over.",
]
LORE_TEXT = {
    "old_sign": "The paint has flaked off. Only the word '...WEG' is still legible.",
    "shrine": "A small wayside shrine. Someone left a fresh ORAN BERRY in front of it.",
    "grave": "A mossy stone. The name worn off it long ago.",
    "ruin": "Old foundation stones. A house stood here once.",
    "hut_plot": "A ring of stones and some charred wood. A woodcutter's hut, long gone.",
}


def _place_name(map_const: str) -> str:
    n = (map_const or "").replace("MAP_", "")
    if n.startswith("ROUTE"):
        return "ROUTE " + n[5:]
    return n.replace("_", " ").title().replace(" Town", "").upper()


def _sign_text(route: Route, e, k: int) -> str:
    kind = e.data.get("type", "")
    name = route.spec.name.upper()
    if kind == "signpost":
        dirs = [f"{x.side.upper()}: {_place_name(x.to)}" for x, _ in route.exits if x.to]
        return f"{name}. " + ". ".join(dirs) + "."
    if kind == "landmark":
        t = e.data.get("text") or ""
        return f"{t.upper()}. Please keep to the path." if t else f"{name}. Please keep to the path."
    if kind == "view":
        return VIEW_TEXT[k % len(VIEW_TEXT)]
    return LORE_TEXT.get(kind, e.data.get("text") or f"{name}.")


def _area_access(route: Route) -> dict:
    out = {}
    for res in route.__dict__.get("_area_results") or []:
        try:
            from .areas import requirements

            out[res.area.get("name")] = requirements(res.area).get("access", "none")
        except Exception:
            out[res.area.get("name")] = "none"
    return out


def _npc_text(route: Route, e) -> str:
    role = e.data.get("role", "villager")
    if e.data.get("text"):
        return e.data["text"]
    near = e.data.get("near")
    if near:
        hint = ACCESS_HINT.get(_area_access(route).get(near, "none"), "")
        return f"I'm here to study {near}. It's tucked away off the path. {hint}"
    return NPC_TEXT.get(role, NPC_TEXT["villager"])


def _obj(gfx, x, y, script, flag="0", move="MOVEMENT_TYPE_LOOK_AROUND", rng=1):
    return {"graphics_id": gfx, "x": int(x), "y": int(y), "elevation": 3, "movement_type": move,
            "movement_range_x": rng, "movement_range_y": rng, "trainer_type": "TRAINER_TYPE_NONE",
            "trainer_sight_or_berry_tree_id": "0", "script": script, "flag": flag}


PROP_OBJ = {"cut_tree": ("OBJ_EVENT_GFX_CUTTABLE_TREE", "EventScript_CutTree"),
            "rock_smash": ("OBJ_EVENT_GFX_BREAKABLE_ROCK", "EventScript_RockSmash"),
            "strength_boulder": ("OBJ_EVENT_GFX_PUSHABLE_BOULDER", "EventScript_StrengthBoulder")}
TEMP_FLAGS = [f"FLAG_TEMP_{i:X}" for i in range(0x11, 0x20)]
# Cut trees and smashed rocks need a flag: removeobject sets it, and with flag 0 the object
# respawns as soon as the camera scrolls. FLAG_TEMP_* clear on map load, like vanilla.
HM_TEMP_FLAGS = [f"FLAG_TEMP_{i:X}" for i in range(0x1, 0x11)]


STORY_GATE_BLOCKER_TEXT = "Sorry, you can't go through here yet. Come back later."


def story_gate_export(route_id: str, map_constant_name: str, story_blocker_event, ark_people: bool = False) -> tuple:
    from .areas.rom import text_block

    story_gate_flag = f"FLAG_ARK_{map_constant_name}_STORY_GATE_OPEN"
    story_gate_script_id = f"{route_id}_EventScript_StoryGate"
    story_gate_object = _obj(route_people.npc_gfx("story_blocker", ark_people), story_blocker_event.x, story_blocker_event.y, story_gate_script_id,
                             story_gate_flag, move="MOVEMENT_TYPE_FACE_DOWN", rng=0)
    story_gate_script = (f"@ story gate: {story_blocker_event.data.get('script_note', 'moves after a story beat')}. "
                         f"Set {story_gate_flag} at that beat and this person is gone.\n"
                         f"{story_gate_script_id}::\n\tmsgbox {story_gate_script_id}_Text, MSGBOX_NPC\n\tend\n\n"
                         + text_block(f"{story_gate_script_id}_Text", STORY_GATE_BLOCKER_TEXT, width=30))
    return story_gate_object, story_gate_flag, story_gate_script


def build_events(route: Route, elev, settle, taken: set, cname: str, ark_people: bool = False):
    """Every event of the route and its special areas, plus the scripts.inc body.
    Returns (object_events, bg_events, coord_events, warp_events, permanent flags, scripts)."""
    from .areas.rom import text_block

    s = route.spec
    objs, bgs, coords, warps, flags, sc = [], [], [], [], [], []
    used = set()
    n = {"sign": 0, "npc": 0, "ball": 0, "hidden": 0}
    hm_pool = list(HM_TEMP_FLAGS)

    def free(y, x):
        return (y, x) not in used

    for e in route.events:
        k = e.kind
        if k == "hidden_item":
            n["hidden"] += 1
            flag = f"FLAG_HIDDEN_ITEM_{cname}_{n['hidden']:02d}"
            flags.append(flag)
            y, x = settle(e.y, e.x)
            taken.add((y, x))
            bgs.append({"type": "hidden_item", "x": x, "y": y, "elevation": elev(y, x), "item": e.data["item"],
                        "flag": flag, "quantity": 1, "underfoot": False})
        elif k in PROP_OBJ and free(e.y, e.x):
            gfx, script = PROP_OBJ[k]
            flag = hm_pool.pop(0) if k != "strength_boulder" and hm_pool else "0"
            objs.append(_obj(gfx, e.x, e.y, script, flag))
            used.add((e.y, e.x))
        elif k == "sealed_gate" and free(e.y, e.x):
            # an old stone figure in a hidden gap; its lever (gate_switch) sets the flag that hides it
            flag = f"FLAG_ARK_{cname}_{e.data['gate'].upper()}_OPEN"
            flags.append(flag)
            sid = f"{s.id}_EventScript_{e.data['gate'].title()}"
            objs.append(_obj("OBJ_EVENT_GFX_TRICK_HOUSE_STATUE", e.x, e.y, sid, flag, move="MOVEMENT_TYPE_FACE_DOWN", rng=0))
            sc.append(f"{sid}::\n\tmsgbox {sid}_Text, MSGBOX_SIGN\n\tend\n\n"
                      + text_block(f"{sid}_Text", "An old stone figure fills the gap. It won't budge. "
                                                  "There must be a way to move it somewhere.", width=30))
            used.add((e.y, e.x))
        elif k == "story_blocker" and free(e.y, e.x):
            story_gate_object, story_gate_flag, story_gate_script = story_gate_export(s.id, cname, e, ark_people)
            flags.append(story_gate_flag)
            objs.append(story_gate_object)
            sc.append(story_gate_script)
            used.add((e.y, e.x))
        elif k == "gate_switch":
            flag = f"FLAG_ARK_{cname}_{e.data['opens'].upper()}_OPEN"
            sid = f"{s.id}_EventScript_{e.data['opens'].title()}Lever"
            bgs.append({"type": "sign", "x": e.x, "y": e.y, "elevation": 0,
                        "player_facing_dir": "BG_EVENT_PLAYER_FACING_ANY", "script": sid})
            sc.append(f"{sid}::\n\tlockall\n\tgoto_if_set {flag}, {sid}_Done\n"
                      f"\tmsgbox {sid}_Text, MSGBOX_DEFAULT\n\tsetflag {flag}\n\tplayse SE_M_STRENGTH\n"
                      f"\twaitse\n\tmsgbox {sid}_TextFar, MSGBOX_DEFAULT\n\treleaseall\n\tend\n"
                      f"{sid}_Done::\n\treleaseall\n\tend\n\n"
                      + text_block(f"{sid}_Text", "There's a worn stone handle in the roots. You pull it...", width=30)
                      + text_block(f"{sid}_TextFar", "Somewhere off in the trees, stone grinds on stone.", width=30))
        elif k == "item_ball" and free(e.y, e.x):
            n["ball"] += 1
            flag = f"FLAG_ARK_{cname}_BALL_{n['ball']:02d}"
            flags.append(flag)
            sid = f"{s.id}_EventScript_ItemBall{n['ball']}"
            objs.append(_obj("OBJ_EVENT_GFX_ITEM_BALL", e.x, e.y, sid, flag))
            sc.append(f"{sid}::\n\tfinditem {e.data['item']}\n\tend\n")
            used.add((e.y, e.x))
        elif k == "sign":
            n["sign"] += 1
            sid = f"{s.id}_EventScript_Sign{n['sign']}"
            bgs.append({"type": "sign", "x": e.x, "y": e.y, "elevation": 0,
                        "player_facing_dir": "BG_EVENT_PLAYER_FACING_ANY", "script": sid})
            sc.append(f"{sid}::\n\tmsgbox {s.id}_Text_Sign{n['sign']}, MSGBOX_SIGN\n\tend\n\n"
                      + text_block(f"{s.id}_Text_Sign{n['sign']}", _sign_text(route, e, n["sign"]), width=30))
        elif k == "npc" and free(e.y, e.x):
            n["npc"] += 1
            sid = f"{s.id}_EventScript_Npc{n['npc']}"
            gfx = route_people.npc_gfx(e.data.get("role"), ark_people)
            objs.append(_obj(gfx, e.x, e.y, sid, move="MOVEMENT_TYPE_FACE_DOWN", rng=0))
            sc.append(f"{sid}::\n\tmsgbox {s.id}_Text_Npc{n['npc']}, MSGBOX_NPC\n\tend\n\n"
                      + text_block(f"{s.id}_Text_Npc{n['npc']}", _npc_text(route, e), width=30))
            used.add((e.y, e.x))
        # trainers and berry trees: not exported yet (V1)

    # special areas: AreaResult events/scripts with {MAP} filled in. Each area numbers its
    # FLAG_TEMP_* from the same start, so they are renumbered across the map here.
    pool = list(TEMP_FLAGS)
    for res in route.__dict__.get("_area_results") or []:
        ev = res.render_events(s.id)
        for o in ev["object_events"]:
            if (o["y"], o["x"]) in used:
                continue
            o = dict(o)
            if str(o.get("flag", "")).startswith("FLAG_TEMP_"):
                o["flag"] = pool.pop(0) if pool else "0"
            objs.append(o)
            used.add((o["y"], o["x"]))
        bgs += ev["bg_events"]
        coords += ev["coord_events"]
        if ev["warp_events"]:
            route.log.append(f"{res.area.get('id')}: sub-map warps not exported (V1)")
        flags += [f for f in res.flags if f not in flags]
        sc.append(f"@ ---- special area: {res.area.get('name')}\n" + res.render_scripts(s.id))
    return objs, bgs, coords, warps, flags, "\n".join(sc)

def region_offsets(routes: list) -> dict:
    """For routes generated together: {(route index, exit index): offset} for every exit
    whose target is another route in the set with an exit on the opposite side pointing back."""
    by_map = {f"MAP_{_const(r.spec.id)}": i for i, r in enumerate(routes)}
    out = {}
    for i, r in enumerate(routes):
        me = f"MAP_{_const(r.spec.id)}"
        for k, (e, _) in enumerate(r.exits):
            j = by_map.get(e.to)
            if j is None:
                continue
            back = [x for x, _ in routes[j].exits if x.to == me and x.side == OPPOSITE[e.side]]
            if not back:
                print(f"warning: {r.spec.id} {e.side} -> {e.to}, but it has no {OPPOSITE[e.side]} exit back")
                continue
            out[(i, k)] = connection_offset(exit_span(r, e)[0], exit_span(routes[j], back[0])[0])
    return out


def debug_image(route: Route, scale: int = 8, zones: bool = True) -> Image.Image:
    c = np.array([[CLASS_COLORS.get(v, (255, 0, 255)) for v in row] for row in route.cls], dtype=np.uint8)
    im = Image.fromarray(c).resize((route.w * scale, route.h * scale), Image.NEAREST)
    d = ImageDraw.Draw(im, "RGBA")
    if zones:
        for z in route.zones:
            if z.kind in ("clearing", "special", "lake"):
                continue
            col = ZONE_COLORS.get(z.kind, (255, 255, 255))
            pts = [(x * scale + scale // 2, y * scale + scale // 2) for (y, x) in z.cells]
            if len(pts) > 1:
                d.line(pts, fill=col + (230,), width=max(1, scale // 4))
        for z in route.zones:
            if z.kind == "special":
                ys, xs = zip(*z.cells)
                d.rectangle([min(xs) * scale, min(ys) * scale, (max(xs) + 1) * scale, (max(ys) + 1) * scale],
                            outline=ZONE_COLORS["special"] + (255,), width=2)
                d.text((min(xs) * scale + 2, min(ys) * scale - 11), z.name, fill=(255, 255, 255, 255))
    for wall in route.__dict__.get("false_walls") or []:
        for (y, x) in wall:  # walk-through trees: drawn as trees in the tiled map
            d.rectangle([x * scale + 1, y * scale + 1, (x + 1) * scale - 2, (y + 1) * scale - 2], outline=(150, 110, 255, 255))
    for e in route.events:
        col = {"hidden_item": (255, 60, 60), "cut_tree": (60, 255, 60), "rock_smash": (200, 200, 200),
               "strength_boulder": (150, 110, 70), "story_blocker": (255, 80, 200),
               "sealed_gate": (150, 110, 255), "gate_switch": (150, 110, 255)}.get(e.kind, (255, 255, 0))
        cx, cy = e.x * scale + scale // 2, e.y * scale + scale // 2
        d.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=col + (255,), outline=(0, 0, 0, 255))
    return im


def stage_sheet(route: Route, scale: int = 4) -> Image.Image:
    """One small panel per pipeline stage, left to right."""
    panels = []
    for name, grid in route.snapshots:
        c = np.array([[CLASS_COLORS.get(v, (255, 0, 255)) for v in row] for row in grid], dtype=np.uint8)
        p = Image.fromarray(c).resize((route.w * scale, route.h * scale), Image.NEAREST)
        canvas = Image.new("RGB", (p.width, p.height + 16), (20, 20, 20))
        canvas.paste(p, (0, 16))
        ImageDraw.Draw(canvas).text((3, 2), name, fill=(230, 230, 230))
        panels.append(canvas)
    pad = 6
    sheet = Image.new("RGB", (sum(p.width for p in panels) + pad * (len(panels) + 1), panels[0].height + 2 * pad), (20, 20, 20))
    x = pad
    for p in panels:
        sheet.paste(p, (x, pad))
        x += p.width + pad
    return sheet


def report(route: Route) -> dict:
    seen = reachable(class_grid_with_route_gates_open(route), route.exits[0][1])
    walk = np.isin(route.cls, list("gpt"))
    counts = {k: int((route.cls == k).sum()) for k in "fgptwrs"}
    return {
        "size": [route.w, route.h],
        "walkable_cells": int(walk.sum()),
        "reachable_fraction": round(float((seen & walk).sum() / max(1, walk.sum())), 3),
        "classes": counts,
        "zones": {k: sum(1 for z in route.zones if z.kind == k) for k in sorted({z.kind for z in route.zones})},
        "events": {k: sum(1 for e in route.events if e.kind == k) for k in sorted({e.kind for e in route.events})},
        "special_areas": [z.name for z in route.zones if z.kind == "special"],
        **effect_summary_for_route(route.spec),
        "gates": route.gates,
    }
