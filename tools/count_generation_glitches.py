"""Count generation glitches across a fixed set of routes and region chain maps.

    python3 tools/count_generation_glitches.py [--quick] [--renders out/glitches]

Counts clipped tree crowns (crown art that has nowhere to go), bridges that are not
one straight span over water from bank to bank, river ends the player can walk up to,
islets smaller than 2x2, signs standing in water and water notches at the map edges.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pokeroutes.glitches import glitches_of_route  # noqa: E402

BIOMES_OF_SINGLE_ROUTES = ("forest", "meadow", "beach", "mountain", "riverland")
SEEDS_OF_SINGLE_ROUTES = (0, 1, 2, 3)
QUICK_SEEDS_OF_SINGLE_ROUTES = (0, 1)
SIZE_OF_SINGLE_ROUTES_IN_METATILES = [40, 56]
REGION_SEEDS = (1, 2, 3, 4)
QUICK_REGION_SEEDS = (1, 2)
LAKES_ON_SINGLE_ROUTES = 2


def single_route_specs(seeds):
    from pokeroutes.spec import Exit, RouteSpec

    route_specs = [RouteSpec(name=f"{biome} {seed}", id=f"{biome}_{seed}", size=list(SIZE_OF_SINGLE_ROUTES_IN_METATILES),
                             seed=seed, biome=biome, lakes=LAKES_ON_SINGLE_ROUTES,
                             exits=[Exit("south", 0.5, 3), Exit("north", 0.5, 3)])
                   for biome in BIOMES_OF_SINGLE_ROUTES for seed in seeds]
    return route_specs


def chain_map_specs_of_region_seed(region_seed: int):
    from pokeroutes.region import edit
    from pokeroutes.region.export import chain_map_specs
    from pokeroutes.region.seam_check import route_spec_object_from_dict

    region_document = edit.generate_map(edit.generate_doc(region_seed))
    route_specs = [route_spec_object_from_dict(spec_dict) for spec_dict in chain_map_specs(edit.doc_to_world(region_document))]
    for route_spec in route_specs:
        route_spec.id = f"region{region_seed}_{route_spec.id}"
    return route_specs


def generated_sealed_route(route_spec):
    from pokeroutes.export import seal_edges
    from pokeroutes.generate import generate

    route = generate(route_spec)
    seal_edges(route)
    return route


def save_render(route, tiler, renders_directory: Path) -> None:
    from pokeroutes.ruletile import tile_route

    renders_directory.mkdir(parents=True, exist_ok=True)
    tile_route(tiler, route).render(tiler.pair()).save(renders_directory / f"{route.spec.id}.png")


def main() -> None:
    from pokeroutes.ruletile import ArkTiler

    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="fewer seeds")
    parser.add_argument("--renders", help="also save each map's PokeRoutes render in this directory")
    parser.add_argument("--verbose", action="store_true", help="list every glitch")
    arguments = parser.parse_args()
    single_seeds = QUICK_SEEDS_OF_SINGLE_ROUTES if arguments.quick else SEEDS_OF_SINGLE_ROUTES
    region_seeds = QUICK_REGION_SEEDS if arguments.quick else REGION_SEEDS
    route_specs = single_route_specs(single_seeds) + [route_spec for region_seed in region_seeds
                                                      for route_spec in chain_map_specs_of_region_seed(region_seed)]
    tiler = ArkTiler.load()
    totals = Counter()
    for route_spec in route_specs:
        route = generated_sealed_route(route_spec)
        glitches = glitches_of_route(route, tiler)
        totals.update({kind: len(cells) for kind, cells in glitches.items()})
        if arguments.renders:
            save_render(route, tiler, Path(arguments.renders))
        if arguments.verbose and any(glitches.values()):
            print(route_spec.id, {kind: cells[:6] for kind, cells in glitches.items() if cells})
    print(f"{len(route_specs)} maps")
    for kind in sorted(totals):
        print(f"{kind:24} {totals[kind]}")


if __name__ == "__main__":
    main()
