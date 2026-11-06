---
name: codebase-map
description: Map of the poke-routes code — where each generation stage, tiler, region step, web UI file and test lives, and how to run checks. Load this FIRST for any task that reads or changes code in this repo, before grepping around.
---

# poke-routes codebase map

Use this instead of exploring with grep/sed. Jump straight to the file and function named here.

## Route generation, in pipeline order

1. **Spec**: `pokeroutes/spec.py`, the `RouteSpec` and `Exit` dataclasses, `RouteSpec.load(path)` (YAML). `BIOMES` and `BIOME_TILESET` (biome → learned model) live here.
2. **Skeleton plan**: `pokeroutes/braid.py`, called through `stages.stage_skeleton`. Main lane, sections, side lanes (`kind` loop/dead_end, `fate` full/merge/dead), the meander swing (`meander`), and `enforce_quotas` (`dead_ends` → spurs).
   - **Lane purposes** (camp, berries, shrine, pond, ruin, lookout…): `pokeroutes/purpose.py`, `lane_purposes`.
3. **Stages**: `pokeroutes/generate.py`. `STAGES` is the ordered list; `generate(spec)` runs it and then `density()`.
   - The stage functions live in `pokeroutes/stages.py` unless noted. In order:
     - `stage_base`
     - `stage_skeleton`
     - `stage_branches` (forest/meadow also get cross paths between braided lanes: `pokeroutes/weave.py`)
     - `stage_clearings`
     - `stage_specials` (generate.py → `places.place_specials`)
     - `stage_lakes`: bend ponds, riverland rivers (`rivers.paint_own_rivers`), `_sea`, `_desert` (+ `desert_bands.py`), region water (`region_water.py`), `_ponds`
     - `stage_road`
     - `stage_tall_grass`: `_fields` for meadow/plains, `_lane_patches`
     - `stage_ledges`: `_tiers`, `_elevation`
     - `stage_hidden` (generate.py)
     - `stage_dressing`: signposts, HM obstacles, special-area keepers (no other people: placed by hand), items, `_furniture`
     - `stage_cleanup` (generate.py): `_thin_water`
     - `stage_connect` (generate.py): reachability, `_untrap`
     - `stage_terraces` (`pokeroutes/terraces.py`, mountain / snow / volcanic): contour bands with 2-tall cliff faces, shelves beside the lanes, stairs or one-way ledges where lanes cross a face, boulders, tufts, snowy pines + frozen pond, volcanic crater; then `verticality.py` (rock faces on height steps over open ground, wooded biomes)
     - `stage_open_plains` (`pokeroutes/plains.py`, plains only): opens the hill mass, ledge-faced edge rim, groves, terrace steps
     - `stage_tree_blocks` (`pokeroutes/tree_blocks.py`): settles half-tree 2x2 blocks (fill, clear or flood) so no bush rows
     - `stage_obstacles` (`pokeroutes/obstacles.py`): every Cut tree / smash rock / Strength boulder sits in a one-wide gap on ground, or is removed
     - `stage_rivers_restored` (`pokeroutes/rivers.py`): rivers (smooth, >= 2 wide, painted in stage_lakes) are put back whole; walkable cells in them become bridges
     - `stage_surf_crossings` (`pokeroutes/surf_crossing.py`): where the region path crosses sea/lake, the main path there turns to water + a built Surf gate; `mode: sea` routes become open sea with islets, sandbars, rocks
     - `stage_plateaus` (generate.py): plains only
     - `stage_route_gate` (generate.py → `gates.build_route_gates`): route design B gate (`spec.gate` or `spec.region.gate`) at the gated entrance. The region side picks the entrance in `region/gates.py`.
     - `stage_seams` (`pokeroutes/seams.py`): stamps the 2-deep seam bands from `spec.region.seams` on the map edges (shared with the neighbouring map, so touching edges match tile for tile) and joins band water to the water inside. `seam_mismatches` compares two touching edges.
   - Per-biome palette: `stages.BIOME_CODES`, a tuple of (wall, ground, lane surface, road, encounter grass).
