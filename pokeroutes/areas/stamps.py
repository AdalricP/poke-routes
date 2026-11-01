"""The stamp vocabulary recipes are written in.

A recipe's ``compose`` is a list of ``[stamp, dy, dx, {params}]``: each stamp
paints classes (and sometimes pins real metatiles from :mod:`tiles`) around a
point given relative to the area's centre. Stamps are small and literal, so a
recipe reads like a description of the place: a clearing, a rock rise, a
great tree in the middle, a ring of bare ground, boulders, a reed bed.

Class codes are the generator's (see pokeroutes/classify.py):
g grass, t tall grass, p path, f tree, w pond water, r rock mass, s ledge,
d sand, b sea, h shallow water, y ash grass, v ash ground, k cliff,
z stairs, l lava, n cave entrance, u boulder.
"""
from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Set, Tuple

import numpy as np

from . import tiles

Cell = Tuple[int, int]
BLOCKING = set("frwkulbano")
_MULTI_IDS = {m for n, pc in tiles.PIECES.items() for row in pc["mt"] for m in row
              if m is not None and sum(len(r) for r in pc["mt"]) > 1}
WALK = set("gptdhyvzqn")


class Painter:
    """Paints one area into a class grid. ``cls`` is edited in place."""

    def __init__(self, cls: np.ndarray, rng: np.random.Generator, origin: Cell, size: Tuple[int, int],
                 keep_walkable: Optional[Set[Cell]] = None, tileset: Tuple[str, str] = ("general", "fortree")):
        self.cls = cls
        self.rng = rng
        self.y0, self.x0 = origin
        self.w, self.h = size
        self.cy, self.cx = self.y0 + self.h // 2, self.x0 + self.w // 2
        self.keep = keep_walkable or set()
        self.tileset = tileset
        self.fixed: Dict[Cell, int] = {}
        self.piece_at: Dict[Cell, tuple] = {}  # (piece name, dy, dx) for every pinned cell
        self.custom: Dict[Cell, int] = {}    # ark_special metatile ids (see art.py), drawn over the stock tiles
        self.collision: Dict[Cell, int] = {}
        self.props: List[dict] = []
        self.anchors: Dict[str, Cell] = {"centre": (self.cy, self.cx)}
        self.open: Set[Cell] = set()      # cells that belong to the area's ground
        self.solid: Set[Cell] = set()     # blocking cells the area placed (rocks, trees, pens)
        self.objects: List[dict] = []     # object-event props (breakable rocks, boulders, item balls)
        self.signs: List[dict] = []
        self.doors: List[Tuple[Cell, str]] = []  # (cell, piece) for sub-map entrances

    # -- geometry ----------------------------------------------------------
    @property
    def H(self) -> int:
        return self.cls.shape[0]

    @property
    def W(self) -> int:
        return self.cls.shape[1]

    def at(self, dy: float, dx: float) -> Cell:
        return int(round(self.cy + dy)), int(round(self.cx + dx))

    def inside(self, y: int, x: int, margin: int = 1) -> bool:
        return margin <= y < self.H - margin and margin <= x < self.W - margin

    def in_box(self, y: int, x: int) -> bool:
        return self.y0 <= y < self.y0 + self.h and self.x0 <= x < self.x0 + self.w

    # -- painting ----------------------------------------------------------
    def paint(self, y: int, x: int, c: str) -> bool:
        if not self.inside(y, x):
            return False
        if (y, x) in self.keep and c in BLOCKING:
            return False
        self.cls[y, x] = c
        self.piece_at.pop((y, x), None)
        self.fixed.pop((y, x), None)
        self.custom.pop((y, x), None)
        self.collision.pop((y, x), None)
        if c in WALK:
            self.open.add((y, x))
            self.solid.discard((y, x))
        else:
            self.solid.add((y, x))
            self.open.discard((y, x))
        return True

    def pin(self, name: str, y: int, x: int, anchor: Optional[str] = None) -> Optional[Cell]:
        """Place a tiles.PIECES piece with its top-left at (y, x). Returns its door cell if any."""
        piece = tiles.resolve(name, *self.tileset)
        if piece is None:
            return None
        cells = tiles.cells(piece)
        multi = len(cells) > 1
        for dy, dx, mid, c in cells:
            if not self.inside(y + dy, x + dx) or ((y + dy, x + dx) in self.keep and c in BLOCKING):
                return None
            # never cut into another multi-cell piece (it would break its art)
            if (y + dy, x + dx) in self.custom or (multi and (y + dy, x + dx) in self.fixed) or \
                    ((y + dy, x + dx) in self.fixed and self.fixed[(y + dy, x + dx)] in _MULTI_IDS):
                return None
        for dy, dx, mid, c in cells:
            self.paint(y + dy, x + dx, c)
            if mid is not None:
                self.fixed[(y + dy, x + dx)] = mid
                self.piece_at[(y + dy, x + dx)] = (piece, dy, dx)
        door = None
        if piece in tiles.DOORS:
            ddy, ddx = tiles.DOORS[piece]
            door = (y + ddy, x + ddx)
            self.doors.append((door, piece))
        if anchor:
            self.anchors[anchor] = door or (y, x)
        return door

    def pin_custom(self, piece: str, y: int, x: int, solid: str = "f", floor: str = "g",
                   anchor: Optional[str] = None) -> bool:
        """Place an ark_special piece (art.py) with its top-left at (y, x). Cells get class
        ``solid`` where the piece blocks and ``floor`` where it doesn't, so the stock
        autotiler (and a ROM without the custom tiles) still sees a tree / rock there."""
        from . import art

        data = art.load()
        grid = data["pieces"].get(piece)
        if grid is None:
            return False

        def fits(yy, xx):
            for dy, row in enumerate(grid):
                for dx, mid in enumerate(row):
                    if not self.inside(yy + dy, xx + dx):
                        return False
                    if (yy + dy, xx + dx) in self.keep and data["blocked"][mid]:
                        return False
            return True

        # lanes may cross the site: nudge the piece (up to 4 cells) to the nearest spot
        # where it blocks nothing that has to stay walkable
        self.last_shift = (0, 0)
        if not fits(y, x):
            offs = sorted(((dy, dx) for dy in range(-4, 5) for dx in range(-4, 5)),
                          key=lambda o: abs(o[0]) + abs(o[1]))
            ok = next((o for o in offs if fits(y + o[0], x + o[1])), None)
            if ok is None:
                return False
            y, x = y + ok[0], x + ok[1]
            self.last_shift = ok
        for dy, row in enumerate(grid):
            for dx, mid in enumerate(row):
                b = data["blocked"][mid]
                self.paint(y + dy, x + dx, solid if b else floor)
                self.custom[(y + dy, x + dx)] = mid
                self.collision[(y + dy, x + dx)] = b
                self.piece_at[(y + dy, x + dx)] = (piece, dy, dx)
        if anchor:
            self.anchors[anchor] = (y, x)
        return True

    def ground_cells(self, y: int, x: int, r: float) -> List[Cell]:
        return [(yy, xx) for (yy, xx) in disc(y, x, r) if self.inside(yy, xx) and self.cls[yy, xx] in WALK]

    def noise(self) -> np.ndarray:
        if not hasattr(self, "_noise"):
            self._noise = self.rng.random((self.H, self.W))
        return self._noise


