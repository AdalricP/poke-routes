"""Region renders: hillshaded height, water, biomes, rivers, landmarks, cities and
the route graph. Also the per-stage strip."""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .world import World

BIOME_RGB = {
    "~": (58, 104, 150), "=": (34, 62, 104), "o": (78, 132, 170),
    "b": (226, 210, 160), "d": (210, 178, 118), "m": (156, 182, 98), "f": (86, 136, 70),
    "F": (52, 98, 56), "w": (108, 140, 104), "M": (128, 118, 110), "S": (232, 234, 238),
    "v": (92, 72, 70), "l": (196, 74, 38), "a": (196, 182, 160), "g": (74, 160, 92), "B": (134, 128, 72),
}
ROLE_RGB = {
    "straight": (250, 250, 250), "turn": (255, 214, 92), "crossroads": (255, 120, 90),
    "dead_end": (190, 140, 255), "coastal": (120, 220, 255), "mountain_pass": (230, 190, 160),
    "river_crossing": (110, 200, 255), "loop": (130, 255, 160), "sea": (150, 220, 255),
}
FONT_PATHS = ["/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
              "/Library/Fonts/Arial.ttf", "DejaVuSans-Bold.ttf"]


def font(size: int, bold: bool = True):
    paths = FONT_PATHS if bold else FONT_PATHS[1:]
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def hillshade(h: np.ndarray, az: float = 315, alt: float = 40, z: float = 9.0) -> np.ndarray:
    gy, gx = np.gradient(h * z)
    slope = np.pi / 2 - np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    a, al = np.radians(az), np.radians(alt)
    s = np.sin(al) * np.sin(slope) + np.cos(al) * np.cos(slope) * np.cos(a - aspect)
    return np.clip(s, 0, 1)


def base_image(w: World, mode: str = "biome", scale: int = 6) -> Image.Image:
    """mode: height | biome. Returns an RGB image at ``scale`` px per cell."""
    land = w.land
    h = w.height
    # upsample height smoothly so the shading isn't blocky
    from scipy.ndimage import zoom

    hz = zoom(h, scale, order=1)
    shade = hillshade(hz, z=9.0 / scale * 6)
    landz = zoom(land.astype(np.uint8), scale, order=0).astype(bool)
    if mode == "height":
        hl = np.clip(np.where(land, h, 0) / max(1e-6, float(h[land].max())), 0, 1)
        stops = np.array([[92, 140, 74], [150, 170, 96], [196, 180, 120], [150, 118, 88], [236, 236, 236]], float)
        idx = hl * (len(stops) - 1)
        i0 = np.clip(idx.astype(int), 0, len(stops) - 2)
        t = (idx - i0)[..., None]
        col = stops[i0] * (1 - t) + stops[i0 + 1] * t
    else:
        col = np.zeros(h.shape + (3,))
        for k, rgb in BIOME_RGB.items():
            col[w.biome == k] = rgb
    # water colour by depth
    depth = np.clip(-h / 0.46, 0, 1)
    sea_col = np.array([86, 142, 186])[None, None] * (1 - depth[..., None]) + np.array([26, 50, 92])[None, None] * depth[..., None]
    if mode == "height":
        col = np.where(land[..., None], col, sea_col)
    else:
        col = np.where(land[..., None], col, sea_col)
    if w.lake is not None:
        col[w.lake] = BIOME_RGB["o"]
    colz = np.repeat(np.repeat(col, scale, 0), scale, 1)
    sh = (0.55 + 0.7 * shade)[..., None]
    out = np.where(landz[..., None], colz * sh, colz * (0.92 + 0.08 * shade[..., None]))
    # elevation tiers: a dark rim on the low side of every tier step (reads as cliffs)
    if w.tiers is not None and mode == "biome":
        tz = np.repeat(np.repeat(w.tiers, scale, 0), scale, 1)
        step = np.zeros(tz.shape, bool)
        step[:-1, :] |= tz[1:, :] > tz[:-1, :]
        step[1:, :] |= tz[:-1, :] > tz[1:, :]
        step[:, :-1] |= tz[:, 1:] > tz[:, :-1]
        step[:, 1:] |= tz[:, :-1] > tz[:, 1:]
        step &= landz
        out[step] = out[step] * 0.62
    # coast line
    from scipy.ndimage import binary_erosion

    edge = landz & ~binary_erosion(landz, iterations=1)
    out[edge] = out[edge] * 0.55
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def draw_rivers(img: Image.Image, w: World, scale: int) -> None:
    if w.river is None:
        return
    d = ImageDraw.Draw(img)
    W = w.W
    A = w.flow
    amax = float(A[w.river].max()) if w.river.any() else 1
    for y, x in zip(*np.nonzero(w.river)):
        r = int(w.recv[y * W + x])
        if r < 0:
            continue
        ry, rx = divmod(r, W)
        width = max(1, int(round(scale * (0.25 + 0.6 * (A[y, x] / amax) ** 0.5))))
        d.line([(x * scale + scale / 2, y * scale + scale / 2), (rx * scale + scale / 2, ry * scale + scale / 2)],
               fill=(70, 130, 200), width=width)


