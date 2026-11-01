# Special areas: where things stand (2026-11-01, second session)

## Done
- `pokeroutes/areas/` imports cleanly and does not import `generate.py`.
  - `__init__.py`: `load_areas`, `get`, `pick`, `requirements`, `score_site`, `find_site`, `build`, `connect`, plus the `AreaResult` and `SubMap` classes. `places._build` in the generator already calls `pick`, `requirements` and `build`.
  - `stamps.py`: the stamp vocabulary and a `Painter`. The stamps are clearing, rock_ring, mound, boulders, split_oak, fallen_log, bare_ring (scorch), fence_pen, ruin_room, ruin_wall, standing_stones, cave_mouth, rock_hollow, tree_door, pond, reeds, stream, cliff, sign, objects, and so on.
  - `tiles.py`: stock metatile pieces copied from real Emerald maps (boulder, rock mound, fence, pale grass, cave mouth, tree door). Free-standing pieces report class `o`, so the autotiler doesn't grow rock edges around them.
  - `art.py`: custom "ark_special" metatiles, drawn only in General palette 2 (never colour 0). Pieces: split_oak (6x6, including its burnt apron), fallen_log (5x2), and scorch_a/b/c. Output is in `ark_special/` (tiles.png, palette.pal, metatiles.json, pieces.png). The ids are provisional (0x380+).
  - `rom.py`: overworld mons as `OBJ_EVENT_GFX_SPECIES(X)` object events. Each mon gets a stub script: cry, text, `setwildbattle`/`dowildbattle`, then `removeobject` if caught or beaten. Colony mons use FLAG_TEMP_* so they come back; unique ones get permanent flags. Also covers NPCs, signs, hidden items (including night-only ones via `gettimeofday`), item balls, coord weather, once-per-visit enter triggers and warps.
  - `contexts.py`: terrain contexts, site features and fit scoring, blank canvases for previews, and sub-map interiors.
  - `preview.py`: builds an area on a canvas, autotiles it, paints the custom tiles and overlays the expansion's overworld sprites.
  - `models/general_cave.json`: learned only from the corpus maps that use the Cave secondary tileset.
- Visibility/access (owner rule: areas are mostly hidden). `requirements()` returns `visibility` (hidden | secret | visible | territorial) and `access` (cut_tree | hidden_gap | ledge | npc_block | strength_boulder | rock_smash | none). The lane-distance windows are set by visibility. `connect()` cuts a narrow tree trail for hidden/secret areas, places the gate object (or an NPC blocker plus a second hidden trail) and adds territorial ambush triggers (`rom.ambush`).
- `render_events(map_id)` fills in the `{MAP}` placeholder in event script names (on both AreaResult and SubMap).
- pokedex.json habitat fixes are written: Pikachu forest,deep_forest; Trapinch beach,dunes.
- The ark_special art has been sent to the tiles-outdoor agent.
- Contact sheet: documenting-agent-work/screens/areas-contact.png (all 10 recipes).
- Recipes: source files are in `recipes_src/`. `python3 recipes_src/merge.py` writes `arklarna/design/area_recipes.json`. There are 10 of 79: thunderstruck_oak, blind_cellar, counting_grove, oxbow_flats, heaving_commons, hollow_lane (territorial), lighthouse_rookery, hourglass_dunes, rolling_scree, mint_moulds (sub-map). The first two are described here:
  - `thunderstruck_oak` is the flagship Pikachu nursery: split oak, fallen log, Pichu and Pikachu, a Raichu sentinel and a researcher. It sits in deep forest.
  - `blind_cellar` is a sub-map: a ruin with a collapsed stair leading down to a cellar with Deino and a Zweilous.
- Renders: `arklarna/documenting-agent-work/screens/areas-thunderstruck-oak.png` and `areas-blind-cellar.png`. The last oak render was taken before the class-`o` fix, which should remove the half-mound triangles. That fix hasn't been looked at in a render yet.

