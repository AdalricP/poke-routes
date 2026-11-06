"""Height steps you can see on wooded routes. Where the height map drops a tier
toward the south and the lower side is open ground (a lane, a clearing, a
field), the trees along the top of the step give way to a rock face two tiles
tall, so the clearing sits under a little cliff instead of a wall of trees and
the route's terraces read at a glance. Only trees turn to rock, so nothing that
was walkable changes."""
from __future__ import annotations

import numpy as np

from .generate import WALKABLE, Route

BIOMES_WITH_ROCK_FACES_ON_HEIGHT_STEPS = ("forest", "meadow", "riverland", "beach")
TREE_CLASS = "f"
ROCK_FACE_CLASS = "r"
FACE_HEIGHT_IN_METATILES = 2
SHORTEST_ROCK_FACE_IN_METATILES = 3


def show_height_steps_as_rock_faces(route: Route) -> int:
    from .stages import _tiers

    if route.spec.biome not in BIOMES_WITH_ROCK_FACES_ON_HEIGHT_STEPS:
        return 0
    tiers = _tiers(route)
    face_tops = step_tops_over_open_ground(route, tiers)
    face_count = 0
    for row in range(FACE_HEIGHT_IN_METATILES, route.h - 1):
        for run in runs_of_columns(np.nonzero(face_tops[row])[0]):
            if len(run) < SHORTEST_ROCK_FACE_IN_METATILES:
                continue
            face_count += raise_one_face(route, row, run)
    if face_count:
        route.log.append(f"height: {face_count} tree cells along tier steps turned to rock faces over open ground")
    return face_count


def step_tops_over_open_ground(route: Route, tiers: np.ndarray) -> np.ndarray:
    drops_to_the_south = np.zeros(tiers.shape, dtype=bool)
    drops_to_the_south[:-1] = tiers[:-1] > tiers[1:]
    open_below = np.zeros(tiers.shape, dtype=bool)
    open_below[:-1] = np.isin(route.cls[1:], list(WALKABLE))
    face_tops = drops_to_the_south & open_below & (route.cls == TREE_CLASS) & ~route.protect
    return face_tops


def runs_of_columns(columns: np.ndarray):
    if len(columns) == 0:
        return []
    runs = np.split(columns, np.nonzero(np.diff(columns) > 1)[0] + 1)
    return runs


def raise_one_face(route: Route, bottom_row: int, columns) -> int:
    face_cells = [(bottom_row - lift, int(column)) for lift in range(FACE_HEIGHT_IN_METATILES) for column in columns]
    if not all(route.cls[cell] == TREE_CLASS and not route.protect[cell] for cell in face_cells):
        return 0
    for cell in face_cells:
        route.cls[cell] = ROCK_FACE_CLASS
    raised_count = len(face_cells)
    return raised_count
