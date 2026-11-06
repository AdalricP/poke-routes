"""Plains: an open field held in by a raised grassy verge at the map's edge.

The verge is gentle elevation, not a cliff: a tree line at the very edge, a strip
of raised grass (class ``o``: drawn as grass, never walked on) dotted with bushes
and the odd small tree, and a low ledge lip facing the field, so you can only see
it from below. Only a side the region puts against mountains rises as rock."""
from __future__ import annotations

from typing import List, Sequence

import numpy as np
from scipy.ndimage import binary_dilation, distance_transform_cdt, label

from .generate import WALKABLE, Cell, Route, Zone, astar, can_leave, inward, reachable
from .plains_grass import grow_tall_grass

SEAL_DEPTH = 2
RIM_DEPTHS = (3, 4, 4, 5, 5, 6)
VERGE_TOP = "o"
MOUNTAIN_CODES = "MS"
ROCKY_SIDE_SHARE = 0.3
SIDES = ("north", "south", "west", "east")
STEPS_AT_MOST = 2
RIM_RUN = (10, 22)
GROVE_SPACING = 18
MIN_STEP = 6
LONGEST_STEP = 12
MIN_RUN = 4
CLIFF = "k"
NOTCH_MARGIN = 1
GROVE_SHAPES = ((2, 3), (3, 2), (2, 2), (1, 3), (1, 2), (1, 1))


def stage_open_plains(route: Route) -> None:
    """Open the plains: the hill mass between lanes becomes open grass with a few
    groves, the map's edge rises into a grassy verge with a low ledge lip (rock only
    on a side that faces mountains), at most a couple of gentle steps follow the
    height map, and tall grass grows in soft patches and strips along the road."""
    if route.spec.biome != "plains":
        return
    built = route.cls.copy()
    keep = kept_cells(route)
    rim = rim_mask(route, keep)
    hedge_secrets(route, built)
    open_hill_mass(route, built)
    drop_stray_ledges(route, rim)
    raise_rim(route, rim, rocky_sides(route))
    plant_groves(route, built, rim)
    reconnect_orphans(route, rim)
    terrace_steps(route, rim)
    grow_tall_grass(route, rim)
    route.snap("open plains")


def kept_cells(route: Route) -> np.ndarray:
    keep = np.zeros(route.cls.shape, dtype=bool)
    for cell in route.critical_path:
        keep[cell] = True
    for exit_, cells in route.exits:
        for cell in cells:
            for depth in range(max(RIM_DEPTHS) + 2):
                keep[inward(route, exit_, cell, depth)] = True
    for event in route.events:
        keep[event.y, event.x] = True
    for prop in route.props:
        if "y" in prop and "x" in prop:
            keep[int(prop["y"]), int(prop["x"])] = True
    for cell in secret_cells(route):
        keep[cell] = True
    return binary_dilation(keep, np.ones((3, 3), bool))


def secret_cells(route: Route) -> List[Cell]:
    return route.zone_cells("hidden", "trail", "special")


def rim_mask(route: Route, keep: np.ndarray) -> np.ndarray:
    h, w = route.cls.shape
    rim = np.zeros((h, w), dtype=bool)
    north = clipped_depths(route, keep, w, lambda i, d: keep[d, i])
    south = clipped_depths(route, keep, w, lambda i, d: keep[h - 1 - d, i])
    west = clipped_depths(route, keep, h, lambda i, d: keep[i, d])
    east = clipped_depths(route, keep, h, lambda i, d: keep[i, w - 1 - d])
    for x in range(w):
        rim[:north[x], x] = True
        rim[h - south[x]:, x] = True
    for y in range(h):
        rim[y, :west[y]] = True
        rim[y, w - east[y]:] = True
    return rim & ~keep


def clipped_depths(route: Route, keep: np.ndarray, length: int, kept) -> List[int]:
    profile = depth_profile(route, length)
    clipped = [first_kept(kept, i, profile[i]) for i in range(length)]
    return without_stubs(without_slivers(widened_notches(clipped)))


def without_stubs(depths: List[int]) -> List[int]:
    """Flatten a bump in the verge only a few cells long: it would stick out of the
    lip line as a two-sided ledge tongue."""
    out = list(depths)
    changed = True
    while changed:
        changed = False
        runs = depth_runs(out)
        for i, (start, end, depth) in enumerate(runs):
            sides = [runs[j][2] for j in (i - 1, i + 1) if 0 <= j < len(runs)]
            if end - start < MIN_RUN and sides and depth > max(sides):
                out[start:end] = [max(sides)] * (end - start)
                changed = True
                break
    return out


def depth_runs(depths: List[int]) -> List[tuple]:
    runs, start = [], 0
    for i in range(1, len(depths) + 1):
        if i == len(depths) or depths[i] != depths[start]:
            runs.append((start, i, depths[start]))
            start = i
    return runs


