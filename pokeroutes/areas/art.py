"""Custom metatiles for special areas ("ark_special"), drawn in Emerald style.

Stock Emerald has no lightning-split oak or fallen log, so these are drawn here,
pixel by pixel, in the colours of the General tileset's palette 2 (the
tree/grass palette: greens, teal grass, bark browns, a dark outline brown and two
reds). Every 8x8 tile uses that one 16-colour palette and never index 0, so the
pieces obey the GBA rules and can drop into the outdoor tileset unchanged.

    python3 -m pokeroutes.areas.art            # writes pokeroutes/areas/ark_special/

Output (pokeroutes/areas/ark_special/):
    tiles.png        indexed 4bpp sheet (16 tiles per row), colour indices 0-15
    palette.pal      JASC palette the indices refer to (= General palette 2)
    metatiles.json   {name: {"id": provisional metatile id, "pal": 2, "tiles": [8 tile refs],
                     "behavior": ..., "blocked": 0/1, "layer": ...}} grouped by piece
    pieces.png       RGB preview of every piece
Pieces: split_oak (6x6 metatiles, oak plus its burnt apron), fallen_log (5x2), scorch_a/b/c (1x1 each).
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
from PIL import Image

OUT = Path(__file__).resolve().parent / "ark_special"
ROM_TILESETS = Path("/Users/aryan/Desktop/experiments/arklarna/rom/data/tilesets")
FIRST_ID = 0x380  # provisional: free space after Fortree's last metatile (0x317)

# General palette 2 (index: colour). Index 0 is transparent and never used.
PAL2 = ["182952", "b4ff83", "83c562", "398b31", "395200", "de9473", "6a5a5a", "a4625a",
        "413931", "ffc594", "de6a62", "cd4152", "a4d5c5", "73c5a4", "41b483", "18a46a"]
LG, G, DG, OL, TAN, GB, BR, DK, PEACH, RED, DRED, PALE, GRASS, TEAL, DTEAL = range(1, 16)


def _pal() -> np.ndarray:
    return np.array([[int(c[i:i + 2], 16) for i in (0, 2, 4)] for c in PAL2], dtype=np.uint8)


_samples: dict = {}


def sample(mid: int) -> np.ndarray:
    """A real General/Fortree metatile as palette-2 indices (nearest colour, never 0)."""
    if mid not in _samples:
        from ..tileset import load_pair

        pair = load_pair(ROM_TILESETS, "general", "fortree")
        rgb = pair.render_metatile(mid).astype(int)
        pal = _pal().astype(int)
        d = ((rgb[:, :, None, :] - pal[None, None, 1:, :]) ** 2).sum(-1)
        _samples[mid] = (d.argmin(-1) + 1).astype(np.uint8)
    return _samples[mid]


def tiled(mid: int, h: int, w: int) -> np.ndarray:
    t = sample(mid)
    return np.tile(t, (h // 16 + 1, w // 16 + 1))[:h, :w].copy()


def grass(h: int, w: int, rng) -> np.ndarray:
    """Emerald short grass (General metatile 0x001), mapped into palette 2."""
    return tiled(0x001, h, w)


def blobs(h: int, w: int, rng, scale: float = 3.0) -> np.ndarray:
    """Clumpy 0..1 noise (box-blurred white noise) for patches that look painted, not sprayed."""
    n = rng.random((h + 8, w + 8))
    k = int(max(1, scale))
    for _ in range(2):
        n = sum(np.roll(np.roll(n, dy, 0), dx, 1) for dy in range(-k, k + 1) for dx in range(-k, k + 1)) / (2 * k + 1) ** 2
    n = n[4:4 + h, 4:4 + w]
    return (n - n.min()) / max(1e-6, n.max() - n.min())


def outline(a: np.ndarray, mask: np.ndarray, col=DK) -> None:
    m = mask
    edge = m & ~(np.roll(m, 1, 0) & np.roll(m, -1, 0) & np.roll(m, 1, 1) & np.roll(m, -1, 1))
    a[edge] = col


def scorch(a: np.ndarray, cy: float, cx: float, r: float, rng, strength=1.0) -> None:
    """Burn grass around (cy, cx): char near the centre, singed browns at the edge."""
    h, w = a.shape
    yy, xx = np.mgrid[0:h, 0:w]
    b = blobs(h, w, rng, 2)
    d = np.hypot((yy - cy) / r, (xx - cx) / r) + (b - 0.5) * 0.7
    burnt = d < 0.55 * strength
    singe = (d >= 0.55 * strength) & (d < 1.0 * strength)
    grassy = np.isin(a, [GRASS, PALE, TEAL, DTEAL, G, LG, DG])
    tex = sample(0x001)  # keep the grass's own grain, re-coloured
    grain = np.tile(tex, (h // 16 + 1, w // 16 + 1))[:h, :w]
    light = np.isin(grain, [PALE, LG])
    darkg = np.isin(grain, [DTEAL, DG, OL])
    burnt_col = np.where(darkg, DK, np.where(light, BR, GB)).astype(np.uint8)
    singe_col = np.where(darkg, BR, np.where(light, TAN, GRASS)).astype(np.uint8)
    a[burnt & grassy] = burnt_col[burnt & grassy]
    sm = singe & grassy & (b > 0.35)
    a[sm] = singe_col[sm]
    n = rng.random((h, w))
    a[burnt & (n > 0.992)] = RED


# ---------------------------------------------------------------- pieces

def draw_split_oak(rng) -> np.ndarray:
    """64x80: a great oak split down the middle by lightning. Left half alive and
    leafy, right half blackened with bare limbs, an ember-lit cleft between."""
    H, W = 80, 64
    a = np.zeros((H, W), np.uint8)  # 0 = empty; composed onto scorched grass below
    yy, xx = np.mgrid[0:H, 0:W]

    # roots: flared base
    roots = np.zeros((H, W), bool)
    for (x0, x1, y1, lean) in ((22, 8, 76, -1), (26, 18, 78, 0), (40, 48, 78, 0), (44, 58, 76, 1), (32, 33, 79, 0)):
        for t in np.linspace(0, 1, 40):
            x = 32 + (x1 - 32) * t + (x0 - 32) * (1 - t) * 0.2
            y = 60 + (y1 - 60) * t
            rr = 4.0 * (1 - t) + 1.2
            roots |= (yy - y) ** 2 + (xx - x) ** 2 <= rr * rr

    # two trunk halves leaning apart
    def half(x_bot0, x_bot1, x_top0, x_top1, y_top, y_bot):
        m = np.zeros((H, W), bool)
        for y in range(y_top, y_bot + 1):
            t = (y - y_top) / max(1, (y_bot - y_top))
            l = x_top0 + (x_bot0 - x_top0) * t
            r = x_top1 + (x_bot1 - x_top1) * t
            m[y, int(round(l)):int(round(r)) + 1] = True
        return m

    left = half(14, 30, 8, 27, 30, 66)
    right = half(34, 50, 38, 56, 30, 66)
    trunk = left | right | roots
    # oak bark: wavy vertical furrows, ridges catching light
    wob = (np.sin(yy / 3.1 + xx * 1.7) * 1.2 + np.sin(yy / 7.0 + xx) * 0.8)
    ph = (xx + wob) % 5
    bark = np.where(ph < 1.1, DK, np.where(ph < 2.2, GB, BR)).astype(np.uint8)
    bark[(ph > 3.6) & (rng.random((H, W)) < 0.5)] = TAN
    a[trunk] = bark[trunk]
    # light side on the left of each half
    lit = left & (xx < 13 + (yy - 30) * 0.17)
    a[lit & (bark == BR)] = TAN
    # right half charred
    cb = blobs(H, W, rng, 1.5)
    char = right & (cb > 0.25)
    a[char] = np.where(cb[char] > 0.6, DK, GB)
    # lightning scar: pale zigzag down the right half
    x = 47.0
    for y in range(31, 66):
        x += rng.choice([-1, 0, 1]) * 0.9
        x = min(max(x, 40 + (y - 30) * -0.1 + 1), 52)
        a[y, int(x)] = PEACH
        a[y, int(x) + 1] = PALE if y % 3 else PEACH
    # the cleft: black slit with an ember glow at its root
    cleft = half(30, 34, 27, 38, 26, 68) & ~(left & right)
    cleft &= ~left
    cleft &= ~right
    cleft |= half(31, 33, 29, 35, 28, 70)
    a[cleft] = DK
    glow = cleft & (yy > 56)
    a[glow & ((xx + yy) % 2 == 0)] = DRED
    a[glow & (yy > 63) & ((xx + yy) % 3 == 0)] = RED
    outline(a, trunk | cleft)

    # living crown, left: a lumpy mass of leaves
    crown = np.zeros((H, W), bool)
    for (cy, cx, r) in ((16, 16, 13), (24, 8, 9), (8, 24, 9), (26, 24, 9), (12, 6, 7), (4, 14, 6)):
        crown |= (yy - cy) ** 2 + (xx - cx) ** 2 <= r * r
    crown &= xx < 34
    n = rng.random((H, W))
    leaf = tiled(0x225, H, W)  # Fortree canopy leaves
    leaf[np.isin(leaf, [GRASS, PALE, TEAL, DTEAL])] = G
    # clumps: darker underside of each lump
    for (cy, cx, r) in ((16, 16, 13), (24, 8, 9), (8, 24, 9), (26, 24, 9)):
        under = ((yy - cy) ** 2 + (xx - cx) ** 2 <= r * r) & (yy > cy + r * 0.35)
        leaf[under] = np.where(n[under] < 0.5, DG, OL)
    a[crown] = leaf[crown]
    outline(a, crown, OL)

    # dead crown, right: bare black limbs on scorched leaves
    def limb(x0, y0, x1, y1, wdt):
        for t in np.linspace(0, 1, 60):
            x, y = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
            r = wdt * (1 - 0.6 * t)
            m = (yy - y) ** 2 + (xx - x) ** 2 <= r * r
            a[m] = DK
    limb(46, 34, 60, 6, 2.6)
    limb(52, 22, 63, 18, 1.4)
    limb(44, 32, 40, 4, 2.2)
    limb(41, 16, 34, 8, 1.2)
    limb(56, 12, 50, 2, 1.0)
    limb(50, 30, 62, 30, 1.4)
    # a few scorched leaves still hanging on
    for _ in range(18):
        y, x = rng.integers(2, 28), rng.integers(38, 63)
        if a[y, x] != DK:
            a[y, x] = rng.choice([BR, GB, OL])
    return a


def draw_split_oak_apron(rng) -> np.ndarray:
    """96x96 (6x6 metatiles): the split oak standing in a burnt clearing that fades
    out into ordinary grass at the edges, so it sits in the route without seams."""
    H, W = 96, 96
    a = grass(H, W, rng)
    scorch(a, 78, 48, 40, rng, 1.15)
    scorch(a, 60, 48, 22, rng, 1.0)
    core = draw_split_oak(rng)
    m = core > 0
    a[0:80, 16:80][m] = core[m]
    return a


def draw_fallen_log(rng) -> np.ndarray:
    """80x32: a huge fallen trunk lying east-west, cut end with rings on the left,
    splintered end on the right, moss along the top."""
    H, W = 32, 80
    a = grass(H, W, rng)
    yy, xx = np.mgrid[0:H, 0:W]
    body = (yy >= 7) & (yy <= 26) & (xx >= 10) & (xx <= 72)
    # round the top/bottom edges a little and taper the broken end
    body &= ~(((yy == 7) | (yy == 26)) & ((xx < 12) | (xx > 70)))
    jag = (xx > 66) & ((yy + (xx * 7) % 5) % 6 < 2 + (xx - 66) // 2)
    body &= ~jag
    shadow = (yy >= 26) & (yy <= 29) & (xx >= 8) & (xx <= 74)
    a[shadow & ~body] = DTEAL
    # bark: long horizontal furrows
    bark = np.full((H, W), BR, np.uint8)
    furrow = ((yy - 7) % 4 == 0) | (((yy - 9) % 4 == 0) & ((xx // 6) % 3 == 0))
    bark[furrow] = DK
    bark[(yy <= 10)] = np.where(((xx // 3) % 2 == 0)[(yy <= 10)], TAN, BR)
    bark[(yy >= 23)] = np.where(furrow[(yy >= 23)], DK, GB)
    a[body] = bark[body]
    # cut end: rings
    cap = ((yy - 16.5) / 10.0) ** 2 + ((xx - 10.5) / 6.5) ** 2 <= 1.0
    d = np.hypot((yy - 16.5) / 10.0, (xx - 10.5) / 6.5)
    ringc = np.where((d * 6).astype(int) % 2 == 0, PEACH, TAN).astype(np.uint8)
    a[cap] = ringc[cap]
    a[cap & (d < 0.18)] = BR
    # a crack from the heart
    for i in range(6):
        a[16 + i // 2, 10 + i] = BR
    outline(a, body | cap)
    # splinters at the broken end
    for (y, x) in ((9, 72), (12, 74), (15, 73), (19, 75), (22, 72)):
        a[y, x:x + 3] = TAN
        a[y + 1, x:x + 3] = DK
    # moss
    n = rng.random((H, W))
    moss = body & (yy <= 9) & (n < 0.45) & (xx > 14)
    a[moss] = np.where(n[moss] < 0.25, G, DG)
    for x in range(16, 66, 9):
        a[6, x:x + 3] = G
        a[5, x + 1] = LG
    return a


def draw_scorch(rng, variant: int) -> np.ndarray:
    """Seamless burnt-ground tiles: 0 = singed grass, 1 = mostly burnt, 2 = char.
    Uniform textures (no blob in the middle) so neighbours join into one patch."""
    g = grass(16, 16, rng)
    light = np.isin(g, [PALE, LG])
    darkg = np.isin(g, [DTEAL, DG, OL])
    n = rng.random((16, 16))
    b = blobs(16, 16, rng, 1.2)
    if variant == 0:
        a = g.copy()
        m = b > 0.55
        a[m] = np.where(darkg[m], BR, np.where(light[m], TAN, GB))
    elif variant == 1:
        a = np.where(darkg, DK, np.where(light, BR, GB)).astype(np.uint8)
        keep = b > 0.78
        a[keep] = g[keep]
    else:
        a = np.where(darkg | (n < 0.25), DK, np.where(light, GB, np.where(n < 0.6, GB, BR))).astype(np.uint8)
        a[n > 0.985] = RED
    return a


# ---------------------------------------------------------------- packing

PIECES = {
    # name: (drawer, width in metatiles, height, blocked mask rows ('1' blocked), behaviour)
    "split_oak": (draw_split_oak_apron, 6, 6, ["011110", "011110", "011110", "011110", "001100", "000000"], "MB_NORMAL"),
    "fallen_log": (draw_fallen_log, 5, 2, ["11111", "11111"], "MB_NORMAL"),
    "scorch_a": (lambda r: draw_scorch(r, 0), 1, 1, ["0"], "MB_NORMAL"),
    "scorch_b": (lambda r: draw_scorch(r, 1), 1, 1, ["0"], "MB_NORMAL"),
    "scorch_c": (lambda r: draw_scorch(r, 2), 1, 1, ["0"], "MB_NORMAL"),
}


def build_all(seed: int = 7) -> dict:
    rng = np.random.default_rng(seed)
    OUT.mkdir(parents=True, exist_ok=True)
    pal = _pal()
    tiles: List[np.ndarray] = []
    tile_index: Dict[bytes, int] = {}
    meta: Dict[str, dict] = {}
    previews = []
    mid = FIRST_ID
    for name, (fn, mw, mh, blocked, beh) in PIECES.items():
        art = fn(rng)
        assert art.shape == (mh * 16, mw * 16), (name, art.shape)
        assert art.min() >= 1, f"{name} uses colour 0"
        cells = []
        for my in range(mh):
            row = []
            for mx in range(mw):
                refs = []
                for ty in range(2):
                    for tx in range(2):
                        t = art[my * 16 + ty * 8: my * 16 + ty * 8 + 8, mx * 16 + tx * 8: mx * 16 + tx * 8 + 8]
                        key = t.tobytes()
                        if key not in tile_index:
                            tile_index[key] = len(tiles)
                            tiles.append(t.copy())
                        refs.append({"tile": tile_index[key], "pal": 2, "hflip": False, "vflip": False})
                # bottom layer = the art; top layer = transparent (tile 0 of the sheet is left blank)
                row.append({"id": mid, "name": f"{name}_{my}_{mx}", "bottom": refs, "top": None,
                            "blocked": int(blocked[my][mx]), "behavior": beh, "layer": "LAYER_NORMAL"})
                mid += 1
            cells.append(row)
        meta[name] = {"size": [mw, mh], "metatiles": cells}
        previews.append((name, Image.fromarray(pal[art])))
    # tiles.png: tile 0 blank (transparent), then the art tiles
    n = len(tiles) + 1
    cols = 16
    rows = (n + cols - 1) // cols
    sheet = np.zeros((rows * 8, cols * 8), np.uint8)
    for i, t in enumerate(tiles, start=1):
        r, c = divmod(i, cols)
        sheet[r * 8:r * 8 + 8, c * 8:c * 8 + 8] = t
    for name in meta:
        for row in meta[name]["metatiles"]:
            for m in row:
                for ref in m["bottom"]:
                    ref["tile"] += 1
    im = Image.fromarray(sheet, "P")
    flat = []
    for c in pal:
        flat += list(c)
    im.putpalette(flat + [0] * (768 - len(flat)))
    im.save(OUT / "tiles.png")
    (OUT / "palette.pal").write_text("JASC-PAL\n0100\n16\n" + "\n".join(f"{r} {g} {b}" for r, g, b in pal) + "\n")
    (OUT / "metatiles.json").write_text(json.dumps({
        "about": "Custom special-area metatiles drawn in General palette 2 (slot 2 of the primary). "
                 "Ids are provisional (free space after Fortree's 0x317); the tileset owner assigns real ones. "
                 "Each metatile: 4 bottom-layer tile refs into tiles.png (tile 0 = blank), top layer empty.",
        "palette_slot": 2, "first_id": FIRST_ID, "tiles": "tiles.png", "pieces": meta}, indent=1))
    pw = sum(p.width for _, p in previews) + 10 * len(previews)
    ph = max(p.height for _, p in previews)
    sheet_im = Image.new("RGB", (pw, ph), (30, 30, 30))
    x = 0
    for _, p in previews:
        sheet_im.paste(p, (x, 0))
        x += p.width + 10
    sheet_im.resize((sheet_im.width * 3, sheet_im.height * 3), Image.NEAREST).save(OUT / "pieces.png")
    return meta


# ---------------------------------------------------------------- runtime use

# Where the tiles-outdoor agent put the pieces in the ROM (gTileset_ArkWoodland, branch
# arklarna-tiles-outdoor, commit f48a5c0d; also data/tilesets/metatile_classes_ark.json,
# props.<name>.cells). Each piece is contiguous, row-major. Rebuilding their tileset may shift these.
ROM_FIRST_ID = {"split_oak": 0x2D3, "fallen_log": 0x2F7, "scorch_a": 0x301, "scorch_b": 0x302, "scorch_c": 0x303}
ROM_TILESET = "gTileset_ArkWoodland"


def rom_id(provisional: int) -> int:
    """Provisional ark_special id (0x380+) -> the real ArkWoodland metatile id."""
    meta = json.loads((OUT / "metatiles.json").read_text())
    for name, piece in meta["pieces"].items():
        flat = [m["id"] for row in piece["metatiles"] for m in row]
        if provisional in flat and name in ROM_FIRST_ID:
            return ROM_FIRST_ID[name] + flat.index(provisional)
    return provisional

_cache: dict = {}


def load() -> dict:
    """{metatile id: RGB 16x16 array} plus {piece name: grid of ids}."""
    if "pieces" in _cache:
        return _cache
    if not (OUT / "metatiles.json").exists():
        build_all()
    meta = json.loads((OUT / "metatiles.json").read_text())
    im = Image.open(OUT / "tiles.png")
    sheet = np.array(im)
    pal = _pal()
    rgb, blocked, grids = {}, {}, {}
    for name, piece in meta["pieces"].items():
        grid = []
        for row in piece["metatiles"]:
            grow = []
            for m in row:
                px = np.zeros((16, 16, 3), np.uint8)
                for k, ref in enumerate(m["bottom"]):
                    r, c = divmod(ref["tile"], 16)
                    t = sheet[r * 8:r * 8 + 8, c * 8:c * 8 + 8]
                    ty, tx = divmod(k, 2)
                    px[ty * 8:ty * 8 + 8, tx * 8:tx * 8 + 8] = pal[t]
                rgb[m["id"]] = px
                blocked[m["id"]] = m["blocked"]
                grow.append(m["id"])
            grid.append(grow)
        grids[name] = grid
    _cache.update(rgb=rgb, blocked=blocked, pieces=grids)
    return _cache


def paint(image: Image.Image, custom: Dict[Tuple[int, int], int], offset=(0, 0)) -> Image.Image:
    """Draw custom metatiles over a rendered layout (16 px per cell)."""
    data = load()
    im = image.convert("RGB")
    oy, ox = offset
    for (y, x), mid in custom.items():
        px = data["rgb"].get(mid)
        if px is not None:
            im.paste(Image.fromarray(px), ((x - ox) * 16, (y - oy) * 16))
    return im


if __name__ == "__main__":
    m = build_all()
    print({k: v["size"] for k, v in m.items()}, "->", OUT)
