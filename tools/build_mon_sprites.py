"""Turn fetched indexed sprites into RGBA PNGs (normal + shiny) for the viewer."""
import json
import sys
from pathlib import Path

from PIL import Image

SRC = Path(sys.argv[1])
OUT = Path(sys.argv[2])


def pal(path):
    v = path.read_text().split()[3:]
    cols = [tuple(int(x) for x in v[i : i + 3]) for i in range(0, len(v) - 2, 3)][:16]
    return [c for col in cols for c in col]


def rgba(im, palette):
    im = im.copy()
    im.putpalette(palette + [0] * (768 - len(palette)))
    out = im.convert("RGBA")
    px = out.load()
    idx = im.load()
    for y in range(im.height):
        for x in range(im.width):
            if idx[x, y] == 0:
                px[x, y] = (0, 0, 0, 0)
    return out


index = {}
for d in sorted(SRC.iterdir()):
    if not d.is_dir():
        continue
    front = d / "anim_front.png" if (d / "anim_front.png").exists() else d / "front.png"
    if not front.exists() or not (d / "back.png").exists():
        continue
    fr = Image.open(front)
    if fr.mode != "P":
        continue
    fr = fr.crop((0, 0, 64, 64))
    bk = Image.open(d / "back.png").crop((0, 0, 64, 64))
    o = OUT / d.name
    o.mkdir(parents=True, exist_ok=True)
    np_ = pal(d / "normal.pal") if (d / "normal.pal").exists() else None
    sp_ = pal(d / "shiny.pal") if (d / "shiny.pal").exists() else np_
    for name, im in (("front", fr), ("back", bk)):
        rgba(im, np_ or im.getpalette()[:48]).save(o / f"{name}.png")
        rgba(im, sp_ or im.getpalette()[:48]).save(o / f"{name}_shiny.png")
    index[d.name] = True
(OUT / "index.json").write_text(json.dumps(sorted(index)))
print(len(index), "species")