4. **Height**: `pokeroutes/height.py`. One shared world height map, sampled at `spec.world`.
5. **Special areas**: `pokeroutes/places.py` (archetypes `arch_grove`, `arch_meadow`, `arch_pond`, `arch_hollow`, `arch_stones`, plus `place_specials`) and the `pokeroutes/areas/` package (recipes, stamps).
6. **Tiling: class grid → metatiles.**
   - **Learned tiler**:
     - `pokeroutes/learn.py`: `learn`, `learn_biomes` (train from the vendor/pokeemerald layouts).
     - `pokeroutes/autotile.py`: `Autotiler.tile`, a softmax over 3x3-context counts plus a neighbour-pair score.
     - `pokeroutes/classify.py`: class codes and `FALLBACK`.
     - Models live in `pokeroutes/models/*.json` and `models/index.json`.
   - **Ground texturing**, applied after the learned tiler: `pokeroutes/ground.py` (`texture_open_ground` for light-grass patches and flower clumps, `tile_with_false_walls`).
   - **Rule tiler** (hand-made tilesets, shown to the user as the PokeRoutes tileset): `pokeroutes/ruletile.py`, `ArkTiler`; bridges, sand shores, sideways falls and ledges over ground in `pokeroutes/ruletile_terrain.py`.
   - **Reading tilesets and rendering metatiles**: `pokeroutes/tileset.py`, `load_pair`.
   - **Tile editor overlay** (hand-drawn pixels, palettes, new metatiles and coalesced classes on top of the Ark ROM tiles): `pokeroutes/tileset_overlay.py`, edits in `tile_editing.py`, `tile_coalesce.py`, `tile_operations.py`; applied by `ArkTiler.load` / `ArkTiler.pair`.
   - **Scene-preview breeze** (tree crowns, tall grass, flowers swaying 1 px; preview only, not in the ROM): `pokeroutes/breeze.py`, added to the atlas `anim` and listed in `meta.breeze` by `webui/server.py` `build_atlas`.
   - **Idle motions and per-tile animation** (preview only): `pokeroutes/idle_motion.py` (motions sway/tips/nod/bop/dance/roll/wobble, whole props move together), baked into the atlas by `webui/server.py` `build_atlas` (`meta.anim`, `meta.motion`). Per-tileset overrides: `webui/tile_looks.py` (`/api/tile-looks`, stored in `webui/tile-looks.json`); UI `webui/static/tile-motion.js` (M over a map tile, or right-click a Tiles-palette entry). Event sprites (item ball, berry tree, boulder) move in `route-items.js`.
   - **Under-tree shade** (spec `tree_shade`): `pokeroutes/tree_shade.py` tints the Ark tree floor pixels (`floor_masks` in the class table) in the Ark atlas (`shell_api.ark_atlas`, `?shade=`).
   - **Ark fences** (class `c`, autotile `fence4`, NESW arms) and cross-colour tree crowns / coloured bushes: `ruletile.py` (`fence_arm_key`, `crown_from_other_colour_below`, `bush_in_neighbouring_tree_colour`).
7. **Export**: `pokeroutes/export.py`.
   - `seal_edges`, `write_emerald` (map.bin, border.bin, map.json, scripts.inc)
   - `write_scene` (scene.json), `report` (report.json)
   - Route `effect` (none/leaves/snow/rain/ash) → Emerald weather and notes: `pokeroutes/route_effects.py`. The Scene draws it as a particle overlay (bench.js, `tick_route_effect`).
   - People sprites: `pokeroutes/route_people.py` (npc role / trainer → Ark route-people `OBJ_EVENT_GFX_ARK_ROUTE_*` on Ark maps when the ROM defines them, stock ids otherwise). The Routes view reads the same sheets through `webui/route_items.py` (`/api/sprite.png?kind=npc_<role>|trainer|trainer_f`).
   - People, signs and events placed by hand: `pokeroutes/people_and_signs.py` (person/sign entries → object/bg events, scripts, trainers.party stub), `pokeroutes/route_events.py` ("when trigger then effects" rules → FLAG_PR_EVENT_* flags, ON_LOAD / ON_TRANSITION map scripts, trigger scripts, EVENTS_README.txt), glued into `write_emerald` by `pokeroutes/hand_authored_export.py` (reads `route.__dict__["hand_people_and_signs"]` and `["event_rules"]`). Server: `webui/people_and_events_api.py` (`/api/people-options`, `/api/sprite.png?kind=gfx_<OBJ_EVENT_GFX_*>`, `/api/route-people`, `/api/route-events`). UI: `static/route-people.js` (People & signs tool, P), `static/events.js` (Events tab in the bottom dock), pure logic in `static/people-events-logic.js`. Agent tools: `list_events`, `save_event`, `delete_event`, `list_people_and_signs`, `place_people_and_signs`, `apply_events` in `route_tools.py`.
   - `debug_image` (plan.png), `stage_sheet` (stages.png), `grade_for_biome`
