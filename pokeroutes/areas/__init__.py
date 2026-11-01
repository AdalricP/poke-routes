"""Special areas: small places on a route that belong to one species.

Each of the regional dex's special areas has a recipe in
``arklarna/design/area_recipes.json``: the terrain it needs (a rocky rise in deep
forest, a river bend, a cliff foot...), a composition written in the stamp
vocabulary (:mod:`.stamps`), what the Pokémon there are doing, the overworld
Pokémon that show it, and something to interact with. Some areas are sub-maps:
a separate small map behind an entrance on the route (cave mouth, hollow tree,
cellar).

Interface for the generator::

    areas.load_areas(species_file=None) -> [area]
    areas.pick(route, wanted) -> [area]
    areas.requirements(area) -> dict
    areas.score_site(route, area, cells) -> float        # -inf = invalid
    areas.find_site(route, area) -> (y0, x0) | None       # top-left of the footprint
    areas.build(route, area, origin) -> AreaResult        # paints route.cls in place
    areas.connect(route, result)                          # optional: trail from lane to result.door

Nothing here imports pokeroutes.generate, so it works on any object with
``cls`` (H x W array of class chars), ``rng`` and optionally ``protect``,
``zones``, ``spec``.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import contexts, rom, stamps, tiles

Cell = Tuple[int, int]
DESIGN = Path(os.environ.get("ARKLARNA_DESIGN", "/Users/aryan/Desktop/experiments/arklarna/design"))
RECIPES_FILE = "area_recipes.json"
WALK = stamps.WALK
LANE_KINDS = ("main", "loop", "branch", "dead_end", "trail")

ROUTE_WEATHER = {  # spec.weather -> coord event weather, to restore when leaving an area
    "WEATHER_SUNNY": "COORD_EVENT_WEATHER_SUNNY", "WEATHER_SUNNY_CLOUDS": "COORD_EVENT_WEATHER_SUNNY_CLOUDS",
    "WEATHER_RAIN": "COORD_EVENT_WEATHER_RAIN", "WEATHER_FOG_HORIZONTAL": "COORD_EVENT_WEATHER_FOG_HORIZONTAL",
    "WEATHER_SHADE": "COORD_EVENT_WEATHER_SHADE", "WEATHER_VOLCANIC_ASH": "COORD_EVENT_WEATHER_VOLCANIC_ASH",
    "WEATHER_SNOW": "COORD_EVENT_WEATHER_SNOW", "WEATHER_SANDSTORM": "COORD_EVENT_WEATHER_SANDSTORM",
}


# ---------------------------------------------------------------- data


def _sub_map(evs: List[dict], map_id: str) -> List[dict]:
    return [{k: (v.replace("{MAP}", map_id) if isinstance(v, str) else v) for k, v in e.items()} for e in evs]

@dataclass
class SubMap:
    """A small separate map behind an entrance. Export writes it as MAP_<ROUTE>_<SUFFIX>."""
    id_suffix: str
    name: str
    interior: str              # cave | grotto | cellar | ruin_interior | forest_glade
    cls: np.ndarray
    fixed: Dict[Cell, int] = field(default_factory=dict)
    custom: Dict[Cell, int] = field(default_factory=dict)
    tileset: str = "general_cave"
    music: str = "MUS_CAVE_OF_ORIGIN"
    map_type: str = "MAP_TYPE_UNDERGROUND"
    weather: str = "WEATHER_NONE"
    object_events: List[dict] = field(default_factory=list)
    bg_events: List[dict] = field(default_factory=list)
    coord_events: List[dict] = field(default_factory=list)
    warp_events: List[dict] = field(default_factory=list)  # [0] is the way back out
    scripts: str = ""
    flags: List[str] = field(default_factory=list)
    mons: List[dict] = field(default_factory=list)
    props: List[dict] = field(default_factory=list)
    exit_cell: Cell = (0, 0)
    story: str = ""

    @property
    def width(self) -> int:
        return self.cls.shape[1]

    @property
    def height(self) -> int:
        return self.cls.shape[0]

    def resolve(self, route_map: str, route_warp_index: int) -> None:
        """Point the way out at the route: dest_map = route_map, dest_warp_id = the
        index of the route-side warp in the route's warp_events."""
        for wv in self.warp_events:
            if wv["dest_map"] == "{ROUTE_MAP}":
                wv["dest_map"] = route_map
            if wv["dest_warp_id"] == "{ROUTE_WARP_ID}":
                wv["dest_warp_id"] = str(route_warp_index)

    def render_scripts(self, map_id: str) -> str:
        return self.scripts.replace("{MAP}", map_id)

    def render_events(self, map_id: str) -> Dict[str, List[dict]]:
        """map.json event lists with the {MAP} placeholder in script names filled in."""
        return {k: _sub_map(getattr(self, k), map_id)
                for k in ("object_events", "bg_events", "coord_events", "warp_events")}


