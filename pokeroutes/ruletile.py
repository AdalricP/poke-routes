"""Rule-based tiler for the Arklarna outdoor tilesets (gTileset_ArkOutdoor +
gTileset_ArkWoodland).

The corpus learner (:mod:`pokeroutes.learn`) can't train on a brand-new
tileset that no map uses yet, so this module tiles a class grid with fixed rules
read from ``data/tilesets/metatile_classes_ark.json``, which the tileset builder
writes (``tools/arklarna_tiles/build.py`` in the ROM tree):

* blob47 classes (path, tall grass, water, rock): pick the metatile by the
  8-neighbour mask, so edges and inner corners join up;
* trees: 2x2 blocks on the even grid. A block whose four cells are all 'f'
  becomes a tree; leftover 'f' cells become 1x1 bushes. Grass, path and tall
  grass cells next to trees get crown overhang metatiles (crown drawn above the
  player);
* ledges: south and north ledges as rows, east/west ledges as columns, with end caps;
  where differently-facing ledges touch, corner / T / cross pieces from the
  class's ``corners`` table (keyed by :func:`ledge_arm_key`), when it has one;
* waterfalls: top/mid/bottom by left/mid/right pieces.

Classes this tileset has no art for are swapped first (sand -> path, sea -> water,
cliff -> rock, ...; see :data:`FALLBACK`).

    from pokeroutes.ruletile import ArkTiler
    layout = ArkTiler.load().tile(route.cls)      # -> Layout
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from .classify import seal_waterfalls
from .layout import Layout

EXPERIMENTS = Path(__file__).resolve().parents[2]
ROM_CANDIDATES = [EXPERIMENTS / "arklarna/rom-wt/tiles-outdoor", EXPERIMENTS / "arklarna/rom"]
PRIMARY, SECONDARY = "ark_outdoor", "ark_woodland"
PRIMARY_SYMBOL, SECONDARY_SYMBOL = "gTileset_ArkOutdoor", "gTileset_ArkWoodland"

# classes the Ark tileset draws: g p t w f r s e x j a (and o = grass)
FALLBACK = {
    "d": "p", "v": "p", "z": "p", "q": "p", "n": "r",   # sand, ash, stairs, bridge, cave -> path / rock
    "b": "w", "h": "w",                                  # sea, shallow -> water
    "y": "t",                                            # ash grass -> tall grass
    "k": "r", "l": "r", "u": "r",                        # cliff, lava, boulder -> rock
    "o": "g",
    "j": "s",
}
DIRS = {"N": (0, -1), "NE": (1, -1), "E": (1, 0), "SE": (1, 1),
        "S": (0, 1), "SW": (-1, 1), "W": (-1, 0), "NW": (-1, -1)}
DIAG = {"NE": ("N", "E"), "NW": ("N", "W"), "SE": ("S", "E"), "SW": ("S", "W")}


ROW_LEDGES = ("s", "j")
COLUMN_LEDGES = ("e", "x")
LEDGE_LETTERS = ROW_LEDGES + COLUMN_LEDGES


def ledge_arm(own: str, neighbour: str, side_by_side: bool) -> str:
    """Which band a ledge cell reaches out with towards one neighbour ('.' = none).
    Side by side, ledges join through a row (a south or north ledge); above/below,
    through a column (an east or west ledge). The arm is the letter of the row or
    column that runs across the shared edge."""
    if neighbour not in LEDGE_LETTERS:
        return "."
    own_kind = ROW_LEDGES if own in ROW_LEDGES else COLUMN_LEDGES
    along_own_run = side_by_side == (own in ROW_LEDGES)
    if along_own_run:
        return own if neighbour == own or neighbour not in own_kind else "."
    return neighbour if neighbour not in own_kind else "."


def ledge_arm_key(grid: List[str], x: int, y: int) -> str:
    """The 'NESW' key of a ledge cell into its class's corners table."""
    height, width = len(grid), len(grid[0])
    own = grid[y][x]
    arms = []
    for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
        inside = 0 <= x + dx < width and 0 <= y + dy < height
        neighbour = grid[y + dy][x + dx] if inside else "."
        arms.append(ledge_arm(own, neighbour, side_by_side=dy == 0))
    return "".join(arms)


def ark_joins_ledge_corners(rom: Optional[Path] = None) -> bool:
    """True when the Ark class table has corner pieces for every ledge direction, so
    differently-facing ledges can touch; False without a table or without corners."""
    try:
        return ArkTiler.load(rom).joins_ledge_corners()
    except FileNotFoundError:
        return False


def find_rom(root: Optional[Path] = None) -> Path:
    if root:
        return Path(root)
    if os.environ.get("ARK_ROM"):
        return Path(os.environ["ARK_ROM"])
    for c in ROM_CANDIDATES:
        if (c / "data/tilesets/metatile_classes_ark.json").exists():
            return c
    raise FileNotFoundError("no ROM tree with data/tilesets/metatile_classes_ark.json; set ARK_ROM")