8. **CLI**: `pokeroutes/cli.py`. `_generate_and_write` is the whole route path end to end.
   - `python3 -m pokeroutes generate SPEC [-o OUT] [--seed N] [--stages] [--tileset learned|ark]`
   - `python3 -m pokeroutes region SPEC... [--special-budget N]`
   - `python3 -m pokeroutes learn PRIMARY SECONDARY [--layouts ...]`, or `learn --biomes [--only ...]`
   - `python3 -m pokeroutes atlas PRIMARY SECONDARY`

## Class codes (one char per metatile cell, `pokeroutes/classify.py`)

| Code | Meaning | Code | Meaning |
|---|---|---|---|
| g | grass | d | sand |
| p | path | b | sea |
| t | tall grass | h | shallow water |
| w | pond water | y | ash grass |
| f | tree | v | ash ground |
| r | rock mass | k | cliff |
| s / e / x | ledge south / east / west | z | stairs |
| a | waterfall | l | lava / hot springs |
| o | other | n | cave entrance |
| q | bridge | u | boulder |

Walkable: `gptdhyvzqn`. Surfable: `wb`. Blocked: `frkul`.

## Region generator (`pokeroutes/region/`)

- **Pipeline**: `pipeline.build(seed, geography_kind=None)`: land and sea (`geography.py`, `land_and_sea` picks an archetype, `heightmap`) → erosion and rivers (`terrain.hydrology`: rain + snowmelt + springs, `meandering_receivers`) → climate and biomes → routes between towns (`network.py`, `town_links`; links between landmasses beyond bridge reach are surf links, `sea_cost`). No towns until the editor places them or Generate map runs (`edit.generate_map` → `town_sites.pick_town_sites`, towns per landmass). `w.landmass` labels land components. The Arklarna preset and legends are gone; old documents load as generic (`world.LEGACY_BIOME_REPLACEMENT`).
- **Specs**:
  - `export.route_spec` turns each region route into a RouteSpec dict.
  - `export.write_all` writes `region_out/<seed>/routes/*.yaml`.