@dataclass
class AreaResult:
    area: dict
    cells: List[Cell]
    center: Cell
    door: Cell                 # area cell the trail from the lane should reach
    door_out: Cell             # the cell just outside it
    fixed: Dict[Cell, int] = field(default_factory=dict)       # stock metatiles, safe on any build
    custom: Dict[Cell, int] = field(default_factory=dict)      # ark_special ids (art.py), drawn over
    collision: Dict[Cell, int] = field(default_factory=dict)   # collision for custom cells
    props: List[dict] = field(default_factory=list)
    object_events: List[dict] = field(default_factory=list)
    bg_events: List[dict] = field(default_factory=list)
    coord_events: List[dict] = field(default_factory=list)
    warp_events: List[dict] = field(default_factory=list)
    scripts: str = ""
    flags: List[str] = field(default_factory=list)
    mons: List[dict] = field(default_factory=list)
    story: str = ""
    activity: str = ""
    weather: Optional[str] = None
    encounters: List[dict] = field(default_factory=list)
    submap: Optional[SubMap] = None
    anchors: Dict[str, Cell] = field(default_factory=dict)
    box: Tuple[int, int, int, int] = (0, 0, 0, 0)  # y0, x0, h, w
    gate: dict = field(default_factory=dict)        # set by connect(): access kind, gate cell, trail
    pieces: Dict[Cell, tuple] = field(default_factory=dict)  # (piece, dy, dx) per pinned cell, for ark.translate

    def ark_fixed(self):
        """(fixed ids, class overrides) for the Ark tilesets (ArkOutdoor + ArkWoodland). See ark.py."""
        from . import ark

        return ark.translate(self.pieces)

    def render_scripts(self, map_id: str) -> str:
        return self.scripts.replace("{MAP}", map_id)

    def render_events(self, map_id: str) -> Dict[str, List[dict]]:
        """map.json event lists with the {MAP} placeholder in script names filled in."""
        return {k: _sub_map(getattr(self, k), map_id)
                for k in ("object_events", "bg_events", "coord_events", "warp_events")}

    def paint_custom(self, image, offset=(0, 0)):
        """Draw the custom (ark_special) metatiles over a rendered layout image."""
        from . import art

        return art.paint(image, self.custom, offset)

    def fixed_with_custom(self, rom: bool = True) -> Dict[Cell, int]:
        """fixed + custom ids. rom=True maps the custom pieces to their real ids in
        gTileset_ArkWoodland (art.ROM_FIRST_ID); rom=False keeps the preview ids."""
        from . import art

        return {**self.fixed, **{c: (art.rom_id(m) if rom else m) for c, m in self.custom.items()}}

    def to_json(self) -> dict:
        """A JSON-safe summary for scene files / the web UI."""
        return {
            "id": self.area["id"], "name": self.area["name"], "species": self.area.get("species"),
            "kind": self.area.get("kind"), "activity": self.activity, "story": self.story,
            "center": list(self.center), "door": list(self.door), "box": list(self.box),
            "mons": self.mons, "props": self.props, "weather": self.weather,
            "visibility": requirements(self.area)["visibility"], "access": requirements(self.area)["access"],
            "gate": {k: (list(v) if k == "cell" else v) for k, v in self.gate.items() if k in ("access", "cell")},
            "submap": None if not self.submap else {
                "suffix": self.submap.id_suffix, "name": self.submap.name, "interior": self.submap.interior,
                "size": [self.submap.width, self.submap.height], "grid": ["".join(r) for r in self.submap.cls],
                "mons": self.submap.mons},
        }


# ---------------------------------------------------------------- loading

_cache: Dict[str, object] = {}


def load_recipes(path: Optional[Path] = None) -> dict:
    p = Path(path or DESIGN / RECIPES_FILE)
    key = f"recipes:{p}:{p.stat().st_mtime if p.exists() else 0}"
    if key not in _cache:
        _cache[key] = json.loads(p.read_text()) if p.exists() else {"recipes": {}}
    return _cache[key]


def load_dex(species_file: Optional[str] = None) -> List[dict]:
    p = Path(species_file) if species_file else DESIGN / "pokedex.json"
    key = f"dex:{p}"
    if key not in _cache:
        _cache[key] = json.loads(p.read_text()) if p.exists() else []
    return _cache[key]


