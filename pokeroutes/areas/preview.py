"""Preview harness: build special areas on a blank patch of their terrain,
autotile, render with real tiles, and draw the overworld Pokémon / people /
objects on top with the expansion's own sprites.

    python3 -m pokeroutes.areas.preview thunderstruck_oak blind_cellar --out sheet.png
    python3 -m pokeroutes.areas.preview --all --out-dir out/areas
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import build, connect, contexts, get, load_areas, requirements
from .rom import species_const

ROOT = Path(__file__).resolve().parents[2]
ROM = Path("/Users/aryan/Desktop/experiments/arklarna/rom")
TILESETS = ROM / "data/tilesets"
MODELS = [Path(__file__).resolve().parent / "models", ROOT / "pokeroutes/models"]

_tilers, _pairs, _sprites = {}, {}, {}


def model_path(tileset: str) -> Optional[Path]:
    for d in MODELS[::-1]:  # the shared models dir wins if it has one
        idx = d / "index.json"
        if idx.exists():
            try:
                ix = json.loads(idx.read_text())
                ent = ix.get(tileset) if isinstance(ix, dict) else None
                if isinstance(ent, str) and (d / ent).exists():
                    return d / ent
                if isinstance(ent, dict) and ent.get("file") and (d / ent["file"]).exists():
                    return d / ent["file"]
            except Exception:
                pass
        if (d / f"{tileset}.json").exists():
            return d / f"{tileset}.json"
    return None


def tiler(tileset: str):
    from ..autotile import Autotiler

    if tileset not in _tilers:
        p = model_path(tileset)
        _tilers[tileset] = Autotiler.load(p) if p else None
    return _tilers[tileset]


def pair(tileset: str):
    from ..tileset import load_pair

    if tileset not in _pairs:
        a, _, b = tileset.partition("_")
        _pairs[tileset] = load_pair(TILESETS, a, b)
    return _pairs[tileset]


def fallback_classes(cls: np.ndarray, at) -> np.ndarray:
    """Swap classes the model has (almost) never seen for their FALLBACK."""
    try:
        from ..classify import FALLBACK
    except Exception:
        FALLBACK = {}
    seen = {}
    for k, v in at.ctx5.items():
        seen[k[0]] = seen.get(k[0], 0) + sum(v.values())
    out = cls.copy()
    for c in set(out.ravel()):
        cur, hops = c, 0
        while seen.get(cur, 0) < 20 and cur in FALLBACK and hops < 4:
            cur = FALLBACK[cur]
            hops += 1
        if cur != c:
            out[out == c] = cur
    return out


def tile_and_render(cls: np.ndarray, fixed: dict, tileset: str, seed: int = 0) -> Image.Image:
    at = tiler(tileset)
    if at is None:
        raise SystemExit(f"no autotile model for {tileset}")
    g = fallback_classes(cls, at)
    try:
        from webui.fasttile import FastTiler  # type: ignore

        arr = np.full(g.shape, -1, dtype=np.int64)
        for (y, x), m in (fixed or {}).items():
            arr[y, x] = m
        layout = FastTiler(at).tile(g, seed=seed, fixed=arr)
    except Exception:
        layout = at.tile(g, seed=seed, fixed=fixed)
    return layout.render(pair(tileset))


# ---------------------------------------------------------------- sprites

def _jasc(path: Path):
    lines = path.read_text().split()
    n = int(lines[2])
    vals = [int(v) for v in lines[3:3 + n * 3]]
    return [tuple(vals[i:i + 3]) for i in range(0, len(vals), 3)]


def mon_sprite(species: str, facing: str = "down") -> Optional[Image.Image]:
    key = (species, facing)
    if key in _sprites:
        return _sprites[key]
    d = ROM / "graphics/pokemon" / species_const(species).lower()
    im = None
    if (d / "overworld.png").exists():
        src = Image.open(d / "overworld.png")
        pal = _jasc(d / "overworld_normal.pal") if (d / "overworld_normal.pal").exists() else None
        fs = src.height
        idx = {"down": 0, "up": 1, "left": 2, "right": 2}[facing]
        fr = src.crop((idx * fs, 0, idx * fs + fs, fs))
        a = np.array(fr)
        if pal:
            rgb = np.array(pal + [(0, 0, 0)] * (16 - len(pal)), dtype=np.uint8)[a & 15]
        else:
            rgb = np.array(fr.convert("RGB"))
        alpha = np.where((a & 15) == 0, 0, 255).astype(np.uint8)
        im = Image.fromarray(np.dstack([rgb, alpha]), "RGBA")
        if facing == "right":
            im = im.transpose(Image.FLIP_LEFT_RIGHT)
    elif (d / "front.png").exists() or (d / "anim_front.png").exists():
        f = d / ("front.png" if (d / "front.png").exists() else "anim_front.png")
        src = Image.open(f).convert("RGBA")
        src = src.crop((0, 0, 64, 64)).resize((32, 32), Image.NEAREST)
        im = src
    _sprites[key] = im
    return im


def people_sprite(gfx: str) -> Optional[Image.Image]:
    name = gfx.replace("OBJ_EVENT_GFX_", "").lower()
    for sub in ("people", "misc"):
        f = ROM / "graphics/object_events/pics" / sub / f"{name}.png"
        if f.exists():
            src = Image.open(f)
            fw = 16 if src.width % 16 == 0 and src.height >= 32 and sub == "people" else min(src.width, src.height)
            fh = src.height
            fr = src.crop((0, 0, fw, fh))
            a = np.array(fr)
            rgb = np.array(fr.convert("RGB"))
            if fr.mode == "P":
                alpha = np.where(a == 0, 0, 255).astype(np.uint8)
            else:
                alpha = np.full(a.shape[:2], 255, np.uint8)
            return Image.fromarray(np.dstack([rgb, alpha]), "RGBA")
    return None


FACING = {"UP": "up", "DOWN": "down", "LEFT": "left", "RIGHT": "right"}


def _facing(mtype: str) -> str:
    for k, v in FACING.items():
        if mtype.endswith(k):
            return v
    return "down"


def overlay(im: Image.Image, objs: List[dict], offset=(0, 0), scale: int = 1) -> Image.Image:
    im = im.convert("RGBA")
    oy, ox = offset
    for o in sorted(objs, key=lambda o: o["y"]):
        g = o["graphics_id"]
        y, x = o["y"] - oy, o["x"] - ox
        if g.startswith("OBJ_EVENT_GFX_SPECIES"):
            sp = g[g.index("(") + 1:-1]
            spr = mon_sprite(sp, _facing(o["movement_type"]))
        else:
            spr = people_sprite(g)
        if spr is None:
            continue
        sx = x * 16 + 8 - spr.width // 2
        sy = y * 16 + 16 - spr.height
        if spr.height <= 16:
            sy = y * 16
        im.alpha_composite(spr, (int(sx), int(sy)))
    return im


# ---------------------------------------------------------------- build on canvas

class _Zone:
    def __init__(self, kind, cells):
        self.kind, self.cells, self.name, self.meta = kind, cells, kind, {}


def preview_area(area: dict, seed: int = 1, margin: int = 4):
    req = requirements(area)
    w, h = req["footprint"]
    W, H = w + 2 * margin + 2, h + margin + 6
    rng = np.random.default_rng(seed)
    cls, lane = contexts.canvas(area["context"], W, H, rng)
    route = SimpleNamespace(cls=cls, rng=rng, protect=np.zeros(cls.shape, bool), zones=[_Zone("main", lane)],
                            spec=SimpleNamespace(tileset="general_fortree", weather="WEATHER_SUNNY", biome=area["biomes"][0] if area["biomes"] else "forest",
                                                 species_file="", exclude_areas=[]), critical_path=lane)
    for c in lane:
        route.protect[c] = True
    origin = (2, margin + 1)
    res = build(route, area, origin)
    connect(route, res)
    return route, res


def render_area(area: dict, seed: int = 1, scale: int = 2) -> Image.Image:
    route, res = preview_area(area, seed)
    im = tile_and_render(route.cls, res.fixed, "general_fortree", seed)
    im = res.paint_custom(im)
    im = overlay(im, res.object_events)
    panels = [im]
    if res.submap is not None:
        sm = res.submap
        sim = tile_and_render(sm.cls, sm.fixed, sm.tileset, seed)
        if sm.custom:
            from . import art
            sim = art.paint(sim, sm.custom)
        sim = overlay(sim, sm.object_events)
        panels.append(sim)
    return _frame(area, res, panels, scale)


def _font(sz: int):
    for f in ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(f, sz)
        except Exception:
            pass
    return ImageFont.load_default()


def _wrap(d, text, font, width):
    words, lines, cur = text.split(), [], ""
    for w_ in words:
        t = f"{cur} {w_}".strip()
        if d.textlength(t, font=font) > width:
            lines.append(cur)
            cur = w_
        else:
            cur = t
    if cur:
        lines.append(cur)
    return lines


def _frame(area, res, panels, scale):
    panels = [p.resize((p.width * scale, p.height * scale), Image.NEAREST) for p in panels]
    gap = 10
    pw = sum(p.width for p in panels) + gap * (len(panels) - 1)
    ph = max(p.height for p in panels)
    W = max(pw, 520) + 24
    f1, f2 = _font(18), _font(13)
    tmp = Image.new("RGB", (10, 10))
    d0 = ImageDraw.Draw(tmp)
    rec = area.get("recipe", {})
    mons = ", ".join(sorted({f"{m['species']}" for m in res.mons + (res.submap.mons if res.submap else [])}))
    lines = [(f1, f"{area['name']}  ({area['species']})"),
             (f2, f"{area.get('kind')} · context: {area['context']} · {res.activity}" + (f" · inside: {res.submap.interior}" if res.submap else "")),
             (f2, "Pokémon: " + mons)]
    why = rec.get("ecology", "")
    body = _wrap(d0, why, f2, W - 24)[:4]
    th = 12 + sum(26 if f is f1 else 18 for f, _ in lines) + 18 * len(body) + 8
    out = Image.new("RGB", (W, th + ph + 12), (24, 22, 20))
    d = ImageDraw.Draw(out)
    y = 10
    for f, t in lines:
        d.text((12, y), t, font=f, fill=(240, 230, 210) if f is f1 else (200, 190, 170))
        y += 26 if f is f1 else 18
    for t in body:
        d.text((12, y), t, font=f2, fill=(160, 170, 150))
        y += 18
    x = 12
    for p in panels:
        out.paste(p, (x, th))
        x += p.width + gap
    return out


def contact_sheet(images: List[Image.Image], cols: int = 3, pad: int = 12) -> Image.Image:
    rows = [images[i:i + cols] for i in range(0, len(images), cols)]
    cw = max(i.width for i in images)
    rh = [max(i.height for i in r) for r in rows]
    sheet = Image.new("RGB", (cols * cw + (cols + 1) * pad, sum(rh) + (len(rows) + 1) * pad), (12, 12, 12))
    y = pad
    for r, h in zip(rows, rh):
        x = pad
        for im in r:
            sheet.paste(im, (x, y))
            x += cw + pad
        y += h + pad
    return sheet


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--cols", type=int, default=3)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--scale", type=int, default=2)
    a = ap.parse_args(argv)
    areas = load_areas() if a.all else [get(i) for i in a.ids]
    missing = [i for i, ar in zip(a.ids, areas) if ar is None]
    if missing:
        raise SystemExit(f"no recipe for: {', '.join(missing)}")
    ims = []
    for ar in areas:
        try:
            im = render_area(ar, a.seed, a.scale)
        except Exception as e:  # keep going: one broken recipe shouldn't hide the rest
            import traceback

            traceback.print_exc()
            print(f"{ar['id']}: FAILED {e}")
            continue
        ims.append(im)
        if a.out_dir:
            Path(a.out_dir).mkdir(parents=True, exist_ok=True)
            im.save(Path(a.out_dir) / f"{ar['id']}.png")
        print(f"{ar['id']}: ok")
    if a.out and ims:
        contact_sheet(ims, a.cols).save(a.out)
        print(f"-> {a.out}")


if __name__ == "__main__":
    main()