def disc(y: int, x: int, r: float) -> List[Cell]:
    ir = int(math.ceil(r))
    return [(y + dy, x + dx) for dy in range(-ir, ir + 1) for dx in range(-ir, ir + 1) if dy * dy + dx * dx <= r * r + 0.3]


def ellipse(y: int, x: int, ry: float, rx: float, rough: float, rng) -> List[Cell]:
    out = []
    for yy in range(int(y - ry - 2), int(y + ry + 3)):
        for xx in range(int(x - rx - 2), int(x + rx + 3)):
            d = ((yy - y) / max(ry, 0.5)) ** 2 + ((xx - x) / max(rx, 0.5)) ** 2
            if d + (rng.random() - 0.5) * rough < 1.0:
                out.append((yy, xx))
    return out


def ring(y: int, x: int, ry: float, rx: float, width: float = 1.0) -> List[Cell]:
    out = []
    for yy in range(int(y - ry - 2), int(y + ry + 3)):
        for xx in range(int(x - rx - 2), int(x + rx + 3)):
            d = math.hypot((yy - y) / max(ry, 0.5), (xx - x) / max(rx, 0.5))
            if 1.0 - width / max(ry, rx) <= d <= 1.0:
                out.append((yy, xx))
    return out


def _gap_ok(y, x, cy, cx, gaps, width=0.5):
    """True if (y, x) lies in one of the gap directions (n/s/e/w/ne...)."""
    if not gaps:
        return False
    ang = math.atan2(y - cy, x - cx)  # y down
    dirs = {"e": 0, "se": math.pi / 4, "s": math.pi / 2, "sw": 3 * math.pi / 4, "w": math.pi,
            "nw": -3 * math.pi / 4, "n": -math.pi / 2, "ne": -math.pi / 4}
    for g in gaps:
        a = dirs[g]
        d = abs((ang - a + math.pi) % (2 * math.pi) - math.pi)
        if d < width:
            return True
    return False


