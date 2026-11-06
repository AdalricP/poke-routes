"""Route generation pipeline.

A route is built in stages, each a small function that edits a shared
``Route``. Every stage leaves a snapshot so the process can be inspected
(``pokeroutes generate --stages`` writes one image per stage).

Semantic classes (single chars, shared with the emerald autotiler):
    f tree / forest     g grass      p dirt road     t tall grass
    w water             r rock       s ledge (jump south)
"""
from __future__ import annotations

import heapq
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .classify import ONE_WAY_LEDGE_STEPS
from .noise import fbm
from .spec import Exit, RouteSpec

Cell = Tuple[int, int]  # (y, x)
try:  # classify.py owns the class codes (sand, ash, bridge, stairs, ... are walkable too)
    from .classify import BLOCKED_CLASSES as _BLK, WALKABLE_CLASSES as _WLK

    WALKABLE = set("gpt") | set(_WLK)
    BLOCKING = set("fwr") | set(_BLK) | set("b")
except ImportError:  # pragma: no cover
    WALKABLE = set("gpt")
    BLOCKING = set("fwr")

DIRS4 = ((-1, 0), (1, 0), (0, -1), (0, 1))


@dataclass
class Zone:
    kind: str  # main | branch | loop | dead_end | hidden | trail | clearing | special | lake
    cells: List[Cell]
    name: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class Event:
    kind: str  # hidden_item | item_ball | cut_tree | sign | rock_smash | marker
    y: int
    x: int
    data: dict = field(default_factory=dict)


@dataclass
class Route:
    spec: RouteSpec
    cls: np.ndarray
    rng: np.random.Generator
    zones: List[Zone] = field(default_factory=list)
    events: List[Event] = field(default_factory=list)
    props: List[dict] = field(default_factory=list)  # decorative objects for the scene renderer
    exits: List[Tuple[Exit, List[Cell]]] = field(default_factory=list)
    protect: Optional[np.ndarray] = None  # cells later stages must not block
    snapshots: List[Tuple[str, np.ndarray]] = field(default_factory=list)
    log: List[str] = field(default_factory=list)
    # braided skeleton, as data: nodes / sections / lanes / endings (see braid.py)
    graph: dict = field(default_factory=dict)
    # the way to go: ordered cells from the entry exit to the far exit along the main lane
    critical_path: List[Cell] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    # height tier per cell (0 = lowest); synthesised per section when no region data is given
    elevation: Optional[np.ndarray] = None
    gates: List[dict] = field(default_factory=list)

    @property
    def h(self) -> int:
        return self.cls.shape[0]

    @property
    def w(self) -> int:
        return self.cls.shape[1]

    def inside(self, y: int, x: int, margin: int = 0) -> bool:
        return margin <= y < self.h - margin and margin <= x < self.w - margin

    def snap(self, name: str) -> None:
        self.snapshots.append((name, self.cls.copy()))

    def zone_cells(self, *kinds: str) -> List[Cell]:
        out: List[Cell] = []
        for z in self.zones:
            if z.kind in kinds:
                out.extend(z.cells)
        return out


# ---------------------------------------------------------------- geometry

def astar(cost: np.ndarray, start: Cell, goal: Cell) -> List[Cell]:
    h, w = cost.shape
    openq = [(0.0, start)]
    came: Dict[Cell, Optional[Cell]] = {start: None}
    g = {start: 0.0}
    while openq:
        _, cur = heapq.heappop(openq)
        if cur == goal:
            break
        for dy, dx in DIRS4:
            ny, nx = cur[0] + dy, cur[1] + dx
            if not (0 <= ny < h and 0 <= nx < w) or not np.isfinite(cost[ny, nx]):
                continue
            ng = g[cur] + cost[ny, nx]
            if ng < g.get((ny, nx), 1e18):
                g[(ny, nx)] = ng
                came[(ny, nx)] = cur
                heur = abs(ny - goal[0]) + abs(nx - goal[1])
                heapq.heappush(openq, (ng + heur, (ny, nx)))
    if goal not in came:
        return []
    path, c = [], goal
    while c is not None:
        path.append(c)
        c = came[c]
    return path[::-1]


def disc(y: int, x: int, r: float) -> List[Cell]:
    ir = int(math.ceil(r))
    return [(y + dy, x + dx) for dy in range(-ir, ir + 1) for dx in range(-ir, ir + 1) if dy * dy + dx * dx <= r * r + 0.3]


