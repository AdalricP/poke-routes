from __future__ import annotations

from typing import List, Tuple

import numpy as np
from scipy.ndimage import binary_dilation, label

from .noise import stretched_fbm
from .scenery_layer import RAISED_GROUND_CLASS
from .woodland_closeness import woodland_closeness

TREE_CLASS = "f"
OPEN_GROUND_CLASS = "g"
BIOMES_WITH_OPEN_LAND = ("plains", "meadow", "riverland", "beach", "forest")
WOODLAND_CLOSENESS_THAT_KEEPS_TREES_BY_BIOME = {"plains": 0.7, "meadow": 0.6, "riverland": 0.45, "beach": 0.6, "forest": 0.38}
CLOSENESS_NOISE_SPREAD = 0.3
EDGE_RISE_DEPTHS_IN_CELLS = (3, 4, 5)
EDGE_RISE_NOISE_SPREAD_IN_CELLS = 1.5
SMALLEST_OPENED_MASS_THAT_CAN_BE_A_PLATEAU_IN_CELLS = 16
LARGEST_OPENED_MASS_THAT_CAN_BE_A_PLATEAU_IN_CELLS = 70
MOST_PLATEAUS_PER_MAP = 2
PLATEAU_CHANCE = 0.5
KEEP_TREES_AROUND_SECRETS_IN_CELLS = 3
EVENT_KINDS_THAT_NEED_TREES_AROUND = ("cut_tree", "rock_smash", "strength_boulder", "npc")
ZONE_KINDS_THAT_NEED_TREES_AROUND = ("hidden", "trail", "special")

Cell = Tuple[int, int]


def open_land_by_woodland(route) -> None:
    if route.spec.biome not in BIOMES_WITH_OPEN_LAND:
        return
    from .stepped_terraces import seam_band_cells

    frozen_cells = seam_band_cells(route)
    opened_blocks = tree_blocks_to_open(route, frozen_cells)
    if not opened_blocks.any():
        return
    raised = raised_part_of_opened_land(route, opened_blocks)
    route.cls[opened_blocks & ~raised] = OPEN_GROUND_CLASS
    route.cls[opened_blocks & raised] = RAISED_GROUND_CLASS
    route.cls[edge_ring_to_raise(route, frozen_cells)] = RAISED_GROUND_CLASS
    raise_opened_pockets_nobody_reaches(route, opened_blocks)
    route.snap("open land")


def normalised_noise(route, scale_in_cells: float) -> np.ndarray:
    normalised = stretched_fbm(route.h, route.w, scale_in_cells, route.rng, octaves=3)
    return normalised


def cells_that_need_their_trees(route, frozen_cells: np.ndarray) -> np.ndarray:
    needs_trees = np.zeros(route.cls.shape, dtype=bool)
    for zone_cell in route.zone_cells(*ZONE_KINDS_THAT_NEED_TREES_AROUND):
        needs_trees[zone_cell] = True
    for event in route.events:
        if event.kind in EVENT_KINDS_THAT_NEED_TREES_AROUND:
            needs_trees[event.y, event.x] = True
    for false_wall in route.__dict__.get("false_walls") or []:
        for wall_cell in false_wall:
            needs_trees[tuple(wall_cell)] = True
    for gate in route.gates:
        for gate_cell in gate.get("cells") or []:
            needs_trees[int(gate_cell[0]), int(gate_cell[1])] = True
    reach = 2 * KEEP_TREES_AROUND_SECRETS_IN_CELLS + 1
    near_secrets = binary_dilation(needs_trees, np.ones((reach, reach), dtype=bool))
    keep_trees = near_secrets | frozen_cells
    return keep_trees