def widened_notches(depths: List[int]) -> List[int]:
    n = len(depths)
    return [min(depths[max(0, i - NOTCH_MARGIN):i + NOTCH_MARGIN + 1]) for i in range(n)]


def without_slivers(depths: List[int]) -> List[int]:
    return [d if d > SEAL_DEPTH else min(d, SEAL_DEPTH) for d in depths]


def first_kept(kept, i: int, depth: int) -> int:
    for d in range(depth):
        if kept(i, d):
            return d
    return depth


def depth_profile(route: Route, length: int) -> List[int]:
    rng = route.rng
    out: List[int] = []
    depth = int(rng.choice(RIM_DEPTHS))
    while len(out) < length:
        out.extend([depth] * int(rng.integers(*RIM_RUN)))
        depth = next_depth(rng, depth)
    return out[:length]


def next_depth(rng, depth: int) -> int:
    step = int(rng.choice((-1, 1)))
    return int(np.clip(depth + step, min(RIM_DEPTHS), max(RIM_DEPTHS)))


def hedge_secrets(route: Route, built: np.ndarray) -> None:
    near = np.zeros(built.shape, dtype=bool)
    for cell in secret_cells(route):
        near[cell] = True
    if not near.any():
        return
    ring = binary_dilation(near, np.ones((5, 5), bool)) & (built == CLIFF)
    route.cls[ring] = "f"


def open_hill_mass(route: Route, built: np.ndarray) -> None:
    hill = route.cls == CLIFF
    small, count = label(hill)
    for i in range(1, count + 1):
        blob = small == i
        route.cls[blob] = "f" if blob.sum() <= 6 and is_block(blob) else "g"


def is_block(blob: np.ndarray) -> bool:
    ys, xs = np.nonzero(blob)
    return ys.max() - ys.min() >= 1 and xs.max() - xs.min() >= 1


def drop_stray_ledges(route: Route, rim: np.ndarray) -> None:
    for y, x in np.argwhere(np.isin(route.cls, ["s", "e", "x", "j"]) & ~rim):
        route.cls[y, x] = ground_below(route, int(y), int(x))
    route.zones = [z for z in route.zones if "ledge" not in z.meta]


def ground_below(route: Route, y: int, x: int) -> str:
    below = route.cls[y + 1, x] if y + 1 < route.h else "g"
    return below if below in "gtp" else "g"


