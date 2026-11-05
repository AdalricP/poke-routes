"""Learn autotiling statistics from the real pokeemerald map corpus.

For a target tileset pair we walk every layout that shares its primary tileset
and record, for each cell:
  * the semantic class of its 3x3 neighbourhood -> which metatile sits there
  * which metatile sits to its left / above (pairwise adjacency)
  * how often the metatile is blocked, and its usual elevation
The result is a small JSON model the autotiler uses to turn a class grid into
metatiles that join the way Game Freak's hand-made maps do.

Secondary-tileset cells only count from the *pooled* layouts (by default every
layout using the target secondary; pass ``layouts`` to pick a few, e.g. just the
desert part of the corpus). Primary-tileset cells count from every layout with
the same primary, but cells in pooled layouts weigh ``weight`` times more, so a
beach model prefers how beaches use the shared General tiles.

:data:`BIOMES` lists the models trained for the generator's biomes;
:func:`learn_biomes` trains them all and writes ``models/index.json``.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, Optional, Sequence

import numpy as np

from .classify import OTHER, classify_cell, classify_pair
from .layout import Layout
from .tileset import NUM_PRIMARY_METATILES, load_pair

UNKNOWN = "?"
MIXED = (0.1, 0.9)  # blocked ratio band in which a metatile gets a class per cell

# biome -> secondary tileset, pooled layouts, pooled weight (default 10).
# Model file: general_<secondary>.json
BIOMES: Dict[str, dict] = {
    "beach": {"secondary": "slateport",
              "layouts": ["Route109_Layout", "Route108_Layout", "SlateportCity_Layout"],
              "note": "sand beaches, shoreline, sea rocks (Route 109 / 108)"},
    "sea": {"secondary": "dewford",
            "layouts": ["Route105_Layout", "Route106_Layout", "Route107_Layout", "DewfordTown_Layout"],
            "note": "open sea with small sandy islands and grass (Routes 105-107)"},
    "ocean_currents": {"secondary": "pacifidlog",
                       "layouts": ["Route131_Layout", "Route132_Layout", "Route133_Layout",
                                   "Route134_Layout", "PacifidlogTown_Layout"],
                       "note": "deep sea, shoals, floating-log bridges (Routes 131-134)"},
    "desert": {"secondary": "mauville",
               "layouts": ["Route111_Layout", "Route110_Layout"],
               "note": "Route 111: deep sand, boulders, rock walls; Route 110 adds its wooden river bridge"},
    "mountain": {"secondary": "lavaridge",
                 "layouts": ["Route112_Layout", "JaggedPass_Layout", "MtChimney_Layout",
                             "LavaridgeTown_Layout"],
                 "note": "Route 112, Jagged Pass, Mt. Chimney: tiers, cliffs, stairs, lava"},
    "volcanic": {"secondary": "fallarbor",
                 "layouts": ["Route113_Layout", "FallarborTown_Layout"],
                 "weight": 30,  # other maps' walkable pink mountain floor would swamp the ash field
                 "note": "Route 113: ash grass, ash ground, grey ash cliffs"},
}
# biomes served by models that were trained before (not retrained here)
EXISTING = {"forest": "general_fortree", "meadow": "general_fortree", "riverland": "general_fortree"}


def _tileset_dir(name: str) -> str:
    # gTileset_General -> general, gTileset_SecretBaseBrownCave -> secret_base/brown_cave (rare)
    base = name.replace("gTileset_", "")
    return re.sub(r"(?<!^)(?=[A-Z])", "_", base).lower()


def corpus_layouts(emerald: Path, primary: str):
    data = json.loads((emerald / "data/layouts/layouts.json").read_text())
    for l in data["layouts"]:
        if l.get("primary_tileset") != f"gTileset_{primary.capitalize()}":
            continue
        bd = emerald / l["blockdata_filepath"]
        if not bd.exists():
            continue
        raw = bd.read_bytes()[: l["width"] * l["height"] * 2]
        if len(raw) != l["width"] * l["height"] * 2:
            continue
        yield l, Layout.from_blockdata(raw, l["width"])


def neighbourhood(cls: np.ndarray, y: int, x: int) -> str:
    h, w = cls.shape
    out = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            yy = min(max(y + dy, 0), h - 1)
            xx = min(max(x + dx, 0), w - 1)
            out.append(cls[yy, xx])
    return "".join(out)


def cross(n9: str) -> str:
    """Centre + N/W/E/S from a 9-char neighbourhood key."""
    return n9[4] + n9[1] + n9[3] + n9[5] + n9[7]


def learn(emerald: Path, primary: str, secondary: str, out: Path,
          layouts: Optional[Sequence[str]] = None, weight: int = 3, biome: Optional[str] = None) -> dict:
    emerald = Path(emerald)
    tiles_root = emerald / "data/tilesets"
    target = load_pair(tiles_root, primary, secondary)
    corpus = list(corpus_layouts(emerald, primary))
    if layouts:
        wanted = set(layouts)
        missing = wanted - {m["name"] for m, _ in corpus}
        if missing:
            raise ValueError(f"unknown layouts: {sorted(missing)}")
        bad = [m["name"] for m, _ in corpus if m["name"] in wanted and _tileset_dir(m["secondary_tileset"]) != secondary]
        if bad:
            raise ValueError(f"layouts {bad} do not use secondary tileset {secondary!r}")
        pooled = lambda meta: meta["name"] in wanted  # noqa: E731
    else:
        pooled = lambda meta: _tileset_dir(meta["secondary_tileset"]) == secondary  # noqa: E731

    def valid_mask(meta, mt):
        return (mt < NUM_PRIMARY_METATILES) | pooled(meta)

    # pass 1: blocked ratio, elevation and isolation per metatile.
    blocked = defaultdict(lambda: [0, 0])
    elev: Dict[int, Counter] = defaultdict(Counter)
    iso = defaultdict(lambda: [0, 0])
    for meta, L in corpus:
        mt = L.metatile.astype(int)
        valid = valid_mask(meta, mt)
        open_ = np.pad(L.collision == 0, 1, constant_values=False)
        nb_open = open_[:-2, 1:-1].astype(int) + open_[2:, 1:-1] + open_[1:-1, :-2] + open_[1:-1, 2:]
        nb_n = np.pad(np.ones_like(mt), 1)
        nb_cnt = nb_n[:-2, 1:-1] + nb_n[2:, 1:-1] + nb_n[1:-1, :-2] + nb_n[1:-1, 2:]
        for (y, x), m in np.ndenumerate(mt):
            if not valid[y, x]:
                continue
            blocked[m][0] += int(L.collision[y, x] != 0)
            blocked[m][1] += 1
            elev[m][int(L.elevation[y, x])] += 1
            iso[m][0] += int(nb_open[y, x])
            iso[m][1] += int(nb_cnt[y, x])
    ratio = {m: b / n for m, (b, n) in blocked.items() if n}
    isolation = {m: a / n for m, (a, n) in iso.items() if n}
    classes = classify_pair(target, ratio, isolation)
    # metatiles used both walkable and blocked get a class per (metatile, collision)
    cell_cls = {}
    for m, r in ratio.items():
        if MIXED[0] < r < MIXED[1]:
            pair_cls = (classify_cell(target, m, 0, isolation.get(m)), classify_cell(target, m, 1, isolation.get(m)))
            if pair_cls[0] != pair_cls[1]:
                cell_cls[m] = pair_cls

    ctx9: Dict[str, Counter] = defaultdict(Counter)
    ctx5: Dict[str, Counter] = defaultdict(Counter)
    adj_h: Dict[int, Counter] = defaultdict(Counter)
    adj_v: Dict[int, Counter] = defaultdict(Counter)
    cell_stats: Dict[str, list] = defaultdict(lambda: [0, 0, Counter()])  # "mid:cls" -> blocked, n, elev
    n_pooled = 0
    for meta, L in corpus:
        is_pooled = pooled(meta)
        n_pooled += is_pooled
        wgt = weight if is_pooled else 1
        mt = L.metatile.astype(int)
        valid = valid_mask(meta, mt)
        cls = np.full(mt.shape, UNKNOWN, dtype="<U1")
        for (y, x), m in np.ndenumerate(mt):
            if valid[y, x]:
                if m in cell_cls:
                    cls[y, x] = cell_cls[m][1 if L.collision[y, x] else 0]
                else:
                    cls[y, x] = classes.get(int(m), UNKNOWN)
        h, w = mt.shape
        for y in range(h):
            for x in range(w):
                if not valid[y, x] or cls[y, x] in (UNKNOWN, OTHER):
                    continue
                m = int(mt[y, x])
                n9 = neighbourhood(cls, y, x)
                ctx9[n9][m] += wgt
                ctx5[cross(n9)][m] += wgt
                st = cell_stats[f"{m}:{cls[y, x]}"]
                st[0] += int(L.collision[y, x] != 0)
                st[1] += 1
                st[2][int(L.elevation[y, x])] += 1
                if x > 0 and valid[y, x - 1]:
                    adj_h[int(mt[y, x - 1])][m] += wgt
                if y > 0 and valid[y - 1, x]:
                    adj_v[int(mt[y - 1, x])][m] += wgt

    # borders: the 2x2 block drawn outside the map. Keep the most common one per
    # semantic class (a forest route wants a tree border, a sea route water), so
    # the exporter can match the border to whatever fills the route's edge.
    borders: Dict[str, Counter] = defaultdict(Counter)
    data = json.loads((emerald / "data/layouts/layouts.json").read_text())
    for l in data["layouts"]:
        if l.get("primary_tileset") != f"gTileset_{primary.capitalize()}" or not (emerald / l["border_filepath"]).exists():
            continue
        same = pooled(l)
        vals = tuple(int(v) for v in np.frombuffer((emerald / l["border_filepath"]).read_bytes()[:8], dtype="<u2"))
        if len(vals) != 4 or not (same or all((v & 0x3FF) < NUM_PRIMARY_METATILES for v in vals)):
            continue
        kinds = {classes.get(v & 0x3FF, UNKNOWN) for v in vals}
        if len(kinds) == 1 and UNKNOWN not in kinds:
            borders[kinds.pop()][vals] += 3 if same else 1
    border_by_class = {k: list(c.most_common(1)[0][0]) for k, c in borders.items()}

    model = {
        "primary": primary,
        "secondary": secondary,
        "biome": biome,
        "pooled_layouts": sorted(m["name"] for m, _ in corpus if pooled(m)),
        "borders": border_by_class,
        "layouts": len(corpus),
        "classes": {str(k): v for k, v in classes.items()},
        "cell_classes": {str(k): list(v) for k, v in cell_cls.items()},
        "blocked": {str(k): round(v, 3) for k, v in ratio.items()},
        "elevation": {str(k): c.most_common(1)[0][0] for k, c in elev.items()},
        # per (metatile, class): collision and elevation to write when the
        # autotiler places that metatile for that class
        "cell": {k: [round(b / n, 3), e.most_common(1)[0][0]] for k, (b, n, e) in cell_stats.items()},
        "ctx9": {k: dict(v) for k, v in ctx9.items()},
        "ctx5": {k: dict(v) for k, v in ctx5.items()},
        "adj_h": {str(k): {str(a): b for a, b in v.items()} for k, v in adj_h.items()},
        "adj_v": {str(k): {str(a): b for a, b in v.items()} for k, v in adj_v.items()},
    }
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(model, separators=(",", ":")))
    return model


def learn_biomes(emerald: Path, models: Path, only: Optional[Iterable[str]] = None, log=print) -> dict:
    """Train one model per entry of :data:`BIOMES` and (re)write ``index.json``."""
    models = Path(models)
    index_path = models / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    for biome, name in EXISTING.items():
        if (models / f"{name}.json").exists():
            index.setdefault(biome, {"model": name, "tileset": name, "note": "trained earlier"})
    for biome, cfg in BIOMES.items():
        if only and biome not in only:
            continue
        name = f"general_{cfg['secondary']}"
        m = learn(emerald, "general", cfg["secondary"], models / f"{name}.json",
                  layouts=cfg["layouts"], weight=cfg.get("weight", 10), biome=biome)
        used = Counter()
        for key, cnt in m["ctx5"].items():
            used[key[0]] += sum(cnt.values())
        index[biome] = {"model": name, "tileset": name, "layouts": cfg["layouts"], "note": cfg["note"],
                        "classes": "".join(sorted(c for c, n in used.items() if n >= 20))}
        log(f"{biome:15s} -> {name}.json  classes {index[biome]['classes']}")
    index_path.write_text(json.dumps(index, indent=2))
    return index