def load_areas(species_file: Optional[str] = None, recipes_file: Optional[str] = None) -> List[dict]:
    """Every dex special area that has a recipe, merged into one dict per area."""
    dex = load_dex(species_file)
    rec = load_recipes(Path(recipes_file) if recipes_file else (Path(species_file).parent / RECIPES_FILE
                                                                if species_file else None)).get("recipes", {})
    out = []
    for sp in dex:
        sa = sp.get("special_area")
        if not sa or sa["id"] not in rec:
            continue
        r = rec[sa["id"]]
        w, h = r.get("footprint", sa.get("size", [9, 8]))
        out.append({
            "id": sa["id"], "name": sa["name"], "species": sp["name"], "description": sa.get("description", ""),
            "terrain_marks": sa.get("terrain_marks", []), "shiny_boost": sa.get("shiny_boost", 1),
            "habitats": sp.get("habitats", []), "size": [int(w), int(h)], "kind": r.get("kind", "inline"),
            "context": r.get("context", "forest"), "biomes": r.get("biomes") or
            contexts.CONTEXTS.get(r.get("context", "forest"), {}).get("biomes", []),
            "recipe": r, "wild_level": sp.get("wild_level"), "rarity": sp.get("rarity"),
        })
    return out


def get(key: str, species_file: Optional[str] = None) -> Optional[dict]:
    k = key.lower()
    for a in load_areas(species_file):
        if k in (a["id"], a["species"].lower(), a["name"].lower()):
            return a
    return None


def pick(route, wanted: List[str]) -> List[dict]:
    """Areas for a route: 'auto' picks one that fits the route's biome; a species
    name or area id picks that area. Honours spec.exclude_areas."""
    spec = getattr(route, "spec", None)
    pool = load_areas(getattr(spec, "species_file", None) or None)
    excl = set(getattr(spec, "exclude_areas", []) or [])
    pool = [a for a in pool if a["id"] not in excl]
    biome = getattr(spec, "biome", "forest")
    chosen: List[dict] = []
    for want in wanted:
        if want == "auto":
            fits = [a for a in pool if biome in a["biomes"] and a not in chosen and a["kind"] == "inline"]
            fits = fits or [a for a in pool if biome in a["biomes"] and a not in chosen]
            if fits:
                chosen.append(fits[int(route.rng.integers(0, len(fits)))])
            continue
        w = want.lower()
        hit = [a for a in pool if w in (a["species"].lower(), a["id"], a["name"].lower())]
        if hit and hit[0] not in chosen:
            chosen.append(hit[0])
    return chosen


# ---------------------------------------------------------------- placement

# Owner rule: Pokémon are rare and precious, so special areas are mostly hidden.
#   hidden      off the lanes, reached by a narrow trail behind a gate (cut tree, boulder...)
#   secret      further in still; the trail starts in an unremarkable spot (hidden gap) or
#               behind an NPC who won't move, with a second unmarked way round
#   visible     beside a lane but fenced/gated (rare: farms, shrines people tend)
#   territorial right on a lane: the residents defend it and ambush you as you pass
VISIBILITY = ("hidden", "secret", "visible", "territorial")
VISIBILITY_ALIASES = {"glimpse": "hidden"}
VIS_DIST = {"hidden": (6, 16), "secret": (9, 22), "visible": (1, 6), "territorial": (0, 3)}
DEFAULT_ACCESS = {"hidden": "cut_tree", "secret": "hidden_gap", "visible": "none", "territorial": "none"}
ACCESS = ("cut_tree", "hidden_gap", "ledge", "npc_block", "strength_boulder", "rock_smash", "none")

def requirements(area: dict) -> dict:
    r = area.get("recipe", {})
    ctx = contexts.CONTEXTS.get(area.get("context", "forest"), contexts.CONTEXTS["forest"])
    w, h = area["size"]
    if area.get("kind") == "submap":
        w, h = r.get("approach_footprint", [9, 7])
    vis = VISIBILITY_ALIASES.get(r.get("visibility", "hidden"), r.get("visibility", "hidden"))
    dmin, dmax = r.get("path_dist", VIS_DIST.get(vis, ctx["dist"]))
    access = r.get("access") or DEFAULT_ACCESS[vis]
    out = {
        "kind": area.get("kind", "inline"), "biomes": area.get("biomes", ctx["biomes"]),
        "context": area.get("context"), "footprint": (int(w), int(h)),
        "min_path_dist": int(dmin), "max_path_dist": int(dmax),
        "visibility": vis, "access": access,
        "trail": "narrow" if vis in ("hidden", "secret") else "open",
        "door": r.get("door", "auto"),
    }
    if area.get("kind") == "submap":
        out["entrance"] = r.get("entrance", {"kind": "cave_mouth"})
        out["interior"] = {"type": r.get("interior", {}).get("type", "cave"),
                           "size": r.get("interior", {}).get("size", [16, 14])}
    return out


