"""Command line entry point: ``python -m pokeroutes <command>``."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EMERALD = ROOT / "vendor" / "pokeemerald"
MODELS = Path(__file__).resolve().parent / "models"


def cmd_generate(a) -> None:
    from .spec import RouteSpec

    spec = RouteSpec.load(a.spec)
    if a.seed is not None:
        spec.seed = a.seed
    _generate_and_write(spec, a)


def _generate_and_write(spec, a, route=None, offsets=None):
    from .autotile import Autotiler
    from .export import debug_image, report, seal_edges, stage_sheet, write_emerald, write_scene
    from .generate import generate
    from .tileset import load_pair

    if route is None:
        route = generate(spec)
        seal_edges(route)
    out = Path(a.out or ROOT / "out" / spec.id)
    out.mkdir(parents=True, exist_ok=True)
    write_scene(route, out / "scene.json")
    debug_image(route).save(out / "plan.png")
    if a.stages:
        stage_sheet(route).save(out / "stages.png")
    primary, secondary = spec.tileset.split("_", 1)
    model = MODELS / f"{spec.tileset}.json"
    if getattr(a, "tileset", None) == "ark":
        from .ruletile import ArkTiler, tile_route, write_ark_emerald

        tiler = ArkTiler.load()
        layout = tile_route(tiler, route)
        write_ark_emerald(route, layout, out / "emerald", tiler, offsets=offsets)
        layout.render(tiler.pair()).save(out / "emerald.png")
    elif model.exists() and EMERALD.exists():
        tiler = Autotiler.load(model)
        layout = tiler.tile(route.cls, seed=spec.seed)
        write_emerald(route, layout, out / "emerald", primary, secondary, offsets=offsets,
                      borders=tiler.m.get("borders"))
        layout.render(load_pair(EMERALD / "data/tilesets", primary, secondary)).save(out / "emerald.png")
    rep = report(route)
    (out / "report.json").write_text(json.dumps(rep, indent=2))
    print(f"{spec.name}: {rep['size'][0]}x{rep['size'][1]}, {rep['walkable_cells']} walkable, "
          f"{rep['reachable_fraction'] * 100:.0f}% reachable, specials: {', '.join(rep['special_areas']) or 'none'}")
    print(f"-> {out}")
    return route


def cmd_region(a) -> None:
    """Generate several routes as one region: special areas never repeat, and
    connections between routes in the set get offsets that line their exits up."""
    from .export import region_offsets, seal_edges
    from .generate import generate
    from .spec import RouteSpec

    used: list = []
    specs, routes = [], []
    for path in a.specs:
        spec = RouteSpec.load(path)
        spec.exclude_areas = list(used)
        route = generate(spec)
        seal_edges(route)
        used += [z.meta["area"]["id"] for z in route.zones if z.kind == "special" and "area" in z.meta]
        specs.append(spec)
        routes.append(route)
    offs = region_offsets(routes)
    for i, (spec, route) in enumerate(zip(specs, routes)):
        a2 = argparse.Namespace(out=None, stages=a.stages, tileset=a.tileset)
        _generate_and_write(spec, a2, route=route, offsets={k: v for (r, k), v in offs.items() if r == i})
        if a.viewer:
            dst = ROOT / "viewer" / "scenes" / f"{spec.id}.json"
            dst.write_bytes((ROOT / "out" / spec.id / "scene.json").read_bytes())


def cmd_learn(a) -> None:
    from .learn import learn, learn_biomes

    if a.biomes:
        learn_biomes(EMERALD, MODELS, only=a.only or None)
        return
    if not (a.primary and a.secondary):
        raise SystemExit("learn: give PRIMARY SECONDARY, or --biomes")
    m = learn(EMERALD, a.primary, a.secondary, MODELS / f"{a.primary}_{a.secondary}.json",
              layouts=a.layouts or None, weight=a.weight)
    print(f"learned from {m['layouts']} layouts ({len(m['pooled_layouts'])} pooled): {len(m['ctx9'])} neighbourhoods")


def cmd_atlas(a) -> None:
    from .tileset import load_pair

    pair = load_pair(EMERALD / "data/tilesets", a.primary, a.secondary)
    pair.atlas("primary").save(f"atlas_{a.primary}.png")
    pair.atlas("secondary").save(f"atlas_{a.secondary}.png")


def main() -> None:
    p = argparse.ArgumentParser(prog="pokeroutes")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="generate a route from a YAML spec")
    g.add_argument("spec")
    g.add_argument("-o", "--out")
    g.add_argument("--seed", type=int)
    g.add_argument("--stages", action="store_true", help="also write a per-stage image")
    g.add_argument("--tileset", choices=["learned", "ark"], default="learned",
                   help="ark: tile with the Arklarna outdoor tilesets (rule-based, pokeroutes.ruletile)")
    g.set_defaults(fn=cmd_generate)
    l = sub.add_parser("learn", help="learn autotiling from the pokeemerald map corpus")
    l.add_argument("primary", nargs="?")
    l.add_argument("secondary", nargs="?")
    l.add_argument("--layouts", nargs="+", help="pool only these layouts (names from layouts.json) for secondary tiles")
    l.add_argument("--weight", type=int, default=3, help="weight of pooled layouts vs other same-primary maps")
    l.add_argument("--biomes", action="store_true", help="train every biome model and write models/index.json")
    l.add_argument("--only", nargs="+", help="with --biomes: just these biomes")
    l.set_defaults(fn=cmd_learn)
    r = sub.add_parser("region", help="generate several routes with no repeated special areas")
    r.add_argument("specs", nargs="+")
    r.add_argument("--stages", action="store_true")
    r.add_argument("--viewer", action="store_true", help="copy scenes into viewer/scenes")
    r.add_argument("--tileset", choices=["learned", "ark"], default="learned")
    r.set_defaults(fn=cmd_region)
    t = sub.add_parser("atlas", help="dump labelled metatile sheets")
    t.add_argument("primary")
    t.add_argument("secondary")
    t.set_defaults(fn=cmd_atlas)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
