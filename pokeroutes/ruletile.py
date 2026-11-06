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
from .ruletile_terrain import (grid_with_ledge_grounds, shore_metatile, tile_bridges, tile_ledges_over_ground,
                               tile_sideways_waterfalls)

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
    "m": "p", "i": "p",                                  # snow, ice -> path until the Ark set has snow art
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
    def __init__(self, classes: dict, rom: Path, overlay: Optional[dict] = None):
        from .tileset_overlay import apply_overlay_to_ark_class_table, empty_overlay

        self.overlay = overlay or empty_overlay()
        self.c = apply_overlay_to_ark_class_table(classes, self.overlay) if overlay else classes
        self.rom = rom
        self.C = self.c["classes"]
        self.bits = self.c["mask_bits"]
        self.grass = self.C["g"]["base"][0]

    @classmethod
    def load(cls, rom: Optional[Path] = None) -> "ArkTiler":
        from .tileset_overlay import ARK_TILESET_PAIR_NAME, read_overlay_for_tileset_pair

        rom = find_rom(rom)
        table = json.loads((rom / "data/tilesets/metatile_classes_ark.json").read_text())
        return cls(table, rom, read_overlay_for_tileset_pair(ARK_TILESET_PAIR_NAME))

    def rom_pair(self):
        from .tileset import load_pair
        return load_pair(self.rom / "data/tilesets", PRIMARY, SECONDARY)

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
        from .tileset_overlay import apply_overlay_to_tileset_pair, overlay_has_changes

        base = self.rom_pair()
        return apply_overlay_to_tileset_pair(base, self.overlay) if overlay_has_changes(self.overlay) else base

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
        self._tile_cells(grid, grid, ids, blocked)
        ledge_grounds = tile_ledges_over_ground(grid, ids, C)
        mask_grid = grid_with_ledge_grounds(grid, ledge_grounds)
        if ledge_grounds:
            self._tile_cells(grid, mask_grid, ids, blocked)
            tile_ledges_over_ground(grid, ids, C)
        self._trees(grid, ids, blocked, mask_grid)
        col = blocked.astype(np.uint8)
        el = np.full((H, W), 3, np.uint8)
        el[blocked] = 0
        for y in range(H):
            for x in range(W):
                if grid[y][x] in ("w", "a"):
                    el[y, x] = 1
        return Layout(ids.astype(np.uint16), col, el)

    def _tile_cells(self, grid, mask_grid, ids, blocked):
        H, W = len(grid), len(grid[0])
        C = self.C
        for y in range(H):
            for x in range(W):
                c = grid[y][x]
                k = C[c]
                kind = k.get("autotile")
                if kind == "blob47":
                    ids[y, x] = k["by_mask"][str(self._mask(mask_grid, x, y, set(k.get("joins", c))))]
                elif kind == "shore":
                    shore = shore_metatile(grid, x, y, k, self.bits)
                    body = C[k["body"]]
                    ids[y, x] = shore if shore is not None else body["by_mask"][
                        str(self._mask(mask_grid, x, y, set(k.get("joins", c))))]
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
                elif kind == "fence4":
                    ids[y, x] = k["by_arms"].get(fence_arm_key(grid, x, y, set(k.get("joins", c))), k["base"][0])
                elif kind == "waterfall":
                    u = y > 0 and grid[y - 1][x] == c
                    d = y < H - 1 and grid[y + 1][x] == c
                    l = x > 0 and grid[y][x - 1] == c
                    r = x < W - 1 and grid[y][x + 1] == c
                    row = "top" if not u else "bottom" if not d else "mid"
                    col = "mid" if l == r else "left" if r else "right"
                    single = k["parts"].get(f"{row}_single")
                    is_single_column = not l and not r and single is not None
                    ids[y, x] = single if is_single_column else k["parts"][f"{row}_{col}"]
                elif kind not in ("tree2x2", "bridge"):
                    ids[y, x] = k["base"][(x * 7 + y * 13) % len(k["base"])]
                if k.get("collision"):
                    blocked[y, x] = True
        if "q" in C:
            tile_bridges(grid, ids, C["q"])
        if "a" in C:
            tile_sideways_waterfalls(grid, ids, C["a"], "w")

    def _trees(self, grid, ids, blocked, mask_grid=None):
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
                        ids[y, x] = crown_from_other_colour_below(k, blk, letter, letters, bx, by, px, py, b) or ctx[key]
                        blocked[y, x] = True
                    elif c == letter:
                        ids[y, x] = bush_in_neighbouring_tree_colour(C, grid, blk, letters, x, y)
                        blocked[y, x] = True
                    elif c == "g":
                        ids[y, x] = ov["g"].get(key, ids[y, x])
                    elif c == "p" or c in tall:
                        m = self._mask(mask_grid or grid, x, y, set(C[c].get("joins", c)))
                        # crown-over-path art only exists for some masks (no road bottom
                        # edge, 199); fall back to the same mask without the diagonals
                        tab = ov[c].get(str(m)) or ov[c].get(str(m & orth))
                        if tab and key in tab:
                            ids[y, x] = tab[key]


FENCE_SIDE_OFFSETS = (("N", 0, -1), ("E", 1, 0), ("S", 0, 1), ("W", -1, 0))
GREEN_TREE_LETTER = "f"
TREE_CONTEXT_BELOW_OFFSETS = ((0, 1, 8, 1), (-1, 1, 16, 2), (1, 1, 32, 4))


