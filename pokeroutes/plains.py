"""Plains: an open field held in by a raised rim at the map's edge, faced with ledges."""
from __future__ import annotations

from typing import List, Sequence

import numpy as np
from scipy.ndimage import binary_dilation, distance_transform_cdt, label

from .generate import WALKABLE, Cell, Route, Zone, astar, can_leave, inward, reachable

SEAL_DEPTH = 2
RIM_DEPTHS = (3, 4, 4, 5, 5, 6)
RIM_RUN = (7, 15)
GROVE_SPACING = 18
MIN_STEP = 5
CELLS_PER_STEP = 700
CLIFF = "k"
NOTCH_MARGIN = 1
GROVE_SHAPES = ((2, 3), (3, 2), (2, 2), (1, 3), (1, 2), (1, 1))


def stage_open_plains(route: Route) -> None:
    """Open the plains: the hill mass between lanes becomes open grass with a few
    groves, the map's edge rises into a plateau whose rim is a ledge line, and the
    height map's steps run on as terrace ledges you can always walk round."""
    if route.spec.biome != "plains":
        return
    built = route.cls.copy()
    keep = kept_cells(route)
    rim = rim_mask(route, keep)
    hedge_secrets(route, built)
    open_hill_mass(route, built)
    drop_stray_ledges(route, rim)
    raise_rim(route, rim)
    plant_groves(route, built, rim)
    reconnect_orphans(route, rim)
    terrace_steps(route, rim)
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
    return without_slivers(widened_notches(clipped))


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
    step = int(rng.choice((-2, -1, 1, 2)))
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
    for y, x in np.argwhere(np.isin(route.cls, ["s", "e", "x"]) & ~rim):
        route.cls[y, x] = ground_below(route, int(y), int(x))
    route.zones = [z for z in route.zones if "ledge" not in z.meta]


def ground_below(route: Route, y: int, x: int) -> str:
    below = route.cls[y + 1, x] if y + 1 < route.h else "g"
    return below if below in "gtp" else "g"


def raise_rim(route: Route, rim: np.ndarray) -> None:
    h, w = rim.shape
    cls = route.cls
    seal = edge_ring(h, w) & rim
    cls[seal] = CLIFF
    for y, x in zip(*np.nonzero(rim & ~seal)):
        cls[y, x] = rim_piece(rim, y, x)
    south_band = np.zeros_like(rim)
    south_band[h - max(RIM_DEPTHS):, :] = True
    cls[rim & south_band & ~np.isin(cls, ["e", "x"])] = CLIFF


def edge_ring(h: int, w: int) -> np.ndarray:
    ring = np.ones((h, w), dtype=bool)
    ring[SEAL_DEPTH:-SEAL_DEPTH, SEAL_DEPTH:-SEAL_DEPTH] = False
    return ring


def rim_piece(rim: np.ndarray, y: int, x: int) -> str:
    h, w = rim.shape

    def low(dy: int, dx: int) -> bool:
        ny, nx = y + dy, x + dx
        return 0 <= ny < h and 0 <= nx < w and not rim[ny, nx]

    if low(-1, 0) and not low(1, 0):
        return CLIFF
    if low(1, 0):
        return "s"
    if low(0, 1):
        return "e"
    if low(0, -1):
        return "x"
    if low(1, -1) or low(1, 1):
        return "s"
    return CLIFF


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
    budget = max(2, route.cls.size // CELLS_PER_STEP)
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
    if route.cls[y, x] not in "gtp":
        return False
    return route.cls[y - 1, x] in WALKABLE and route.cls[y + 1, x] in WALKABLE


def place_step(route: Route, y: int, run: List[int]) -> bool:
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
