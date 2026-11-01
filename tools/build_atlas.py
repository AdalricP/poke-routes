"""Pack the curated LPC sprites the viewer uses into one atlas.

Writes viewer/assets/atlas.png and atlas.json. Each entry is
[x, y, w, h]; groups list interchangeable variants (the renderer picks among
them per cell, which is where the "block vomit" texture comes from).
"""
import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from extract_sprites import components  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TS = ROOT / "inspirations" / "tilesets"
OUT = ROOT / "viewer" / "assets"

T = TS / "lpc-tile-atlas" / "terrain_atlas.png"
B = TS / "lpc-tile-atlas" / "base_out_atlas.png"
F = TS / "lpc-forest-reemax" / "src" / "LPC_forest" / "forest_tiles.png"
TREES = TS / "lpc-trees" / "src" / "lpc-trees"
PLAYER = TS / "lpc-character" / "player.png"
COT = TS / "lpc-thatched-cottage"
WD = TS / "lpc-windows-doors" / "src" / "lpc-windows-doors-v2" / "windows-doors.png"


def compose_house(roof_row, wall_row):
    """A 5-tile-wide half-timbered cottage: thatched hip roof over a two-row timber wall."""
    walls = Image.open(COT / "cottage.png").convert("RGBA")
    roofs = Image.open(COT / "thatched-roof.png").convert("RGBA")
    wd = Image.open(WD).convert("RGBA")
    base = Image.open(B).convert("RGBA")
    t = lambda im, cx, cy, w=1, h=1: im.crop((cx * 32, cy * 32, (cx + w) * 32, (cy + h) * 32))
    W, roof_h, overlap = 160, 128, 26
    house = Image.new("RGBA", (W, roof_h + 64 - overlap))
    wy = roof_h - overlap
    for i in range(4):
        col = 0 if i == 0 else 2 if i == 3 else 1
        house.alpha_composite(t(walls, col, wall_row + 1), (16 + i * 32, wy))
        house.alpha_composite(t(walls, col, wall_row + 2), (16 + i * 32, wy + 32))
    for x in (22, 106):
        house.alpha_composite(t(wd, 5, 1), (x, wy + 4))
        house.alpha_composite(t(wd, 14, 2), (x, wy + 20))
    house.alpha_composite(t(base, 5, 9, 1, 2), (64, wy))
    house.alpha_composite(t(roofs, 2, roof_row, 5, 4), (0, 0))
    return house


def cell(sheet, cx, cy, w=1, h=1):
    return (sheet, (cx * 32, cy * 32, (cx + w) * 32, (cy + h) * 32))


SPRITES = {
    # ground fills, 32x32, seamless
    "grass": [cell(F, 0, 0)],
    "grass_flowers": [cell(F, x, y) for x in range(1, 5) for y in range(3)],
    "grass_dark": [cell(T, 1, 25), cell(T, 2, 25), cell(T, 0, 25)],
    "dirt": [cell(T, x, 22) for x in range(8, 12)] + [cell(T, x, 21) for x in range(9, 12)],
    "dirt_stony": [cell(T, 8, 20), cell(T, 9, 20), cell(T, 10, 20), cell(T, 11, 20)],
    "mud": [cell(T, 4, 29), cell(T, 4, 30), cell(T, 5, 30)],
    "water": [cell(B, 10, 14), cell(B, 11, 14)],
    "canopy_round": [cell(F, 0, 10, 2, 2)],
    "canopy_pine": [cell(F, 2, 6, 2, 2)],
    # tall grass: body + top edge
    "tallgrass": [cell(T, 9, 18), cell(T, 10, 18)],
    "tallgrass_top": [cell(T, 9, 16), cell(T, 10, 16)],
    # props
    "mushrooms": [cell(F, 13, 0)],
    "plant": [cell(F, x, 3) for x in range(0, 6)],
    "stump": [cell(F, 6, 3)],
    "log": [cell(F, 7, 3, 2, 1), cell(F, 9, 3, 2, 1)],
    "web": [cell(F, 12, 4, 2, 2)],
    "beehive": [cell(F, 14, 2, 2, 2)],
    "boulder": [cell(F, 11, 1), cell(T, 14, 21), cell(T, 15, 15)],
    "pebbles": [cell(F, 12, 1)],
    "lily": [cell(T, x, y) for x in range(6, 9) for y in (30, 31)],
    "reeds": [cell(F, 0, 3), cell(F, 3, 3)],
    "tuft": [cell(T, 1, 25), cell(T, 2, 25)],
    "pine": [cell(F, 0, 6, 2, 2)],
    "roundtree": [cell(F, 0, 8, 2, 2)],
    "campfire": [cell(F, x, 5) for x in range(1, 4)],
    "butterfly": [cell(F, 9, 5), cell(F, 10, 5), cell(F, 11, 6), cell(F, 12, 6)],
    "bridge": [cell(T, 13, 18), cell(T, 14, 18)],
    "crate": [cell(F, 14, 0, 1, 2)],
}