def fence_arm_key(grid: List[str], x: int, y: int, joining_letters: set) -> str:
    height, width = len(grid), len(grid[0])
    arms = [side if 0 <= x + dx < width and 0 <= y + dy < height and grid[y + dy][x + dx] in joining_letters else "-"
            for side, dx, dy in FENCE_SIDE_OFFSETS]
    arm_key = "".join(arms)
    return arm_key


def tree_block_letter(blocks, block_x: int, block_y: int):
    if not (0 <= block_y < len(blocks) and 0 <= block_x < len(blocks[0])):
        return None
    return blocks[block_y][block_x]


def other_colour_below(blocks, letter: str, tree_letters, block_x: int, block_y: int):
    below_letters = [tree_block_letter(blocks, block_x + dx, block_y + dy) for dx, dy, _bit, _other_bit in TREE_CONTEXT_BELOW_OFFSETS]
    other_letters = [below for below in below_letters if below in tree_letters and below != letter]
    if not other_letters:
        return None
    return other_letters[0]


def crown_from_other_colour_below(tree_class: dict, blocks, letter: str, tree_letters, block_x: int, block_y: int,
                                  px: int, py: int, context_bits: int):
    other_letter = other_colour_below(blocks, letter, tree_letters, block_x, block_y)
    crowns = tree_class.get("crowns_from_below", {}).get(other_letter)
    if not crowns:
        return None
    same_colour_bits, other_colour_bits = context_bits, 0
    for dx, dy, bit, other_bit in TREE_CONTEXT_BELOW_OFFSETS:
        below_letter = tree_block_letter(blocks, block_x + dx, block_y + dy)
        if below_letter != letter:
            same_colour_bits &= ~bit
        if below_letter == other_letter:
            other_colour_bits |= other_bit
    crown_metatile = crowns.get(f"{px},{py},{same_colour_bits},{other_colour_bits}")
    return crown_metatile


def bush_colour_letter(grid: List[str], letter: str, tree_letters, x: int, y: int) -> str:
    if letter != GREEN_TREE_LETTER:
        return letter
    height, width = len(grid), len(grid[0])
    neighbour_letters = [grid[y + dy][x + dx] for dx, dy in ((0, 1), (-1, 0), (1, 0), (0, -1))
                         if 0 <= x + dx < width and 0 <= y + dy < height]
    coloured_neighbours = [neighbour for neighbour in neighbour_letters if neighbour in tree_letters and neighbour != letter]
    colour_letter = coloured_neighbours[0] if coloured_neighbours else letter
    return colour_letter


def same_colour_neighbour_bits(blocks, colour_letter: str, block_x: int, block_y: int) -> int:
    neighbour_bits = sum(bit for dx, dy, bit in ((-1, 0, 2), (1, 0, 4), (0, 1, 8), (-1, 1, 16), (1, 1, 32))
                         if tree_block_letter(blocks, block_x + dx, block_y + dy) == colour_letter)
    return neighbour_bits


def bush_in_neighbouring_tree_colour(classes: dict, grid: List[str], blocks, tree_letters, x: int, y: int) -> int:
    block_x, block_y, px, py = x // 2, y // 2, x % 2, y % 2
    colour_letter = bush_colour_letter(grid, grid[y][x], tree_letters, x, y)
    colour_class = classes[colour_letter]
    phase_count = colour_class.get("phases", 1)
    bits = same_colour_neighbour_bits(blocks, colour_letter, block_x, block_y)
    bush_metatile = colour_class["overlays"]["bush"].get(f"{(block_x + block_y) % phase_count},{px},{py},{bits}", colour_class["bush"])
    return bush_metatile


def write_ark_emerald(route, layout: Layout, out: Path, tiler: ArkTiler, offsets=None) -> None:
    """write_emerald, then point the layout at the Ark tilesets and a forest border."""
    from .export import write_emerald

    write_emerald(route, layout, out, PRIMARY, SECONDARY, offsets=offsets)
    lay = Path(out) / "data/layouts" / route.spec.id
    entry = json.loads((lay / "layouts_entry.json").read_text())
    entry["primary_tileset"], entry["secondary_tileset"] = PRIMARY_SYMBOL, SECONDARY_SYMBOL
    (lay / "layouts_entry.json").write_text(json.dumps(entry, indent=2))
    (lay / "border.bin").write_bytes(np.array(tiler.border(), dtype="<u2").tobytes())
    from .tileset_overlay import overlay_has_changes, write_overlaid_tileset_pair_files

    if overlay_has_changes(tiler.overlay):
        write_overlaid_tileset_pair_files(tiler.rom_pair(), tiler.overlay, Path(out) / "data/tilesets")


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
    from .scenery_layer import block_raised_ground, class_grid_as_drawn, stamp_placed_props

    layout = tile_with_false_walls(route, tiler.tile, class_grid_as_drawn(route, cls))
    block_raised_ground(cls, layout)
    stamped_prop_metatiles = stamp_placed_props(route, route.cls, layout, tiler.c.get("props", {}))
    for (y, x), mid in fixed.items():
        layout.metatile[y, x] = mid
        layout.collision[y, x] = coll[(y, x)]
        layout.elevation[y, x] = 0 if coll[(y, x)] else 3
    fixed.update({cell: metatile_id for cell, metatile_id in stamped_prop_metatiles.items() if cell not in fixed})
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
