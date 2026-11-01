"""Find individual sprites on a loose sprite sheet via connected alpha components."""
import json
import sys

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage


def components(path, min_px=300, merge=0):
    a = np.array(Image.open(path).convert("RGBA"))[..., 3] > 16
    grown = ndimage.binary_dilation(a, iterations=merge) if merge else a
    lab, n = ndimage.label(grown)
    out = []
    for i, sl in enumerate(ndimage.find_objects(lab)):
        ys, xs = sl
        if (a[sl] & (lab[sl] == i + 1)).sum() < min_px:
            continue
        # shrink back to real pixels
        sub = a[sl] & (lab[sl] == i + 1)
        yy, xx = np.nonzero(sub)
        out.append([int(xs.start + xx.min()), int(ys.start + yy.min()), int(xx.max() - xx.min() + 1), int(yy.max() - yy.min() + 1)])
    return sorted(out, key=lambda r: (r[1] // 40, r[0]))


if __name__ == "__main__":
    path, out_png = sys.argv[1], sys.argv[2]
    comps = components(path)
    im = Image.open(path).convert("RGBA")
    bg = Image.new("RGBA", im.size, (40, 40, 40, 255))
    bg.alpha_composite(im)
    d = ImageDraw.Draw(bg)
    for i, (x, y, w, h) in enumerate(comps):
        d.rectangle([x, y, x + w - 1, y + h - 1], outline=(0, 255, 255))
        d.text((x + 1, y + 1), str(i), fill=(255, 255, 0))
    bg.convert("RGB").save(out_png)
    print(json.dumps(comps))
