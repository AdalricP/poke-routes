"""Why things are where they are.

Owner feedback on the played routes: "there's no point for things being in certain
places". So every side lane, pocket and clearing gets a *purpose*: a short record of
what is there and why a path would lead to it (a woodcutters' camp, a shrine, a pond
with a fisherman, a lookout...). The record goes into the zone meta and
``route.purposes`` (both end up in scene.json), and where it's natural the purpose is
also put in the map as a sign, an NPC or props, so a player can see the reason too.
"""
from __future__ import annotations

from typing import List, Set, Tuple

import numpy as np

from .generate import DIRS4, WALKABLE, Event, Route, Zone

Cell = Tuple[int, int]

# kind: (label for signposts, reason, needs a pocket)
PURPOSES = {
    "woodcutter_camp": ("woodcutters' camp", "Woodcutters fell timber here; the lane is their cart track.", True),
    "berry_thicket": ("berry bushes", "Wild berry bushes; villagers walk out to pick them.", True),
    "shrine": ("wayside shrine", "An old wayside shrine; the path was worn by people leaving offerings.", True),
    "pond_fisher": ("fishing pond", "A quiet pond; an old fisherman keeps the path trodden.", True),
    "ruin": ("old ruin", "Foundations of a farmstead that burned long ago; the lane was its drive.", True),
    "hunters_blind": ("hunter's blind", "A hunter's blind over tall grass where wild Pokemon come to feed.", False),
    "lookout": ("lookout", "The trees open at the edge of the wood: a view over the valley.", False),
    "grass_track": ("tall grass", "The long way round through tall grass, where catchers go after wild Pokemon.", False),
    "berry_track": ("berry track", "The old track before the road was laid; berry bushes still grow along it.", False),
    "shortcut": ("shortcut", "Locals cut through the trees here between the two tracks.", False),
    "rest_stop": ("rest stop", "A clearing where travellers stop halfway; a bench and a signpost.", False),
    "bend": ("bend", "The road turns here, keeping to the dry ground.", False),
    "fishing_spot": ("fishing spot", "The road runs along a pond; someone is always fishing here.", False),
    "field": ("hayfield", "A hedged hayfield; the gap in the hedge is the farmer's gate.", False),
    "plains_plot": ("grazing plot", "A fenced plot; the rise of ground behind it keeps stock from wandering off.", False),
    "road": ("road", "The road on to the next place.", False),
    "secret_exit": ("hidden way out", "An overgrown way out that only locals know.", False),
}
DEAD_END_CYCLE = ["woodcutter_camp", "berry_thicket", "shrine", "pond_fisher", "hunters_blind", "ruin"]


def record(route: Route, kind: str, cell: Cell, **extra) -> dict:
    label, reason, _ = PURPOSES[kind]
    p = {"kind": kind, "label": label, "reason": reason, "cell": [int(cell[0]), int(cell[1])]}
    p.update(extra)
    route.__dict__.setdefault("purposes", []).append(p)
    return p


def label(kind: str) -> str:
    return PURPOSES.get(kind, (kind.replace("_", " "),))[0]


def _free_trees(route: Route, cells, own: Set[Cell]) -> bool:
    """All cells are plain trees and nothing walkable is near them except ``own``."""
    walk = np.isin(route.cls, list(WALKABLE) + ["w"])
    for (y, x) in cells:
        if not route.inside(y, x, 3) or route.cls[y, x] != "f":
            return False
        box = walk[y - 2:y + 3, x - 2:x + 3]
        ys, xs = np.nonzero(box)
        if any((y - 2 + a, x - 2 + b) not in own for a, b in zip(ys, xs)):
            return False
    return True