def score_site(route, area: dict, cells) -> float:
    """How good a footprint is for this area (higher is better, -inf invalid).
    ``cells`` is the footprint's cells (any iterable of (y, x)) or a (y0, x0) origin."""
    req = requirements(area)
    w, h = req["footprint"]
    cells = list(cells)
    if len(cells) == 2 and isinstance(cells[0], (int, np.integer)):
        y0, x0 = int(cells[0]), int(cells[1])
    else:
        ys, xs = zip(*cells)
        y0, x0 = min(ys), min(xs)
    H, W = route.cls.shape
    if y0 < 2 or x0 < 2 or y0 + h > H - 2 or x0 + w > W - 2:
        return -math.inf
    prot = getattr(route, "protect", None)
    lane = contexts.lane_mask(route)
    blocked_frac = float(prot[y0:y0 + h, x0:x0 + w].mean()) if prot is not None else 0.0
    lane_frac = float(lane[y0:y0 + h, x0:x0 + w].mean())
    dist = contexts.lane_distance(route)
    d_centre = int(dist[y0 + h // 2, x0 + w // 2])
    if not lane.any():
        d_centre = req["min_path_dist"]
    f = contexts.features(route, y0, x0, h, w)
    s = contexts.context_fit(req["context"], f)
    lo, hi = req["min_path_dist"], req["max_path_dist"] + max(w, h) // 2
    if d_centre < lo:
        s -= (lo - d_centre) * 0.8
    elif d_centre > hi:
        s -= (d_centre - hi) * 0.5
    if req["visibility"] == "hidden":
        s -= 3 * lane_frac
    s -= 6 * max(0.0, blocked_frac - lane_frac)   # other areas / reserved cells
    s -= 3 * f["water_in"] if req["context"] not in ("lakeshore", "marsh", "sea", "riverbank") else 0
    return float(s)


def find_site(route, area: dict, tries: int = 300) -> Optional[Cell]:
    w, h = requirements(area)["footprint"]
    H, W = route.cls.shape
    best, best_s = None, -math.inf
    for _ in range(tries):
        y0 = int(route.rng.integers(2, max(3, H - h - 2)))
        x0 = int(route.rng.integers(2, max(3, W - w - 2)))
        s = score_site(route, area, (y0, x0)) + route.rng.random() * 0.2
        if s > best_s:
            best, best_s = (y0, x0), s
    return best if best_s > -math.inf else None


# ---------------------------------------------------------------- build

def _keep_cells(route, y0, x0, h, w) -> set:
    keep = set()
    prot = getattr(route, "protect", None)
    for z in getattr(route, "zones", []):
        if z.kind in LANE_KINDS:
            keep.update(c for c in z.cells if y0 - 1 <= c[0] <= y0 + h and x0 - 1 <= c[1] <= x0 + w)
    if prot is not None:
        ys, xs = np.nonzero(prot[max(0, y0 - 1):y0 + h + 1, max(0, x0 - 1):x0 + w + 1])
        keep.update((int(y) + max(0, y0 - 1), int(x) + max(0, x0 - 1)) for y, x in zip(ys, xs))
    return keep


def _tileset(route) -> Tuple[str, str]:
    ts = getattr(getattr(route, "spec", None), "tileset", "general_fortree") or "general_fortree"
    a, _, b = ts.partition("_")
    return a, b or "fortree"


def _level(area: dict, species: str, explicit=None, lead=False) -> int:
    if explicit:
        return int(explicit)
    for sp in load_dex():
        if sp["name"].lower() == species.lower() and sp.get("wild_level"):
            lo, hi = sp["wild_level"]
            return int(hi + (3 if lead else 0))
    lo, hi = area.get("wild_level") or [12, 18]
    return int(hi + (8 if lead else 5))


def _door_side(route, p: stamps.Painter, want: str) -> str:
    if want in ("n", "s", "e", "w"):
        return want
    lane = contexts.lane_mask(route)
    if lane.any():
        ys, xs = np.nonzero(lane)
        k = int(np.argmin((ys - p.cy) ** 2 + (xs - p.cx) ** 2))
        dy, dx = ys[k] - p.cy, xs[k] - p.cx
        if abs(dy) >= abs(dx):
            return "s" if dy > 0 else "n"
        return "e" if dx > 0 else "w"
    return "s"


def _make_door(p: stamps.Painter, side: str) -> Tuple[Cell, Cell]:
    """Find the open cell nearest the middle of ``side``, cut straight to the box
    edge and one cell beyond. Returns (door inside, door_out outside)."""
    edge = {"s": (p.y0 + p.h - 1, p.cx), "n": (p.y0, p.cx), "e": (p.cy, p.x0 + p.w - 1), "w": (p.cy, p.x0)}[side]
    step = {"s": (1, 0), "n": (-1, 0), "e": (0, 1), "w": (0, -1)}[side]
    opens = [c for c in p.open if p.cls[c] in WALK] or [(p.cy, p.cx)]
    start = min(opens, key=lambda c: abs(c[0] - edge[0]) * 1.0 + abs(c[1] - edge[1]) * 1.5)
    y, x = start
    while p.in_box(y + step[0], x + step[1]):
        y, x = y + step[0], x + step[1]
        if (y, x) in p.fixed:
            break
        if p.cls[y, x] not in WALK:
            p.paint(y, x, "g")
    door = (y, x)
    out = (y + step[0], x + step[1])
    if p.inside(*out) and p.cls[out] not in WALK:
        p.paint(out[0], out[1], "g")
        p.open.discard(out)
    return door, out


def _connect_inside(p: stamps.Painter, door: Cell) -> None:
    """Every patch of the area's ground must be reachable from the door: carve
    the cheapest way through trees/rock (never through pinned pieces or water)."""
    from collections import deque
    import heapq

    def flood(src):
        seen = {src}
        q = deque([src])
        while q:
            y, x = q.popleft()
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                c = (y + dy, x + dx)
                if c not in seen and p.in_box(*c) and p.cls[c] in WALK:
                    seen.add(c)
                    q.append(c)
        return seen

    for _ in range(12):
        seen = flood(door)
        orphans = [c for c in p.open if c not in seen and p.cls[c] in WALK and p.in_box(*c)]
        if not orphans:
            return
        tgt = orphans[0]
        dist = {tgt: 0.0}
        prev = {}
        pq = [(0.0, tgt)]
        hit = None
        while pq:
            d, c = heapq.heappop(pq)
            if c in seen:
                hit = c
                break
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (c[0] + dy, c[1] + dx)
                if not p.in_box(*n) or n in p.fixed or p.cls[n] in "wbal":
                    continue
                cost = 1.0 if p.cls[n] in WALK else 4.0
                if d + cost < dist.get(n, 1e9):
                    dist[n] = d + cost
                    prev[n] = c
                    heapq.heappush(pq, (d + cost, n))
        if hit is None:
            for c in orphans:
                p.open.discard(c)
            continue
        c = hit
        while c != tgt:
            if p.cls[c] not in WALK:
                p.paint(c[0], c[1], "g")
            c = prev[c]


def _free_cell(p: stamps.Painter, y: int, x: int, taken: set, allow=WALK, radius=4) -> Optional[Cell]:
    best = None
    for r in range(0, radius + 1):
        for (yy, xx) in stamps.disc(y, x, r):
            if (yy, xx) in taken or not p.inside(yy, xx) or (yy, xx) in p.keep:
                continue
            if p.cls[yy, xx] in allow and (yy, xx) not in p.fixed or (p.cls[yy, xx] in allow and p.fixed.get((yy, xx)) in (0x004, 0x1D9, 0x1D0, 0x1D1, 0x1D2, 0x1D8, 0x1DA, 0x1E0, 0x1E1, 0x1E2)):
                return (yy, xx)
        if best:
            break
    return best


def _populate(p: stamps.Painter, book: rom.ScriptBook, area: dict, rec: dict, taken: set) -> None:
    """Overworld Pokémon and the interaction: recipe ``mons`` and ``interact``."""
    for m in rec.get("mons", []):
        sp = m["species"]
        n = int(m.get("n", 1))
        at = m.get("at", [0, 0])
        y, x = p.anchors.get(at, (p.cy, p.cx)) if isinstance(at, str) else p.at(*at)
        target = m.get("face")
        tgt = None
        if isinstance(target, str) and target in p.anchors:
            tgt = p.anchors[target]
        elif isinstance(target, list):
            tgt = p.at(*target)
        move = m.get("move", "wander")
        if tgt is not None and move in ("face", "spar", "walk_in_place"):
            pass
        on = set(m.get("on", ""))
        allow = WALK | on | ({"o"} if on & set("uf") else set())
        for i in range(n):
            spread = m.get("spread", 1.5)
            yy = y + (int(round((p.rng.random() - 0.5) * 2 * spread)) if i else 0)
            xx = x + (int(round((p.rng.random() - 0.5) * 2 * spread)) if i else 0)
            c = _free_cell(p, yy, xx, taken, allow=allow)
            if c is None:
                continue
            taken.add(c)
            book.mon(sp, c[0], c[1], _level(area, sp, m.get("level"), m.get("lead", False)), move=move,
                     target=tgt, text=m.get("text", ""), unique=bool(m.get("unique", False)),
                     shiny=bool(m.get("shiny", False)), note=m.get("note", ""))
    for it in rec.get("interact", []):
        kind = it["type"]
        at = it.get("at", [0, 0])
        y, x = p.anchors.get(at, (p.cy, p.cx)) if isinstance(at, str) else p.at(*at)
        if kind == "npc":
            c = _free_cell(p, y, x, taken)
            if c:
                taken.add(c)
                tgt = p.anchors.get(it["face"]) if isinstance(it.get("face"), str) else None
                book.npc(it.get("gfx", "MAN_1"), c[0], c[1], it.get("text", "..."), move=it.get("move", "look_around"),
                         target=tgt, give=it.get("give"))
        elif kind == "sign":
            # signs stand on a pinned sign tile; place it where asked if that cell is free ground
            c = _free_cell(p, y, x, taken)
            if c:
                taken.add(c)
                p.pin("sign", *c)
                book.sign(c[0], c[1], it.get("text", ""))
        elif kind == "hidden_item":
            c = _free_cell(p, y, x, taken, allow=WALK | set("fur"))
            if c:
                book.hidden_item(c[0], c[1], it["item"], it.get("when"), it.get("text", ""))
        elif kind == "item_ball":
            c = _free_cell(p, y, x, taken)
            if c:
                taken.add(c)
                book.item_ball(c[0], c[1], it["item"])
    for s in p.signs:
        book.sign(s["y"], s["x"], s.get("text", ""))
    for o in p.objects:
        if (o["y"], o["x"]) not in taken:
            taken.add((o["y"], o["x"]))
            book.prop_object(o["kind"], o["y"], o["x"])


def build(route, area: dict, origin: Cell) -> AreaResult:
    """Paint the area into route.cls with its footprint's top-left at ``origin``
    and return everything the export needs."""
    rec = area.get("recipe") or {}
    req = requirements(area)
    w, h = req["footprint"]
    H, W = route.cls.shape
    y0 = int(np.clip(origin[0], 2, max(2, H - h - 2)))
    x0 = int(np.clip(origin[1], 2, max(2, W - w - 2)))
    keep = _keep_cells(route, y0, x0, h, w)
    p = stamps.Painter(route.cls, route.rng, (y0, x0), (w, h), keep_walkable=keep, tileset=_tileset(route))
    elev = (lambda y, x: 3)
    book = rom.ScriptBook(area["id"], elev)
    compose = rec.get("approach", []) if area.get("kind") == "submap" else rec.get("compose", [])
    stamps.run(p, compose)
    side = _door_side(route, p, req["door"])
    door, door_out = _make_door(p, side)
    _connect_inside(p, door)
    taken: set = {door, door_out}
    sub = None
    if area.get("kind") == "submap":
        sub = _build_submap(area, rec, route.rng)
        ent = [d for d, _ in p.doors]
        wcell = ent[0] if ent else p.anchors.get("entrance", (p.cy, p.cx))
        book.warp(wcell[0], wcell[1], "{SUBMAP}", "0")
        taken.add(wcell)
        _populate(p, book, area, {"mons": rec.get("approach_mons", []), "interact": rec.get("approach_interact", [])}, taken)
    else:
        _populate(p, book, area, rec, taken)
    weather = rec.get("weather")
    if weather and area.get("kind") != "submap":
        outside = ROUTE_WEATHER.get(getattr(getattr(route, "spec", None), "weather", "WEATHER_SUNNY"),
                                    "COORD_EVENT_WEATHER_SUNNY")
        book.weather([door], [door_out], weather, outside)
    if rec.get("on_enter"):
        oe = rec["on_enter"]
        book.on_enter([door], oe.get("text", ""), oe.get("cry"))
    cells = sorted(c for c in p.open | p.solid if p.in_box(*c))
    if getattr(route, "protect", None) is not None:
        for c in p.open:
            route.protect[c] = True
    scripts = "\n".join(book.scripts) + "\n" + "\n".join(book.texts)
    objs, coords, scripts = rom.assign_temps(book.objects, book.coords, scripts, list(rom.TEMP_FLAGS),
                                             rom.TEMP_VARS[len(route.__dict__.get("_area_results", [])) % len(rom.TEMP_VARS)])
    enc = [{"species": area["species"], "weight": 30, "levels": area.get("wild_level") or [10, 15],
            "shiny_boost": area.get("shiny_boost", 1)}]
    for e in rec.get("encounters", []):
        enc.append({"species": e[0], "weight": e[1] if len(e) > 1 else 10, "levels": e[2] if len(e) > 2 else None})
    res = AreaResult(area=area, cells=cells, center=(p.cy, p.cx), door=door, door_out=door_out,
                     fixed=dict(p.fixed), custom=dict(p.custom), collision=dict(p.collision), props=[dict(pr, area=area["id"]) for pr in p.props],
                     object_events=objs, bg_events=book.bgs, coord_events=coords, warp_events=book.warps,
                     scripts=scripts, flags=book.flags, mons=book.mons, story=rec.get("story", area.get("description", "")),
                     activity=rec.get("activity", ""), weather=weather, encounters=enc, submap=sub,
                     anchors=dict(p.anchors), box=(y0, x0, h, w), pieces=dict(p.piece_at))
    return res


def _trail(route, res: "AreaResult", narrow: bool, start_hint=None) -> List[Cell]:
    import heapq

    lane = contexts.lane_mask(route)
    if not lane.any():
        return []
    cls = route.cls
    H, W = cls.shape
    src = res.door_out
    dist = {src: 0.0}
    prev = {}
    pq = [(0.0, src)]
    end = None
    y0, x0, h, w = res.box
    ldist = contexts.lane_distance(route)
    while pq:
        d, c = heapq.heappop(pq)
        if lane[c] and (start_hint is None or c != start_hint):
            end = c
            break
        if d > dist.get(c, 1e9):
            continue
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (c[0] + dy, c[1] + dx)
            if not (1 <= n[0] < H - 1 and 1 <= n[1] < W - 1):
                continue
            if y0 <= n[0] < y0 + h and x0 <= n[1] < x0 + w:
                continue
            v = cls[n]
            if narrow:
                # a hidden trail keeps to the trees: open ground near the lane would give it away
                cost = 1.0 if v == "f" else 1.6 if v in WALK else 2.0 if v == "r" else math.inf
                if v in WALK and 0 < ldist[n] <= 2:
                    cost += 3.0
            else:
                cost = 1.0 if v in WALK else 2.5 if v in "fr" else math.inf
            if start_hint is not None and abs(n[0] - start_hint[0]) + abs(n[1] - start_hint[1]) < 6:
                cost += 4.0
            if cost == math.inf:
                continue
            if d + cost < dist.get(n, 1e9):
                dist[n] = d + cost
                prev[n] = c
                heapq.heappush(pq, (d + cost, n))
    if end is None:
        return []
    path = [end]
    while path[-1] != src:
        path.append(prev[path[-1]])
    return path[::-1]  # lane -> door_out


def connect(route, res: "AreaResult") -> List[Cell]:
    """Cut the way in from the nearest lane to the area's door, and put the area's
    access gate on it (see VISIBILITY / ACCESS). Gate objects are appended to the
    result's events. Returns the trail cells (lane end first)."""
    req = requirements(res.area)
    vis, access = req["visibility"], req["access"]
    narrow = vis in ("hidden", "secret")
    path = _trail(route, res, narrow)
    if not path:
        return []
    cls = route.cls
    was = [str(cls[c]) for c in path]
    for c in path:
        if cls[c] not in WALK:
            cls[c] = "g"
        if getattr(route, "protect", None) is not None:
            route.protect[c] = True
    book = rom.ScriptBook(res.area["id"] + "_gate")
    # gate: first trail cell that used to be tree/rock (the mouth of the gap), else 2 cells in
    mouth = next((i for i, v in enumerate(was) if v not in WALK), min(2, len(path) - 1))
    mouth = max(1, min(mouth, len(path) - 2)) if len(path) > 2 else len(path) - 1
    gy, gx = path[mouth]
    res.gate = {"access": access, "cell": (gy, gx), "trail": path}
    if access == "cut_tree":
        book.prop_object("cuttable_tree", gy, gx)
    elif access == "strength_boulder":
        book.prop_object("pushable_boulder", gy, gx)
    elif access == "rock_smash":
        book.prop_object("breakable_rock", gy, gx)
    elif access == "ledge":
        # one-way: you drop out of the area onto the lane side; the way in is a second, hidden gap
        if mouth + 1 < len(path):
            ly, lx = path[mouth]
            cls[ly, lx] = "s"
        back = _trail(route, res, True, start_hint=path[0])
        for c in back:
            if cls[c] not in WALK:
                cls[c] = "g"
        res.gate["second_trail"] = back
    elif access == "npc_block":
        txt = res.area.get("recipe", {}).get("blocker_text",
                                              "Nobody goes down there. Nobody. Go round if you're that stubborn.")
        book.npc(res.area.get("recipe", {}).get("blocker_gfx", "OLD_MAN"), gy, gx, txt, move="face:down")
        back = _trail(route, res, True, start_hint=path[0])
        for c in back:
            if cls[c] not in WALK:
                cls[c] = "g"
        res.gate["second_trail"] = back
    elif access == "hidden_gap":
        # the mouth is screened by tall grass and starts where the lane bends past trees
        for c in path[:3]:
            if cls[c] in "g":
                cls[c] = "t"
    if vis == "territorial":
        sp = res.area.get("recipe", {}).get("ambush") or {}
        if sp:
            cells = [c for c in path[: max(1, len(path) // 2)]] or [path[0]]
            book.ambush(cells, sp["species"], _level(res.area, sp["species"], sp.get("level")), sp.get("text", ""))
    scripts = "\n".join(book.scripts) + "\n" + "\n".join(book.texts)
    objs, coords, scripts = rom.assign_temps(book.objects, book.coords, scripts, [], "VAR_TEMP_9")
    res.object_events.extend(objs)
    res.coord_events.extend(coords)
    res.scripts += "\n" + scripts
    res.flags.extend(book.flags)
    return path


# ---------------------------------------------------------------- sub-maps

INTERIOR_DEFAULTS = {
    "cave": ("general_cave", "MUS_CAVE_OF_ORIGIN", "MAP_TYPE_UNDERGROUND"),
    "grotto": ("general_cave", "MUS_CAVE_OF_ORIGIN", "MAP_TYPE_UNDERGROUND"),
    "cellar": ("general_cave", "MUS_ABANDONED_SHIP", "MAP_TYPE_UNDERGROUND"),
    "ruin_interior": ("general_cave", "MUS_ABANDONED_SHIP", "MAP_TYPE_INDOOR"),
    "forest_glade": ("general_fortree", "MUS_ROUTE119", "MAP_TYPE_ROUTE"),
}


class _Grid:
    """Minimal route stand-in for painting a sub-map."""

    def __init__(self, cls, rng):
        self.cls, self.rng = cls, rng


def _build_submap(area: dict, rec: dict, rng) -> SubMap:
    inter = rec.get("interior", {})
    kind = inter.get("type", "cave")
    w, h = inter.get("size", [16, 14])
    ts, music, mtype = INTERIOR_DEFAULTS.get(kind, INTERIOR_DEFAULTS["cave"])
    ts = inter.get("tileset", ts)
    g = contexts.interior_canvas(kind, w, h, rng)
    a, _, b = ts.partition("_")
    p = stamps.Painter(g, rng, (1, 1), (w - 2, h - 2), tileset=(a, b))
    exit_cell = (h - 2, w // 2)
    p.keep = {exit_cell, (h - 3, w // 2)}
    stamps.run(p, inter.get("compose", []))
    for (y, x) in ((h - 2, w // 2), (h - 3, w // 2)):
        if g[y, x] not in WALK:
            g[y, x] = "v" if kind != "forest_glade" else "g"
    p.open.add(exit_cell)
    _connect_inside(p, exit_cell)
    book = rom.ScriptBook(area["id"] + "_in")
    taken = {exit_cell, (h - 3, w // 2)}
    _populate(p, book, area, inter, taken)
    book.warps.insert(0, {"x": exit_cell[1], "y": exit_cell[0], "elevation": 3, "dest_map": "{ROUTE_MAP}",
                          "dest_warp_id": "{ROUTE_WARP_ID}"})
    scripts = "\n".join(book.scripts) + "\n" + "\n".join(book.texts)
    objs, coords, scripts = rom.assign_temps(book.objects, book.coords, scripts, list(rom.TEMP_FLAGS), "VAR_TEMP_1")
    if inter.get("on_enter"):
        pass
    return SubMap(id_suffix=rom.const_name(inter.get("suffix", area["id"])), name=area["name"], interior=kind, cls=g,
                  fixed=dict(p.fixed), custom=dict(p.custom), tileset=ts, music=inter.get("music", music), map_type=mtype,
                  weather=inter.get("weather", "WEATHER_NONE"), object_events=objs, bg_events=book.bgs,
                  coord_events=coords, warp_events=book.warps, scripts=scripts, flags=book.flags, mons=book.mons,
                  props=p.props, exit_cell=exit_cell, story=inter.get("story", rec.get("story", "")))