class ArkTiler:
    def __init__(self, classes: dict, rom: Path):
        self.c = classes
        self.rom = rom
        self.C = classes["classes"]
        self.bits = classes["mask_bits"]
        self.grass = self.C["g"]["base"][0]

    @classmethod
    def load(cls, rom: Optional[Path] = None) -> "ArkTiler":
        rom = find_rom(rom)
        return cls(json.loads((rom / "data/tilesets/metatile_classes_ark.json").read_text()), rom)

    # -- helpers -----------------------------------------------------------
    def prepare(self, cls: np.ndarray) -> List[str]:
        g = np.asarray(cls).astype(str)
        rows = []
        for row in g:
            rows.append("".join(c if c in self.C else FALLBACK.get(c, "g") for c in row))
        return rows

    def _mask(self, grid, x, y, same) -> int:
        H, W = len(grid), len(grid[0])
        n = {}
        for d, (dx, dy) in DIRS.items():
            xx, yy = x + dx, y + dy
            n[d] = not (0 <= xx < W and 0 <= yy < H) or grid[yy][xx] in same
        for d, (a, b) in DIAG.items():
            if not (n[a] and n[b]):
                n[d] = False
        return sum(1 << i for i, d in enumerate(self.bits) if n[d])

    def pair(self):
        from .tileset import load_pair
        return load_pair(self.rom / "data/tilesets", PRIMARY, SECONDARY)

    def joins_ledge_corners(self) -> bool:
        drawn = [letter for letter in LEDGE_LETTERS if letter in self.C]
        return bool(drawn) and all("corners" in self.C[letter] for letter in drawn)

    def border(self) -> List[int]:
        return list(self.C["f"]["base"])

    # -- main --------------------------------------------------------------
    def tile(self, cls: np.ndarray, seed: int = 0) -> Layout:
        grid = self.prepare(seal_waterfalls(cls))
        H, W = len(grid), len(grid[0])
        C = self.C
        ids = np.full((H, W), self.grass, np.int32)
        blocked = np.zeros((H, W), bool)
        for y in range(H):
            for x in range(W):
                c = grid[y][x]
                k = C[c]
                kind = k.get("autotile")
                if kind == "blob47":
                    ids[y, x] = k["by_mask"][str(self._mask(grid, x, y, set(k.get("joins", c))))]
                elif kind in ("row", "column") and "corners" in k:
                    ids[y, x] = k["corners"][ledge_arm_key(grid, x, y)]
                elif kind == "row":
                    l = x > 0 and grid[y][x - 1] == c
                    r = x < W - 1 and grid[y][x + 1] == c
                    ids[y, x] = k["parts"]["mid" if l and r else "left" if r else "right" if l else "single"]
                elif kind == "column":
                    u = y > 0 and grid[y - 1][x] == c
                    d = y < H - 1 and grid[y + 1][x] == c
                    ids[y, x] = k["parts"]["mid" if u and d else "top" if d else "bottom" if u else "single"]
                elif kind == "waterfall":
                    u = y > 0 and grid[y - 1][x] == c
                    d = y < H - 1 and grid[y + 1][x] == c
                    l = x > 0 and grid[y][x - 1] == c
                    r = x < W - 1 and grid[y][x + 1] == c
                    row = "top" if not u else "bottom" if not d else "mid"
                    col = "mid" if l == r else "left" if r else "right"
                    ids[y, x] = k["parts"][f"{row}_{col}"]
                elif kind != "tree2x2":
                    ids[y, x] = k["base"][(x * 7 + y * 13) % len(k["base"])]
                if k.get("collision"):
                    blocked[y, x] = True
        self._trees(grid, ids, blocked)
        col = blocked.astype(np.uint8)
        el = np.full((H, W), 3, np.uint8)
        el[blocked] = 0
        for y in range(H):
            for x in range(W):
                if grid[y][x] in ("w", "a"):
                    el[y, x] = 1
        return Layout(ids.astype(np.uint16), col, el)

    def _trees(self, grid, ids, blocked):
        C = self.C
        H, W = len(grid), len(grid[0])
        letters = [k for k, v in C.items() if v.get("autotile") == "tree2x2"]
        BH, BW = (H + 1) // 2, (W + 1) // 2
        blk = [[None] * BW for _ in range(BH)]
        for by in range(BH):
            for bx in range(BW):
                cells = [grid[y][x] if (y < H and x < W) else None
                         for y in (2 * by, 2 * by + 1) for x in (2 * bx, 2 * bx + 1)]
                if cells[0] in letters and all(c == cells[0] for c in cells):
                    # the crown overhangs the row above; water, rock, ledges... have no
                    # crown art, so a tree under them would look chopped -> bushes instead
                    above = {grid[2 * by - 1][x] for x in (2 * bx, 2 * bx + 1) if by > 0 and x < W}
                    if above <= set(C[cells[0]]["overlays"]) | set(letters):
                        blk[by][bx] = cells[0]

        def pres(bx, by, letter):
            if not (0 <= bx < BW and 0 <= by < BH):
                return False
            return blk[by][bx] in letters

        def pres_same(bx, by, letter):
            if not (0 <= bx < BW and 0 <= by < BH):
                return False
            return blk[by][bx] == letter

        orth = sum(1 << i for i, d in enumerate(self.bits) if d in ("N", "E", "S", "W"))
        for letter in letters:
            k = C[letter]
            ctx, ov, nph = k["by_context"], k["overlays"], k.get("phases", 1)
            tall = [t for t in ov if t not in ("g", "bush", "p")]
            for y in range(H):
                for x in range(W):
                    bx, by, px, py = x // 2, y // 2, x % 2, y % 2
                    own = blk[by][bx] == letter
                    b = (own | pres(bx - 1, by, letter) * 2 | pres(bx + 1, by, letter) * 4 |
                         pres(bx, by + 1, letter) * 8 | pres(bx - 1, by + 1, letter) * 16 |
                         pres(bx + 1, by + 1, letter) * 32)
                    if not own:
                        b = (pres_same(bx - 1, by, letter) * 2 | pres_same(bx + 1, by, letter) * 4 |
                             pres_same(bx, by + 1, letter) * 8 | pres_same(bx - 1, by + 1, letter) * 16 |
                             pres_same(bx + 1, by + 1, letter) * 32)
                    key = f"{(bx + by) % nph},{px},{py},{b}"
                    c = grid[y][x]
                    if own:
                        ids[y, x] = ctx[key]
                        blocked[y, x] = True
                    elif c == letter:
                        ids[y, x] = ov["bush"].get(key, k["bush"])
                        blocked[y, x] = True
                    elif c == "g":
                        ids[y, x] = ov["g"].get(key, ids[y, x])
                    elif c == "p" or c in tall:
                        m = self._mask(grid, x, y, set(C[c].get("joins", c)))
                        # crown-over-path art only exists for some masks (no road bottom
                        # edge, 199); fall back to the same mask without the diagonals
                        tab = ov[c].get(str(m)) or ov[c].get(str(m & orth))
                        if tab and key in tab:
                            ids[y, x] = tab[key]