def tree_blocks_to_open(route, frozen_cells: np.ndarray) -> np.ndarray:
    closeness = woodland_closeness(route)
    noise = (normalised_noise(route, 6.0) - 0.5) * 2 * CLOSENESS_NOISE_SPREAD
    threshold = WOODLAND_CLOSENESS_THAT_KEEPS_TREES_BY_BIOME[route.spec.biome]
    wants_open = (closeness + noise < threshold) & (route.cls == TREE_CLASS) & ~cells_that_need_their_trees(route, frozen_cells)
    opened = np.zeros(route.cls.shape, dtype=bool)
    for block_y in range(0, route.h, 2):
        for block_x in range(0, route.w, 2):
            block = (slice(block_y, block_y + 2), slice(block_x, block_x + 2))
            is_trees = route.cls[block] == TREE_CLASS
            if is_trees.any() and wants_open[block][is_trees].mean() >= 0.5:
                opened[block] = is_trees
    return opened


def distance_to_map_edge(route) -> np.ndarray:
    rows = np.arange(route.h)[:, None]
    columns = np.arange(route.w)[None, :]
    distance = np.minimum(np.minimum(rows, route.h - 1 - rows), np.minimum(columns, route.w - 1 - columns))
    return distance


def edge_band_of_opened_land(route) -> np.ndarray:
    edge_depth = EDGE_RISE_DEPTHS_IN_CELLS[int(route.rng.integers(0, len(EDGE_RISE_DEPTHS_IN_CELLS)))]
    noise = (normalised_noise(route, 8.0) - 0.5) * 2 * EDGE_RISE_NOISE_SPREAD_IN_CELLS
    in_edge_band = distance_to_map_edge(route) + noise < edge_depth
    return in_edge_band


def whole_blocks_of(mask: np.ndarray) -> np.ndarray:
    whole_blocks = np.zeros(mask.shape, dtype=bool)
    for block_y in range(0, mask.shape[0], 2):
        for block_x in range(0, mask.shape[1], 2):
            block = (slice(block_y, block_y + 2), slice(block_x, block_x + 2))
            whole_blocks[block] = mask[block].mean() >= 0.5
    return whole_blocks


def raised_part_of_opened_land(route, opened_blocks: np.ndarray) -> np.ndarray:
    from .export import SEAL_DEPTH

    raised = opened_blocks & whole_blocks_of(edge_band_of_opened_land(route) | (distance_to_map_edge(route) <= SEAL_DEPTH))
    pieces, piece_count = label(opened_blocks & ~raised)
    plateau_count = 0
    for piece_index in route.rng.permutation(np.arange(1, piece_count + 1)):
        piece = pieces == piece_index
        piece_size = int(piece.sum())
        is_plateau_sized = SMALLEST_OPENED_MASS_THAT_CAN_BE_A_PLATEAU_IN_CELLS <= piece_size <= LARGEST_OPENED_MASS_THAT_CAN_BE_A_PLATEAU_IN_CELLS
        if plateau_count < MOST_PLATEAUS_PER_MAP and is_plateau_sized and route.rng.random() < PLATEAU_CHANCE:
            raised |= piece
            plateau_count += 1
    return raised


def edge_ring_to_raise(route, frozen_cells: np.ndarray) -> np.ndarray:
    from .export import SEAL_DEPTH

    is_edge_ring = distance_to_map_edge(route) < SEAL_DEPTH
    closeness = woodland_closeness(route)
    threshold = WOODLAND_CLOSENESS_THAT_KEEPS_TREES_BY_BIOME[route.spec.biome]
    raise_ring = is_edge_ring & ~frozen_cells & (route.cls == TREE_CLASS) & (closeness < threshold)
    return raise_ring


def raise_opened_pockets_nobody_reaches(route, opened_blocks: np.ndarray) -> None:
    from .generate import WALKABLE

    is_walkable = np.isin(route.cls, list(WALKABLE))
    pieces, _piece_count = label(is_walkable)
    pieces_holding_old_ground = np.unique(pieces[is_walkable & ~opened_blocks])
    joins_old_ground = np.isin(pieces, pieces_holding_old_ground[pieces_holding_old_ground > 0])
    unreached_open_ground = opened_blocks & (route.cls == OPEN_GROUND_CLASS) & ~joins_old_ground
    route.cls[unreached_open_ground] = RAISED_GROUND_CLASS
