"""Items in the Routes view: overworld sprites read from the user's own ROM tree, the
hidden-item glitter, the item constant list, and items placed by hand.

    GET  /api/sprite.png?kind=item_ball[&tiler=ark|learned]   first frame of the event's overworld sprite
                                          (404 when no ROM tree has it); tiler picks Ark or Emerald art first.
                                          People: kind=npc_<role> (npc_hiker ...), trainer, trainer_f,
                                          story_blocker; Ark routes get the Arklarna route people
                                          (pokeroutes/route_people.py), learned routes the stock sprites
    GET  /api/sprites[?tiler=ark|learned] which event kinds have a sprite
    GET  /api/glitter.png                 the hidden-item glitter frames (glitter.py)
    GET  /api/item-constants              ITEM_* names, from the ROM's include/constants/items.h when there is one
    POST /api/route-items {id, items}     replace the hand-placed items of a generated route

No sprite art lives in this repository: the PNGs are read from pokeemerald-style trees
on this machine (the Arklarna ROM found by ruletile.find_rom, then vendor/pokeemerald)."""
import io
import json
import re
from pathlib import Path

from PIL import Image

import glitter
from pokeroutes import route_people

ROOT = Path(__file__).resolve().parent.parent
PICS = Path("graphics/object_events/pics")

# event kind -> (candidate sprite files under PICS, frame index); the first file found wins
SPRITE_SOURCES = {
    "item_ball": [("misc/item_ball.png", 0), ("misc/ball_poke.png", 0)],
    "cut_tree": [("misc/cuttable_tree.png", 0)],
    "rock_smash": [("misc/breakable_rock.png", 0)],
    "strength_boulder": [("misc/pushable_boulder.png", 0)],
    "berry_tree": [("berry_trees/oran.png", 4)],
    "npc": [("people/man_3.png", 0)],
}


def person_sources(sheet: str) -> list:
    return [(f"{route_people.ARK_SHEET_DIR}/{sheet}.png", 0), (route_people.STOCK_SHEET_FILE[sheet], 0)]


SPRITE_SOURCES.update({f"npc_{role}": person_sources(sheet) for role, sheet in route_people.ROLE_SHEET.items()
                       if role != "story_blocker"})
SPRITE_SOURCES["story_blocker"] = person_sources(route_people.ROLE_SHEET["story_blocker"])
SPRITE_SOURCES["trainer"] = person_sources(route_people.TRAINER_SHEETS[0])
SPRITE_SOURCES["trainer_f"] = person_sources(route_people.TRAINER_SHEETS[1])
ARK_ONLY_PREFIX = "people/arklarna/"
FRAME_WIDTH = 16
SPRITE_TILERS = ("ark", "learned")
PLACEABLE_KINDS = ("item_ball", "hidden_item")
WALKABLE = set("gptdhyvzqn")
ITEM_NAME = re.compile(r"^ITEM_[A-Z0-9_]+$")
ITEM_DEFINITION = re.compile(r"^\s*(?:#define\s+)?(ITEM_[A-Z0-9_]+)\s*=?\s*\d", re.M)
NOT_ITEMS = {"ITEM_NONE", "ITEM_LIST_END"}
BUILTIN_ITEMS = ["ITEM_POTION", "ITEM_SUPER_POTION", "ITEM_HYPER_POTION", "ITEM_FULL_RESTORE", "ITEM_REVIVE",
                 "ITEM_POKE_BALL", "ITEM_GREAT_BALL", "ITEM_ULTRA_BALL", "ITEM_ANTIDOTE", "ITEM_PARALYZE_HEAL",
                 "ITEM_AWAKENING", "ITEM_BURN_HEAL", "ITEM_ICE_HEAL", "ITEM_FULL_HEAL", "ITEM_ETHER", "ITEM_ELIXIR",
                 "ITEM_ESCAPE_ROPE", "ITEM_REPEL", "ITEM_SUPER_REPEL", "ITEM_X_ATTACK", "ITEM_RARE_CANDY",
                 "ITEM_NUGGET", "ITEM_PEARL", "ITEM_STARDUST", "ITEM_ORAN_BERRY", "ITEM_PECHA_BERRY"]

_sprite_cache: dict = {}
_item_cache: list = []


def ark_roots() -> list:
    roots = []
    try:
        from pokeroutes.ruletile import ROM_CANDIDATES, find_rom

        try:
            roots.append(find_rom())
        except FileNotFoundError:
            pass
        roots += list(ROM_CANDIDATES)
    except ImportError:
        pass
    return roots + [parent / "arklarna/rom" for parent in ROOT.parents]


def emerald_roots() -> list:
    return [ROOT / "vendor/pokeemerald"]


def graphics_roots(tiler: str = "") -> list:
    """ROM trees to read sprites from, best first: Emerald art first for routes tiled with the
    learned (Emerald) tilesets, the Arklarna ROM first otherwise."""
    roots = emerald_roots() + ark_roots() if tiler == "learned" else ark_roots() + emerald_roots()
    return [Path(root) for root in dict.fromkeys(roots) if Path(root).is_dir()]


def sprite_file(kind: str, tiler: str = ""):
    for root in graphics_roots(tiler):
        for relative_path, frame_index in SPRITE_SOURCES.get(kind, []):
            if tiler == "learned" and relative_path.startswith(ARK_ONLY_PREFIX):
                continue  # Emerald-tiled routes keep the stock people
            candidate = root / PICS / relative_path
            if candidate.is_file():
                return candidate, frame_index
    return None, 0


