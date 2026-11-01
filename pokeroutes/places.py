"""Special areas and hidden things.

A special area is a small authored-feeling place stamped into the generated
route: a shape (archetype), the props that dress it, and the encounter /
ecology data that makes it matter. Species-driven areas come from the
regional pokedex (``special_area`` entries); otherwise built-in archetypes
are used.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from .generate import DIRS4, WALKABLE, Event, Route, Zone, astar, blob, disc

Cell = Tuple[int, int]

HABITAT_ARCHETYPE = {
    "forest": "grove", "deep_forest": "grove", "flower_field": "meadow", "meadow": "meadow",
    "tall_grass": "meadow", "farmland": "meadow", "town_edge": "meadow",
    "lake": "pond", "riverbank": "pond", "marsh": "pond", "sea": "pond", "beach": "pond",
    "cave": "hollow", "cave_deep": "hollow", "mountain": "hollow", "cliff": "hollow",
    "volcanic": "hollow", "snowfield": "hollow", "ruins": "stones",
}

BUILTIN = [
    {"id": "old_oak", "name": "The Old Oak", "archetype": "grove",
     "description": "A single oak older than the road, ringed by grass nobody walks on.",
     "terrain_marks": [], "size": [9, 8]},
    {"id": "sunken_pond", "name": "Sunken Pond", "archetype": "pond",
     "description": "A still pond in a dip of the forest floor.", "terrain_marks": [], "size": [10, 8]},
    {"id": "stone_hollow", "name": "Stone Hollow", "archetype": "hollow",
     "description": "A ring of rock with one way in.", "terrain_marks": [], "size": [10, 9]},
    {"id": "standing_stones", "name": "Standing Stones", "archetype": "stones",
     "description": "Old stones set in a circle, half swallowed by grass.", "terrain_marks": [], "size": [9, 9]},
]

HIDDEN_ITEMS = ["ITEM_TINY_MUSHROOM", "ITEM_BIG_MUSHROOM", "ITEM_NUGGET", "ITEM_PEARL", "ITEM_STARDUST",
                "ITEM_HEART_SCALE", "ITEM_REVIVE", "ITEM_ETHER", "ITEM_RARE_CANDY", "ITEM_HP_UP"]


def load_species_areas(path: str) -> List[dict]:
    if not path or not Path(path).exists():
        return []
    data = json.loads(Path(path).read_text())
    out = []
    for sp in data:
        sa = sp.get("special_area")
        if not sa:
            continue
        hab = (sp.get("habitats") or ["forest"])[0]
        out.append({
            "id": sa["id"], "name": sa["name"], "description": sa.get("description", ""),
            "terrain_marks": sa.get("terrain_marks", []), "size": sa.get("size", [9, 8]),
            "archetype": HABITAT_ARCHETYPE.get(hab, "grove"), "species": sp["name"],
            "shiny_boost": sa.get("shiny_boost", 1), "habitats": sp.get("habitats", []),
        })
    return out


def _choose_areas(route: Route) -> List[dict]:
    spec, rng = route.spec, route.rng
    pool = load_species_areas(spec.species_file)
    biome_hab = {"forest": {"forest", "deep_forest", "flower_field", "meadow", "tall_grass", "lake", "ruins"},
                 "meadow": {"meadow", "flower_field", "tall_grass", "farmland", "lake", "riverbank"},
                 "riverland": {"lake", "riverbank", "marsh", "meadow"}}.get(spec.biome, set())
    chosen: List[dict] = []
    pool = [a for a in pool if a["id"] not in spec.exclude_areas]
    for want in spec.special_areas:
        if want == "auto":
            fits = [a for a in pool if set(a.get("habitats", [])) & biome_hab and a not in chosen]
            if fits:
                chosen.append(fits[int(rng.integers(0, len(fits)))])
            continue
        hit = [a for a in pool + BUILTIN if want.lower() in (a.get("species", "").lower(), a["id"], a["archetype"])]
        if hit:
            chosen.append(hit[0])
    return chosen


def _find_spot(route: Route, size: Tuple[int, int]) -> Optional[Cell]:
    """A forest spot near (but not on) the paths, so it needs a short trail."""
    w, h = size
    paths = route.zone_cells("main", "loop", "branch", "dead_end")
    walk = np.isin(route.cls, list(WALKABLE))
    best, best_score = None, -1e9
    for _ in range(400):
        y, x = paths[int(route.rng.integers(0, len(paths)))]
        ang = route.rng.uniform(0, 2 * math.pi)
        d = route.rng.uniform(5, 11) + max(w, h) / 2
        cy, cx = int(y + math.sin(ang) * d), int(x + math.cos(ang) * d)
        y0, x0 = cy - h // 2 - 1, cx - w // 2 - 1
        if y0 < 2 or x0 < 2 or y0 + h + 2 >= route.h - 2 or x0 + w + 2 >= route.w - 2:
            continue
        box = (slice(y0, y0 + h + 2), slice(x0, x0 + w + 2))
        if route.protect[box].any():
            continue
        overlap = walk[box].mean()
        score = -overlap * 10 + route.rng.random()
        if score > best_score:
            best, best_score = (cy, cx), score
    return best


def _trail(route: Route, a: Cell, b: Cell, kind: str) -> List[Cell]:
    cost = np.where(route.cls == "w", np.inf, 1.0)
    cost[route.cls == "f"] = 1.6
    cost += route.rng.random(cost.shape) * 0.8  # wobble
    path = astar(cost, a, b)
    for (y, x) in path:
        if route.cls[y, x] in "fr":
            route.cls[y, x] = "g"
        route.protect[y, x] = True
    route.zones.append(Zone(kind, path, kind))
    return path


def _nearest(cells: List[Cell], p: Cell) -> Cell:
    return min(cells, key=lambda c: (c[0] - p[0]) ** 2 + (c[1] - p[1]) ** 2)


# ------------------------------------------------------------ archetypes
# Each paints classes into the route and returns (cells, entrance_cell).

def arch_grove(route: Route, cy: int, cx: int, w: int, h: int, area: dict):
    cells = blob(route, cy, cx, h / 2, w / 2, rough=0.2)
    for (y, x) in cells:
        route.cls[y, x] = "g"
    # a ring of tall grass, the old tree in the middle stays a tree
    for (y, x) in cells:
        d = math.hypot((y - cy) / (h / 2), (x - cx) / (w / 2))
        if 0.55 < d < 0.85:
            route.cls[y, x] = "t"
    for (y, x) in disc(cy, cx, 0.8):
        route.cls[y, x] = "f"
    route.props.append({"kind": "great_tree", "y": cy, "x": cx, "area": area["id"]})
    return cells


def arch_meadow(route: Route, cy: int, cx: int, w: int, h: int, area: dict):
    cells = blob(route, cy, cx, h / 2, w / 2, rough=0.4)
    for (y, x) in cells:
        route.cls[y, x] = "g"
    for (y, x) in cells:
        if route.rng.random() < 0.35:
            route.props.append({"kind": "flowers", "y": y, "x": x, "area": area["id"]})
    return cells


def arch_pond(route: Route, cy: int, cx: int, w: int, h: int, area: dict):
    cells = blob(route, cy, cx, h / 2, w / 2, rough=0.25)
    for (y, x) in cells:
        route.cls[y, x] = "g"
    water = blob(route, cy, cx, max(2.6, h / 2 - 1.4), max(2.6, w / 2 - 1.8), rough=0.7)
    for (y, x) in water:
        route.cls[y, x] = "w"
    for (y, x) in cells:
        if route.cls[y, x] == "g" and any(route.cls[y + dy, x + dx] == "w" for dy, dx in ((0, 1), (1, 0), (0, -1), (-1, 0))):
            if route.rng.random() < 0.4:
                route.props.append({"kind": "reeds", "y": y, "x": x, "area": area["id"]})
    return cells


def arch_hollow(route: Route, cy: int, cx: int, w: int, h: int, area: dict):
    cells = blob(route, cy, cx, h / 2, w / 2, rough=0.15)
    inner = set(blob(route, cy, cx, h / 2 - 1.5, w / 2 - 1.5, rough=0.1))
    for (y, x) in cells:
        route.cls[y, x] = "t" if (y, x) in inner else "r"
    for (y, x) in inner:
        if route.rng.random() < 0.3:
            route.cls[y, x] = "g"
    return cells


def arch_stones(route: Route, cy: int, cx: int, w: int, h: int, area: dict):
    cells = blob(route, cy, cx, h / 2, w / 2, rough=0.2)
    for (y, x) in cells:
        route.cls[y, x] = "g"
    n = 7
    for i in range(n):
        a = 2 * math.pi * i / n
        y, x = int(round(cy + math.sin(a) * (h / 2 - 1.5))), int(round(cx + math.cos(a) * (w / 2 - 1.5)))
        route.events.append(Event("rock_smash", y, x))
        route.props.append({"kind": "standing_stone", "y": y, "x": x, "area": area["id"]})
    return cells


ARCHETYPES = {"grove": arch_grove, "meadow": arch_meadow, "pond": arch_pond, "hollow": arch_hollow, "stones": arch_stones}


def _ground(route: Route) -> str:
    from .stages import ground_class

    return ground_class(route)


def _areas_api():
    """The new special-areas package (pokeroutes/areas) if it has landed, else None."""
    try:
        from . import areas  # type: ignore

        if hasattr(areas, "build"):
            return areas
    except Exception:
        return None
    return None


def choose_areas(route: Route) -> List[dict]:
    """Special areas are rare (owner: "not every route"). Explicitly named areas are
    kept; each "auto" entry only turns into an area with ``special_area_chance``; and a
    region budget (``special_budget``, set by ``pokeroutes region``) caps the total."""
    spec, rng = route.spec, route.rng
    chance = float(getattr(spec, "special_area_chance", 0.35))
    wanted = [w for w in spec.special_areas if w != "auto" or rng.random() < chance]
    orig = spec.special_areas
    spec.special_areas = wanted
    try:
        raw = _choose_areas_raw(route)
    finally:
        spec.special_areas = orig
    seen, out = set(), []
    for a in raw:
        if a.get("id") not in seen:
            seen.add(a.get("id"))
            out.append(a)
    budget = int(getattr(spec, "special_budget", -1))
    if budget >= 0 and len(out) > budget:
        route.log.append(f"special budget: keeping {budget} of {len(out)} areas")
        out = out[:budget]
    return out


def _choose_areas_raw(route: Route) -> List[dict]:
    api = _areas_api()
    if api is not None and hasattr(api, "pick"):
        try:
            got = list(api.pick(route, list(route.spec.special_areas)))
            if got or not route.spec.special_areas:
                return got
            route.log.append("areas.pick found nothing; using built-in archetypes")
        except Exception as e:  # the package is new: never let it break generation
            route.log.append(f"areas.pick failed ({type(e).__name__}: {e}); using built-in archetypes")
    return _choose_areas(route)


def _slots(route: Route) -> List[dict]:
    """Places the skeleton reserved for special areas: convergence clearings (spectacle)
    and dead-end pockets (hidden)."""
    p = route.__dict__.get("_plan")
    out = []
    if p is None:
        return out
    for n in p.nodes:
        if n.area is not None:
            out.append({"area": n.area, "center": n.area_center or (n.y, n.x), "box": n.rect(), "where": "spectacle"})
    for l in p.lanes:
        if l.area is not None and l.pocket_cells:
            ys, xs = zip(*l.pocket_cells)
            out.append({"area": l.area, "center": ((min(ys) + max(ys)) // 2, (min(xs) + max(xs)) // 2),
                        "box": (min(ys), max(ys), min(xs), max(xs)), "where": "pocket"})
    return out


def _build(route: Route, area: dict, cy: int, cx: int, w: int, h: int, box) -> Optional[List[Cell]]:
    api = _areas_api()
    if api is not None and "archetype" not in area:
        try:
            req = api.requirements(area)
            fw, fh = req.get("footprint", (w, h))
            origin = (cy - fh // 2, cx - fw // 2)
            res = api.build(route, area, origin)
            route.props.extend(getattr(res, "props", []) or [])
            area = dict(area)
            route.__dict__.setdefault("_area_results", []).append(res)
            return list(res.cells)
        except Exception as e:
            route.log.append(f"areas.build failed for {area.get('id')} ({type(e).__name__}: {e}); archetype fallback")
            area = dict(area, archetype=area.get("archetype", "grove"))
    return ARCHETYPES[area.get("archetype", "grove")](route, cy, cx, w, h, area)


def _record_area_events(route: Route, res) -> None:
    """Mirror an AreaResult's events into route.events (scene/debug views). Call once,
    after connect(), so gate objects are included and nothing is listed twice."""
    cy, cx = res.center
    for key in ("object_events", "bg_events", "coord_events", "warp_events"):
        for ev in getattr(res, key, []) or []:
            route.events.append(Event("area_" + key[:-7], int(ev.get("y", cy)), int(ev.get("x", cx)),
                                      {"area": res.area.get("id"), "map_json": ev}))


def _gate(route: Route, res) -> bool:
    """areas.connect(): cut the way in from the nearest lane and put the access gate on it."""
    api = _areas_api()
    if api is None or not hasattr(api, "connect"):
        return False
    try:
        api.connect(route, res)
        return True
    except Exception as e:
        route.log.append(f"areas.connect failed for {res.area.get('id')} ({type(e).__name__}: {e})")
        return False


REAL_GATES = ("cut_tree", "strength_boulder", "npc_block")


def _hidden_area(area: dict) -> dict:
    """Every special area sits behind a real secret: a hidden trail plus a Cut tree, a
    Strength boulder or an NPC you have to go round. Visible / territorial / gap-only
    recipes are upgraded to that."""
    rec = dict(area.get("recipe") or {})
    vis = rec.get("visibility", "hidden")
    if vis not in ("hidden", "secret"):
        rec["visibility"] = "hidden"
    if rec.get("access") not in REAL_GATES:
        rec["access"] = "cut_tree"
    return dict(area, recipe=rec)


def place_specials(route: Route) -> None:
    """Build each special area at the hidden site the planner reserved for it (deep in
    the route, far from every exit, out of sight of the road) and gate its trail."""
    p = route.__dict__.get("_plan")
    if p is None or not hasattr(p, "sites"):
        return _place_specials_legacy(route)
    api = _areas_api()
    for st in p.sites:
        area = st["area"]
        y0, x0, h, w = st["box"]
        cy, cx = y0 + h // 2, x0 + w // 2
        if api is not None and "archetype" not in area:
            area = _hidden_area(area)
            res = _build_reachable(route, api, area, (y0, x0))
            if res is None:
                continue
            area = res.area
            route.props.extend(getattr(res, "props", []) or [])
            route.__dict__.setdefault("_area_results", []).append(res)
            cells = list(res.cells)
            gated = _gate(route, res) and bool(getattr(res, "gate", None))
            _record_area_events(route, res)
            gate = getattr(res, "gate", None) or {}
            trail = gate.get("trail") or []
            if trail:
                route.zones.append(Zone("trail", list(trail), f"hidden trail to {area['name']}",
                                        {"secret": True, "access": gate.get("access")}))
            if not gated:
                route.log.append(f"special: {area.get('id')} has no gated trail")
        else:
            cells = ARCHETYPES[area.get("archetype", "grove")](route, cy, cx, w, h, area)
            src = _nearest(route.zone_cells("main", "loop", "dead_end"), (cy, cx))
            trail = _straight_trail(route, src, _nearest(cells, src), "trail")
            mid = trail[len(trail) // 3]
            route.events.append(Event("cut_tree", mid[0], mid[1], {"why": f"hides the way to {area['name']}"}))
        purpose = {"kind": "special_area", "reason": f"{area['name']}: hidden deep in the route, "
                   f"behind a gated trail, out of sight of the road", "area": area.get("id")}
        route.zones.append(Zone("special", cells, area["name"], {"area": area, "center": (cy, cx),
                                                                 "where": "hidden", "purpose": purpose}))
        for c in cells:
            route.protect[c] = True


def _inside_reach(route: Route, res) -> float:
    """Share of the area's open ground you can walk to from its door."""
    from collections import deque

    y0, x0, h, w = res.box
    opens = {(y, x) for y in range(y0, y0 + h) for x in range(x0, x0 + w) if route.cls[y, x] in "gt"}
    if not opens:
        return 0.0
    start = res.door_out
    seen, q = {start}, deque([start])
    while q:
        y, x = q.popleft()
        for dy, dx in DIRS4:
            c = (y + dy, x + dx)
            if c not in seen and y0 <= c[0] < y0 + h and x0 <= c[1] < x0 + w and route.cls[c] in "gtp":
                seen.add(c)
                q.append(c)
    return len(opens & seen) / len(opens)