## Next
1. Re-render the oak and check the rock mounds. Look at the blind_cellar interior render; cave tiling is rough.
2. (DONE) Owner rule: areas are mostly HIDDEN. Change `visibility` to hidden | secret | visible | territorial, and make `requirements()` return a matching `access` field (cut_tree, secret_path, puzzle_door, npc_block, none). `build()` should place the gate object (cut tree, or an NPC blocking the gap) on `door_out`. Territorial areas (for example Mankey) sit beside the lane and their mons use chase/approach movement (`MOVEMENT_TYPE_CHASE_PLAYER_OWE`).
3. Write the other 69 recipes. Fix the dune funnel pits (v and d both fall back to p, so the pits are invisible; they need a custom pit tile). Volcanic and coast previews need the biome models when they land in models/index.json. Use the dex list and the ecology notes. Sub-maps: drowned_chapel, bellrope_roost, ribbed_tunnel, swallowed_mine, mint_moulds, clockmakers_shop, puppeteers_attic, silent_reading_room, hidden_herbarium, tithe_barn, collapsed_granary, widows_cottage, whorl_spire, abyssal_cradle, caldera_throne. Write the `dex_habitats` fixes back to pokedex.json. So far only Pikachu changes: meadow,forest becomes forest,deep_forest.
4. Send the ark_special PNGs and metatiles.json to the tiles-outdoor agent (a44295477c39dec53). The oak's canopy rows should go on the top layer.
5. Tell the generator agent (acf6a123ba2d17ba9) about the following:
   - `res.custom`/`res.collision`. Paint them with `res.paint_custom(img)`, and use `res.fixed_with_custom()` once the tiles are in the ROM.
   - The route-side warp `dest_map` is the placeholder `{SUBMAP}`. Replace it with `MAP_<ROUTE>_<res.submap.id_suffix>`, then call `res.submap.resolve(route_map, warp_index)`.
   - Scripts use the `{MAP}` placeholder; `render_scripts(map_id)` fills it in.
   - Flags to allocate are in `res.flags`.
6. Make a contact sheet of about 12 areas, including sub-map interiors: `python3 -m pokeroutes.areas.preview <ids> --out ...`.

## Known gaps
- `preview_area` only uses the general_fortree model. Sand, sea and ash fall back through `classify.FALLBACK`.
- `elevation` is always 3.
- Weather and enter triggers sit only at the door cells (`door` inside, `door_out` outside).

## ROM ids for the custom pieces
The tiles-outdoor agent added the pieces to gTileset_ArkWoodland (branch arklarna-tiles-outdoor, commit f48a5c0d):
- split_oak: 0x2D3-0x2F6
- fallen_log: 0x2F7-0x300
- scorch_a/b/c: 0x301-0x303

`art.ROM_FIRST_ID` and `art.rom_id()` map the preview ids to these, and `res.fixed_with_custom()` uses them by default. Caveat: the stock `fixed` ids are General/Fortree numbers. Ark maps use ArkOutdoor + ArkWoodland, so those stock ids need translating through the tiles agent's data/tilesets/metatile_classes_ark.json before export.

## Ark tilesets (current)
Use `res.ark_fixed()` -> (fixed ids, class overrides) for a route tiled with ArkTiler (`pokeroutes/ruletile.py`, `--tileset ark`).
- It looks ids up by name at runtime in `metatile_classes_ark.json` via `ark.py`, so a tileset rebuild doesn't break it.
- These pieces map to real Ark ids: split oak, log, scorch, sign, fence, flowers.
- Pieces with no Ark art yet are returned as class overrides instead: 1x1 boulders, rock mounds, cave mouth and old tree become 'r'/'f'.
- `fixed_with_custom()` / `ROM_FIRST_ID` are the hardcoded fallback.
- Cave-mouth sub-map entrances have no Ark art yet.