def first_frame(path: Path, frame_index: int) -> Image.Image:
    sheet = Image.open(path)
    if sheet.mode == "P":
        sheet.info["transparency"] = 0
    sheet = sheet.convert("RGBA")
    frame_height = 16 if sheet.height <= 16 else 32
    left = min(frame_index * FRAME_WIDTH, sheet.width - FRAME_WIDTH)
    return sheet.crop((left, 0, left + FRAME_WIDTH, frame_height))


def sprite_png(kind: str, tiler: str = ""):
    if (kind, tiler) in _sprite_cache:
        return _sprite_cache[(kind, tiler)]
    path, frame_index = sprite_file(kind, tiler)
    if path is None:
        return None
    buffer = io.BytesIO()
    first_frame(path, frame_index).save(buffer, "PNG")
    _sprite_cache[(kind, tiler)] = buffer.getvalue()
    return _sprite_cache[(kind, tiler)]


def available_sprites(tiler: str = "") -> dict:
    return {kind: sprite_file(kind, tiler)[0] is not None for kind in SPRITE_SOURCES}


def query_tiler(query) -> str:
    tiler = query.get("tiler", [""])[0]
    return tiler if tiler in SPRITE_TILERS else ""


def item_constants() -> dict:
    if _item_cache:
        return {"items": _item_cache, "source": "rom"}
    for root in graphics_roots():
        header = root / "include/constants/items.h"
        if not header.is_file():
            continue
        names = [name for name in ITEM_DEFINITION.findall(header.read_text(errors="replace")) if name not in NOT_ITEMS]
        if names:
            _item_cache.extend(dict.fromkeys(names))
            return {"items": _item_cache, "source": "rom"}
    return {"items": BUILTIN_ITEMS, "source": "builtin"}


# ------------------------------------------------------------------ hand-placed items

def is_hand_placed(event) -> bool:
    return bool((getattr(event, "data", None) or {}).get("hand"))


def placement_problem(route, item: dict, occupied: set):
    kind, item_name = item.get("kind"), str(item.get("item", ""))
    if kind not in PLACEABLE_KINDS:
        return f"unknown item kind {kind!r}"
    if not ITEM_NAME.match(item_name):
        return f"{item_name!r} is not an ITEM_* constant"
    try:
        x, y = int(item["x"]), int(item["y"])
    except (KeyError, TypeError, ValueError):
        return "needs whole-number x and y"
    if not (0 <= x < route.w and 0 <= y < route.h):
        return "outside the map"
    if route.cls[y][x] not in WALKABLE:
        return f"cell is not walkable (class '{route.cls[y][x]}')"
    if (y, x) in occupied:
        return "another event is already there"
    return None


def place_hand_items(route, items: list) -> dict:
    """Drop the route's previous hand-placed items and add these on top of the generated
    events. Returns what was placed and what was skipped (with the reason)."""
    from pokeroutes.generate import Event

    route.events = [event for event in route.events if not is_hand_placed(event)]
    occupied = {(int(event.y), int(event.x)) for event in route.events}
    placed, skipped = [], []
    for item in items:
        problem = placement_problem(route, item, occupied) if isinstance(item, dict) else "not an object"
        if problem:
            skipped.append({"item": item, "reason": problem})
            continue
        x, y = int(item["x"]), int(item["y"])
        route.events.append(Event(item["kind"], y, x, {"item": str(item["item"]), "hand": True, "why": "placed by hand"}))
        occupied.add((y, x))
        placed.append({"kind": item["kind"], "x": x, "y": y, "item": str(item["item"])})
    return {"placed": placed, "skipped": skipped}


# ------------------------------------------------------------------ routing

def read_body(h) -> dict:
    length = int(h.headers.get("Content-Length") or 0)
    body = json.loads(h.rfile.read(length) or b"{}")
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    return body


def handle_get(h, u, query) -> bool:
    if u.path == "/api/sprite.png":
        png = sprite_png(query.get("kind", [""])[0], query_tiler(query))
        if png is None:
            return h._json({"error": "no sprite for that kind in any ROM tree on this machine"}, 404) or True
        return h._send(200, png, "image/png") or True
    if u.path == "/api/sprites":
        return h._json({"sprites": available_sprites(query_tiler(query))}) or True
    if u.path == "/api/glitter.png":
        return h._send(200, glitter.glitter_strip_png(), "image/png") or True
    if u.path == "/api/item-constants":
        return h._json(item_constants()) or True
    return False


def handle_post(h, u, srv) -> bool:
    if u.path != "/api/route-items":
        return False
    try:
        body = read_body(h)
    except ValueError as error:
        return h._json({"error": f"bad request: {error}"}, 400) or True
    route_id = str(body.get("id", ""))
    if route_id not in srv._results:
        return h._json({"error": "unknown or expired route id; generate again"}, 404) or True
    items = body.get("items")
    if not isinstance(items, list):
        return h._json({"error": "expected {id, items: [...]}"}, 400) or True
    route, _final = srv._results[route_id]
    return h._json(place_hand_items(route, items)) or True


def handle(h, method: str, u, query, srv) -> bool:
    if method == "GET":
        return handle_get(h, u, query)
    if method == "POST":
        return handle_post(h, u, srv)
    return False