def _build_reachable(route: Route, api, area: dict, origin):
    """Build the area, trying door sides until its inside is reachable from the door
    (some compositions wall themselves off from one side with their big pieces)."""
    cls0, prot0 = route.cls.copy(), route.protect.copy()
    best = None
    for door in ("auto", "s", "e", "w", "n"):
        a = dict(area, recipe=dict(area.get("recipe") or {}, door=door))
        try:
            res = api.build(route, a, origin)
        except Exception as e:
            route.log.append(f"areas.build failed for {area.get('id')} ({type(e).__name__}: {e})")
            return None
        share = _inside_reach(route, res)
        if best is None or share > best[0]:
            best = (share, door)
        if share >= 0.9:
            return res
        route.cls[:], route.protect[:] = cls0, prot0
    door = best[1]
    route.log.append(f"special: {area.get('id')} inside only {best[0]:.0%} reachable (door {door})")
    return api.build(route, dict(area, recipe=dict(area.get("recipe") or {}, door=door)), origin)


def _place_specials_legacy(route: Route) -> None:
    """Spectacle areas go in the convergence clearings the skeleton reserved for them,
    hidden ones in dead-end pockets. Anything without a slot falls back to a spot off
    the lanes, reached by a short straight trail."""
    placed = set()
    api0 = _areas_api()
    skipped: List[dict] = []
    for slot in _slots(route):
        area = slot["area"]
        # hidden / secret areas don't take a lane clearing: lanes would cross them (and
        # block their big pieces). They go off-lane via find_site + connect below.
        if api0 is not None and hasattr(api0, "find_site") and "archetype" not in area:
            try:
                if api0.requirements(area).get("visibility") in ("hidden", "secret"):
                    skipped.append(area)
                    continue
            except Exception:
                pass
        y0, y1, x0, x1 = slot["box"]
        from .braid import _area_size

        w, h = _area_size(area)
        cy, cx = slot["center"]
        cells = _build(route, area, cy, cx, w, h, slot["box"])
        if not cells:
            continue
        res = (route.__dict__.get("_area_results") or [None])[-1]
        if res is not None and list(getattr(res, "cells", [])) == list(cells):
            # hidden / secret areas sit behind their access gate even in a reserved slot
            api = _areas_api()
            if api.requirements(res.area).get("visibility") in ("hidden", "secret", "territorial"):
                _gate(route, res)
            _record_area_events(route, res)
        # lanes that cross the area keep a way through rock rings (a hollow gets doorways)
        lane = set(route.zone_cells("main", "loop", "dead_end"))
        for c in cells:
            if c in lane and route.cls[c] == "r":
                route.cls[c] = _ground(route)
        route.zones.append(Zone("special", cells, area["name"], {"area": area, "center": (cy, cx), "where": slot["where"]}))
        for (y, x) in cells:
            route.protect[y, x] = True
        placed.add(area["id"])
    p = route.__dict__.get("_plan")
    rest = [a for a in (p.areas if p is not None else _choose_areas(route)) + skipped if a["id"] not in placed]
    for area in rest:
        w, h = area.get("size", [9, 8])
        w, h = int(np.clip(w, 5, 12)), int(np.clip(h, 5, 10))
        spot = None
        api = _areas_api()
        if api is not None and hasattr(api, "find_site") and "archetype" not in area:
            try:
                o = api.find_site(route, area)
                if o is not None:
                    fw, fh = api.requirements(area).get("footprint", (w, h))
                    spot = (int(o[0]) + fh // 2, int(o[1]) + fw // 2)
            except Exception as e:
                route.log.append(f"areas.find_site failed ({type(e).__name__})")
        spot = spot or _find_spot(route, (w, h))
        if not spot:
            route.log.append(f"no room for {area['id']}")
            continue
        cy, cx = spot
        cells = _build(route, area, cy, cx, w, h, None)
        if not cells:
            continue
        res = (route.__dict__.get("_area_results") or [None])[-1]
        connected = False
        if res is not None and getattr(res, "cells", None) is not None and list(res.cells) == list(cells):
            connected = _gate(route, res) and bool(getattr(res, "gate", None))
            _record_area_events(route, res)
        if not connected:
            paths = route.zone_cells("main", "loop", "dead_end")
            src = _nearest(paths, spot)
            dst = _nearest(cells, src)
            _straight_trail(route, src, dst, "trail")
        route.zones.append(Zone("special", cells, area["name"], {"area": area, "center": spot, "where": "off-lane"}))
        for (y, x) in cells:
            route.protect[y, x] = True


def _straight_trail(route: Route, a: Cell, b: Cell, kind: str, width: int = 1) -> List[Cell]:
    """An L of two straight runs (never a staircase)."""
    (ay, ax), (by, bx) = a, b
    path = [(ay, x) for x in range(ax, bx, 1 if bx >= ax else -1)] + [(y, bx) for y in range(ay, by + (1 if by >= ay else -1), 1 if by >= ay else -1)]
    for (y, x) in path:
        if route.cls[y, x] in "fr":
            route.cls[y, x] = _ground(route)
        route.protect[y, x] = True
    route.zones.append(Zone(kind, path, kind))
    return path


def _lane_cells_owner(route: Route):
    p = route.__dict__.get("_plan")
    return p.owner if p is not None else None


def place_hidden(route: Route) -> None:
    """Hidden things live in the tree masses between lanes:
    - hidden links: a 1-wide gap straight through the trees between two parallel
      lanes (sometimes behind a Cut tree);
    - lore spots: a tiny clearing in the middle of a big tree mass, reached by a
      narrow straight gap, holding a sign / grave / ruin / hut plot / berry patch
      (emitted as events with a `lore` id so writers can fill them in);
    - hidden items in quiet corners and lane ends."""
    spec, rng = route.spec, route.rng
    from scipy.ndimage import distance_transform_cdt

    owner = _lane_cells_owner(route)
    walk = np.isin(route.cls, list(WALKABLE))
    n_links = (spec.hidden_paths + 1) // 2
    # a special area has exactly one way in (its gated trail): nothing else may open
    # onto it, so keep links and lore nooks well away
    from scipy.ndimage import binary_dilation

    sp = np.zeros(route.cls.shape, dtype=bool)
    for z in route.zones:
        if z.kind in ("special", "trail"):
            for c in z.cells:
                sp[c] = True
    near_sp = binary_dilation(sp, iterations=8) if sp.any() else sp
    cands = []
    for y in range(3, route.h - 3):
        for x in range(3, route.w - 3):
            if not walk[y, x]:
                continue
            for dy, dx in ((0, 1), (1, 0)):
                k = 1
                while route.inside(y + dy * k, x + dx * k, 3) and route.cls[y + dy * k, x + dx * k] == "f":
                    k += 1
                if not (4 <= k <= 6):
                    continue
                ey, ex = y + dy * k, x + dx * k
                if not route.inside(ey, ex, 3) or not walk[ey, ex]:
                    continue
                if near_sp[y, x] or near_sp[ey, ex]:
                    continue
                if owner is not None and (owner[y, x] < 0 or owner[ey, ex] < 0 or owner[y, x] == owner[ey, ex]):
                    continue
                # the trees beside the link must be solid (a clean straight gap)
                py, px = dx, dy
                side_ok = all(route.cls[y + dy * i + py * s, x + dx * i + px * s] == "f"
                              for i in range(1, k) for s in (-1, 1))
                if side_ok:
                    cands.append(((y, x), (dy, dx), k))
    rng.shuffle(cands)
    used: List[Cell] = []
    made = 0
    for (y, x), (dy, dx), k in cands:
        if made >= n_links:
            break
        if any(abs(y - uy) + abs(x - ux) < 12 for (uy, ux) in used):
            continue
        cells = [(y + dy * i, x + dx * i) for i in range(1, k)]
        for c in cells:
            route.cls[c] = _ground(route)
            route.protect[c] = True
        from .purpose import record

        rec = record(route, "shortcut", cells[len(cells) // 2])
        route.zones.append(Zone("hidden", [(y, x)] + cells + [(y + dy * k, x + dx * k)], f"hidden link {made + 1}",
                                {"purpose": rec}))
        if rng.random() < 0.5:
            route.events.append(Event("cut_tree", cells[len(cells) // 2][0], cells[len(cells) // 2][1], {"why": "hidden link"}))
        used.append((y, x))
        made += 1
    # lore spots in the biggest tree masses
    n_lore = min(1, int(getattr(spec, "lore_spots", 2)))  # one lore nook at most: fewer, meaningful
    kinds = ["grave", "ruin", "shrine", "old_sign", "hut_plot", "berry_patch"]
    for i in range(n_lore):
        walk = np.isin(route.cls, list(WALKABLE) + ["w", "s"])
        d = distance_transform_cdt(~walk, metric="chessboard").astype(float)
        d[:5, :] = d[-5:, :] = 0
        d[:, :5] = d[:, -5:] = 0
        d[near_sp] = 0
        d += rng.random(d.shape) * 0.5
        y, x = np.unravel_index(int(np.argmax(d)), d.shape)
        if d[y, x] < 5:
            break
        y, x = int(y), int(x)
        kind = kinds[int(rng.integers(0, len(kinds)))]
        hy, hx = (2, 3) if kind == "hut_plot" else (1, 1)
        spot = [(yy, xx) for yy in range(y - hy, y + hy + 1) for xx in range(x - hx, x + hx + 1)]
        # straight narrow gap to the nearest lane in one of the four directions
        best = None
        for dy, dx in DIRS4:
            k = 1
            sy, sx = y + dy * (hy + 1), x + dx * (hx + 1)
            while route.inside(sy + dy * k, sx + dx * k, 2) and not walk[sy + dy * k, sx + dx * k] and k < 12:
                k += 1
            ty, tx = sy + dy * k, sx + dx * k
            if sp[min(ty, route.h - 1), min(tx, route.w - 1)]:
                continue
            if route.inside(ty, tx, 2) and route.cls[ty, tx] in WALKABLE and (best is None or k < best[0]):
                best = (k, [(sy + dy * j, sx + dx * j) for j in range(0, k)])
        if best is None:
            break
        for c in spot:
            route.cls[c] = _ground(route)
            route.protect[c] = True
        for c in best[1]:
            route.cls[c] = _ground(route)
            route.protect[c] = True
        lore = f"{spec.id}_lore_{100 + i:03d}"
        route.zones.append(Zone("hidden", best[1][::-1], f"gap to {kind.replace('_', ' ')}", {"lore": lore}))
        reason = {"grave": "A lone grave in the deep wood, tended by someone.",
                  "ruin": "A tumbled wall, all that is left of a forester's hut.",
                  "shrine": "A tiny shrine hidden in the trees.",
                  "old_sign": "An old boundary sign, from when the wood belonged to someone.",
                  "hut_plot": "The plot of a hermit's hut, long gone.",
                  "berry_patch": "A secret berry patch somebody planted."}[kind]
        prec = {"kind": "lore_" + kind, "label": kind.replace("_", " "), "reason": reason, "cell": [y, x], "lore": lore}
        route.__dict__.setdefault("purposes", []).append(prec)
        route.zones.append(Zone("clearing", spot, kind.replace("_", " "), {"center": (y, x), "lore": lore, "secret": True,
                                                                           "purpose": prec}))
        if kind == "berry_patch":
            for (yy, xx) in [(y, x - 1), (y, x + 1)]:
                route.events.append(Event("berry_tree", yy, xx, {"lore": lore}))
        else:
            route.events.append(Event("sign", y - hy, x, {"lore": lore, "type": kind, "text": ""}))
        route.props.append({"kind": kind, "y": y, "x": x, "lore": lore})
    # hidden items: lane tips first, then quiet corners
    walk = np.isin(route.cls, list(WALKABLE))
    cands2 = []
    for y in range(3, route.h - 3):
        for x in range(3, route.w - 3):
            if walk[y, x] and route.cls[y, x] != "p":
                trees = (route.cls[y - 1 : y + 2, x - 1 : x + 2] == "f").sum()
                if trees >= 5:
                    cands2.append((y, x))
    rng.shuffle(cands2)
    taken = {(e.y, e.x) for e in route.events}
    k = 0
    for (y, x) in cands2:
        if k >= spec.hidden_items:
            break
        if (y, x) not in taken and all(abs(y - ty) + abs(x - tx) > 6 for (ty, tx) in taken):
            route.events.append(Event("hidden_item", y, x, {"item": HIDDEN_ITEMS[int(rng.integers(0, len(HIDDEN_ITEMS)))]}))
            taken.add((y, x))
            k += 1