# Leafy, detailed trees only (the smooth blob-style ones clash with the rest).
TREE_IDS = {"tree": [8, 20, 23, 34], "tree_big": [18, 19, 33, 38, 39, 40, 41, 42, 43], "tree_great": [46, 47]}
TREE_COLOURS = ["orange", "brown", "green", "pale"]


def main():
    items = []  # (name, group, image)
    for name, srcs in SPRITES.items():
        for i, (sheet, box) in enumerate(srcs):
            items.append((f"{name}_{i}", name, Image.open(sheet).convert("RGBA").crop(box)))
    ref = components(TREES / "trees-orange.png")
    for colour in TREE_COLOURS:
        sheet = Image.open(TREES / f"trees-{colour}.png").convert("RGBA")
        for group, ids in TREE_IDS.items():
            for idx in ids:
                x, y, w, h = ref[idx]
                items.append((f"{group}_{colour}_{idx}", f"{group}_{colour}", sheet.crop((x, y, x + w, y + h))))
    # player: walk rows 8-11 (up, left, down, right), 9 frames of 64x64
    player = Image.open(PLAYER).convert("RGBA")
    for row, d in enumerate(["up", "left", "down", "right"]):
        for f in range(9):
            items.append((f"player_{d}_{f}", f"player_{d}", player.crop((f * 64, (8 + row) * 64, f * 64 + 64, (9 + row) * 64))))

    rival = Image.open(TS / "lpc-character" / "rival.png").convert("RGBA")
    for row, d in enumerate(["up", "left", "down", "right"]):
        items.append((f"rival_{d}_0", f"rival_{d}", rival.crop((0, (8 + row) * 64, 64, (9 + row) * 64))))
    items.append(("house_0", "house", compose_house(0, 0)))
    items.append(("house_1", "house", compose_house(7, 4)))
    white = Image.new("RGBA", (4, 4), (255, 255, 255, 255))
    items.append(("white_0", "white", white))
    # shelf pack, tallest first, 1px gutter (2px padding for filtering safety)
    items.sort(key=lambda t: -t[2].height)
    width = 2048
    x = y = shelf = 0
    pos = {}
    for name, group, im in items:
        if x + im.width + 2 > width:
            x, y, shelf = 0, y + shelf + 2, 0
        pos[name] = (x, y, im.width, im.height)
        x += im.width + 2
        shelf = max(shelf, im.height)
    height = y + shelf
    atlas = Image.new("RGBA", (width, 1 << (height - 1).bit_length()))
    groups = {}
    for name, group, im in items:
        px, py, _, _ = pos[name]
        atlas.paste(im, (px, py))
        groups.setdefault(group, []).append(name)
    OUT.mkdir(parents=True, exist_ok=True)
    atlas.save(OUT / "atlas.png")
    (OUT / "atlas.json").write_text(json.dumps({"size": atlas.size, "sprites": pos, "groups": groups}, indent=0))
    print(f"atlas {atlas.size}, {len(items)} sprites, {len(groups)} groups")


if __name__ == "__main__":
    main()