def rocky_sides(route: Route) -> List[str]:
    """Sides of the map the region puts against mountains: their verge may rise as rock."""
    region = route.spec.region if isinstance(getattr(route.spec, "region", None), dict) else {}
    rows = (region.get("biome") or {}).get("grid") or []
    if not rows:
        return []
    grid = np.array([list(row) for row in rows if row])
    band_y, band_x = max(1, grid.shape[0] // 3), max(1, grid.shape[1] // 3)
    bands = {"north": grid[:band_y], "south": grid[-band_y:], "west": grid[:, :band_x], "east": grid[:, -band_x:]}
    return [side for side in SIDES if np.isin(bands[side], list(MOUNTAIN_CODES)).mean() >= ROCKY_SIDE_SHARE]


def nearest_side(h: int, w: int, y: int, x: int) -> str:
    gaps = {"north": y, "south": h - 1 - y, "west": x, "east": w - 1 - x}
    return min(SIDES, key=lambda side: gaps[side])


def raise_rim(route: Route, rim: np.ndarray, rocky: Sequence[str] = ()) -> None:
    """The map's edge rises into raised grass (no tree wall): the stepped terraces
    stage later draws its ledge bands and dresses it. On a mountain side the whole
    verge is rock (its face shows where it meets the field)."""
    h, w = rim.shape
    cls = route.cls
    raised_edge = (edge_ring(h, w) & (rim | ~exit_mouths(route))) | rim
    cls[raised_edge] = VERGE_TOP
    for y, x in zip(*np.nonzero(raised_edge)):
        if nearest_side(h, w, y, x) in rocky:
            cls[y, x] = CLIFF


def exit_mouths(route: Route) -> np.ndarray:
    """The exits' cells through the outer ring (what ``export.seal_edges`` leaves open)."""
    mouth = np.zeros(route.cls.shape, dtype=bool)
    for exit_, cells in route.exits:
        for cell in cells:
            for depth in range(SEAL_DEPTH):
                mouth[inward(route, exit_, cell, depth)] = True
    return mouth


def edge_ring(h: int, w: int) -> np.ndarray:
    ring = np.ones((h, w), dtype=bool)
    ring[SEAL_DEPTH:-SEAL_DEPTH, SEAL_DEPTH:-SEAL_DEPTH] = False
    return ring


def plant_groves(route: Route, built: np.ndarray, rim: np.ndarray) -> None:
    room = distance_transform_cdt(built == CLIFF, metric="chessboard") >= 2
    room &= ~binary_dilation(rim, np.ones((5, 5), bool)) & (route.cls == "g")
    taken: List[Cell] = []
    for y, x in shuffled(route, np.argwhere(room)):
        if any(abs(y - ty) + abs(x - tx) < GROVE_SPACING for ty, tx in taken):
            continue
        if plant_grove(route, room, y, x):
            taken.append((y, x))


def shuffled(route: Route, cells: np.ndarray) -> List[Cell]:
    order = route.rng.permutation(len(cells))
    return [(int(cells[i][0]), int(cells[i][1])) for i in order]


def plant_grove(route: Route, room: np.ndarray, y: int, x: int) -> bool:
    for rows, cols in GROVE_SHAPES:
        cells = [(y + 3 * r, x + 3 * c) for r in range(rows) for c in range(cols)]
        if all(tree_fits(room, ty, tx) for ty, tx in cells):
            for ty, tx in cells:
                route.cls[ty:ty + 2, tx:tx + 2] = "f"
            return True
    return False


def tree_fits(room: np.ndarray, y: int, x: int) -> bool:
    h, w = room.shape
    return y + 1 < h and x + 1 < w and bool(room[y:y + 2, x:x + 2].all())


def reconnect_orphans(route: Route, rim: np.ndarray) -> None:
    starts = route.exits[0][1]
    for _ in range(12):
        seen = reachable(route.cls, starts)
        orphan = np.isin(route.cls, list(WALKABLE)) & ~seen & ~rim
        if not orphan.any():
            return
        carve_to(route, seen, tuple(int(v) for v in np.argwhere(orphan)[0]))


def carve_to(route: Route, seen: np.ndarray, target: Cell) -> None:
    ys, xs = np.nonzero(seen)
    nearest = int(np.argmin((ys - target[0]) ** 2 + (xs - target[1]) ** 2))
    cost = np.where(np.isin(route.cls, ["w", "s", "e", "x"]), np.inf, 1.0)
    for cell in astar(cost, (int(ys[nearest]), int(xs[nearest])), target):
        if route.cls[cell] in ("f", "r", CLIFF):
            route.cls[cell] = "g"


def terrace_steps(route: Route, rim: np.ndarray) -> None:
    from .stages import _tiers

    elevation = _tiers(route)
    drop = np.zeros(route.cls.shape, dtype=bool)
    drop[:-1] = elevation[:-1] > elevation[1:]
    clear = ~binary_dilation(rim, np.ones((7, 7), bool))
    runs = [(y, run) for y in range(1, route.h - 1) for run in step_runs(route, drop[y] & clear[y], y)]
    budget = int(route.rng.integers(0, STEPS_AT_MOST + 1))
    for i in route.rng.permutation(len(runs)):
        if budget == 0:
            return
        budget -= place_step(route, *runs[i])


def step_runs(route: Route, row: np.ndarray, y: int) -> List[List[int]]:
    runs: List[List[int]] = [[]]
    for x in range(route.w):
        if row[x] and steppable(route, y, x):
            runs[-1].append(x)
        elif runs[-1]:
            runs.append([])
    return [run for run in runs if len(run) >= MIN_STEP]


def steppable(route: Route, y: int, x: int) -> bool:
    if route.cls[y, x] not in "gt":
        return False
    return route.cls[y - 1, x] in WALKABLE and route.cls[y + 1, x] in WALKABLE


def place_step(route: Route, y: int, run: List[int]) -> bool:
    """A short grassy step (at most LONGEST_STEP wide) somewhere along the drop."""
    if len(run) > LONGEST_STEP:
        start = int(route.rng.integers(0, len(run) - LONGEST_STEP + 1))
        run = run[start:start + LONGEST_STEP]
    for part in (run, run[:-1], run[1:]):
        if len(part) >= MIN_STEP and try_step(route, y, part):
            route.zones.append(Zone("loop", [(y, x) for x in part], "height step",
                                    {"ledge": (y, part[0], part[-1]), "one_way": "south", "why": "height map"}))
            return True
    return False


def try_step(route: Route, y: int, xs: Sequence[int]) -> bool:
    cls = route.cls
    starts = route.exits[0][1]
    exits = [c for _, cells in route.exits for c in cells]
    before = reachable(cls, starts)
    stuck = int((before & ~can_leave(cls, exits)).sum())
    was = cls[y, list(xs)].copy()
    cls[y, list(xs)] = "s"
    after = reachable(cls, starts)
    lost = before & ~after
    lost[y, list(xs)] = False
    if lost.any() or int((after & ~can_leave(cls, exits)).sum()) > stuck or not two_way(route):
        cls[y, list(xs)] = was
        return False
    return True


def two_way(route: Route) -> bool:
    first = route.exits[0][1][0]
    return all(reachable(route.cls, cells)[first] for _, cells in route.exits[1:])
