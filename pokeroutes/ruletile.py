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
        letters = tree_letters_of(C)
        blk = self.tree_blocks(grid, mask_grid or grid)
        for letter in letters:
            k = C[letter]
            ctx, ov, nph = k["by_context"], k["overlays"], k.get("phases", 1)
            for y in range(H):
                for x in range(W):
                    bx, by, px, py = x // 2, y // 2, x % 2, y % 2
                    own = blk[by][bx] == letter
                    b = (own | neighbour_tree_block_bits(blk, bx, by, set(letters))) if own else \
                        neighbour_tree_block_bits(blk, bx, by, {letter})
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
                    else:
                        crown_metatile = self.crown_over_ground(grid, mask_grid or grid, letter, x, y, key)
                        if crown_metatile is not None:
                            ids[y, x] = crown_metatile

    def crown_over_ground(self, grid, mask_grid, letter: str, x: int, y: int, key: str) -> Optional[int]:
        overlays = self.C[letter]["overlays"]
        ground_letter = grid[y][x]
        if ground_letter == "g":
            return overlays["g"].get(key)
        if ground_letter not in overlays or ground_letter == "bush":
            return None
        orthogonal_bits = sum(1 << bit_index for bit_index, side in enumerate(self.bits) if side in ("N", "E", "S", "W"))
        ground_mask = self._mask(mask_grid, x, y, set(self.C[ground_letter].get("joins", ground_letter)))
        crown_table = overlays[ground_letter].get(str(ground_mask)) or overlays[ground_letter].get(str(ground_mask & orthogonal_bits)) or {}
        crown_metatile = crown_table.get(key)
        return crown_metatile

    def tree_blocks(self, grid, mask_grid):
        letters = tree_letters_of(self.C)
        blocks = whole_tree_blocks(grid, letters)
        clipped_blocks = self.blocks_with_clipped_crowns(grid, mask_grid, blocks)
        while clipped_blocks:
            for block_x, block_y in clipped_blocks:
                blocks[block_y][block_x] = None
            clipped_blocks = self.blocks_with_clipped_crowns(grid, mask_grid, blocks)
        return blocks

    def blocks_with_clipped_crowns(self, grid, mask_grid, blocks) -> set:
        clipped_blocks = {block for cell, blocks_reaching_cell in self.cells_with_clipped_crowns(grid, mask_grid, blocks)
                          if not self.is_crown_tip_over_ground_without_art(grid, cell)
                          for block in blocks_reaching_cell}
        return clipped_blocks

    def is_crown_tip_over_ground_without_art(self, grid, cell) -> bool:
        x, y = cell
        ground_letter = grid[y][x]
        is_tip_row = y % 2 == 0
        is_ground_with_crown_art = any(ground_letter in self.C[letter]["overlays"] for letter in tree_letters_of(self.C))
        is_tip_without_art = is_tip_row and is_ground_with_crown_art and ground_letter not in ("g", "bush")
        return is_tip_without_art

    def cells_with_clipped_crowns(self, grid, mask_grid, blocks) -> list:
        letters = tree_letters_of(self.C)
        clipped_cells = []
        for letter in letters:
            for y, grid_row in enumerate(grid):
                for x in range(len(grid_row)):
                    blocks_reaching_cell = self.tree_blocks_clipped_at(grid, mask_grid, blocks, letter, letters, x, y)
                    if blocks_reaching_cell:
                        clipped_cells.append(((x, y), blocks_reaching_cell))
        return clipped_cells

    def tree_blocks_clipped_at(self, grid, mask_grid, blocks, letter: str, letters, x: int, y: int) -> list:
        block_x, block_y, px, py = x // 2, y // 2, x % 2, y % 2
        if blocks[block_y][block_x] is not None or grid[y][x] in letters:
            return []
        overlays = self.C[letter]["overlays"]
        phase = (block_x + block_y) % self.C[letter].get("phases", 1)
        bits = neighbour_tree_block_bits(blocks, block_x, block_y, {letter})
        key = f"{phase},{px},{py},{bits}"
        if key not in overlays["g"] or self.crown_over_ground(grid, mask_grid, letter, x, y, key) is not None:
            return []
        reaching_bits = [bit for _dx, _dy, bit in TREE_BLOCK_NEIGHBOUR_BITS if bits & bit and f"{phase},{px},{py},{bit}" in overlays["g"]]
        reaching_bits = reaching_bits or [bit for _dx, _dy, bit in TREE_BLOCK_NEIGHBOUR_BITS if bits & bit]
        blocks_reaching_cell = [(block_x + dx, block_y + dy) for dx, dy, bit in TREE_BLOCK_NEIGHBOUR_BITS if bit in reaching_bits]
        return blocks_reaching_cell

    def clipped_crown_cells(self, cls: np.ndarray) -> dict:
        grid = self.prepare(seal_waterfalls(cls))
        mask_grid = self.mask_grid_of(grid)
        blocks = self.tree_blocks(grid, mask_grid)
        clipped_cells = [cell for cell, _blocks in self.cells_with_clipped_crowns(grid, mask_grid, blocks)]
        clipped_cells_by_kind = {
            "clipped tree crowns": [cell for cell in clipped_cells if not self.is_crown_tip_over_ground_without_art(grid, cell)],
            "crown tips with no art": [cell for cell in clipped_cells if self.is_crown_tip_over_ground_without_art(grid, cell)],
        }
        return clipped_cells_by_kind

    def mask_grid_of(self, grid):
        scratch_ids = np.full((len(grid), len(grid[0])), self.grass, np.int32)
        scratch_blocked = np.zeros(scratch_ids.shape, bool)
        self._tile_cells(grid, grid, scratch_ids, scratch_blocked)
        mask_grid = grid_with_ledge_grounds(grid, tile_ledges_over_ground(grid, scratch_ids, self.C))
        return mask_grid


TREE_BLOCK_NEIGHBOUR_BITS = ((-1, 0, 2), (1, 0, 4), (0, 1, 8), (-1, 1, 16), (1, 1, 32))


def tree_letters_of(classes: dict) -> List[str]:
    letters = [letter for letter, class_entry in classes.items() if class_entry.get("autotile") == "tree2x2"]
    return letters


def whole_tree_blocks(grid: List[str], letters: List[str]):
    height, width = len(grid), len(grid[0])
    block_rows, block_columns = (height + 1) // 2, (width + 1) // 2
    blocks = [[whole_tree_block_letter(grid, letters, block_x, block_y) for block_x in range(block_columns)]
              for block_y in range(block_rows)]
    return blocks


def whole_tree_block_letter(grid: List[str], letters: List[str], block_x: int, block_y: int) -> Optional[str]:
    height, width = len(grid), len(grid[0])
    cells = [grid[y][x] if y < height and x < width else None
             for y in (2 * block_y, 2 * block_y + 1) for x in (2 * block_x, 2 * block_x + 1)]
    if cells[0] not in letters or any(cell != cells[0] for cell in cells):
        return None
    return cells[0]


def neighbour_tree_block_bits(blocks, block_x: int, block_y: int, counted_letters: set) -> int:
    neighbour_bits = sum(bit for dx, dy, bit in TREE_BLOCK_NEIGHBOUR_BITS
                         if tree_block_letter(blocks, block_x + dx, block_y + dy) in counted_letters)
    return neighbour_bits


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
