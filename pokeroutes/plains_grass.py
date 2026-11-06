"""Plains tall grass: soft patches grown over the open field, plus a few long strips
along the road. Replaces the scattered plots and lane speckles the earlier stages
left, so the field reads as meadow with clumps of long grass, not noise."""
from __future__ import annotations

import heapq
from typing import List, Optional, Tuple

import numpy as np
from scipy.ndimage import binary_dilation, label

from .noise import fbm

Cell = Tuple[int, int]

TALL, GRASS, ROAD = "t", "g", "p"
SHARE_OF_OPEN_GRASS = 0.27  # times spec.tall_grass: how much of the open field grows tall
PATCH_SIZES = (8, 36)
SMALLEST_PATCH = 6
LARGEST_PATCH = 40
PATCH_GAP = 2  # plain grass between two patches, so they never merge into a prairie
STRIP_LENGTHS = (7, 15)
STRIPS_PER_THOUSAND_CELLS = 0.6
ROAD_RUN = 8
NOISE_PULL = 5.0
JITTER = 1.6
SEED_TRIES = 400
DIRS4 = ((1, 0), (-1, 0), (0, 1), (0, -1))


def grow_tall_grass(route, rim: np.ndarray) -> None:
    """Clear the earlier tall grass, then grow new patches and road strips."""
    clear_old_tall_grass(route)
    room = grass_room(route, rim)
    target = int(room.sum() * float(route.spec.tall_grass) * SHARE_OF_OPEN_GRASS)
    if target <= 0:
        return
    tall = np.zeros(room.shape, dtype=bool)
    for strip in road_strips(route, room):
        if tall.sum() >= target // 3:
            break
        if fits(strip, room, tall):
            mark(tall, strip)
    grow_patches(route, room, tall, target)
    route.cls[tall] = TALL


def clear_old_tall_grass(route) -> None:
    """Tall grass goes back to what was under it before the tall-grass stage."""
    before = next((grid for name, grid in route.snapshots if name == "road"), None)
    old = route.cls == TALL
    if before is not None:
        route.cls[old & (before == ROAD)] = ROAD
    route.cls[route.cls == TALL] = GRASS


def grass_room(route, rim: np.ndarray) -> np.ndarray:
    """Open grass tall grass may cover: not under people, items, props or signs, not
    in the exits' mouths, not on the verge."""
    room = (route.cls == GRASS) & ~rim
    busy = np.zeros(room.shape, dtype=bool)
    for event in route.events:
        busy[event.y, event.x] = True
    for prop in route.props:
        if "y" in prop and "x" in prop:
            busy[int(prop["y"]), int(prop["x"])] = True
    for _, cells in route.exits:
        for cell in cells:
            busy[cell] = True
    busy = binary_dilation(busy, np.ones((3, 3), bool))
    for _, cells in route.exits:
        mouth = np.zeros(room.shape, dtype=bool)
        for cell in cells:
            mouth[cell] = True
        busy |= binary_dilation(mouth, np.ones((9, 9), bool))
    return room & ~busy


def fits(cells: List[Cell], room: np.ndarray, tall: np.ndarray) -> bool:
    if not all(room[c] for c in cells):
        return False
    near = binary_dilation(tall, np.ones((2 * PATCH_GAP + 1,) * 2, bool))
    return not any(near[c] for c in cells)


def mark(tall: np.ndarray, cells: List[Cell]) -> None:
    for c in cells:
        tall[c] = True


def road_strips(route, room: np.ndarray) -> List[List[Cell]]:
    """Candidate strips of tall grass one or two cells off the road's edge, running
    along a straight stretch of it; shuffled, a few per map."""
    road = route.cls == ROAD
    h, w = road.shape
    rng = route.rng
    out: List[List[Cell]] = []
    for horizontal in (True, False):
        lines = range(h) if horizontal else range(w)
        for line in lines:
            for side in (-1, 1):
                out += strips_beside(road, room, line, side, horizontal, rng)
    order = rng.permutation(len(out))
    want = max(1, int(round(route.cls.size / 1000 * STRIPS_PER_THOUSAND_CELLS)))
    picked: List[List[Cell]] = []
    for i in order:
        picked.append(out[int(i)])
        if len(picked) >= want * 6:
            break
    return picked