def blob(route: Route, cy: int, cx: int, ry: float, rx: float, rough: float = 0.35) -> List[Cell]:
    """Organic blob: ellipse distance perturbed by noise."""
    h, w = route.h, route.w
    n = fbm(h, w, 6.0, route.rng, octaves=3)
    n = (n - n.min()) / max(1e-6, float(n.max() - n.min()))  # fbm clusters near 0.5; use the full range
    cells = []
    for y in range(max(1, int(cy - ry * 1.5)), min(h - 1, int(cy + ry * 1.5) + 1)):
        for x in range(max(1, int(cx - rx * 1.5)), min(w - 1, int(cx + rx * 1.5) + 1)):
            d = ((y - cy) / ry) ** 2 + ((x - cx) / rx) ** 2
            if d + (n[y, x] - 0.5) * rough * 2 < 1.0:
                cells.append((y, x))
    return cells


def exit_cells(route: Route, e: Exit) -> Tuple[Cell, List[Cell]]:
    h, w = route.h, route.w
    if e.side in ("north", "south"):
        x = int(round(e.at * (w - 1 - 2 * e.width))) + e.width
        y = 0 if e.side == "north" else h - 1
        cells = [(y, x + i) for i in range(-(e.width // 2), e.width - e.width // 2)]
    else:
        y = int(round(e.at * (h - 1 - 2 * e.width))) + e.width
        x = 0 if e.side == "west" else w - 1
        cells = [(y + i, x) for i in range(-(e.width // 2), e.width - e.width // 2)]
    return cells[len(cells) // 2], cells


def inward(route: Route, e: Exit, cell: Cell, depth: int) -> Cell:
    y, x = cell
    if e.side == "north":
        return (min(y + depth, route.h - 1), x)
    if e.side == "south":
        return (max(y - depth, 0), x)
    if e.side == "west":
        return (y, min(x + depth, route.w - 1))
    return (y, max(x - depth, 0))


def emptiest(route: Route, margin: int = 5, taken: Sequence[Cell] = ()) -> Optional[Cell]:
    """The blocked cell farthest from anything walkable: where the map is still blank."""
    from scipy.ndimage import distance_transform_edt

    open_ = np.isin(route.cls, list(WALKABLE) + ["w", "s"])
    for (y, x) in taken:
        open_[max(0, y - 2) : y + 3, max(0, x - 2) : x + 3] = True
    if not open_.any():
        return None
    d = distance_transform_edt(~open_)
    d[:margin, :] = d[-margin:, :] = 0
    d[:, :margin] = d[:, -margin:] = 0
    d = d + route.rng.random(d.shape) * 1.5
    y, x = np.unravel_index(int(np.argmax(d)), d.shape)
    return (int(y), int(x)) if d[y, x] > 3 else None


from .stages import (  # noqa: E402  (stages need the helpers above)
    stage_base, stage_skeleton, stage_branches, stage_clearings, stage_lakes, stage_road,
    stage_tall_grass, stage_ledges, stage_dressing, _walk_mask,
)


def stage_cleanup(route: Route) -> None:
    """Fix only what breaks tiling: 1-wide tree walls (GBA trees are 2x2), lone
    one-cell holes boxed in by trees, single water cells. Texture stays."""
    cls = route.cls
    for _ in range(3):
        changed = 0
        for y in range(1, route.h - 1):
            for x in range(1, route.w - 1):
                c = cls[y, x]
                n4 = [cls[y + dy, x + dx] for dy, dx in DIRS4]
                if c == "f":
                    open_v = n4[0] != "f" and n4[1] != "f"
                    open_h = n4[2] != "f" and n4[3] != "f"
                    if open_v or open_h:
                        cls[y, x] = "g"
                        changed += 1
                elif c in "gt" and not route.protect[y, x]:
                    if all(v == "f" for v in n4):
                        cls[y, x] = "f"
                        changed += 1
                elif c == "w":
                    if sum(1 for v in n4 if v == "w") == 0:
                        cls[y, x] = "g"
                        changed += 1
        changed += _thin_water(route)
        if not changed:
            break
    route.snap("cleanup")


def _thin_water(route: Route) -> int:
    """Water no 2x2 block of water covers (1-wide strips, L-shaped puddles) has no
    bank tiles in either tileset and comes out as broken edges: make it grass.
    Bridges and waterfalls count as water here so the river around them stays."""
    cls = route.cls
    wet = np.isin(cls, ["w", "q", "a"])
    blk = wet[:-1, :-1] & wet[1:, :-1] & wet[:-1, 1:] & wet[1:, 1:]
    cover = np.zeros_like(wet)
    for dy in (0, 1):
        for dx in (0, 1):
            cover[dy:dy + blk.shape[0], dx:dx + blk.shape[1]] |= blk
    thin = (cls == "w") & ~cover
    cls[thin] = "g"
    return int(thin.sum())


# ---------------------------------------------------------------- reachability

def reachable(cls: np.ndarray, starts: Sequence[Cell]) -> np.ndarray:
    """Flood fill honouring one-way ledges: south ledges are hopped going south, north
    ledges going north."""
    h, w = cls.shape
    seen = np.zeros((h, w), dtype=bool)
    stack = [s for s in starts if cls[s] in WALKABLE]
    for s in stack:
        seen[s] = True
    while stack:
        y, x = stack.pop()
        for dy, dx in DIRS4:
            ny, nx = y + dy, x + dx
            if not (0 <= ny < h and 0 <= nx < w) or seen[ny, nx]:
                continue
            c = cls[ny, nx]
            if c in ONE_WAY_LEDGE_STEPS:
                landing_y, landing_x = ny + dy, nx + dx
                hops = (dy, dx) == ONE_WAY_LEDGE_STEPS[c] and 0 <= landing_y < h and 0 <= landing_x < w
                if hops and cls[landing_y, landing_x] in WALKABLE and not seen[landing_y, landing_x]:
                    seen[landing_y, landing_x] = True
                    stack.append((landing_y, landing_x))
                continue
            if c in WALKABLE:
                seen[ny, nx] = True
                stack.append((ny, nx))
    return seen


def stage_connect(route: Route) -> None:
    """Every walkable pocket must be reachable from the first exit; carve if not."""
    starts = route.exits[0][1]
    for _ in range(12):
        seen = reachable(route.cls, starts)
        walk = np.isin(route.cls, list(WALKABLE))
        orphan = walk & ~seen
        if not orphan.any():
            break
        ys, xs = np.nonzero(orphan)
        target = (int(ys[0]), int(xs[0]))
        sy, sx = np.nonzero(seen)
        d = (sy - target[0]) ** 2 + (sx - target[1]) ** 2
        k = int(np.argmin(d))
        src = (int(sy[k]), int(sx[k]))
        cost = np.where(np.isin(route.cls, ["w", "s"]), np.inf, 1.0)
        cost[route.cls == "f"] = 3.0
        for (y, x) in astar(cost, src, target):
            if route.cls[y, x] in "fr":
                route.cls[y, x] = "g"
    _untrap(route)
    route.snap("connect")


def can_leave(cls: np.ndarray, exits: Sequence[Cell]) -> np.ndarray:
    """Cells from which some exit can still be walked to (ledges are one-way)."""
    h, w = cls.shape
    seen = np.zeros((h, w), dtype=bool)
    stack = [c for c in exits if cls[c] in WALKABLE]
    for s in stack:
        seen[s] = True
    while stack:
        y, x = stack.pop()
        prev = [(y + dy, x + dx) for dy, dx in DIRS4]
        for ledge, (step_y, step_x) in ONE_WAY_LEDGE_STEPS.items():
            ledge_y, ledge_x = y - step_y, x - step_x
            if 0 <= ledge_y < h and 0 <= ledge_x < w and cls[ledge_y, ledge_x] == ledge:
                prev.append((ledge_y - step_y, ledge_x - step_x))
        for ny, nx in prev:
            if 0 <= ny < h and 0 <= nx < w and not seen[ny, nx] and cls[ny, nx] in WALKABLE:
                seen[ny, nx] = True
                stack.append((ny, nx))
    return seen


def _untrap(route: Route) -> None:
    """No softlocks: a ledge that drops into a pocket with no way back to an exit
    (a field boxed in by hedges, a lane that dead-ends below the drop) becomes
    plain grass, so the pocket is two-way."""
    starts = route.exits[0][1]
    exits = [c for _, cells in route.exits for c in cells]
    for _ in range(20):
        trapped = reachable(route.cls, starts) & ~can_leave(route.cls, exits)
        ys, xs = np.nonzero(route.cls[:-1] == "s")
        bad = [(y, x) for y, x in zip(ys, xs) if trapped[y + 1, x]]
        if not bad:
            break
        for c in bad:
            route.cls[c] = "g"
        route.log.append(f"untrap: {len(bad)} ledge cells over a dead-end pocket made two-way")


# ---------------------------------------------------------------- places & events

def stage_specials(route: Route) -> None:
    from .places import place_specials

    place_specials(route)
    route.snap("special areas")


def stage_hidden(route: Route) -> None:
    from .places import place_hidden

    place_hidden(route)
    route.snap("hidden")


from .plains import stage_open_plains  # noqa: E402  (plains needs the helpers above)

def stage_terraces(route: Route) -> None:
    from .terraces import sculpt_terraces
    from .verticality import show_height_steps_as_rock_faces

    sculpt_terraces(route)
    show_height_steps_as_rock_faces(route)
    route.snap("terraces")


def stage_obstacles(route: Route) -> None:
    from .obstacles import seat_field_move_obstacles_in_single_gaps

    seat_field_move_obstacles_in_single_gaps(route)
    route.snap("obstacles")


def stage_tree_blocks(route: Route) -> None:
    from .tree_blocks import settle_half_tree_blocks

    settle_half_tree_blocks(route)
    route.snap("tree blocks")


def stage_rivers_restored(route: Route) -> None:
    from .rivers import restore_rivers

    restore_rivers(route)
    route.snap("rivers")


def stage_surf_crossings(route: Route) -> None:
    from .surf_crossing import open_surf_crossings

    open_surf_crossings(route)
    route.snap("surf")


def stage_route_gate(route: Route) -> None:
    from .gates import build_route_gates

    build_route_gates(route)


def stage_open_land(route: Route) -> None:
    from .open_land import open_land_by_woodland
    from .walkability_guard import run_keeping_walkability

    run_keeping_walkability(route, "open land", open_land_by_woodland)


def stage_worn_paths(route: Route) -> None:
    from .worn_ground import wear_paths_into_patches

    wear_paths_into_patches(route)


def stage_stepped_terraces(route: Route) -> None:
    from .stepped_terraces import lay_stepped_terraces
    from .walkability_guard import run_keeping_walkability

    run_keeping_walkability(route, "stepped terraces", lay_stepped_terraces)


def stage_seams(route: Route) -> None:
    from .seams import stage_seams as stamp_seam_bands_shared_with_neighbours

    stamp_seam_bands_shared_with_neighbours(route)


STAGES: List[Tuple[str, Callable[[Route], None]]] = [
    ("base", stage_base),
    ("skeleton", stage_skeleton),
    ("branches", stage_branches),
    ("clearings", stage_clearings),
    ("specials", stage_specials),
    ("lakes", stage_lakes),
    ("road", stage_road),
    ("tall_grass", stage_tall_grass),
    ("ledges", stage_ledges),
    ("hidden", stage_hidden),
    ("dressing", stage_dressing),
    ("cleanup", stage_cleanup),
    ("connect", stage_connect),
    ("terraces", stage_terraces),
    ("open plains", stage_open_plains),
    ("tree blocks", stage_tree_blocks),
    ("obstacles", stage_obstacles),
    ("rivers", stage_rivers_restored),
    ("surf", stage_surf_crossings),
    ("gate", stage_route_gate),
    ("seams", stage_seams),
    ("open land", stage_open_land),
    ("worn paths", stage_worn_paths),
    ("stepped terraces", stage_stepped_terraces),
]


def density(route: Route) -> dict:
    """How open the route is: walkable fraction, and how many places two different
    open areas are separated by fewer than 2 trees (thin walls)."""
    walk = np.isin(route.cls, list(WALKABLE) + ["s"])
    frac = float(walk.mean())
    thin = 0
    h, w = walk.shape
    for y in range(h):
        for x in range(w):
            if walk[y, x]:
                continue
            for dy, dx in ((0, 1), (1, 0)):
                # tree runs of length 1 between two open cells
                y0, x0, y1, x1 = y - dy, x - dx, y + dy, x + dx
                if 0 <= y0 and 0 <= x0 and y1 < h and x1 < w and walk[y0, x0] and walk[y1, x1]:
                    thin += 1
    return {"walkable_fraction": round(frac, 3), "thin_walls": thin,
            "max_walkable": float(getattr(route.spec, "max_walkable", 0.40))}


SMALLEST_ROUTE_SIDE_IN_METATILES = 16


def spec_with_a_buildable_size(spec: RouteSpec) -> RouteSpec:
    from dataclasses import replace

    width_in_metatiles, height_in_metatiles = int(spec.size[0]), int(spec.size[1])
    if min(width_in_metatiles, height_in_metatiles) >= SMALLEST_ROUTE_SIDE_IN_METATILES:
        return spec
    buildable_size = [max(width_in_metatiles, SMALLEST_ROUTE_SIDE_IN_METATILES),
                      max(height_in_metatiles, SMALLEST_ROUTE_SIDE_IN_METATILES)]
    buildable_spec = replace(spec, size=buildable_size)
    return buildable_spec


def generate(spec: RouteSpec) -> Route:
    requested_size = list(spec.size)
    spec = spec_with_a_buildable_size(spec)
    route = Route(spec, np.full((spec.height, spec.width), "f", dtype="<U1"), np.random.default_rng(spec.seed))
    if list(spec.size) != requested_size:
        route.log.append(f"size: {requested_size[0]}x{requested_size[1]} is too small to lay a route in; built at {spec.size[0]}x{spec.size[1]}")
    for name, fn in STAGES:
        fn(route)
        route.log.append(name)
    route.stats = density(route)
    if route.stats["walkable_fraction"] > route.stats["max_walkable"]:
        route.log.append(f"density: {route.stats['walkable_fraction']:.2f} walkable is over the budget")
    return route