- **Hand editing**: `edit.py`, used by the editor's recompute, paint-map and generate-map actions (`recompute`, `paint_map`, `generate_map`, `generate_doc`, `save`, `load`). The document's `map_grid` (`{cell, size, rows}`, `.` empty / `r` route / `t` town) is the painted Map grid; the routes stage derives towns and routes from it.
- **Map grid**: `map_grid.py` (pure): `with_cells_painted` (brushes route/town/erase), `town_groups_of` (neighbouring town cells = one town), `route_chains` (neighbouring route cells walked end to end, straight through junctions), `chain_stretches` (2+ water cells = own sea route), `painted_network` + `route_records_of` (exits where a route touches a town or another route, dead-end goal, `network.classify` roles), `map_from_marks` (sets `w.cities`, `w.routes` with ordered `grid_cells`, `w.links`; keeps names/gates/overrides by cell overlap), `marks_of_places` (snaps documents from before the grid). Generate map: `grid_map_generation.py` (`generated_marks`: town sites snapped to grid cells, metro-style grid search between `network.town_tree` pairs with a turn cost). `network.py` still lays routes for the CLI pipeline.
- **Routes as chains of maps**: `route_chain.py`. A route's rect is cut on the region's Map grid (`map_grid.region_cells_per_grid_cell(world)`, passed into `chain_maps_of_route` / `ordered_grid_cells_of_route`); each grid cell is one map, joined edge to edge along a breadth-first tree from the first map (`chain_parent_indices`, so branches appended after the longest run join the map beside them) (seam exits carry `to_chain_map` and Porymap `offset`). `ordered_grid_cells_of_route` is THE route -> grid cells function (prefers a painted `grid_cells` field); `map_grid_of_route` / `region_rect_of_grid_cell` give the frame. `chain_maps_of_world` builds every chain map (single-map routes are a chain of one). `export.chain_map_specs` turns them into specs (with `region.seams` from `seam_bands.py` and `region.chain`); `write_all` writes one YAML per chain map; chain maps share the route's MAPSEC (`export.map_section_name`). `edit.with_specs` adds `routes[].chain` for the UI; `edit.full_spec` resolves chain map ids.
- **Seam bands and seam check**: `seam_bands.py` builds the band classes along every shared edge from the shared region cells (water both sides, openings where exits cross, trees at corners); `seam_check.py` generates maps (`generate_maps_of_routes`), lists touching pairs (`seam_checks_of_maps`) and describes mismatches. Tests: `webui/tests/test_route_chains_and_seams.py`; stuck-link regression: `test_region_dangling_links.py` (`network.unintended_dead_end_routes`).
- **Region size**: `region_resize.py` (`resize_region(w, width_in_squares, height_in_squares)`): grids cropped or grown at the right/bottom; new area gets land continuing the old edge plus noise islands at about the old land share, heights blended from the edge, then water/climate/biomes run and the old area's grids are put back; the painted Map grid is cropped and re-derived. `edit.resize_region`, `POST /api/editor/resize-region {doc, width, height}`. UI: the Map inspector's Region fold has "Map grid" (square size, `regrid-map`) and "Size" (width x height in squares); undo restores `size` (in `ED` History `DATA_KEYS`). The Map's "Squares" layer (Hierarchy eye) draws route/town squares see-through.
- **Map -> Routes live sync**: the editor calls `host.on_document_change(doc)` after every document change; bench.js `follow_map_changes_of_open_route` finds the open map again (`SL.sync_target_for_open_map`: same route name, else the route over the open square; same square, else nearest map), compares `SL.open_map_signature`, fetches the spec (`spec_of_map`) and regenerates with `SL.params_after_map_sync` (seed and Routes-view edits kept) and `keepPaint`; painted cells move with `SL.paint_memory_after_chain_change`. An erased route is reported in the Console and the view keeps its last version. Record lives in `state.region.map_sync`.
- **Routes view chain walking**: `SL.region_route_named`, `SL.chain_of_map`, `SL.chain_map_with_constant` (shell-logic.js); the Region link inspector shows a Maps 1..n row (`chain_map_row` in bench.js); the Map frame finds chain maps via `ED.route_or_chain_map_by_name`; opening a route opens its first chain map (`ED.first_map_of_route`).
- **Map grid**: `map_grid.py` (pure): `painted_map` (brushes route/town/erase; a route stroke goes to `route_key` or carries on the route it starts on, else a new key), the document's `map_grid.routes` (`[{key, cells}]`: which route owns each tile), `repaired_assignment` (pieces of a cut route become their own routes), `town_groups_of` (neighbouring town cells = one town), `ordered_cells_of_route` (cells walked end to end), `painted_network` + `route_records_of` (exits where a route touches a town or another route, dead-end goal, `network.classify` roles), `map_from_marks` (sets `w.cities`, `w.routes` with ordered `grid_cells` and `grid_key`, `w.links`; keeps names/gates/overrides by key, then by cell overlap), another square size is `map_grid_square_size.py` `regrid_world` (towns snap to compact blocks at their place, routes keep one connected piece, the map at each earlier size is remembered in `map_grid.earlier_square_sizes` until the next paint so a round trip gives it back; `edit.regrid_map` then grows the region to whole squares). Generate map: `grid_map_generation.py` (`generated_marks_and_assignment`: town sites snapped to grid cells, metro-style grid search between `network.town_tree` pairs with a turn cost). `network.py` still lays routes for the CLI pipeline.
- **CLI**: `python3 -m pokeroutes.region generate --seed N [--geography KIND]` (`region/cli.py`, `generate`) writes `region_out/N/`.

## Web UI (`webui/`)

- **`server.py`**: stdlib HTTP server on 127.0.0.1:8790 (the `PORT` env var overrides it).
  - `spec_from(params)` turns editor JSON into a RouteSpec. `HINTS` holds the UI slider ranges.
  - `generate()` handles `POST /api/generate`; `export_zip` handles `/api/export`.
  - Read-only endpoints: `/api/options`, `/api/spec-schema`, `/api/atlas`.
- **Other API modules**:
  - `shell_api.py`: `/api/tilers`, `/api/scene`, `/api/spec-parse`, `/api/region-specs.zip`, `/api/commits`, `/api/retile`, plus the redirects.
  - `editor_api.py`: `/api/editor/*` (generate, load, recompute, generate-map, paint-map, save, spec, render, export, list).
  - `region_api.py`: `/api/region`, `/api/region.png`, `/api/region-spec`.
  - `tile_overrides.py`: `/api/tile-editor/*` (tilesets, tileset, edit, save) for Window > Tools > Tile editor; `tile_agent_tools.py`: the `tile_*` route tools.
  - `paint_palette.py`: `/api/paint-palette?id=` (what the Tiles tab can paint: classes, samples, objects, exact tiles per tiler); `/api/retile` (in `editor_api.py`) takes those classes.
