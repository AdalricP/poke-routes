"""``python3 -m pokeroutes.region generate --seed N``"""
from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "region_out"


def generate(seed: int, out: Path = None, strip: bool = True, verbose: bool = True):
    from .export import write_all
    from .pipeline import build
    from .render import legend, render_region, stage_strip, title_bar

    t = time.time()
    w = build(seed, routes=True, strip=strip)
    out = Path(out or OUT / str(seed))
    out.mkdir(parents=True, exist_ok=True)
    paths = write_all(w, out)
    img = render_region(w, 7, "routes")
    sub = f"seed {seed}  |  {len(w.cities)} towns, {len(w.routes)} routes, {len(w.landmarks)} landmarks"
    full = title_bar(img, "Arklarna (provisional)", sub)
    leg = legend(w, full.width)
    from PIL import Image

    sheet = Image.new("RGB", (full.width, full.height + leg.height), (24, 26, 30))
    sheet.paste(full, (0, 0))
    sheet.paste(leg, (0, full.height))
    sheet.save(out / "region.png")
    render_region(w, 4, "routes").save(out / "region_map.png")  # bare map for the web page
    if strip:
        stage_strip(w, out / "stages.png")
    if verbose:
        for line in w.log:
            print(" ", line)
        print(f"region seed {seed}: {len(w.routes)} routes -> {out} ({time.time() - t:.1f}s)")
    return w, out, paths


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="python3 -m pokeroutes.region")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="generate the region: terrain, legendaries, cities, routes, specs")
    g.add_argument("--seed", type=int, default=1)
    g.add_argument("-o", "--out", help="output folder (default region_out/<seed>)")
    g.add_argument("--no-strip", action="store_true", help="skip the per-stage strip (faster)")
    g.add_argument("--screens", help="also copy region.png / stages.png here as region-<seed>-*.png")
    a = p.parse_args(argv)
    if a.cmd == "generate":
        w, out, _ = generate(a.seed, a.out, strip=not a.no_strip)
        if a.screens:
            d = Path(a.screens)
            d.mkdir(parents=True, exist_ok=True)
            shutil.copy(out / "region.png", d / f"region-{a.seed}-map.png")
            if (out / "stages.png").exists():
                shutil.copy(out / "stages.png", d / f"region-{a.seed}-stages.png")
            print(f"  screens -> {d}")