def write_ark_emerald(route, layout: Layout, out: Path, tiler: ArkTiler, offsets=None) -> None:
    """write_emerald, then point the layout at the Ark tilesets and a forest border."""
    from .export import write_emerald

    write_emerald(route, layout, out, PRIMARY, SECONDARY, offsets=offsets)
    lay = Path(out) / "data/layouts" / route.spec.id
    entry = json.loads((lay / "layouts_entry.json").read_text())
    entry["primary_tileset"], entry["secondary_tileset"] = PRIMARY_SYMBOL, SECONDARY_SYMBOL
    (lay / "layouts_entry.json").write_text(json.dumps(entry, indent=2))
    (lay / "border.bin").write_bytes(np.array(tiler.border(), dtype="<u2").tobytes())


def tile_route(tiler: ArkTiler, route) -> Layout:
    """Tile a generated route, then pin the special areas' pieces (split oak, fallen log,
    scorch, signs, fences...) by name via AreaResult.ark_fixed(). Pieces with no Ark art
    come back as class overrides and are tiled as rock / tree / grass."""
    results = route.__dict__.get("_area_results") or []
    cls = np.array(route.cls, copy=True)
    fixed: Dict[tuple, int] = {}
    coll: Dict[tuple, int] = {}
    for res in results:
        try:
            f, over = res.ark_fixed()
        except Exception as e:  # never let one area break the route
            route.log.append(f"ark_fixed failed for {res.area.get('id')} ({type(e).__name__}: {e})")
            continue
        for c, k in over.items():
            cls[c] = k
        for c, mid in f.items():
            fixed[c] = int(mid)
            col = getattr(res, "collision", {}).get(c)
            coll[c] = int(col) if col is not None else (1 if str(route.cls[c]) in "ofr" else 0)
    from .ground import tile_with_false_walls

    layout = tile_with_false_walls(route, tiler.tile, cls)
    for (y, x), mid in fixed.items():
        layout.metatile[y, x] = mid
        layout.collision[y, x] = coll[(y, x)]
        layout.elevation[y, x] = 0 if coll[(y, x)] else 3
    # route signs get a signpost metatile where that can't cut a lane: lane-end tips
    # (one open neighbour) or open ground (all four neighbours open)
    sign = tiler.c.get("props", {}).get("sign", {}).get("cells", [[None]])[0][0]
    walk = set("gpt")
    H, W = cls.shape
    for e in route.events:
        if e.kind != "sign" or sign is None or (e.y, e.x) in fixed or str(cls[e.y, e.x]) not in walk:
            continue
        nb = sum(1 for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1))
                 if 0 <= e.y + dy < H and 0 <= e.x + dx < W and str(cls[e.y + dy, e.x + dx]) in walk)
        if nb in (1, 4):
            layout.metatile[e.y, e.x] = sign
            layout.collision[e.y, e.x] = 1
            fixed[(e.y, e.x)] = sign
    route.__dict__["_ark_fixed"] = fixed
    return layout