# ---------------------------------------------------------------- stamps
# signature: fn(p: Painter, y, x, **params). (y, x) is absolute.

def st_clearing(p: Painter, y, x, ry=3.5, rx=4.5, ground="g", rough=0.5, **_):
    """Open ground: the floor of the area."""
    for c in ellipse(y, x, ry, rx, rough, p.rng):
        p.paint(*c, ground)


def st_fill(p: Painter, y, x, h=3, w=3, c="g", **_):
    """A literal rectangle of one class, centred on (y, x)."""
    for yy in range(y - h // 2, y - h // 2 + h):
        for xx in range(x - w // 2, x - w // 2 + w):
            p.paint(yy, xx, c)


def st_thicket(p: Painter, y, x, ry=2.0, rx=2.0, rough=0.4, **_):
    """A clump of trees (blocking), e.g. to close a side or make a hollow."""
    for c in ellipse(y, x, ry, rx, rough, p.rng):
        p.paint(*c, "f")


def st_tree_wall(p: Painter, y, x, ry=5, rx=6, width=2, gaps=("s",), **_):
    """A ring of trees around a clearing with gaps (the only ways in)."""
    for (yy, xx) in ring(y, x, ry, rx, width):
        if not _gap_ok(yy, xx, y, x, gaps):
            p.paint(yy, xx, "f")


def st_tall_grass(p: Painter, y, x, ry=2.0, rx=3.0, density=0.85, **_):
    """Encounter grass (and, beside water, a reed bed)."""
    for (yy, xx) in ellipse(y, x, ry, rx, 0.5, p.rng):
        if p.inside(yy, xx) and p.cls[yy, xx] in "gpv" and p.rng.random() < density:
            p.paint(yy, xx, "t")


def st_reeds(p: Painter, y, x, r=4.0, width=1, density=0.8, **_):
    """Tall grass hugging every water edge within r: a reed bed."""
    for (yy, xx) in disc(y, x, r):
        if not p.inside(yy, xx) or p.cls[yy, xx] not in "gp":
            continue
        near = any(p.inside(yy + dy, xx + dx) and p.cls[yy + dy, xx + dx] in "wbh"
                   for dy in range(-width, width + 1) for dx in range(-width, width + 1))
        if near and p.rng.random() < density:
            p.paint(yy, xx, "t")


def st_pond(p: Painter, y, x, ry=2.0, rx=3.0, water="w", rough=0.4, **_):
    for c in ellipse(y, x, ry, rx, rough, p.rng):
        p.paint(*c, water)
    p.anchors.setdefault("water", (y, x))


def st_stream(p: Painter, y, x, length=12, axis="x", width=2, water="w", wobble=0.6, **_):
    """A narrow watercourse through (y, x)."""
    off = 0.0
    for i in range(-length // 2, length // 2 + 1):
        off += (p.rng.random() - 0.5) * wobble
        off = max(-2, min(2, off))
        for k in range(width):
            if axis == "x":
                p.paint(y + int(round(off)) + k - width // 2, x + i, water)
            else:
                p.paint(y + i, x + int(round(off)) + k - width // 2, water)


def st_rock_rise(p: Painter, y, x, ry=2.0, rx=3.0, rough=0.4, c="r", **_):
    """A blocking mass of rock: an outcrop or the shoulder of a rise."""
    for cc in ellipse(y, x, ry, rx, rough, p.rng):
        p.paint(*cc, c)


def st_rock_ring(p: Painter, y, x, ry=4, rx=5, gaps=("s",), piece="rock_mound", step=2.6, **_):
    """Boulders/rock mounds set around (y, x): a rocky rise with a way up."""
    n = max(5, int(2 * math.pi * max(ry, rx) / step))
    for i in range(n):
        a = 2 * math.pi * i / n
        yy = int(round(y + math.sin(a) * ry))
        xx = int(round(x + math.cos(a) * rx))
        if _gap_ok(yy, xx, y, x, gaps, 0.6):
            continue
        if piece == "rock_mound":
            if p.pin("rock_mound", yy - 1, xx - 1) is None:
                p.pin("boulder", yy, xx)
        else:
            p.pin(piece, yy, xx)


def st_boulders(p: Painter, y, x, n=3, r=3.0, piece="boulder", **_):
    """A scatter of loose boulders on open ground."""
    cands = [c for c in p.ground_cells(y, x, r) if c not in p.keep]
    p.rng.shuffle(cands)
    placed = 0
    for (yy, xx) in cands:
        if placed >= n:
            break
        if any(p.cls[yy + dy, xx + dx] in "uo" for dy in (-1, 0, 1) for dx in (-1, 0, 1)):
            continue
        p.pin(piece, yy, xx)
        placed += 1


def st_boulder(p: Painter, y, x, piece="boulder", **_):
    p.pin(piece, y, x)


def st_mound(p: Painter, y, x, **_):
    """One 2x2 rock mound with its top-left at (y, x)."""
    if p.pin("rock_mound", y, x) is None:
        p.pin("boulder", y, x)


def st_great_tree(p: Painter, y, x, name="tree", **_):
    """A single broad old tree (2x2) standing apart; (y, x) is its bottom-left trunk cell."""
    p.pin("old_tree", y - 1, x, anchor=name)
    p.anchors[name] = (y, x)
    p.anchors[name + "_roots"] = (y + 1, x)


def st_bare_ring(p: Painter, y, x, r=2.5, c="p", inner=0.0, scorch=False, **_):
    """Ground where nothing grows: scorched, trampled, salted. Bare earth (p) or ash (v);
    scorch=true uses the custom burnt-grass tiles (falls back to ``c``)."""
    from . import art

    sc = art.load()["pieces"] if scorch else {}
    for (yy, xx) in disc(y, x, r):
        dd = math.hypot(yy - y, xx - x)
        if dd < inner:
            continue
        if p.inside(yy, xx) and p.cls[yy, xx] in "gtypv" and (yy, xx) not in p.custom:
            if scorch and p.rng.random() < 0.85 - 0.25 * dd / max(r, 1):
                p.paint(yy, xx, c)
                k = "scorch_c" if dd < r * 0.45 else "scorch_b" if dd < r * 0.8 else "scorch_a"
                p.custom[(yy, xx)] = sc[k][0][0]
                p.collision[(yy, xx)] = 0
                p.piece_at[(yy, xx)] = (k, 0, 0)
            elif not scorch:
                p.paint(yy, xx, c)


def st_split_oak(p: Painter, y, x, name="oak", **_):
    """A great oak split by lightning in its own burnt apron, 6x6 (custom tiles). (y, x) = the cleft at its foot."""
    if not p.pin_custom("split_oak", y - 4, x - 3, solid="f", floor="v", anchor=name):
        p.pin("old_tree", y - 1, x - 1, anchor=name)
    else:
        y, x = y + p.last_shift[0], x + p.last_shift[1]
    p.anchors[name] = (y, x)
    p.anchors[name + "_roots"] = (y + 1, x)
    p.anchors[name + "_left"] = (y, x - 3)
    p.anchors[name + "_right"] = (y, x + 2)


def st_fallen_log(p: Painter, y, x, name="log", **_):
    """A huge fallen trunk lying east-west, 5x2 (custom tiles). (y, x) = its middle top."""
    if not p.pin_custom("fallen_log", y, x - 2, solid="o", anchor=name):
        for i in range(-2, 3):
            p.pin("boulder", y, x + i)
    else:
        y, x = y + p.last_shift[0], x + p.last_shift[1]
    p.anchors[name] = (y, x)
    p.anchors[name + "_top"] = (y, x)
    p.anchors[name + "_front"] = (y + 2, x)
    p.anchors[name + "_end"] = (y + 1, x - 3)


def st_worn_ring(p: Painter, y, x, ry=2.5, rx=3.0, c="p", **_):
    """A trodden circle: a ring path (wrestling ring, dancing ground, sundial)."""
    for (yy, xx) in ring(y, x, ry, rx, 1.0):
        if p.inside(yy, xx) and p.cls[yy, xx] in "gt":
            p.paint(yy, xx, c)


def st_pale_patch(p: Painter, y, x, h=3, w=4, **_):
    """Pale trodden grass (General 9-slice): a lawn, a sunning patch, a dancing floor."""
    y0, x0 = y - h // 2, x - w // 2
    for yy in range(h):
        for xx in range(w):
            row = "t" if yy == 0 else "b" if yy == h - 1 else ""
            col = "l" if xx == 0 else "r" if xx == w - 1 else ""
            name = "pale_" + ((row + col) if (row or col) else "c")
            name = {"pale_t": "pale_t", "pale_b": "pale_b"}.get(name, name)
            Y, X = y0 + yy, x0 + xx
            if p.inside(Y, X) and p.cls[Y, X] in WALK:
                p.pin(name, Y, X)


def st_flowers(p: Painter, y, x, n=6, r=3.0, **_):
    cands = [c for c in p.ground_cells(y, x, r) if p.cls[c] == "g"]
    p.rng.shuffle(cands)
    for (yy, xx) in cands[:n]:
        p.pin("flowers", yy, xx)


def st_flower_bed(p: Painter, y, x, h=2, w=3, **_):
    for yy in range(y - h // 2, y - h // 2 + h):
        for xx in range(x - w // 2, x - w // 2 + w):
            if p.inside(yy, xx) and (yy, xx) not in p.keep:
                p.pin("flowers", yy, xx)


def st_fence_pen(p: Painter, y, x, h=5, w=7, gate="s", inside="g", **_):
    """A wooden pen (Route 117 fence) with a gate gap on one side."""
    y0, x0, y1, x1 = y - h // 2, x - w // 2, y - h // 2 + h - 1, x - w // 2 + w - 1
    for yy in range(y0 + 1, y1):
        for xx in range(x0 + 1, x1):
            p.paint(yy, xx, inside)
    gy, gx = {"s": (y1, x), "n": (y0, x), "e": (y, x1), "w": (y, x0)}[gate]
    for xx in range(x0, x1 + 1):
        for yy, name in ((y0, "fence_h"), (y1, "fence_h")):
            nm = name
            if xx == x0:
                nm = "fence_tl" if yy == y0 else "fence_bl"
            elif xx == x1:
                nm = "fence_tr" if yy == y0 else "fence_br"
            if (yy, xx) == (gy, gx) or (gate in "ns" and yy == gy and abs(xx - gx) <= 0):
                p.paint(yy, xx, "g")
                continue
            p.pin(nm, yy, xx)
    for yy in range(y0 + 1, y1):
        for xx, nm in ((x0, "fence_l"), (x1, "fence_r")):
            if (yy, xx) == (gy, gx):
                p.paint(yy, xx, "g")
                continue
            p.pin(nm, yy, xx)
    p.anchors.setdefault("pen", (y, x))
    p.anchors.setdefault("gate", (gy, gx))


def st_standing_stones(p: Painter, y, x, n=6, ry=3, rx=3.5, piece="boulder_grey", **_):
    """Grey stones set in a ring: shrine stones, a court, a cairn circle."""
    for i in range(n):
        a = 2 * math.pi * i / n + 0.3
        p.pin(piece, int(round(y + math.sin(a) * ry)), int(round(x + math.cos(a) * rx)))


def st_ruin_wall(p: Painter, y, x, length=5, axis="x", broken=0.3, piece="boulder_grey", **_):
    """A tumbled line of grey stone (the foundation of a wall), with gaps."""
    for i in range(-(length // 2), length - length // 2):
        if p.rng.random() < broken:
            continue
        yy, xx = (y, x + i) if axis == "x" else (y + i, x)
        p.pin(piece, yy, xx)


def st_ruin_room(p: Painter, y, x, h=5, w=6, door="s", broken=0.25, floor="p", **_):
    """The footprint of a ruined building: a rectangle of tumbled stone around a bare floor."""
    y0, x0 = y - h // 2, x - w // 2
    y1, x1 = y0 + h - 1, x0 + w - 1
    for yy in range(y0 + 1, y1):
        for xx in range(x0 + 1, x1):
            p.paint(yy, xx, floor)
    dy_, dx_ = {"s": (y1, x), "n": (y0, x), "e": (y, x1), "w": (y, x0)}[door]
    for yy in range(y0, y1 + 1):
        for xx in range(x0, x1 + 1):
            if yy in (y0, y1) or xx in (x0, x1):
                if abs(yy - dy_) + abs(xx - dx_) <= 0:
                    p.paint(yy, xx, floor)
                elif p.rng.random() < broken:
                    p.paint(yy, xx, "g")
                else:
                    p.pin("boulder_grey", yy, xx)
    p.anchors.setdefault("room", (y, x))


def st_cliff(p: Painter, y, x, side="n", depth=3, c="r", **_):
    """A cliff face along one side of the footprint (the wall the area backs onto)."""
    for yy in range(p.y0, p.y0 + p.h):
        for xx in range(p.x0, p.x0 + p.w):
            d = {"n": yy - p.y0, "s": p.y0 + p.h - 1 - yy, "w": xx - p.x0, "e": p.x0 + p.w - 1 - xx}[side]
            jag = int(p.noise()[min(yy, p.H - 1), min(xx, p.W - 1)] * 1.6)
            if d < depth + jag - 0:
                p.paint(yy, xx, c)


def st_cave_mouth(p: Painter, y, x, name="mouth", mass=(3, 5), **_):
    """A rock face with a cave mouth; (y, x) is the door cell. Also a sub-map entrance."""
    mh, mw = mass
    for yy in range(y - mh, y + 1):
        for xx in range(x - mw // 2, x + mw // 2 + 1):
            p.paint(yy, xx, "r")
    door = p.pin("cave_mouth", y - 1, x - 1, anchor=name)
    p.paint(y + 1, x, "g")
    return door


def st_rock_hollow(p: Painter, y, x, name="hollow", **_):
    """A big rock with a dark hollow at its foot (Fortree). (y, x) is the hollow."""
    door = p.pin("rock_hollow", y - 2, x - 2, anchor=name)
    p.paint(y + 1, x, "g")
    return door


def st_tree_door(p: Painter, y, x, name="door", **_):
    """A huge trunk with a door in it (Fortree tree house). (y, x) is the door."""
    door = p.pin("tree_door", y - 2, x - 2, anchor=name)
    p.paint(y + 1, x, "g")
    return door


def st_sign(p: Painter, y, x, text="", **_):
    p.pin("sign", y, x)
    p.signs.append({"y": y, "x": x, "text": text})


def st_object(p: Painter, y, x, kind="breakable_rock", **_):
    """An object-event prop: breakable_rock, cuttable_tree, pushable_boulder."""
    if p.inside(y, x) and p.cls[y, x] in WALK and (y, x) not in p.keep:
        p.objects.append({"kind": kind, "y": y, "x": x})


def st_objects(p: Painter, y, x, kind="breakable_rock", n=3, r=3.0, **_):
    cands = [c for c in p.ground_cells(y, x, r) if c not in p.keep]
    p.rng.shuffle(cands)
    for (yy, xx) in cands[:n]:
        p.objects.append({"kind": kind, "y": yy, "x": xx})


def st_burrows(p: Painter, y, x, n=5, r=3.0, **_):
    """Single-cell bare patches dotted over grass: burrow mouths and spoil heaps."""
    cands = [c for c in p.ground_cells(y, x, r) if p.cls[c] in "gt"]
    p.rng.shuffle(cands)
    for (yy, xx) in cands[:n]:
        p.paint(yy, xx, "p")
        p.props.append({"kind": "burrow", "y": yy, "x": xx})


def st_path(p: Painter, y, x, to=(0, 0), c="p", width=1, **_):
    """A straight-ish trodden path from (y, x) to centre+to."""
    ty, tx = p.at(*to)
    n = max(abs(ty - y), abs(tx - x), 1)
    for i in range(n + 1):
        yy = int(round(y + (ty - y) * i / n))
        xx = int(round(x + (tx - x) * i / n))
        for k in range(width):
            p.paint(yy + (k if abs(tx - x) > abs(ty - y) else 0), xx + (k if abs(ty - y) >= abs(tx - x) else 0), c)


def st_ledge(p: Painter, y, x, length=5, **_):
    """A south-facing ledge (one-way drop)."""
    for i in range(-(length // 2), length - length // 2):
        p.paint(y, x + i, "s")


def st_prop(p: Painter, y, x, kind="", **kw):
    """A purely descriptive prop for the scene viewer (no tile change)."""
    p.props.append({"kind": kind, "y": y, "x": x, **{k: v for k, v in kw.items() if k != "name"}})


def st_anchor(p: Painter, y, x, name="spot", **_):
    p.anchors[name] = (y, x)


STAMPS: Dict[str, Callable] = {
    "clearing": st_clearing, "fill": st_fill, "thicket": st_thicket, "tree_wall": st_tree_wall,
    "tall_grass": st_tall_grass, "reeds": st_reeds, "pond": st_pond, "stream": st_stream,
    "rock_rise": st_rock_rise, "rock_ring": st_rock_ring, "boulders": st_boulders, "boulder": st_boulder,
    "mound": st_mound, "great_tree": st_great_tree, "bare_ring": st_bare_ring, "worn_ring": st_worn_ring,
    "pale_patch": st_pale_patch, "flowers": st_flowers, "flower_bed": st_flower_bed, "fence_pen": st_fence_pen,
    "standing_stones": st_standing_stones, "ruin_wall": st_ruin_wall, "ruin_room": st_ruin_room,
    "cliff": st_cliff, "cave_mouth": st_cave_mouth, "rock_hollow": st_rock_hollow, "tree_door": st_tree_door,
    "split_oak": st_split_oak, "fallen_log": st_fallen_log,
    "sign": st_sign, "object": st_object, "objects": st_objects, "burrows": st_burrows, "path": st_path,
    "ledge": st_ledge, "prop": st_prop, "anchor": st_anchor,
}

STAMP_DOCS = {k: (v.__doc__ or "").strip().split("\n")[0] for k, v in STAMPS.items()}


def run(p: Painter, compose: List[list]) -> None:
    for step in compose:
        name, dy, dx = step[0], step[1], step[2]
        params = step[3] if len(step) > 3 else {}
        fn = STAMPS.get(name)
        if fn is None:
            raise KeyError(f"unknown stamp {name!r}")
        y, x = p.at(dy, dx)
        fn(p, y, x, **params)
