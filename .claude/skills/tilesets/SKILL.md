---
name: tilesets
description: How poke-routes turns class grids into metatiles and how to find, add, switch or apply a tileset or tiling model (learned models from pokeemerald layouts, the rule tiler for hand-made tilesets, bring-your-own-assets, credits). Load when a task mentions tilesets, tiles, art style, a tiling model, or "make it look like" a specific game.
---

# Tilesets and tilers

The generator never draws pixels itself. It produces a class grid (see the `codebase-map` skill for the codes). A tiler then maps that grid to metatile ids of a real tileset, and the PNG is rendered from the tileset's own `tiles.png`, palettes and `metatiles.bin` (`pokeroutes/tileset.py`, `load_pair(root/data/tilesets, primary, secondary)` → `primary/<name>`, `secondary/<name>`).

## Bring your own assets (read this first)

- No Pokémon art ships in this repo. The learned tiler reads tilesets and maps from the user's own pret/pokeemerald checkout in `vendor/pokeemerald` (`pokeroutes/cli.py`, `EMERALD`). If it is missing, routes still generate, but no `emerald.png` or tiled export is written.
- Any new tileset is the user's asset. Third-party art is allowed only from authors who released it for ROM hacks, downloaded from the author's own page under their stated terms.
- Keep a `CREDITS.md` next to the art with the author, source URL and licence or terms text.
- If the terms are unclear, or getting the art needs a login, an account, a purchase or accepting a terms page, **stop** and tell the user exactly what they need to do. Never pull assets out of other hacks' ROMs, and never download ROMs.
- Fetching the art file as a one-off is fine. Do not add network calls to the code.

## Learned tiler (Emerald-style tilesets that real maps already use)

- **Training**: `pokeroutes/learn.py`, `learn(emerald, primary, secondary, out, layouts=..., weight=...)`.
  - It reads every layout in `vendor/pokeemerald/data/layouts/layouts.json` whose primary tileset matches (`corpus_layouts`).
  - It classifies each cell from its metatile plus its collision bit (`classify.classify_cell`).
  - It counts 3x3 and 5-cell contexts and neighbour pairs.
  - `--layouts` pools specific maps for the secondary tileset, weighted by `--weight`.
- **Tiling**: `pokeroutes/autotile.py`, `Autotiler.tile`. It samples from a softmax over context counts plus a pair score (temperature 0.35, 3 sweeps). Classes with fewer than 20 corpus cells use `classify.FALLBACK`.
- **Models**: `pokeroutes/models/<primary>_<secondary>.json`. `models/index.json` maps biome → model.

  | Biome(s) | Model | Learned from |
  |---|---|---|
  | forest, meadow, riverland | general_fortree | |
  | beach | general_slateport | Routes 108 and 109 |
  | sea | general_dewford | Routes 105 to 107 |
  | ocean_currents | general_pacifidlog | |
  | desert | general_mauville | Route 111 |
  | mountain | general_lavaridge | Route 112, Jagged Pass |
  | volcanic | general_fallarbor | Route 113 |

- **Which model a route uses**: `spec.tileset`, which defaults to `spec.BIOME_TILESET[biome]`. The web UI checks the model exists (`webui/server.py`, `route_tileset`).
  - Volcanic deliberately maps to `general_lavaridge`, because the fallarbor model draws cliff interiors as brick.
- **Commands**:
  - `python3 -m pokeroutes learn --biomes [--only desert volcanic]` retrains the biome models and rewrites `index.json`.
  - `python3 -m pokeroutes learn PRIMARY SECONDARY --layouts Route119_Layout ...` writes `models/PRIMARY_SECONDARY.json`.
  - `python3 -m pokeroutes atlas PRIMARY SECONDARY` dumps labelled metatile sheets so you can look up ids.

### Switch a route to another learned tileset

1. Check the model exists (`tilers` tool, or `ls pokeroutes/models`). If not, train it with `learn PRIMARY SECONDARY --layouts <maps that use it>`.
2. Set the spec's `tileset: <primary>_<secondary>` (for example `general_mauville`), or the biome default in `BIOME_TILESET`. Then `generate_route` and look at the PNG.
3. A tileset only covers classes that its corpus maps contain (`index.json` has a `classes` field for each biome). Missing classes fall back, so check the render for wrong-looking art.

### Use a custom tileset that the user's own maps already use

Put it in a pokeemerald-format tree: `data/tilesets/{primary,secondary}/<name>/` (`tiles.png`, `palettes/`, `metatiles.bin`, `metatile_attributes.bin`), with the maps listed in `data/layouts/layouts.json`. Learning reads `vendor/pokeemerald`, so the tileset and maps have to be there, or `vendor/pokeemerald` has to point at that tree. Then train as above. You need at least a few maps that use the tileset; there is nothing to learn from an unused one.