- **Agent jobs**: `improve.py` runs the job queue and launches `claude`. `route_tools.py` is the stdio MCP server that provides your `routes` tools.
- **Front end** (`webui/static/`):
  - `shell.html`: layout and docks.
  - `shell.js`: menus, docks, console, keys. Its public surface is `window.Shell`.
  - `bench.js`: Scene, Inspector, Hierarchy, Timeline, files, paint. Its public surface is `window.Bench`.
  - `shell-logic.js`: pure functions (shortcuts, layout, undo, `specToParams`).
  - `editor/app.js` and `editor/logic.js`: the region editor (Geography and Map tabs; route gates are set in the Map inspector). `editor/map-grid-logic.js` (pure, `window.MAP_GRID`): Map-grid strokes, metro line geometry, line colours, station shapes. Any two of Routes, Geography and Map can sit side by side in a split centre (`shell.js`, `split_*` and `center_tab_of_each_view` in `shell-logic.js`). Geography beside Map is one editor with two render targets (`#viewMap`, `#viewMapBeside`; `make_render_target`, `bind_render_target`, `set_view_modes` in `app.js`): one document, selection and undo history, a camera, mode and layers per view, and tools, Hierarchy and Inspector follow the focused view (`Shell.layout.center`).
  - `editor/app.js` and `editor/logic.js`: the region editor (Geography and Map tabs; route gates are set in the Map inspector). `editor/map-grid-logic.js` (pure, `window.MAP_GRID`): Map-grid strokes, filled-square shapes, joints, outlines (Sinnoh town-map look), label placement (`placed_map_labels`: candidate spots per label, first free one wins; route labels that do not fit are dropped unless the route is selected or hovered; drawn by `draw_town_map_labels` in `app.js`). The play-test sends `pokeroutes-player-on-map` window events (`playtest.js`) and the editor draws the player's head there. Routes can sit beside either in a split centre (`shell.js`, `split_*` in `shell-logic.js`).
  - `agent.js`: the Agent tab.
  - `tile-editor.js`, `tile-editor-logic.js` (pure), `tile-editor.css`: the Tile editor window.
  - `playtest.js` + `playtest-logic.js` (pure, node-tested): the Play-test window over the Routes view; data from `webui/playtest_api.py` (`/api/playtest/route`, `/api/playtest/sheet.png`), hooks from `Bench.playtest_hooks`.
- **Reloading after edits**:
  - Generator edits (`pokeroutes/*.py`) are picked up by the running server automatically (`server.G()` reloads when file mtimes change).
  - Static files are read on every request.
  - Edits to `webui/*.py` need a server restart.

## Tests and checks (run from the repo root)

```sh
python3 -m pytest -q webui/tests            # job queue (with fake_claude.py) + region network
node webui/tests/shell-logic.test.js        # shell pure logic
node webui/tests/editor-logic.test.js       # region editor pure logic
node webui/tests/map-grid-logic.test.js     # Map grid painter pure logic
node webui/tests/tile-editor-logic.test.js  # tile editor pure logic
node webui/tests/playtest-logic.test.js     # play-test movement, collision, encounters
node webui/tests/people-events-logic.test.js  # people, signs and events pure logic
node --check webui/static/<changed>.js
python3 -m pokeroutes generate examples/bernsteinweg.yaml   # also erlenbruch.yaml, kiefernheide.yaml
```

The route tool `check_examples` runs all three example specs, and `check_web_ui` starts and checks a temporary copy of the server. Prefer them over starting servers by hand.

## Gotchas

- Generated output goes to `out/` and `region_out/`, which are gitignored. Never commit them, or `vendor/`.
- `vendor/pokeemerald` is the user's own pret checkout. The learned tiler and the PNG renders read tiles from it. If it is missing, `_generate_and_write` skips tiling (no `emerald.png`).
- Some spec fields are accepted but not read by the route generator: `branches`, `loops` and `palette`. Only `dead_ends` drives spurs, and `clearings` only matters as 0 versus more than 0 (`braid.py`).