def _label(d: ImageDraw.ImageDraw, xy, text, f, fill=(255, 255, 255), outline=(20, 20, 20), anchor="mm"):
    d.text(xy, text, font=f, fill=fill, anchor=anchor, stroke_width=2, stroke_fill=outline)


def draw_landmarks(img: Image.Image, w: World, scale: int, labels: bool = True) -> None:
    d = ImageDraw.Draw(img)
    f = font(max(10, scale * 2))
    icon = {"volcano": (230, 80, 40), "trench": (40, 90, 200), "seacave": (60, 120, 220), "den": (60, 200, 110),
            "pillar": (60, 200, 110), "islands": (200, 220, 255), "whirlpool": (150, 200, 255),
            "tower": (240, 190, 60), "burn": (200, 150, 60), "station": (255, 230, 140),
            "summit": (240, 240, 240), "lake": (150, 210, 255), "marsh": (170, 220, 170), "dunes": (250, 220, 150),
            "deepwood": (140, 220, 140)}
    for lm in w.landmarks:
        cx, cy = lm.x * scale + scale / 2, lm.y * scale + scale / 2
        col = icon.get(lm.kind, (255, 255, 255))
        if lm.kind in ("burn", "den", "islands", "trench"):
            x0, y0, x1, y1 = lm.footprint
            d.rectangle([x0 * scale, y0 * scale, x1 * scale, y1 * scale], outline=col + (255,), width=1)
        r = scale * (1.3 if lm.kind != "station" else 0.8)
        if lm.kind == "volcano":
            d.polygon([(cx, cy - r * 1.4), (cx - r * 1.3, cy + r), (cx + r * 1.3, cy + r)], fill=col, outline=(30, 20, 20))
        elif lm.kind in ("whirlpool",):
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=2)
        else:
            d.rectangle([cx - r, cy - r, cx + r, cy + r] if lm.kind in ("tower", "pillar") else [cx - r, cy - r, cx + r, cy + r],
                        fill=col, outline=(20, 20, 20))
        if labels and lm.kind not in ("whirlpool", "station", "pillar"):
            _label(d, (cx, cy + r + scale * 1.6), lm.name, f, fill=col)


def draw_cities(img: Image.Image, w: World, scale: int) -> None:
    d = ImageDraw.Draw(img)
    f = font(max(11, int(scale * 2.6)))
    for c in w.cities:
        x0, y0, x1, y1 = c.rect
        d.rectangle([x0 * scale, y0 * scale, x1 * scale - 1, y1 * scale - 1], fill=(196, 92, 70), outline=(40, 20, 16), width=2)
        # roofs: a few blocks so it reads as a town
        rng = np.random.default_rng(len(c.id))
        for _ in range(int((x1 - x0) * (y1 - y0) / 6)):
            bx = rng.uniform(x0 + 0.6, x1 - 1.6) * scale
            by = rng.uniform(y0 + 0.6, y1 - 1.6) * scale
            d.rectangle([bx, by, bx + scale * 0.9, by + scale * 0.7], fill=(150, 64, 50))
        _label(d, ((x0 + x1) / 2 * scale, (y0 + y1) / 2 * scale), c.name, f)