def strips_beside(road, room, line: int, side: int, horizontal: bool, rng) -> List[List[Cell]]:
    """Strips in the row (or column) just beside the road's edge on one side."""
    h, w = road.shape
    length = w if horizontal else h
    out: List[List[Cell]] = []
    run: List[Cell] = []
    for i in range(length + 1):
        cell = (line, i) if horizontal else (i, line)
        edge = (line - side, i) if horizontal else (i, line - side)
        ok = i < length and in_grid(cell, h, w) and in_grid(edge, h, w) and road[edge] and room[cell]
        if ok:
            run.append(cell)
            continue
        if len(run) >= ROAD_RUN:
            n = int(rng.integers(*STRIP_LENGTHS))
            n = min(n, len(run))
            start = int(rng.integers(0, len(run) - n + 1))
            strip = run[start:start + n]
            out.append(strip + widened(strip, side, horizontal, room, rng))
        run = []
    return out


def widened(strip: List[Cell], side: int, horizontal: bool, room: np.ndarray, rng) -> List[Cell]:
    """A second row behind the middle of the strip, so its ends taper."""
    if len(strip) < 6 or rng.random() < 0.4:
        return []
    inner = strip[2:-2]
    out = [((y + side, x) if horizontal else (y, x + side)) for y, x in inner]
    h, w = room.shape
    return [c for c in out if in_grid(c, h, w) and room[c]]


def in_grid(cell: Cell, h: int, w: int) -> bool:
    return 0 <= cell[0] < h and 0 <= cell[1] < w


def grow_patches(route, room: np.ndarray, tall: np.ndarray, target: int) -> None:
    rng = route.rng
    h, w = room.shape
    noise = fbm(h, w, 3.0, rng, octaves=2)
    noise = (noise - noise.min()) / max(1e-6, float(noise.max() - noise.min()))
    for _ in range(SEED_TRIES):
        if tall.sum() >= target:
            return
        free = room & ~binary_dilation(tall, np.ones((2 * PATCH_GAP + 3,) * 2, bool))
        seeds = np.argwhere(free)
        if not len(seeds):
            return
        # favour the noise's high ground, so patches gather where the field is lush
        weights = noise[free] ** 2 + 0.05
        pick = seeds[int(rng.choice(len(seeds), p=weights / weights.sum()))]
        size = int(rng.integers(*PATCH_SIZES))
        patch = grown_patch(room & ~binary_dilation(tall, np.ones((2 * PATCH_GAP + 1,) * 2, bool)),
                            (int(pick[0]), int(pick[1])), size, noise, rng)
        if patch is not None:
            tall |= patch


def grown_patch(room: np.ndarray, seed: Cell, size: int, noise: np.ndarray, rng) -> Optional[np.ndarray]:
    """Grow a blob from ``seed`` over ``room``: the nearest cells first, pulled out of
    round by the noise, then smoothed so it has no one-cell spurs or pinholes."""
    h, w = room.shape
    patch = np.zeros((h, w), dtype=bool)
    heap = [(0.0, seed)]
    queued = {seed}
    while heap and patch.sum() < size:
        _, (y, x) = heapq.heappop(heap)
        patch[y, x] = True
        for dy, dx in DIRS4:
            c = (y + dy, x + dx)
            if in_grid(c, h, w) and room[c] and c not in queued:
                queued.add(c)
                dist = ((c[0] - seed[0]) ** 2 + (c[1] - seed[1]) ** 2) ** 0.5
                heapq.heappush(heap, (dist - NOISE_PULL * noise[c] + rng.random() * JITTER, c))
    patch = smoothed(patch, room)
    biggest = largest_part(patch)
    if biggest is None or not SMALLEST_PATCH <= biggest.sum() <= LARGEST_PATCH:
        return None
    return biggest


def smoothed(patch: np.ndarray, room: np.ndarray) -> np.ndarray:
    for _ in range(3):
        n = neighbours(patch)
        patch = (patch & (n >= 2)) | (room & ~patch & (n >= 3))
    return patch


def neighbours(mask: np.ndarray) -> np.ndarray:
    m = mask.astype(np.int8)
    n = np.zeros(m.shape, dtype=np.int8)
    n[1:] += m[:-1]
    n[:-1] += m[1:]
    n[:, 1:] += m[:, :-1]
    n[:, :-1] += m[:, 1:]
    return n


def largest_part(patch: np.ndarray) -> Optional[np.ndarray]:
    parts, count = label(patch)
    if count == 0:
        return None
    sizes = np.bincount(parts.ravel())[1:]
    return parts == (int(np.argmax(sizes)) + 1)
