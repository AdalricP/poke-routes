"""Pipeline stages for the braided-linear generator.

The skeleton is planned once (``braid.plan``) and then carved stage by stage so
every step leaves a snapshot: main lane, side lanes, clearings, specials, water,
surfaces, grass, ledges, hidden things, dressing. Biome recipes live here too:
forest is trees-by-default with lanes carved out; meadow lays hedged fields of
grass between the lanes; riverland runs real rivers across the route.

Class codes for the new biomes (sand, sea, ash, cliff, lava, bridge) come from
``classify`` when the tiling work publishes them; until then they fall back to
the nearest existing class so the pipeline still runs.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

from .generate import DIRS4, WALKABLE, Event, Route, Zone, exit_cells, inward
from .noise import fbm

Cell = Tuple[int, int]


# ------------------------------------------------------------------ class codes

def code(name: str, fallback: str) -> str:
    """A semantic class published by classify.py (e.g. SAND), or ``fallback``."""
    try:
        from . import classify

        v = getattr(classify, name.upper(), None)
        if isinstance(v, str) and len(v) == 1:
            return v
    except Exception:  # pragma: no cover - classify is being reworked concurrently
        pass
    return fallback


TREE_BIOMES = ("forest", "meadow", "riverland")


# per-biome palette: (wall between lanes, ground, lane surface, road, encounter grass)
BIOME_CODES = {
    "forest": ("f", "g", "g", "g", "t"),  # no dirt road under the canopy: the main lane is trodden forest floor
    "meadow": ("f", "g", "g", "p", "t"),  # dirt only on the main road; side lanes stay grass
    # like meadow, but open: the ground between lanes is cliff only while the route is
    # built; the last stage (plains.stage_open_plains) opens it into grass and groves
    # and raises a ledge-faced rim along the map's edge
    "plains": (("CLIFF", "r"), "g", "g", "p", "t"),
    "riverland": ("f", "g", "g", "p", "t"),
    "beach": ("f", ("SAND", "p"), ("SAND", "p"), ("SAND", "p"), "t"),
    "desert": (("CLIFF", "r"), ("SAND", "p"), ("SAND", "p"), ("SAND", "p"), ("SAND", "p")),
    "mountain": (("CLIFF", "r"), ("ASH", "p"), ("ASH", "p"), ("ASH", "p"), "t"),
    "snow": (("CLIFF", "r"), ("SNOW", "p"), ("SNOW", "p"), ("SNOW", "p"), ("ASH_GRASS", "t")),
    "volcanic": (("CLIFF", "r"), ("ASH", "p"), ("ASH", "p"), ("ASH", "p"), ("ASH_GRASS", "t")),
}


def _bc(route: Route, i: int) -> str:
    v = BIOME_CODES.get(route.spec.biome, BIOME_CODES["forest"])[i]
    return code(*v) if isinstance(v, tuple) else v


def wall_class(route: Route) -> str:
    """What the ground between lanes is made of."""
    return _bc(route, 0)


def ground_class(route: Route) -> str:
    return _bc(route, 1)


def side_surface(route: Route) -> str:
    return _bc(route, 2)


def path_class(route: Route) -> str:
    biome_path_class = _bc(route, 3)
    if route.spec.road and biome_path_class == ground_class(route):
        return "p"
    return biome_path_class


def grass_class(route: Route) -> str:
    return _bc(route, 4)


def plan_of(route: Route):
    return route.__dict__["_plan"]


def carve(route: Route, cells, c: Optional[str] = None, over: str = "fr", protect: bool = True) -> None:
    c = c or ground_class(route)
    over = over + wall_class(route)
    for (y, x) in cells:
        if 0 <= y < route.h and 0 <= x < route.w:
            if route.cls[y, x] in over:
                route.cls[y, x] = c
            if protect:
                route.protect[y, x] = True


def _walk_mask(route: Route) -> np.ndarray:
    return np.isin(route.cls, list(WALKABLE))


def _dir_word(d: Tuple[int, int]) -> str:
    return {(-1, 0): "north", (1, 0): "south", (0, -1): "west", (0, 1): "east"}[d]


# ------------------------------------------------------------------ stages

def stage_base(route: Route) -> None:
    route.cls[:] = wall_class(route)
    route.protect = np.zeros(route.cls.shape, dtype=bool)
    route.snap("forest" if route.spec.biome in TREE_BIOMES else route.spec.biome)


def stage_skeleton(route: Route) -> None:
    """Plan the braid (nodes, sections, lanes) and carve the main lane: the critical path."""
    from .braid import plan
    from .places import choose_areas

    p = plan(route, choose_areas(route))
    route.__dict__["_plan"] = p
    for (e, c, cells) in p.exits:
        if not e.__dict__.get("virtual"):
            route.exits.append((e, cells))
    if p.exits[1][0].__dict__.get("virtual"):
        # the goal of a dead-end route: a clearing where the reward / landmark sits
        gy, gx = p.exits[1][1]
        goal = [(y, x) for y in range(gy - 3, gy + 4) for x in range(gx - 4, gx + 5)
                if route.inside(y, x, 3) and not (abs(y - gy) == 3 and abs(x - gx) == 4)]
        carve(route, goal)
        reg = (route.spec.region.get("goal") or {}) if isinstance(route.spec.region, dict) else {}
        route.zones.append(Zone("clearing", goal, "goal", {"center": (gy, gx), "goal": reg.get("landmark", True)}))
    for e, cells in route.exits:
        for cell in cells:
            carve(route, [inward(route, e, cell, d) for d in range(4)])
    crit: List[Cell] = []
    for s in p.sections:
        l = p.lane(s.lanes[0])
        carve(route, l.cells)
        cl = l.center()
        if crit and cl and cl[0] == crit[-1]:
            cl = cl[1:]
        crit.extend(cl)
    route.critical_path = crit
    route.zones.append(Zone("main", list(crit), "main road", {"critical": True}))
    route.graph = graph_dict(p)
    route.snap("skeleton")


def graph_dict(p) -> dict:
    return {
        "nodes": [{"id": n.id, "kind": n.kind, "y": n.y, "x": n.x, "rect": list(n.rect()) if (n.hy or n.hx) else None,
                   "area": (n.area or {}).get("id")} for n in p.nodes],
        "sections": [{"id": s.id, "a": s.a, "b": s.b, "lanes": list(s.lanes)} for s in p.sections],
        "lanes": [{"id": l.id, "kind": l.kind, "section": l.section, "fate": l.fate, "side": l.side, "level": l.level,
                   "parent": l.parent, "width": l.width, "path": [list(c) for c in l.center()]} for l in p.lanes],
        "endings": [{"kind": e["kind"], "side": e["exit"].side, "to": e["exit"].to,
                     "path": [list(c) for c in e["path"]]} for e in p.endings],
    }


def stage_branches(route: Route) -> None:
    """Side lanes: loops that rejoin, lanes that merge early, lanes that stop short; plus
    the spurs to extra / secret exits."""
    from .spec import Exit  # noqa: F401

    p = plan_of(route)
    for l in p.lanes:
        if l.kind == "main":
            continue
        carve(route, l.cells)
        what = {"full": "rejoins", "merge": "merges", "dead": "ends"}[l.fate]
        route.zones.append(Zone(l.kind, l.center(), f"lane {l.id} ({what})",
                                {"section": l.section, "fate": l.fate, "lane": l.id}))
    from .weave import weave_cross_paths

    weave_cross_paths(route, p, carve)
    for l in p.lanes:
        link = l.meta.get("secret_link")
        if link:
            carve(route, link)
            route.zones.append(Zone("hidden", list(link), "secret way to the old trail", {"secret": True, "lane": l.id}))
            y, x = link[min(2, len(link) - 1)]
            route.events.append(Event("cut_tree", y, x, {"why": "hides the old trail"}))
    for k, end in enumerate(p.endings):
        ex = end["exit"]
        _, cells = exit_cells(route, ex)
        route.exits.append((ex, cells))
        sd = {"north": (1, 0), "south": (-1, 0), "west": (0, 1), "east": (0, -1)}[ex.side]
        lat = (abs(sd[1]), abs(sd[0]))
        lo, hi = -((end["width"] - 1) // 2), end["width"] // 2
        spur = [(y + lat[0] * o, x + lat[1] * o) for (y, x) in end["path"] for o in range(lo, hi + 1)]
        carve(route, spur)
        kind = "hidden" if end["kind"] == "secret" else "branch"
        route.zones.append(Zone(kind, list(end["path"][::-1]), f"to {ex.to or ex.side}",
                                {"ending": end["kind"], "to": ex.to, "side": ex.side}))
        if end["kind"] == "secret" and len(end["path"]) >= 5:
            y, x = end["path"][len(end["path"]) // 2]
            route.events.append(Event("cut_tree", y, x, {"why": "secret exit"}))
    route.snap("branches")


def stage_clearings(route: Route) -> None:
    """Clearings only where they mean something: convergence nodes (spectacle), the
    corner of an L, and some lane ends. Most dead ends are simple stops."""
    p, rng, spec = plan_of(route), route.rng, route.spec
    for n in p.nodes:
        if not (n.hy or n.hx):
            continue
        cells = n.cells()
        carve(route, cells)
        route.zones.append(Zone("clearing", cells, f"{n.kind} {n.id}", {"center": (n.y, n.x), "node": n.kind}))
    from .purpose import goal_purpose, lane_purposes, record

    for n in p.nodes:
        if n.hy or n.hx:
            kind = {"corner": "bend"}.get(n.kind, "rest_stop")
            rec = record(route, kind, (n.y, n.x), node=n.id)
            for z in route.zones:
                if z.kind == "clearing" and z.meta.get("center") == (n.y, n.x):
                    z.meta["purpose"] = rec
            if kind == "rest_stop":
                route.props.append({"kind": "bench", "y": n.y + n.oy + n.hy - 1, "x": n.x + n.ox + n.hx - 2, "why": kind})
    lane_purposes(route, p, carve, ground_class(route), grass_class(route), _item)
    goal_purpose(route, ground_class(route), grass_class(route), _item)
    for k, end in enumerate(p.endings):
        kind = "secret_exit" if end["kind"] == "secret" else "road"
        record(route, kind, end["path"][0], to=end["exit"].to or end["exit"].side)
    # (no random bulges: grass bits with no reason read as weird shapes)
    _signposts(route, p)
    _beacons(route, p)
    route.snap("clearings")


def _bulges(route: Route, p) -> None:
    """Organic width: lanes open into small grassy bits and narrow again. Each bulge
    is a rectangle (2 deep, 3-7 long) on one side of a straight run, only where it
    still leaves two trees to anything else; half of them get tall grass."""
    rng = route.rng
    tall = grass_class(route)
    walk = np.isin(route.cls, list(WALKABLE) + ["w"])
    for l in p.lanes:
        f = l.frame
        lo, hi = -((l.width - 1) // 2), l.width // 2
        for a, b in zip(l.pts, l.pts[1:]):
            if a[1] != b[1] or abs(b[0] - a[0]) < 7:
                continue
            for _ in range(max(1, abs(b[0] - a[0]) // 10)):
                if rng.random() > 0.55:
                    continue
                ln = int(rng.integers(3, min(8, abs(b[0] - a[0]) - 2)))
                u0 = int(rng.integers(min(a[0], b[0]) + 1, max(a[0], b[0]) - ln))
                side = 1 if rng.random() < 0.5 else -1
                depth = int(rng.integers(1, 3)) if l.kind == "main" else 2
                vs = [a[1] + (hi + k if side > 0 else lo - k) for k in range(1, depth + 1)]
                cells = [f.yx(u, v) for u in range(u0, u0 + ln) for v in vs]
                if not all(route.inside(y, x, 3) and route.cls[y, x] == "f" for (y, x) in cells):
                    continue
                own = l.cells | set(cells)
                ok = True
                for (y, x) in cells:
                    box = walk[y - 2:y + 3, x - 2:x + 3]
                    ys, xs = np.nonzero(box)
                    if any((y - 2 + yy, x - 2 + xx) not in own for yy, xx in zip(ys, xs)):
                        ok = False
                        break
                if not ok:
                    continue
                grass = tall if rng.random() < 0.5 else ground_class(route)
                for c in cells:
                    route.cls[c] = grass
                    route.protect[c] = True
                    walk[c] = True


ITEMS = ["ITEM_POTION", "ITEM_POKE_BALL", "ITEM_ANTIDOTE", "ITEM_PARALYZE_HEAL", "ITEM_ESCAPE_ROPE",
         "ITEM_GREAT_BALL", "ITEM_SUPER_POTION", "ITEM_REPEL", "ITEM_X_ATTACK", "ITEM_ETHER"]


def _item(rng) -> str:
    return ITEMS[int(rng.integers(0, len(ITEMS)))]


def _lore_id(route: Route) -> str:
    n = route.__dict__.setdefault("_lore_n", 0) + 1
    route.__dict__["_lore_n"] = n
    return f"{route.spec.id}_lore_{n:02d}"


def _signposts(route: Route, p) -> None:
    """A signpost at every split off the main lane; the main lane carries on straight."""
    far = p.exits[1][0]
    dest = far.to or f"the {far.side}"
    for l in p.lanes:
        if l.kind == "main" or l.level != 1 or l.side == 0 or l.meta.get("spur"):
            continue
        f = l.frame
        A = p.node(p.sections[l.section].a)
        u_s = l.meta["u_s"]
        if A.inside(*f.yx(u_s, l.pts[0][1])):
            continue  # leaves from a clearing: the clearing gets one sign
        vm = l.pts[0][1]
        y, x = f.yx(u_s - 2, vm + (1 if l.side > 0 else -1))
        if route.cls[y, x] not in WALKABLE:
            continue
        side_dir = _dir_word((f.l[0] * l.side, f.l[1] * l.side))
        main_dir = _dir_word(f.d)
        what = (l.meta.get("purpose") or {}).get("label", "side path")
        route.events.append(Event("sign", int(y), int(x), {
            "type": "signpost", "text": f"{main_dir.upper()}: {dest}  /  {side_dir.upper()}: {what}",
            "why": "split"}))
    for n in p.nodes:
        if n.kind in ("spectacle", "clearing") and (n.hy or n.hx):
            # just inside the clearing, next to where the main lane comes in
            route.events.append(Event("sign", n.y - n.hy + 1, n.x - n.hx + 1, {
                "type": "landmark", "text": (n.area or {}).get("name", ""), "lore": _lore_id(route),
                "why": "convergence"}))


def _beacons(route: Route, p) -> None:
    """Flowers lining the last stretch into a spectacle clearing: you see it coming."""
    for s in p.sections:
        B = p.node(s.b)
        if B.kind != "spectacle":
            continue
        m = p.lane(s.lanes[0])
        cl = [c for c in m.center() if not B.inside(*c)]
        f = m.frame
        for (y, x) in cl[-8:]:
            u, v = f.uv(y, x)
            for dv in (-1, 1):
                yy, xx = f.yx(u, v + dv)
                if (u % 2 == 0) and route.cls[yy, xx] in WALKABLE:
                    route.props.append({"kind": "flowers", "y": int(yy), "x": int(xx), "why": "beacon"})


# ------------------------------------------------------------------ water

def stage_lakes(route: Route) -> None:
    from . import region_water

    b = route.spec.biome
    if b in TREE_BIOMES:
        _bend_obstacles(route)
    if b == "riverland" and not region_water.has_region_rivers(route):
        from .rivers import paint_own_rivers

        paint_own_rivers(route, 1 if route.h * route.w < 3600 else 2)
    if b == "beach" and not region_water.has_region_sea(route):
        _sea(route)
    if b == "desert":
        _desert(route)
    region_water.paint_region_water(route)
    if b != "desert":  # no ponds in the sand
        _ponds(route, route.spec.lakes if b != "riverland" else max(1, route.spec.lakes // 2))
    route.snap("water")


def _bend_obstacles(route: Route) -> None:
    """A real road bends because something is in the way. Where the main road jogs
    sideways, put a pond right where it would have run straight on, so the bend reads
    as going round the water rather than a random kink. Either end of the jog will do,
    and a small pond when a big one would crowd a side lane."""
    from .purpose import record

    p = plan_of(route)
    for s in p.sections:
        m = p.lane(s.lanes[0])
        for pts in (m.pts, m.pts[::-1]):
            for i in range(1, len(pts) - 1):
                (u0, v0), (u1, v1), (u2, v2) = pts[i - 1], pts[i], pts[i + 1]
                if not (v0 == v1 and u1 == u2 and abs(v2 - v1) >= 4):
                    continue  # want: a run along u, then a sideways jog
                if _jog_has_pond(route, m.frame, (u1, v1), (u2, v2)):
                    continue
                du = 1 if u1 > u0 else -1
                cells = _bend_pond_cells(route, m, u1, v1, du)
                if cells is None:
                    continue
                _dig_bend_pond(route, m.frame, cells, u1, v1, du)
                rec = record(route, "bend", m.frame.yx(u1, v1), why="pond")
                rec["reason"] = "The road bends round a pond instead of running straight on."
                route.zones.append(Zone("lake", cells, "pond at the bend", {"purpose": rec}))


BEND_POND_SHAPES = (  # (rows along the road beyond the corner, half-width across it)
    (range(3, 7), 2),
    (range(3, 6), 1),
)


def _jog_has_pond(route: Route, f, corner_a, corner_b) -> bool:
    ends = {f.yx(*corner_a), f.yx(*corner_b)}
    return any(tuple(x["cell"]) in ends for x in route.__dict__.get("purposes", [])
               if x["kind"] == "bend" and x.get("why") == "pond")


def _bend_pond_cells(route: Route, m, u1: int, v1: int, du: int) -> Optional[List[Cell]]:
    f = m.frame
    walk = np.isin(route.cls, list(WALKABLE) + ["w"])
    for ks, half in BEND_POND_SHAPES:
        last = ks[-1]
        cells = [f.yx(u1 + du * k, v1 + dv) for k in ks for dv in range(-half, half + 1)
                 if not (half >= 2 and k == last and abs(dv) == half)]
        own = set(m.cells) | set(cells)
        shore = [f.yx(u1 + du * 2, v1 + dv) for dv in range(-half, half + 1)]
        if _pond_fits(route, walk, cells, own) and all(route.inside(*c, 3) and route.cls[c] == "f" for c in shore):
            return cells
    return None


def _pond_fits(route: Route, walk: np.ndarray, cells: List[Cell], own: set) -> bool:
    for (y, x) in cells:
        if not route.inside(y, x, 3) or route.cls[y, x] != "f" or route.protect[y, x]:
            return False
        box = walk[y - 2:y + 3, x - 2:x + 3]
        ys, xs = np.nonzero(box)
        if any((y - 2 + a, x - 2 + b) not in own for a, b in zip(ys, xs)):
            return False
    return True


def _dig_bend_pond(route: Route, f, cells: List[Cell], u1: int, v1: int, du: int) -> None:
    """The shore between the road and the pond is a strip of grass you can walk."""
    half = max(abs(f.uv(*c)[1] - v1) for c in cells)
    for c in cells:
        route.cls[c] = "w"
    for dv in range(-half, half + 1):
        c = f.yx(u1 + du * 2, v1 + dv)
        route.cls[c] = ground_class(route)
        route.protect[c] = True


def _ponds(route: Route, count: int) -> None:
    """Ponds sit in plain clearings or in the trees right beside a lane (the lane is the
    shore). They replace trees, never open ground, so they don't open the map up."""
    p, rng = plan_of(route), route.rng
    made = 0
    for n in p.nodes:
        if made >= count:
            break
        if n.kind in ("clearing", "corner") and n.area is None and n.hy >= 3 and n.hx >= 3 and rng.random() < 0.6:
            y0, y1, x0, x1 = n.y - n.hy + 2, n.y + n.hy - 2, n.x - n.hx + 2, n.x + n.hx - 2
            crit = set(route.critical_path)
            cells = [(y, x) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
            # keep the main lane dry: the pond goes to one side of it
            cells = [c for c in cells if all((c[0] + dy, c[1] + dx) not in crit for dy in (-2, -1, 0, 1, 2) for dx in (-2, -1, 0, 1, 2))]
            if len(cells) >= 4:
                for (y, x) in cells:
                    route.cls[y, x] = "w"
                route.zones.append(Zone("lake", cells, f"pond {made + 1}", {"purpose": {
                    "kind": "pond", "reason": "The rest stop is by a pond.", "cell": list(cells[0])}}))
                made += 1
    tries = 0
    walk = _walk_mask(route)
    while made < count and tries < 200:
        tries += 1
        lanes = [l for l in p.lanes if l.kind in ("main", "loop")]
        l = lanes[int(rng.integers(0, len(lanes)))]
        cl = l.center()
        if len(cl) < 10:
            continue
        y, x = cl[int(rng.integers(3, len(cl) - 3))]
        f = l.frame
        u, v = f.uv(y, x)
        side = 1 if rng.random() < 0.5 else -1
        hu, hv = int(rng.integers(2, 4)), int(rng.integers(2, 3))
        lo, hi = -((l.width - 1) // 2), l.width // 2
        vin = v + (hi + 1 if side > 0 else lo - 1)
        cells = []
        for du in range(-hu, hu + 1):
            for k in range(0, 2 * hv + 1):
                vv = vin + side * k
                corner = abs(du) == hu and k == 2 * hv
                if not corner:
                    cells.append(f.yx(u + du, vv))
        ok = all(route.inside(cy, cx, 3) and route.cls[cy, cx] == "f" and not route.protect[cy, cx] for cy, cx in cells)
        if not ok:
            continue
        # nothing else walkable close by apart from the lane it borders
        ring = set()
        for (cy, cx) in cells:
            for dy in (-2, -1, 0, 1, 2):
                for dx in (-2, -1, 0, 1, 2):
                    ring.add((cy + dy, cx + dx))
        near = [c for c in ring if route.inside(*c) and walk[c] and c not in l.cells]
        if near:
            continue
        for (cy, cx) in cells:
            route.cls[cy, cx] = "w"
        from .purpose import record

        fy, fx = f.yx(u, v + (hi if side > 0 else lo))
        rec = record(route, "fishing_spot", (fy, fx))
        route.zones.append(Zone("lake", cells, f"pond {made + 1}", {"purpose": rec}))
        made += 1


def _desert(route: Route) -> None:
    """Desert, after Route 111 (and Desert Resort): wide open sand between rock
    mountains, dotted with boulders and little pointed rocks; not a cliff maze and
    not an empty box. The wall between the lanes shrinks back to its core (rounded,
    clear of every lane), so the lanes become wide tracks of sand that still run
    between outcrops; a ragged rim holds the map edge; special areas keep their
    walls. Like Route 111, a mountain is a cliff rim round a rock-mass top (all
    cliff tiles as stacked faces, i.e. stripes). Boulders and 2x2 rock cones sit in
    drifts on the open sand, never on the main road, with sand all round so they
    can't cut anything off. Stray water from the clearings goes."""
    from scipy.ndimage import binary_dilation, binary_opening, distance_transform_cdt

    h, w, rng = route.h, route.w, route.rng
    cliff, sand = wall_class(route), ground_class(route)
    route.cls[route.cls == "w"] = sand
    wall = route.cls == cliff
    far = distance_transform_cdt(wall, metric="chessboard") >= 4
    n = fbm(h, w, 5.0, rng)
    yy, xx = np.mgrid[:h, :w]
    edge = np.minimum.reduce([yy, xx, h - 1 - yy, w - 1 - xx])
    keep = (edge < 2 + 4 * n) | binary_dilation(far & (n > 0.35), iterations=1)
    special = np.zeros(wall.shape, dtype=bool)
    for c in route.zone_cells("special"):
        special[c] = True
    disc = np.array([[0, 1, 1, 0], [1, 1, 1, 1], [1, 1, 1, 1], [0, 1, 1, 0]], dtype=bool)
    keep = binary_opening(np.pad(wall & keep, 4, constant_values=True), disc)[4:-4, 4:-4]  # round, no slivers
    keep |= wall & binary_dilation(special, iterations=3)
    route.cls[wall & ~keep] = sand
    # sand nobody can walk to (old water boxed in by the wall) goes back to rock
    from .generate import reachable

    hid = np.zeros(wall.shape, dtype=bool)
    for c in route.zone_cells("special", "hidden"):
        hid[c] = True
    lost = (route.cls == sand) & ~reachable(route.cls, route.exits[0][1]) & ~hid
    route.cls[lost] = cliff
    keep |= lost
    depth_into_rock = distance_transform_cdt(np.pad(keep, 1, constant_values=True), metric="chessboard")[1:-1, 1:-1]
    route.cls[depth_into_rock >= 2] = "r"  # rock-mass top inside the cliff rim
    from .desert_bands import band_the_mesas

    band_the_mesas(route, depth_into_rock, cliff)
    # boulders and rock cones in loose drifts (high noise), as on Route 111
    busy = np.zeros(wall.shape, dtype=bool)
    for c in route.critical_path + [(e.y, e.x) for e in route.events] + [c for _, cs in route.exits for c in cs]:
        busy[c] = True
    free = (route.cls == sand) & ~binary_dilation(busy, iterations=2) & (edge >= 4)
    m = fbm(h, w, 8.0, rng)
    boulder = code("BOULDER", "u")
    cones = rocks = 0
    for y, x in zip(*np.nonzero(free & (m > 0.5))):
        if rng.random() < 0.06 and free[y - 1:y + 3, x - 1:x + 3].all():
            route.cls[y:y + 2, x:x + 2] = [["r", "r"], [cliff, boulder]]  # a pointed rock, 2x2
            free[y - 1:y + 3, x - 1:x + 3] = False
            cones += 1
        elif rng.random() < 0.15 and free[y - 1:y + 2, x - 1:x + 2].all():
            route.cls[y, x] = boulder
            free[y - 1:y + 2, x - 1:x + 2] = False
            rocks += 1
    route.log.append(f"desert: {int(keep.sum())} cells of mountain, {cones} rock cones, {rocks} boulders")


def _sea(route: Route) -> None:
    """Beach: the planner kept the lanes off one lateral third of the map; that side
    is open sea (touching the map edge), with a sand shore between it and the land,
    sea rocks offshore and a few tide pools on the beach. The waterline and the
    beach's width drift slowly along the route (coves and sand bars, not a ruler
    line), and the beach starts where the woods end, so it is part of the route."""
    p, rng = plan_of(route), route.rng
    sea, sand, rock, shoal = code("SEA", "w"), code("SAND", "p"), code("BOULDER", "r"), code("SHALLOW", "w")
    f = p.legs[0][0]
    vlo, vhi = p.vrange(f)
    us = [f.uv(y, x)[0] for y in (0, route.h - 1) for x in (0, route.w - 1)]
    u0, n = min(us), max(us) - min(us) + 1
    wave = fbm(2, n, 16.0, rng, octaves=2)  # row 0: waterline, row 1: beach width
    wave = (wave - wave.min(1, keepdims=True)) / np.ptp(wave, 1, keepdims=True).clip(1e-6)
    shore = [vhi + 2 + int(round(6 * wave[0, i])) for i in range(n)]
    width = [4 + int(round(5 * wave[1, i])) for i in range(n)]
    # lanes and clearings stay on land: no sand spits or islands cut off by an inlet
    far = np.full(n, -10 ** 6)
    for y, x in zip(*np.nonzero(route.protect)):
        if route.cls[y, x] != "f":
            u, v = f.uv(y, x)
            far[u - u0] = max(far[u - u0], v)
    for i in range(n):
        reach = far[max(0, i - 3):i + 4].max() + 3
        if shore[i] < reach:
            width[i] += reach - shore[i]
            shore[i] = reach
    for y in range(route.h):
        for x in range(route.w):
            u, v = f.uv(y, x)
            if route.protect[y, x]:
                continue
            s = shore[u - u0]
            if v >= s:
                route.cls[y, x] = sea
            elif v >= s - width[u - u0]:
                route.cls[y, x] = sand
    # one straight path from the road down to the beach if no lane reaches it yet
    walk = WALKABLE | {sand}
    H, W = route.h, route.w

    def on_beach(y, x):
        return any(0 <= y + dy < H and 0 <= x + dx < W and route.cls[y + dy, x + dx] == sand
                   and not route.protect[y + dy, x + dx] for dy, dx in DIRS4)

    if not any(route.cls[c] in walk and route.protect[c] and on_beach(*c) for c in zip(*np.nonzero(route.protect))):
        crit = [c for c in route.critical_path if route.inside(*c, 4)]
        if crit:
            u, v = f.uv(*max(crit, key=lambda c: f.uv(*c)[1]))
            for k in range(1, 40):
                cells = [f.yx(u + du, v + k) for du in (-1, 0, 1)]
                cells = [c for c in cells if route.inside(*c, 1)]
                if not cells or all(route.cls[c] in (sand, sea) for c in cells):
                    break
                for c in cells:
                    if not route.protect[c]:
                        route.cls[c] = sand
    for _ in range(int(route.h * route.w / 250) if min(route.h, route.w) > 7 else 0):
        y, x = int(rng.integers(3, route.h - 3)), int(rng.integers(3, route.w - 3))
        u, v = f.uv(y, x)
        if route.cls[y, x] == sea and v >= shore[u - u0] + 2:
            route.cls[y, x] = rock
    for z in route.zones:
        if z.kind == "clearing" and rng.random() < 0.4 and len(z.cells) >= 16:
            y, x = z.cells[len(z.cells) // 3]
            if not route.protect[y, x] and (y, x) not in set(route.critical_path):
                route.cls[y, x] = shoal


# ------------------------------------------------------------------ surfaces

def stage_road(route: Route) -> None:
    """Lanes get a readable surface: a dirt road on the critical path (3 wide) that
    forks where a lane splits off and rejoins it; merges, dead ends and trails stay
    grass, so the road still reads at a glance."""
    p = plan_of(route)
    special = set(route.zone_cells("special"))
    pc = path_class(route)
    if route.spec.road:
        for s in p.sections:
            # the road itself forks: a lane that splits off and rejoins it is road too
            # (a narrower one); merges and dead ends stay grass
            road = [p.lane(i) for i in s.lanes if i == s.lanes[0]
                    or (p.lane(i).kind == "loop" and p.lane(i).fate == "full" and p.lane(i).level == 1)]
            for m in road:
                for (y, x) in m.cells:
                    if (y, x) not in special and route.cls[y, x] == ground_class(route):
                        route.cls[y, x] = pc
        for e, cells in route.exits[:2]:
            for c in cells:
                for d in range(3):
                    yy, xx = inward(route, e, c, d)
                    if route.cls[yy, xx] == ground_class(route):
                        route.cls[yy, xx] = pc
    ss = side_surface(route)
    if ss != ground_class(route):
        for l in p.lanes:
            if l.kind in ("loop", "dead_end"):
                for (y, x) in l.cells:
                    if route.cls[y, x] == ground_class(route):
                        route.cls[y, x] = ss
    route.snap("road")


# ------------------------------------------------------------------ grass

def stage_tall_grass(route: Route) -> None:
    b = route.spec.biome
    if b in ("meadow", "plains"):
        _fields(route)
    _lane_patches(route)
    route.snap("tall grass")


def _lane_patches(route: Route) -> None:
    """Rectangular tall grass across side lanes (you have to wade through it) and
    patches in pockets and clearings. The main road mostly stays readable, but every
    so often a patch of tall grass grows right over it too, so it isn't one unbroken
    dirt strip the whole way."""
    p, rng, spec = plan_of(route), route.rng, route.spec
    tg = float(spec.tall_grass)
    tall = grass_class(route)
    for l in p.lanes:
        f = l.frame
        segs = list(zip(l.pts, l.pts[1:]))
        for (a, b2) in segs:
            if a[1] != b2[1]:
                continue
            n = abs(b2[0] - a[0])
            if n < 6:
                continue
            pk = (l.meta.get("purpose") or {}).get("kind")
            p_patch = 1.0 if pk == "grass_track" else tg * (0.8 if l.kind != "main" else 0.6)
            if rng.random() > p_patch:
                continue
            on_road = l.kind == "main" and route.spec.road
            grass_over_road = on_road and rng.random() < 0.3
            ln = int(rng.integers(3, 5)) if l.kind == "main" else int(rng.integers(4, min(9, n)))
            st = int(rng.integers(min(a[0], b2[0]) + 1, max(min(a[0], b2[0]) + 2, max(a[0], b2[0]) - ln)))
            lo, hi = -((l.width - 1) // 2), l.width // 2
            for u in range(st, st + ln):
                for v in range(a[1] + lo, a[1] + hi + 1):
                    y, x = f.yx(u, v)
                    if on_road and not grass_over_road and route.cls[y, x] == path_class(route):
                        continue
                    if route.cls[y, x] in (ground_class(route), path_class(route), side_surface(route)):
                        route.cls[y, x] = tall
    for z in route.zones:
        if z.kind != "clearing" or "center" not in z.meta:
            continue
        cy, cx = z.meta["center"]
        if z.meta.get("pocket"):
            for (y, x) in z.cells:
                if route.cls[y, x] == ground_class(route) and (abs(y - cy) + abs(x - cx)) >= 2 and rng.random() < 0.8:
                    route.cls[y, x] = tall
        elif z.meta.get("node") in ("clearing", "corner"):
            ys, xs = zip(*z.cells)
            y0, x0 = min(ys) + 1, min(xs) + 1
            crit = set(route.critical_path)
            for y in range(y0, y0 + 3):
                for x in range(x0, x0 + 3):
                    if route.cls[y, x] == ground_class(route) and (y, x) not in crit:
                        route.cls[y, x] = tall


def _fields(route: Route) -> None:
    """Meadow/plains: the space between lanes is farmland, not forest. Rectangular
    fields of tall and short grass, each fenced along the lanes by whatever the
    biome's wall is (a hedge for meadow, a hill's cliff rim for plains), entered
    through a gate, or dropped into from a lane over a ledge."""
    p, rng = plan_of(route), route.rng
    from scipy.ndimage import distance_transform_cdt, label

    wc = wall_class(route)
    walk = np.isin(route.cls, list("gpts"))
    water = route.cls == "w"
    d = distance_transform_cdt(~(walk | water), metric="chessboard")
    room = (d >= 3) & (route.cls == wc)
    room[:3, :] = room[-3:, :] = False
    room[:, :3] = room[:, -3:] = False
    n_fields = 0
    n = fbm(route.h, route.w, 4.0, rng, octaves=2)
    n = (n - n.min()) / max(1e-6, float(n.max() - n.min()))
    lab, k = label(room)
    for i in range(1, k + 1):
        comp = lab == i
        if comp.sum() < 12:
            continue
        ys, xs = np.nonzero(comp)
        # largest axis-aligned rectangle inside the component (greedy around its centre)
        rect = _max_rect(comp)
        if rect is None:
            continue
        y0, y1, x0, x1 = rect
        # fields are plots, not prairies: cap the size, keep a copse around them
        hy_, hx_ = min(y1 - y0 + 1, 8), min(x1 - x0 + 1, 11)
        cy_, cx_ = (y0 + y1) // 2, (x0 + x1) // 2
        y0, x0 = cy_ - hy_ // 2, cx_ - hx_ // 2
        y1, x1 = y0 + hy_ - 1, x0 + hx_ - 1
        rect = (y0, y1, x0, x1)
        if float(np.isin(route.cls, list("gpts")).mean()) + hy_ * hx_ / route.cls.size > float(getattr(route.spec, "max_walkable", 0.4)):
            break
        if (y1 - y0 + 1) < 3 or (x1 - x0 + 1) < 3:
            continue
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                route.cls[y, x] = "t" if n[y, x] > 0.42 else "g"
        # a lone 2x2 obstacle (tree, or hill nub for plains) in bigger fields
        if (y1 - y0) >= 6 and (x1 - x0) >= 6:
            ty, tx = int(rng.integers(y0 + 2, y1 - 2)), int(rng.integers(x0 + 2, x1 - 2))
            route.cls[ty:ty + 2, tx:tx + 2] = wc
        cells = [(y, x) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
        from .purpose import record

        rec = record(route, "plains_plot" if route.spec.biome == "plains" else "field",
                      ((y0 + y1) // 2, (x0 + x1) // 2))
        route.zones.append(Zone("clearing", cells, f"field {n_fields + 1}", {"center": ((y0 + y1) // 2, (x0 + x1) // 2),
                                                                            "field": True, "purpose": rec}))
        _gate(route, rect)
        n_fields += 1


def _max_rect(mask: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
    h, w = mask.shape
    best, area = None, 0
    heights = np.zeros(w, dtype=int)
    for y in range(h):
        heights = np.where(mask[y], heights + 1, 0)
        stack: List[int] = []
        for x in range(w + 1):
            hh = heights[x] if x < w else 0
            start = x
            while stack and heights[stack[-1]] >= hh:
                top = stack.pop()
                height = heights[top]
                left = stack[-1] + 1 if stack else 0
                a = height * (x - left)
                if a > area and height >= 3 and (x - left) >= 3:
                    area, best = a, (y - height + 1, y, left, x - 1)
            stack.append(x)
    return best


def _gate(route: Route, rect) -> None:
    """Open the field to its nearest lane: a ledge if the lane is right above it
    (jump down into the field), otherwise a 2-wide gap in the wall around it
    (the hedge, for meadow; the hill's rim, for plains)."""
    y0, y1, x0, x1 = rect
    wc = wall_class(route)
    walk = np.isin(route.cls, list("gpt"))
    best = None
    for side in ("n", "s", "w", "e"):
        if side in ("n", "s"):
            for x in range(x0, x1):
                yy = y0 - 1 if side == "n" else y1 + 1
                dy = -1 if side == "n" else 1
                for k in range(1, 5):
                    c1 = (yy + dy * (k - 1), x)
                    c2 = (yy + dy * (k - 1), x + 1)
                    if not (route.inside(*c1, 2) and route.inside(*c2, 2)):
                        break
                    if route.protect[c1] and route.protect[c2] and walk[c1] and walk[c2]:
                        if best is None or k < best[0]:
                            best = (k, side, x)
                        break
        else:
            for y in range(y0, y1):
                xx = x0 - 1 if side == "w" else x1 + 1
                dx = -1 if side == "w" else 1
                for k in range(1, 5):
                    c1 = (y, xx + dx * (k - 1))
                    c2 = (y + 1, xx + dx * (k - 1))
                    if not (route.inside(*c1, 2) and route.inside(*c2, 2)):
                        break
                    if route.protect[c1] and route.protect[c2] and walk[c1] and walk[c2]:
                        if best is None or k < best[0]:
                            best = (k, side, y)
                        break
    if best is None:
        return
    k, side, a = best
    cells = []
    if side in ("n", "s"):
        dy = -1 if side == "n" else 1
        yy = y0 - 1 if side == "n" else y1 + 1
        cells = [(yy + dy * i, a + j) for i in range(k - 1) for j in (0, 1)]
        if side == "n" and k - 1 >= 2:
            # lane is north of the field: the wall row next to the field becomes a ledge
            for j in (0, 1):
                route.cls[y0 - 1, a + j] = "s"
            cells = [c for c in cells if c[0] != y0 - 1]
            route.zones.append(Zone("loop", [(y0 - 1, a), (y0 - 1, a + 1)], "field ledge", {"ledge": (y0 - 1, a, a + 1)}))
    else:
        dx = -1 if side == "w" else 1
        xx = x0 - 1 if side == "w" else x1 + 1
        cells = [(a + j, xx + dx * i) for i in range(k - 1) for j in (0, 1)]
    for (y, x) in cells:
        if route.cls[y, x] == wc:
            route.cls[y, x] = "g"
        route.protect[y, x] = True


# ------------------------------------------------------------------ ledges

def stage_ledges(route: Route) -> None:
    """One-way drops. Where two lanes run side by side north/south of each other, a
    short connector with a ledge lets you drop from the upper lane to the lower one
    (sometimes with an item on the ledge top, visible from below). Where lanes run
    north-south, a ledge across a loop lane makes it a downhill-only shortcut."""
    from .terraces import TERRACE_BIOMES

    if route.spec.biome in ("desert", "beach") + TERRACE_BIOMES:  # sand has no steps; terraces cut their own
        route.snap("ledges")
        return
    p, rng, spec = plan_of(route), route.rng, route.spec
    chance = float(getattr(spec, "ledge_chance", 0.35))
    walk = _walk_mask(route)
    owner = p.owner
    for s in p.sections:
        lanes = [p.lane(i) for i in s.lanes]
        f, _ = p.legs[s.leg]
        # where the road forks, one fork always gets a ledge: a one-way shortcut, so
        # which way round you can take it depends on the direction you walk
        forks = [l for l in lanes if l.kind == "loop" and l.fate == "full" and l.level == 1]
        if len(lanes) < 2 or (not (forks and f.vertical) and rng.random() > chance * 1.6):
            continue
        if not f.vertical:
            # lanes stacked north/south: connector at some column
            cands = []
            for l in lanes:
                for (y, x) in l.center():
                    if not (3 <= x < route.w - 4):
                        continue
                    # march south from this lane through trees to another lane
                    lo, hi = -((l.width - 1) // 2), l.width // 2
                    yb = y + hi + 1 if f.l[0] > 0 else y + 1
                    yy = yb
                    while yy < route.h - 3 and route.cls[yy, x] == "f" and route.cls[yy, x + 1] == "f":
                        yy += 1
                    gap = yy - yb
                    if 3 <= gap <= 6 and walk[yy, x] and walk[yy, x + 1] and owner[yy, x] >= 0 and owner[yy, x] != l.id \
                            and owner[yy, x] == owner[yy, x + 1]:
                        side_ok = all(route.cls[r, x - 1] == "f" and route.cls[r, x + 2] == "f" for r in range(yb, yy))
                        if side_ok:
                            cands.append((yb, yy, x))
            if not cands:
                continue
            yb, yy, x = cands[int(rng.integers(0, len(cands)))]
            ly = yb + (yy - yb) // 2
            for r in range(yb, yy):
                for c in (x, x + 1):
                    route.cls[r, c] = "s" if r == ly else "g"
                    route.protect[r, c] = True
            route.zones.append(Zone("loop", [(r, x) for r in range(yb - 1, yy + 1)], "ledge drop",
                                    {"ledge": (ly, x, x + 1), "one_way": "south"}))
            if ly - yb >= 1 and rng.random() < 0.5:
                route.events.append(Event("item_ball", ly - 1, x, {"item": _item(rng), "why": "on the ledge, seen from below"}))
        else:
            loops = forks or [lane for lane in lanes if lane.kind == "loop" and lane.fate in ("full", "merge")]
            if not loops:
                continue
            l = loops[int(rng.integers(0, len(loops)))]
            segs = [(a, b) for a, b in zip(l.pts, l.pts[1:]) if a[1] == b[1] and abs(b[0] - a[0]) >= 7]
            if not segs:
                continue
            a, b = segs[int(rng.integers(0, len(segs)))]
            um = (a[0] + b[0]) // 2
            lo, hi = -((l.width - 1) // 2), l.width // 2
            cells = [f.yx(um, a[1] + k) for k in range(lo, hi + 1)]
            ys = {c[0] for c in cells}
            if len(ys) != 1:
                continue
            ly = cells[0][0]
            xs = sorted(c[1] for c in cells)
            if not all(route.cls[ly, xx] in WALKABLE for xx in xs):
                continue
            if not (route.cls[ly, xs[0] - 1] == "f" and route.cls[ly, xs[-1] + 1] == "f"):
                continue
            for xx in xs:
                route.cls[ly, xx] = "s"
            route.zones.append(Zone("loop", [(ly, xx) for xx in xs], f"ledge on lane {l.id}",
                                    {"ledge": (ly, xs[0], xs[-1]), "one_way": "south"}))
    _elevation(route, p)
    route.snap("ledges")


def _tiers(route: Route) -> np.ndarray:
    """Height tier per cell: the region's tiers when the route came from a region,
    else the world height map (``height.py``). Computed once, kept on the route."""
    if route.elevation is not None:
        return route.elevation
    from . import height

    elev = height.tiers(route.spec, route.h, route.w)
    reg = route.spec.region if isinstance(getattr(route.spec, "region", None), dict) else {}
    tiers = (reg.get("elevation") or {}).get("tiers") if reg else None
    if tiers:
        try:  # coarse region grid (one digit per region cell), upsampled to metatiles
            cz = int(reg.get("cell", 4) or 4)
            g = np.array([[int(ch) for ch in row] for row in tiers], dtype=np.int8)
            up = np.kron(g, np.ones((cz, cz), dtype=np.int8))[:route.h, :route.w]
            elev = np.pad(up, ((0, route.h - up.shape[0]), (0, route.w - up.shape[1])), mode="edge")
        except Exception as e:
            route.log.append(f"region tiers ignored ({type(e).__name__})")
    route.elevation = elev
    return elev


def _elevation(route: Route, p) -> None:
    """Draw the height map's steps where the ground drops one tier to the south:
    a river falls (waterfall), a side lane or clearing gets a one-way ledge (only
    where nothing gets cut off; the main path stays two-way), and on the mountain
    every lane climbs it on stairs."""
    from .generate import can_leave, reachable

    elev = _tiers(route)
    cls = route.cls
    fall, stairs = code("WATERFALL", "a"), code("STAIRS", "p")
    mountain = route.spec.biome == "mountain"
    river = {c for z in route.zones if z.meta.get("river") for c in z.cells}
    keep = set(route.critical_path) | {(e.y, e.x) for e in route.events} \
        | {(int(q["y"]), int(q["x"])) for q in route.props if "y" in q} \
        | {c for z in route.zones if z.kind == "special" for c in z.cells}
    drop = np.zeros(cls.shape, dtype=bool)
    drop[:-1] = elev[:-1] > elev[1:]
    rise = np.zeros(cls.shape, dtype=bool)
    rise[:-1] = elev[:-1] < elev[1:]
    wet = np.zeros(cls.shape, dtype=bool)
    for c in river:
        wet[c] = cls[c] == "w"
    lip = drop & wet
    q = np.pad(cls == code("BRIDGE", "q"), 3)
    for dy in range(-3, 4):  # keep falls off the crossings: a fall cuts the river
        for dx in range(-2, 3):
            lip &= ~q[3 + dy:3 + dy + route.h, 3 + dx:3 + dx + route.w]
    lip[:-1] &= wet[1:]
    lip[1:-1, 1:-1] &= lip[1:-1, :-2] | lip[1:-1, 2:]  # at least two wide
    falls = 0
    for y, x in zip(*np.nonzero(lip[1:-1, 1:-1])):
        y, x = y + 1, x + 1
        cls[y, x] = fall
        if wet[y - 1, x]:
            cls[y - 1, x] = fall  # two rows tall, so it reads as a fall
        falls += 1
    walk = np.isin(cls, list(WALKABLE))
    ok = walk.copy()
    ok[1:-1] &= walk[:-2] & walk[2:]
    ok[[0, -1]] = False
    ok &= cls != code("BRIDGE", "q")
    starts = route.exits[0][1]
    exits = [c for _, cs in route.exits for c in cs]
    reach = reachable(cls, starts)
    stuck = int((reach & ~can_leave(cls, exits)).sum())
    ledges = 0
    crit = set(route.critical_path)
    fixed = keep - crit

    def two_way(c) -> bool:  # every exit can still walk back to the first one
        return all(reachable(c, cs)[starts[0]] for _, cs in route.exits[1:])

    for y in range(1, route.h - 1):
        x = 0
        while x < route.w:
            run = []
            while x < route.w and drop[y, x] and ok[y, x] and (y, x) not in fixed:
                run.append(x)
                x += 1
            x += 1
            if len(run) < 2:
                continue
            on_main = any((y, xx) in crit for xx in run)
            # the whole step as a ledge, else a ledge with a one-cell gap at an end
            # (the classic way round), so the step shows on the main road too
            tries = [run] if not on_main else []
            if len(run) >= 3:
                tries += [run[:-1], run[1:]]
            for part in tries:
                was = cls[y, part].copy()
                cls[y, part] = "s"
                r2 = reachable(cls, starts)
                lost = reach & ~r2
                lost[y, part] = False
                if lost.any() or int((r2 & ~can_leave(cls, exits)).sum()) > stuck or (on_main and not two_way(cls)):
                    cls[y, part] = was  # it would cut something off: no ledge here
                    continue
                reach = r2
                ledges += len(part)
                route.zones.append(Zone("loop", [(y, xx) for xx in part], "height step",
                                        {"ledge": (y, part[0], part[-1]), "one_way": "south", "why": "height map"}))
                break
    if mountain:  # every crossing the ledges left two-way climbs on stairs
        for y, x in zip(*np.nonzero((drop | rise) & ok & np.isin(cls, list(WALKABLE)))):
            if cls[y, x] != "s":
                cls[y, x] = stairs
        # a lane running along a step would be one long flight: keep three stairs in
        # the middle; either side becomes a ledge (a drop) or a two-tall cliff face,
        # where that cuts nothing off (the main road too: the flight keeps it two-way)
        fixed = keep - set(route.critical_path)
        wall = wall_class(route)
        for y in range(1, route.h - 2):
            xs = np.nonzero(cls[y] == stairs)[0]
            for run in np.split(xs, np.nonzero(np.diff(xs) > 1)[0] + 1):
                if len(run) < 6:
                    continue
                mid = len(run) // 2
                for side in (run[:mid - 1], run[mid + 2:]):
                    side = [int(x) for x in side if (y, x) not in fixed and (y + 1, x) not in fixed]
                    if len(side) < 2:
                        continue
                    rows = [y] if all(drop[y, x] for x in side) else [y, y + 1]
                    was = cls[np.ix_(rows, side)].copy()
                    cls[np.ix_(rows, side)] = "s" if len(rows) == 1 else wall
                    r2 = reachable(cls, starts)
                    lost = reach & ~r2
                    lost[np.ix_(rows, side)] = False
                    if lost.any() or int((r2 & ~can_leave(cls, exits)).sum()) > stuck:
                        cls[np.ix_(rows, side)] = was
                        continue
                    reach = r2
                    ledges += len(side) if len(rows) == 1 else 0
    route.log.append(f"height: {falls} waterfall cells, {ledges} ledge cells on the tier steps"
                     + (", stairs on the rest" if mountain else ""))


# ------------------------------------------------------------------ dressing

def stage_dressing(route: Route) -> None:
    """Deliberate texture, never tiling-breaking slivers: lone 2x2 trees and rock pairs
    in clearings, tall-grass tufts, flowers, a few bites out of long straight edges."""
    p, rng = plan_of(route), route.rng
    crit = set(route.critical_path)
    lane_core: Set[Cell] = set()
    for l in p.lanes:
        lane_core |= set(l.center())
    walk = _walk_mask(route)
    for z in route.zones:
        if z.kind != "clearing" or z.meta.get("field") or len(z.cells) < 30:
            continue
        cells = set(z.cells)
        ys, xs = zip(*z.cells)
        for _ in range(2):
            y, x = int(rng.integers(min(ys) + 1, max(ys))), int(rng.integers(min(xs) + 1, max(xs)))
            block = [(y, x), (y + 1, x), (y, x + 1), (y + 1, x + 1)]
            ring = [(y + dy, x + dx) for dy in range(-1, 3) for dx in range(-1, 3)]
            if all(c in cells and route.inside(*c) and route.cls[c] in (ground_class(route), grass_class(route)) for c in block) and not any(c in lane_core or c in crit for c in ring):
                kind = wall_class(route) if rng.random() < 0.6 else code("BOULDER", "r")
                for c in block:
                    route.cls[c] = kind
                route.props.append({"kind": "lone_tree" if kind == "f" else "boulders", "y": y, "x": x})
        for (y, x) in z.cells:
            if route.inside(y, x) and route.cls[y, x] == "g" and rng.random() < 0.08:
                route.props.append({"kind": "flowers", "y": int(y), "x": int(x)})
    _furniture(route, p)
    # flower borders along meadow lanes
    if route.spec.biome == "meadow":
        for l in p.lanes:
            for i, (y, x) in enumerate(l.center()):
                if i % 3 == 0:
                    route.props.append({"kind": "flowers", "y": int(y), "x": int(x), "why": "border"})
    route.snap("dressing")


def _furniture(route: Route, p) -> None:
    """Route furniture, as events/props: HM obstacles on optional lanes (two cut trees
    across a side lane's mouth, a smashable rock or a Strength boulder in a spur), the
    keepers of special areas (a researcher inside each, someone guarding a hidden way
    in), and small set dressing (bench, stump, fence stub, flowers). No other people:
    trainers and passers-by are placed by hand."""
    rng = route.rng
    crit = set(route.critical_path)
    taken = {(e.y, e.x) for e in route.events}

    def free(c):
        return route.inside(*c, 3) and route.cls[c] in WALKABLE and c not in taken and c not in crit

    loops = [l for l in p.lanes if l.kind == "loop" and l.level == 1]
    rng.shuffle(loops)
    for l in loops[: max(1, len(loops) // 3)]:
        cl = l.center()
        if len(cl) < 12:
            continue
        a, b = cl[4], cl[5]
        f = l.frame
        lo, hi = -((l.width - 1) // 2), l.width // 2
        u, v = f.uv(*a)
        cells = [f.yx(u, v + k) for k in range(lo, hi + 1)] if cl[3][1] != a[1] or cl[3][0] == a[0] else []
        cells = [c for c in cells if free(c)] or [c for c in (a,) if free(c)]
        for c in cells[:2]:
            route.events.append(Event("cut_tree", c[0], c[1], {"why": "cut trees across a side path"}))
            taken.add(c)
    for l in p.lanes:
        if l.fate == "dead" and l.meta.get("spur") and rng.random() < 0.35:
            cl = l.center()
            if len(cl) > 6 and free(cl[len(cl) // 2]):
                kind = "rock_smash" if rng.random() < 0.6 else "strength_boulder"
                y, x = cl[len(cl) // 2]
                route.events.append(Event(kind, y, x, {"why": "HM obstacle on a side path"}))
                taken.add((y, x))
    # hidden special areas: the way in is guarded (Cut tree, boulder, or someone in the way)
    for l in p.lanes:
        if l.area is not None and not l.meta.get("back_road"):
            cl = l.center()
            c = cl[min(3, len(cl) - 1)] if cl else None
            if c is not None and free(c):
                g = ["cut_tree", "cut_tree", "strength_boulder", "npc"][int(rng.integers(0, 4))]
                data = {"why": f"guards the way to {l.area.get('name', 'a special area')}"}
                if g == "npc":
                    data.update(role="blocker", text="", lore=_lore_id(route))
                route.events.append(Event(g, c[0], c[1], data))
                taken.add(c)
    for z in route.zones:
        if z.kind == "special" and z.cells:
            for (y, x) in z.cells[:: max(1, len(z.cells) // 12)]:
                if free((y, x)):
                    route.events.append(Event("npc", y, x, {"role": "researcher", "near": z.name,
                                                            "text": "", "lore": _lore_id(route)}))
                    taken.add((y, x))
                    break
    clearings = [z for z in route.zones if z.kind == "clearing" and len(z.cells) >= 12]
    for z in clearings:
        if rng.random() < 0.5:
            y, x = z.cells[int(rng.integers(0, len(z.cells)))]
            if free((y, x)):
                route.props.append({"kind": ["bench", "stump", "fence_stub", "flower_patch"][int(rng.integers(0, 4))],
                                    "y": int(y), "x": int(x)})
