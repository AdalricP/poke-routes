"""Assign every metatile in a tileset pair a coarse semantic class.

Classes are what the generator paints with; the autotiler then picks the
concrete metatile. Classification uses (in order) a short table of hand-checked
metatiles (stairs, cave mouths), the metatile behaviour byte, how often real
maps mark the metatile as blocked, how isolated it sits among walkable cells,
and its pixel colours.

Class codes (one char each; the first eleven are the original set and keep
their meaning):

  code  name            what it covers
  ----  --------------  ----------------------------------------------------------
  g     grass           short walkable grass
  p     path            walkable dirt / paving. In the General tileset the sandy
                        route path and beach sand are the same metatiles, so a
                        model may hold them under ``d``; FALLBACK maps p <-> d.
  t     tall grass      wild-encounter grass (MB_TALL_GRASS, MB_LONG_GRASS)
  w     pond water      calm inland water (MB_POND_WATER, interior deep water)
  f     tree            blocked green: trees, hedges
  r     rock mass       blocked flat rock: mountain tops nobody walks on
  s/e/x/j ledges        MB_JUMP_SOUTH / MB_JUMP_EAST / MB_JUMP_WEST / MB_JUMP_NORTH
  a     waterfall       MB_WATERFALL
  o     other           buildings, signs, decorations: never learned or painted
  d     sand            loose sand: beaches, desert (MB_SAND, MB_DEEP_SAND, pale
                        yellow walkable ground)
  b     sea             surfable open water (MB_OCEAN_WATER, MB_DEEP_WATER)
  h     shallow water   wade-able water: shoals, puddles (MB_SHALLOW_WATER, MB_PUDDLE)
  y     ash grass       ash-covered grass (MB_ASHGRASS, Route 113)
  v     ash ground      bare volcanic / ash / rocky walkable ground: Route 113 ash
                        field, Jagged Pass soil, walkable mountain tops
  k     cliff           blocked rock wall: cliff faces and rims between tiers
  z     stairs          walkable steps cut through a cliff
  l     lava / hot      hot springs (MB_HOT_SPRINGS) and blocked lava (Mt. Chimney)
  n     cave entrance   the door cell of a cave mouth in a cliff face
  q     bridge          bridge deck over water (MB_BRIDGE_*, Pacifidlog logs)
  u     boulder         small blocked rock standing alone on land or in the sea
  D     dive water      deep sea you can Dive in (MB_DEEP_WATER); open-sea routes only

Emerald reuses some metatiles as both walkable and blocked (the mountain-top
tile is plateau floor in one place and unreachable rock in another), so the
learner classifies each corpus *cell* from its metatile plus its collision bit
(:func:`classify_cell`), not just each metatile.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

import numpy as np

from .tileset import NUM_PRIMARY_METATILES, TilesetPair

GRASS, PATH, TALL, WATER, TREE, ROCK, LEDGE_S, LEDGE_E, LEDGE_W, WATERFALL, OTHER = (
    "g", "p", "t", "w", "f", "r", "s", "e", "x", "a", "o",
)
LEDGE_N = "j"
SNOW, ICE = "m", "i"
FENCE = "c"
DIVE = "D"
SAND, SEA, SHALLOW, ASH_GRASS, ASH, CLIFF, STAIRS, LAVA, CAVE_ENTRANCE, BRIDGE, BOULDER = (
    "d", "b", "h", "y", "v", "k", "z", "l", "n", "q", "u",
)
# friendlier aliases for the generator
POND = WATER
TALL_GRASS = TALL
HOT_SPRINGS = LAVA
CAVE = CAVE_ENTRANCE

CLASS_NAMES = {
    GRASS: "grass", PATH: "path", TALL: "tall grass", WATER: "pond water", TREE: "tree",
    ROCK: "rock mass", LEDGE_S: "ledge (south)", LEDGE_E: "ledge (east)", LEDGE_W: "ledge (west)",
    LEDGE_N: "ledge (north)", SNOW: "snow", ICE: "ice",
    WATERFALL: "waterfall", OTHER: "other",
    SAND: "sand", SEA: "sea", SHALLOW: "shallow water", ASH_GRASS: "ash grass", ASH: "ash ground",
    CLIFF: "cliff", STAIRS: "stairs", LAVA: "lava / hot springs", CAVE_ENTRANCE: "cave entrance",
    BRIDGE: "bridge", BOULDER: "boulder", FENCE: "fence", DIVE: "dive water",
}
CLASS_COLORS = {
    GRASS: (120, 190, 90), PATH: (214, 180, 120), TALL: (40, 130, 50), WATER: (70, 120, 220),
    TREE: (20, 70, 30), ROCK: (130, 90, 60), LEDGE_S: (230, 120, 40), LEDGE_E: (230, 160, 40),
    LEDGE_W: (230, 160, 40), LEDGE_N: (230, 190, 60), WATERFALL: (160, 210, 255), OTHER: (200, 0, 200),
    SAND: (240, 220, 140), SEA: (30, 60, 170), SHALLOW: (130, 190, 230), ASH_GRASS: (150, 150, 110),
    ASH: (190, 185, 160), CLIFF: (90, 50, 30), STAIRS: (230, 230, 230), LAVA: (240, 60, 0),
    CAVE_ENTRANCE: (20, 20, 20), BRIDGE: (170, 120, 60), BOULDER: (110, 100, 90), FENCE: (150, 105, 70),
    SNOW: (236, 240, 246), ICE: (170, 215, 240), DIVE: (14, 30, 110),
}
WALKABLE_CLASSES = frozenset("gptdhyvzqnci")  # shallow water is wade-able
SURFABLE_CLASSES = frozenset("wbD")
BLOCKED_CLASSES = frozenset("frkulc")  # hot springs ('l' walkable in towns) are rare; lava blocks
LEDGE_CLASSES = frozenset({LEDGE_S, LEDGE_E, LEDGE_W, LEDGE_N})
ONE_WAY_LEDGE_STEPS = {LEDGE_S: (1, 0), LEDGE_N: (-1, 0)}


def defuse_ledge_corners(cls):
    """Real maps never sit a south ledge against an east or west one: each brink
    metatile only has art for a straight run over plain ground, so a corner
    between two directions has no matching tile and looks broken. Where a hand
    edit put different-facing ledges side by side, drop the touching cell back
    to its ground so they don't try to join. Only for tilesets without corner
    pieces: the Ark rule tiler joins them (``ruletile.ArkTiler.joins_ledge_corners``)."""
    from collections import Counter

    out = cls.copy()
    h, w = cls.shape
    for y in range(h):
        for x in range(w):
            c = cls[y, x]
            if c not in LEDGE_CLASSES:
                continue
            neighbours = [cls[y, x - 1] if x > 0 else None, cls[y, x + 1] if x < w - 1 else None,
                          cls[y - 1, x] if y > 0 else None, cls[y + 1, x] if y < h - 1 else None]
            if any(n is not None and n in LEDGE_CLASSES and n != c for n in neighbours):
                ground = [n for n in neighbours if n is not None and n not in LEDGE_CLASSES]
                out[y, x] = Counter(ground).most_common(1)[0][0] if ground else GRASS
    return out

WATER_LIKE_CLASSES = frozenset("wbhaqD")  # pond, sea, shallow, waterfall, bridge: a fall can meet any of these

# When a model has (almost) no examples of a class, the autotiler paints the
# next class in this chain instead, so any biome's grid can be tiled with any model.
FALLBACK = {
    SAND: PATH, PATH: SAND, ASH: PATH, ASH_GRASS: TALL, TALL: ASH_GRASS,
    SEA: WATER, WATER: SEA, SHALLOW: WATER, BRIDGE: PATH, STAIRS: PATH,
    CLIFF: ROCK, ROCK: CLIFF, BOULDER: ROCK, LAVA: ROCK, CAVE_ENTRANCE: CLIFF,
    GRASS: ASH, WATERFALL: WATER, TREE: CLIFF, FENCE: OTHER,
    SNOW: ASH, ICE: SHALLOW, DIVE: SEA,
    LEDGE_N: GRASS,  # Emerald has no north-ledge art: the lip just reads as grass (a plains verge's south edge)
}


def seal_waterfalls(cls):
    """A waterfall (``a``) only tiles cleanly in Emerald's art when it pours
    straight into, or straight out of, open water: close a single stray row
    (grass, path, ...) separating a fall from the pond or river it belongs to,
    north or south, so hand-painted and generated falls both read as connected.
    """
    out = np.array(cls, copy=True)
    h = out.shape[0]
    for y, x in zip(*np.nonzero(out == WATERFALL)):
        for dy in (-1, 1):
            ny, gy = y + dy, y + 2 * dy
            if not (0 <= ny < h) or out[ny, x] in WATER_LIKE_CLASSES:
                continue
            if 0 <= gy < h and out[gy, x] in WATER_LIKE_CLASSES:
                out[ny, x] = out[gy, x] if out[gy, x] != WATERFALL else WATER
    return out


_BEHAVIOR_CLASS = {
    0x02: TALL, 0x03: TALL, 0x09: TALL,
    0x10: WATER, 0x11: WATER, 0x14: WATER,
    0x12: SEA, 0x15: SEA,
    0x16: SHALLOW, 0x17: SHALLOW,
    0x13: WATERFALL,
    0x06: SAND, 0x21: SAND, 0x25: SAND,
    0x24: ASH_GRASS,
    0x28: LAVA,
    0x3B: LEDGE_S, 0x38: LEDGE_E, 0x39: LEDGE_W, 0x3A: LEDGE_N,
    0x70: BRIDGE, 0x71: BRIDGE, 0x72: BRIDGE, 0x73: BRIDGE, 0x78: BRIDGE,
    0x7A: BRIDGE, 0x7B: BRIDGE, 0x7C: BRIDGE, 0x7D: BRIDGE,
    0x74: BRIDGE, 0x75: BRIDGE, 0x76: BRIDGE, 0x77: BRIDGE,  # Pacifidlog floating logs
}
_MOUNTAIN_TOP, _CAVE_FLOOR = 0x0C, 0x08

# Hand-checked metatiles whose behaviour byte says nothing useful. Names follow
# include/constants/metatile_labels.h where it has one.
OVERRIDES: Dict[Tuple[str, int], str] = {
    ("general", 0x0AF): STAIRS, ("general", 0x0CF): STAIRS,  # stone steps (Route 112 foot)
    ("lavaridge", 0x2AF): STAIRS,                            # log steps (Jagged Pass)
    ("general", 0x0A7): CAVE_ENTRANCE, ("general", 0x09F): CLIFF,      # General_CaveEntrance_*
    ("lavaridge", 0x25E): CAVE_ENTRANCE, ("lavaridge", 0x256): CLIFF,  # Lavaridge_CaveEntrance_*
    ("fallarbor", 0x261): CAVE_ENTRANCE, ("fallarbor", 0x259): CLIFF,  # Fallarbor_BrownCaveEntrance_*
    ("fallarbor", 0x34F): CAVE_ENTRANCE, ("fallarbor", 0x347): CLIFF,  # Fallarbor_RedCaveEntrance_*
}


def _colour_shares(rgb: np.ndarray):
    r, g, b = (rgb[..., i].astype(int) for i in range(3))
    green = (g > r + 8) & (g >= b)
    tan = (r > g) & (g > b) & (r > 150)
    brown = (r > g) & (g >= b) & (r <= 150) & (r > 60)
    blue = (b > r + 30) & (b > g)
    n = rgb.shape[0] * rgb.shape[1]
    return green.sum() / n, tan.sum() / n, brown.sum() / n, blue.sum() / n


def _share(mask: np.ndarray) -> float:
    return float(mask.sum()) / mask.size


def _ash_share(rgb: np.ndarray) -> float:
    """Grey-olive volcanic ash (Route 113): red ~ green, blue a little lower."""
    r, g, b = (rgb[..., i].astype(int) for i in range(3))
    return _share((abs(r - g) <= 12) & (r - b >= 10) & (r - b <= 70) & (r > 90))


def _sand_share(rgb: np.ndarray) -> float:
    """Pale yellow sand: bright, red and green close, blue well below."""
    r, g, b = (rgb[..., i].astype(int) for i in range(3))
    return _share((r > 185) & (g > 165) & (r - g < 32) & (g - b > 40))


def _ash_tree_share(rgb: np.ndarray) -> float:
    """Pink-grey ash on Jagged Pass trees: red well above green, green ~ blue."""
    r, g, b = (rgb[..., i].astype(int) for i in range(3))
    return _share((r - g >= 20) & (abs(g - b) <= 8) & (r > 100))


def _lava_share(rgb: np.ndarray) -> float:
    """Saturated lava red/orange (Mt. Chimney)."""
    r, g, b = (rgb[..., i].astype(int) for i in range(3))
    return _share((r > 180) & (g < 90) & (b < 40))


def _tileset_of(pair: TilesetPair, mid: int) -> str:
    return pair.primary.name if mid < NUM_PRIMARY_METATILES else pair.secondary.name


def classify_metatile(pair: TilesetPair, mid: int, blocked_ratio: Optional[float],
                      isolation: Optional[float] = None) -> str:
    """Class of metatile ``mid``.

    ``blocked_ratio``: share of corpus cells using it that are blocked (None if unused).
    ``isolation``: share of its corpus 4-neighbours that are walkable or water (None if
    unknown); a blocked rock mostly surrounded by open cells is a boulder, not a cliff.
    """
    o = OVERRIDES.get((_tileset_of(pair, mid), mid))
    if o:
        return o
    beh = pair.behavior(mid)
    if beh in _BEHAVIOR_CLASS:
        return _BEHAVIOR_CLASS[beh]
    if 0x50 <= beh <= 0x53:  # sea currents: too special to paint by accident
        return OTHER
    rgb = pair.render_metatile(mid)
    green, tan, brown, blue = _colour_shares(rgb)
    blocked = blocked_ratio is not None and blocked_ratio > 0.5
    if beh in (_MOUNTAIN_TOP, _CAVE_FLOOR):
        if blocked_ratio is None:
            return ROCK if beh == _MOUNTAIN_TOP else OTHER
        if not blocked:
            return ASH
        if green > 0.45:
            return TREE
        return ROCK if rgb.mean() > 150 else CLIFF
    if beh != 0:
        return OTHER
    if blue > 0.6:
        if blocked:
            return BOULDER  # rocks and rough patches standing in the sea
        # real sea carries an ocean behaviour; plain blue walkable tiles are
        # beach furniture (deck chairs) unless they are solid water colour
        return SEA if blue > 0.95 else OTHER
    if blocked_ratio is None:
        return OTHER
    ash, sand = _ash_share(rgb), _sand_share(rgb)
    if not blocked:
        if sand > 0.6:
            return SAND
        if ash > 0.5 and green < 0.2:
            return ASH
        if tan > 0.45:
            return PATH
        if green > 0.35:
            return GRASS
        return OTHER
    if green > 0.33:
        return TREE  # includes Route 113's olive ash-dusted trees
    if _ash_tree_share(rgb) > 0.35 and brown < 0.15:
        return TREE  # Jagged Pass pink-grey ash trees
    rocky = brown + tan + ash
    if _lava_share(rgb) > 0.5:
        return LAVA
    if rocky > 0.3 and isolation is not None and isolation >= 0.55:
        return BOULDER
    if 0.3 <= rocky < 0.45 or (rocky >= 0.15 and blue > 0.25):
        return BOULDER  # pieces of big rocks with sand / sea showing round them
    if rocky > 0.45:
        # light flat rock tops are rock mass; darker shaded walls are cliff faces
        return ROCK if rgb.mean() > 150 else CLIFF
    return OTHER


def classify_cell(pair: TilesetPair, mid: int, collision: int, isolation: Optional[float] = None) -> str:
    """Class of one placed metatile given that cell's collision bit (0/1)."""
    return classify_metatile(pair, mid, 1.0 if collision else 0.0, isolation)


def classify_pair(pair: TilesetPair, blocked: Dict[int, float],
                  isolation: Optional[Dict[int, float]] = None) -> Dict[int, str]:
    isolation = isolation or {}
    p, s = pair.metatile_count()
    ids = list(range(p)) + [NUM_PRIMARY_METATILES + i for i in range(s)]
    return {mid: classify_metatile(pair, mid, blocked.get(mid), isolation.get(mid)) for mid in ids}