def _tip_pocket(route: Route, lane) -> Set[Cell]:
    """A small 4x4 pocket at a lane tip that has none, pushed out past the tip."""
    cl = lane.center()
    if len(cl) < 2:
        return set()
    (ay, ax), (by, bx) = cl[-2], cl[-1]
    dy, dx = by - ay, bx - ax
    cy, cx = by + dy * 2, bx + dx * 2
    cells = {(y, x) for y in range(cy - 1, cy + 3) for x in range(cx - 1, cx + 3)} if dy >= 0 and dx >= 0 else \
        {(y, x) for y in range(cy - 2, cy + 2) for x in range(cx - 2, cx + 2)}
    cells -= lane.cells
    own = set(lane.cells) | cells
    return cells if _free_trees(route, cells, own) else set()


def _near_edge(route: Route, c: Cell, d: int = 7) -> bool:
    y, x = c
    return min(y, x, route.h - 1 - y, route.w - 1 - x) <= d


def lane_purposes(route: Route, p, carve, ground, tall, item) -> None:
    """Give every side lane a purpose and dress its end so the reason shows."""
    rng = route.rng
    cycle = list(DEAD_END_CYCLE)
    rng.shuffle(cycle)
    used: List[str] = []

    def next_kind(ok) -> str:
        for k in cycle:
            if k not in used and ok(k):
                used.append(k)
                return k
        for k in cycle:
            if ok(k):
                return k
        return "hunters_blind"

    n_loop = 0
    for l in p.lanes:
        if l.kind == "main":
            continue
        if l.fate != "dead":
            kind = "grass_track" if n_loop % 2 == 0 else "berry_track"
            n_loop += 1
            cl = l.center()
            mid = cl[len(cl) // 2]
            l.meta["purpose"] = record(route, kind, mid, lane=l.id)
            if kind == "berry_track":
                f = l.frame
                for c in (cl[len(cl) // 3], cl[2 * len(cl) // 3]):
                    u, v = f.uv(*c)
                    for dv in (l.width // 2 + 1, -((l.width - 1) // 2) - 1):
                        yy, xx = f.yx(u, v + dv)
                        if route.inside(yy, xx, 3) and route.cls[yy, xx] == "f" and not route.protect[yy, xx]:
                            route.cls[yy, xx] = ground
                            route.protect[yy, xx] = True
                            route.events.append(Event("berry_tree", int(yy), int(xx), {"why": "berry track"}))
                            break
            continue
        tip = l.center()[-1]
        if l.meta.get("back_road") and "ruin" not in used:
            kind = "ruin"
            used.append(kind)
        elif _near_edge(route, tip) and "lookout" not in used:
            kind = "lookout"
            used.append(kind)
        else:
            kind = next_kind(lambda k: True)
        pocket = set(l.pocket_cells)
        if PURPOSES[kind][2] and not pocket:
            pocket = _tip_pocket(route, l)
            if not pocket:
                kind = "lookout" if _near_edge(route, tip) else "hunters_blind"
        if not PURPOSES[kind][2]:
            pocket = set()
        l.meta["end_style"] = kind
        if pocket:
            carve(route, pocket)
            ys, xs = zip(*pocket)
            c = (int(round(np.mean(ys))), int(round(np.mean(xs))))
        else:
            c = tip
        rec = record(route, kind, c, lane=l.id)
        l.meta["purpose"] = rec
        if pocket:
            route.zones.append(Zone("clearing", sorted(pocket), f"{label(kind)} {l.id}",
                                    {"center": c, "pocket": True, "lane": l.id, "purpose": rec}))
        _dress(route, kind, c, tip, sorted(pocket), ground, tall, item)


def _dress(route: Route, kind: str, c: Cell, tip: Cell, pocket: List[Cell], ground, tall, item) -> None:
    rng = route.rng
    cy, cx = c
    inner = [q for q in pocket if q != c and route.cls[q] in WALKABLE]

    def spot(i: int) -> Cell:
        return inner[i % len(inner)] if inner else tip

    if kind == "woodcutter_camp":
        route.props += [{"kind": "stump", "y": spot(0)[0], "x": spot(0)[1], "why": kind},
                        {"kind": "log_pile", "y": spot(5)[0], "x": spot(5)[1], "why": kind}]
        route.events.append(Event("npc", cy, cx, {"role": "woodcutter", "text": "", "why": kind}))
    elif kind == "berry_thicket":
        for i in (1, 4, 9):
            q = spot(i)
            route.events.append(Event("berry_tree", q[0], q[1], {"why": kind}))
    elif kind == "shrine":
        route.props.append({"kind": "shrine", "y": cy, "x": cx, "why": kind})
        route.events.append(Event("sign", cy + 1, cx, {"type": "shrine", "text": "", "why": kind}))
    elif kind == "pond_fisher":
        far = sorted(pocket, key=lambda q: -(abs(q[0] - tip[0]) + abs(q[1] - tip[1])))
        water = [q for q in far[:6]]
        for q in water:
            route.cls[q] = "w"
        dry = [q for q in pocket if q not in water and route.cls[q] in WALKABLE]
        if dry:
            q = min(dry, key=lambda d: min(abs(d[0] - w[0]) + abs(d[1] - w[1]) for w in water))
            route.events.append(Event("npc", q[0], q[1], {"role": "fisherman", "text": "", "why": kind}))
        route.zones.append(Zone("lake", water, "fishing pond"))
    elif kind == "ruin":
        route.props.append({"kind": "ruin_wall", "y": spot(2)[0], "x": spot(2)[1], "why": kind})
        route.events.append(Event("sign", cy, cx, {"type": "ruin", "text": "", "why": kind}))
        if rng.random() < 0.6:
            q = spot(7)
            route.events.append(Event("item_ball", q[0], q[1], {"item": item(rng), "why": "left in the ruin"}))
    elif kind == "hunters_blind":
        route.props.append({"kind": "hunters_blind", "y": tip[0], "x": tip[1], "why": kind})
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                q = (tip[0] + dy, tip[1] + dx)
                if (dy or dx) and route.inside(*q, 3) and route.cls[q] == ground:
                    route.cls[q] = tall
        route.events.append(Event("hidden_item", tip[0], tip[1], {"item": "ITEM_POKE_BALL", "why": "dropped by a hunter"}))
    elif kind == "lookout":
        route.events.append(Event("sign", tip[0], tip[1], {"type": "view", "text": "", "why": kind}))


GOAL_KINDS = ["shrine", "ruin", "berry_thicket", "woodcutter_camp"]


def goal_zone(route: Route):
    return next((zone for zone in route.zones if zone.kind == "clearing" and zone.name == "goal"), None)


def farthest_from_entry(route: Route, cells: List[Cell]) -> Cell:
    entry_y, entry_x = route.exits[0][1][0]
    return max(cells, key=lambda cell: abs(cell[0] - entry_y) + abs(cell[1] - entry_x))


def goal_purpose(route: Route, ground, tall, item) -> None:
    """The far end of a one-exit route: a dressed clearing with an item at the back,
    so walking to the end of a dead end pays off."""
    zone = goal_zone(route)
    if zone is None or not route.exits:
        return
    center = tuple(zone.meta["center"])
    kind = GOAL_KINDS[int(route.rng.integers(0, len(GOAL_KINDS)))]
    walkable_cells = [cell for cell in zone.cells if route.cls[cell] in WALKABLE] or [center]
    reward_cell = farthest_from_entry(route, walkable_cells)
    landmark = zone.meta.get("goal")
    purpose = record(route, kind, center, goal=True, landmark=landmark if isinstance(landmark, str) else None)
    zone.meta["purpose"] = purpose
    pocket = [cell for cell in zone.cells if cell != reward_cell]
    _dress(route, kind, center, center, pocket, ground, tall, item)
    route.events.append(Event("item_ball", int(reward_cell[0]), int(reward_cell[1]),
                              {"item": item(route.rng), "why": "the end of the dead end"}))