## Rule tiler (a brand-new tileset that no map uses yet)

- **Name**: the user sees it as the **PokeRoutes** tileset (tiler id `ark`, ROM symbols `gTileset_ArkOutdoor` / `gTileset_ArkWoodland` unchanged).
- **Where it lives**: `pokeroutes/ruletile.py`, `ArkTiler`, with the newer terrain rules in `pokeroutes/ruletile_terrain.py` (bridges, sand shores, sideways falls, ledges over ground). It is driven by a class table JSON, `data/tilesets/metatile_classes_ark.json`, found inside a ROM tree located by `find_rom()` (the `ARK_ROM` env var, then fallback candidates).
- **Secondaries**: one primary (`ark_outdoor`) and several secondaries. `ark_woodland` (default: forests, meadows) plus the terrain secondaries the ROM builder adds (`tools/arklarna_tiles/terrain_secondaries.py`, listed under `secondary_tilesets` in the class table):

  | Secondary | Symbol | Picked for | Art (borrowed from the pret decomps, credited in each folder's `CREDITS.md`) |
  |---|---|---|---|
  | `ark_coast` | `gTileset_ArkCoast` | `beach`, open-sea routes (`region.mode: sea`) | FireRed sea in 3 depth bands + darker Dive water, FireRed sand with water and grass rims, Emerald Route 105-109 sea ridges and peak rocks, FireRed grey/brown sea rocks, Emerald Route 124 shallows as wet flats |
  | `ark_mountain` | `gTileset_ArkMountain` | `mountain` | FireRed Mt. Ember: rocky ground, slope rock, 2-row terrace faces, stairs, boulders |
  | `ark_snow` | `gTileset_ArkSnow` | `snow` | FireRed Icefall Cave: snow, snow plateaus ringed by ice ridges, ice, snow clumps, ice boulders, ladder |
  | `ark_desert` | `gTileset_ArkDesert` | `desert` | Emerald Route 111: sand, raised dunes, mesa rock, desert rocks |
  | `ark_cave` | `gTileset_ArkCave` | spec key `ark_secondary: ark_cave` (no cave biome yet) | FireRed Mt. Moon: floor, sandy floor, walls with the dark void, ladders, rocks |

  `ruletile_secondaries.secondary_for_spec` picks one (spec key `ark_secondary` wins, then open sea, then biome); `ArkTiler.load(secondary=...)` / `tiler.for_secondary(...)` gives a tiler for it, whose `c` is the primary's classes plus the secondary's (`class_table_for_secondary`). The tile editor overlay applies to the woodland pair only. Not there yet: cacti, dead shrubs, snow-covered trees, cave water, crystals; secondary water is not animated.
- **CLI and web UI**: `--tileset ark`, and Generate > Tiler in the web UI (`webui/shell_api.py`, `ark_tiler`).
- **Class table format** (read `ArkTiler.tile` and `_trees`):
  - `mask_bits`: the order of `N NE E SE S SW W NW` bits for blob masks.
  - `classes.<letter>`: one entry per class letter, with a `kind` (`autotile` value) and the fields listed below. A class may also set `collision: true`, and `joins` (the letters it connects with).

  | `autotile` value | Fields |
  |---|---|
  | `blob47` | `by_mask` {mask → id} |
  | `row` (ledges) | `parts` {left, mid, right, single} |
  | `column` | `parts` {top, mid, bottom, single} |
  | `waterfall` | `parts` {`top_left` … `bottom_right`} |
  | `tree2x2` | `by_context`, `overlays` (per neighbour class: crown art over g, p, tall grass), `bush`, `phases` |
  | `shore` (sand `d`) | `body` (blob class used away from water), `joins`, `water`, `by_mask` {water-neighbour mask → id} |
  | `bridge` (`q`) | `parts` {horizontal_/vertical_ single, top/left, middle, bottom/right, single_<side>_end} |
  | none | `base` [ids], picked deterministically by position |
  | `sea_depth` (`b`, coast) | `bands` [blob tables shallow, mid, deep], `band_starts_in_metatiles`, `depth_water` (band = smoothed distance to cells outside it; a band's rim sits on its cells, lighter neighbours open the mask) |
  | `sea_rocks` (`u`, coast) | `blocks_2x2` (whole 2x2 `u` groups in water), `single`; `u` on land takes the `boulder` prop |
  | `cliff_face` (`k`, mountain) | `parts` `<upper or lower>_<left, mid, right or single>`: upper when the cell below is face too |
  | `sand_with_dunes` (`d`, desert) | `base` flat sand, `dune_by_mask`, `dune_noise_threshold` |

  Extras on existing classes: `blob47` `fill_variants` (full mask picked by position), `deep_interior` (cave void where a whole 5x5 is wall), `water_by_mask` + `water` on coast sand (rim against water; `by_mask` is then the grass rim), `r.in_sea_by_mask` (coast: rock regions touching the sea take the Emerald sea-ridge art), `surf: true` (elevation 1 like `w`). `a.sideways_parts` ('<east|west>_<north|middle|south>_<lip|fall|foot>', used when a fall touches more water west and east than north and south), `a.parts.<row>_single` (one-wide falls), `s/j/e/x.over_ground` {ground letter → blob mask → id} and `over_ground_ends` (ledge on the top layer over path/sand or tall grass on its high side; the ground blob is computed as if the ledge cell were that ground).

  Letters missing from the table go through `ruletile.FALLBACK` (sea → water, cliff → rock …; sand and bridges have their own art since the a1-terrain ROM branch).

### Add or change a terrain secondary

1. In the ROM tree, `tools/arklarna_tiles/land_secondaries.py` (or `coast_secondary.py`) names the pieces as source metatile ids of a pret tileset already in the tree (`general_frlg`, `seafoam_islands_frlg`, `general` + `mauville`, ...). `terrain_kits.py` turns them into classes: `PLAIN_SCHEME` (variants), `QUARTER_SCHEME` (thin rims: 8x8 quarters from N/E/S/W/corner/inner pieces, missing ones mirrored or synthesised), `WHOLE_PIECE_SCHEME` (thick rims: whole pieces per open side), `CliffFace`. `borrowed_metatiles.py` reads any pret pair (FireRed layout 640/640); `borrowed_autotiles.py` swaps backgrounds (`rebased_onto_new_background`) and builds the autotiles.
2. Register it in `terrain_secondaries.TERRAIN_SECONDARIES` (directory, symbol, biomes), add the C entries in `src/data/tilesets/{headers,graphics,metatiles}.h`, run `python3 tools/arklarna_tiles/build.py` and `make`. Primary and woodland outputs must stay byte-identical; check the diff.
3. Find source ids by rendering the source maps (`data/layouts/*/map.bin`) with their own pair, then look at generated routes at 3x.

### Add a new rule-tiled tileset (needs a small code change)

1. Put the tileset in a pokeemerald-format tree under `data/tilesets/primary/<p>` and `secondary/<s>`, with `CREDITS.md`.
2. Write its class table JSON (same shape as above). Use `python3 -m pokeroutes atlas` or `Tileset.atlas()` to find the metatile ids.
3. Generalise `ruletile.py`: make the table path and the `PRIMARY`/`SECONDARY` names parameters (for example `ArkTiler.load(rom, table=..., primary=..., secondary=...)`) instead of constants. Thread a choice through `cli.py --tileset` and `webui/shell_api.py` (`/api/tilers`). Keep Ark working unchanged.
4. Generate a forest route with it. Look at the PNG for broken edges, then check tree blocks (2x2), pond edges and ledge rows.
5. Commit the code plus the credits.

## Editing tiles (tile editor overlay)

- Pixels, palettes and new metatiles for the Ark/PokeRoutes pair live in an overlay, `tilesets/overrides/ark/overlay.json` in the main checkout (gitignored; `POKEROUTES_TILE_OVERRIDES` moves it). Never edit the ROM tree's `tiles.png` / `metatiles.bin` for this.
- `pokeroutes/tileset_overlay.py`: read/write (revisioned, atomic), `apply_overlay_to_tileset_pair`, `apply_overlay_to_ark_class_table` (custom tiles become `custom_<id>` props, coalesced classes join `classes`), `write_overlaid_tileset_pair_files` (pokeemerald-format export). `ArkTiler.load()` and `ArkTiler.pair()` apply it; `ArkTiler.rom_pair()` is the untouched ROM pair.
- Edits: `pokeroutes/tile_editing.py` (copy-on-write for shared 8x8 tiles, flips, palettes, new metatiles, behaviour/layer/class), `pokeroutes/tile_coalesce.py` (47 blob variants of a terrain against a surrounding tile), `pokeroutes/tile_operations.py` (one dispatcher).
- Agent tools: `tile_sheet` to find ids, `tile_read` to look at one (Read its preview PNG), `tile_edit {operation}` to draw or create (saves at once), `tile_coalesce {inner, outer, class}` to give the Ark tiler a new blob class (e.g. `d` sand from a sand texture tile against grass `0x000`). Then `generate_route` with `tiler: ark` and look.
- Web UI: Window > Tools > Tile editor (`webui/README.md`). Learned Emerald pairs are read-only there.
- Export to the ROM is not automated yet: the Porymap zip of an Ark route carries the overlaid tilesets under `data/tilesets/`.

## Checking tiling work

- Generate at least 3 seeds in 2 biomes and read the PNGs.
- Zoom into edges with `crop_route_image`: pond banks, tree borders, ledge ends, road edges.
- `route_stats` shows variety per class. A real Hoenn route's most common tree metatile covers about 40% of tree cells; much higher than that looks flat.