def draw_routes(img: Image.Image, w: World, scale: int, roles: bool = True) -> None:
    if not w.routes:
        return
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    f = font(max(10, int(scale * 1.9)))
    fs = font(max(9, int(scale * 1.6)), bold=False)
    for r in w.routes:
        x0, y0, x1, y1 = r["rect"]
        col = ROLE_RGB.get(r["role"], (255, 255, 255))
        d.rectangle([x0 * scale, y0 * scale, x1 * scale - 1, y1 * scale - 1], fill=col + (34,), outline=col + (235,), width=2)
    for r in w.routes:
        pts = [(x * scale + scale / 2, y * scale + scale / 2) for x, y in r["path"]]
        if len(pts) > 1:
            d.line(pts, fill=(255, 255, 255, 235), width=max(2, scale // 2), joint="curve")
    for r in w.routes:
        for e in r["exits"]:
            ex, ey = e["cell"]
            d.rectangle([ex * scale, ey * scale, ex * scale + scale, ey * scale + scale], fill=(255, 255, 255, 255), outline=(20, 20, 20, 255))
        if r.get("goal"):
            gx, gy = r["goal"]
            d.ellipse([gx * scale - scale, gy * scale - scale, gx * scale + 2 * scale, gy * scale + 2 * scale],
                      outline=(255, 255, 255, 255), width=2)
    img.paste(ov, (0, 0), ov)
    d = ImageDraw.Draw(img)
    for r in w.routes:
        x0, y0, x1, y1 = r["rect"]
        col = ROLE_RGB.get(r["role"], (255, 255, 255))
        cx, cy = (x0 + x1) / 2 * scale, (y0 + y1) / 2 * scale
        _label(d, (cx, cy - scale * 1.2), r["name"], f)
        if roles:
            tag = r["role"].replace("_", " ")
            _label(d, (cx, cy + scale * 1.3), tag, fs, fill=col)


def title_bar(img: Image.Image, text: str, sub: str = "") -> Image.Image:
    f, fs = font(22), font(14, bold=False)
    out = Image.new("RGB", (img.width, img.height + 46), (24, 26, 30))
    out.paste(img, (0, 46))
    d = ImageDraw.Draw(out)
    d.text((12, 8), text, font=f, fill=(240, 240, 240))
    if sub:
        d.text((12 + d.textlength(text, font=f) + 14, 14), sub, font=fs, fill=(170, 176, 186))
    return out


def legend(w: World, width: int) -> Image.Image:
    f = font(13, bold=False)
    used = [k for k in BIOME_RGB if (w.biome == k).any()]
    from .world import BIOME_NAMES

    items = [(BIOME_RGB[k], BIOME_NAMES[k]) for k in used]
    items += [(ROLE_RGB[k], "route: " + k.replace("_", " ")) for k in ROLE_RGB if any(r["role"] == k for r in w.routes)]
    cols = max(1, width // 170)
    rows = (len(items) + cols - 1) // cols
    img = Image.new("RGB", (width, rows * 22 + 12), (24, 26, 30))
    d = ImageDraw.Draw(img)
    for i, (rgb, name) in enumerate(items):
        cx, cy = 12 + (i % cols) * 170, 8 + (i // cols) * 22
        d.rectangle([cx, cy, cx + 14, cy + 14], fill=rgb, outline=(0, 0, 0))
        d.text((cx + 20, cy), name, font=f, fill=(220, 220, 220))
    return img


def render_region(w: World, scale: int = 7, stage: str = "routes") -> Image.Image:
    """Full map. ``stage`` limits what is drawn (for the strip)."""
    mode = "height" if stage in ("terrain", "erosion") else "biome"
    img = base_image(w, mode, scale)
    if stage in ("erosion", "climate", "legends", "cities", "routes"):
        draw_rivers(img, w, scale)
    if stage in ("legends", "cities", "routes"):
        draw_landmarks(img, w, scale)
    if stage in ("routes",):
        draw_routes(img, w, scale)
    if stage in ("cities", "routes"):
        draw_cities(img, w, scale)
    return img


def stage_strip(w: World, out: Path, cols: int = 3) -> Image.Image:
    tiles = []
    for title, im in w.stages:
        tiles.append(title_bar(im, title))
    if not tiles:
        return Image.new("RGB", (10, 10))
    tw, th = tiles[0].size
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (tw * cols + 8 * (cols - 1), th * rows + 8 * (rows - 1)), (12, 12, 14))
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % cols) * (tw + 8), (i // cols) * (th + 8)))
    sheet.save(out)
    return sheet
