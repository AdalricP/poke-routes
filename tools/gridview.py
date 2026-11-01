"""Crop a region of a sprite sheet and overlay a labelled grid (for picking sprite rects)."""
import sys
from PIL import Image, ImageDraw

path, x0, y0, w, h = sys.argv[1], *map(int, sys.argv[2:6])
cell = int(sys.argv[6]) if len(sys.argv) > 6 else 32
out = sys.argv[7] if len(sys.argv) > 7 else "/tmp/grid.png"
scale = 2
im = Image.open(path).convert("RGBA").crop((x0, y0, x0 + w, y0 + h))
bg = Image.new("RGBA", im.size, (255, 0, 255, 255))
bg.alpha_composite(im)
bg = bg.resize((w * scale, h * scale), Image.NEAREST).convert("RGB")
d = ImageDraw.Draw(bg)
for gx in range(0, w + 1, cell):
    d.line([(gx * scale, 0), (gx * scale, h * scale)], fill=(0, 255, 255), width=1)
for gy in range(0, h + 1, cell):
    d.line([(0, gy * scale), (w * scale, gy * scale)], fill=(0, 255, 255), width=1)
for gy in range(0, h, cell):
    for gx in range(0, w, cell):
        d.text((gx * scale + 2, gy * scale + 1), f"{(x0+gx)//cell},{(y0+gy)//cell}", fill=(255, 255, 0))
bg.save(out)
