---
name: route-design
description: How to make, tune or restyle a poke-routes route or region: what every RouteSpec field really does, which biomes work, recipes for iconic route styles, and the generate → look → tweak loop with the routes MCP tools. Load when asked to create a route, make one look like X, change a route's feel, or build a region.
---

# Route design with poke-routes

## The loop (always do this, never guess from the spec alone)

1. Start from the user's current editor spec (path given in your prompt) or an example: `list_specs`, `spec_fields`.
2. `generate_route` with `overrides` and **3 or more seeds**. One seed lies.
3. Read the `tiled` PNG of each result. Use `crop_route_image` for close-ups and `route_stats` for numbers (tree share, walkable share, metatile variety). `route_grid` gives the class letters.
4. Tweak one or two fields at a time and regenerate. Stop when it reads right on most seeds.
5. Deliver:
   - `save_spec` writes the YAML.
   - `show_in_editor` offers it to the user's Scene.
   - Mention the best seed.
   - `paint_route` fixes only the session copy; a durable fix goes in the spec or the generator.

Other tools:
- `generate_world` builds a whole region from a seed (map and route specs).
- `tilers` reports which tilers and models are available.
- `check_examples`, `check_web_ui` and `screenshot_web_ui` are for code changes.

## RouteSpec fields by what you see (`pokeroutes/spec.py`)

**Size and exits**
- `size: [w, h]` in metatiles. The web UI clamps width to 24–96 and height to 24–112. Very large maps go past the GBA map buffer and lose the Porymap export.
- `exits`: a list of `{side, at 0..1, width 1..6, to: MAP_CONST}`. The first two exits are the main road's ends.
- `extra_exits` (0–4): extra side lanes run out to an edge (rare).

**Shape of the path** (`braid.py`)
- `meander` (0–1): how far the main road swings sideways and how often it jogs. 0 is a straight corridor; 0.7 is the default wandering look.
- `min_run` (default 5, minimum 4): the shortest straight run between turns. Raise it for longer, calmer straights.
- `sections` (0 = automatic, about 1 per 30 tiles of length, 2–5): how many braided segments sit along the route.
- `max_lanes` (1–3; values above 3 are clipped): lanes side by side in a section. 1 gives a single path with no braids.
- `merge_chance` (0–1): side lanes rejoin early.
- `dead_end_chance` (0–1): side lanes stop short at an item, lookout or pocket.
- `dead_ends`: the minimum number of spurs. Each spur gets a purpose (`purpose.py`).
- `branches`, `loops`: accepted but **not read** by the route generator today. Use `max_lanes`, `merge_chance` and `dead_end_chance` instead.
- `clearings`: only 0 versus more than 0 matters. It adds at most one rest-stop clearing on the spine.
- `max_walkable` (default 0.40): the density budget. Lower it for a denser forest; raise it for open fields.

**Ground and water**
- `biome`: see the next section.
- `road` (bool): the dirt road on the critical path. Forest has none either way, because its main lane is trodden grass.
- `tall_grass` (0–0.9): how much tall grass lands across lanes and pockets. Meadow and plains also get hedged fields.
- `lakes` (0–4): pond count. Riverland halves it. Desert has none.
- `ledge_chance` (0–1): one-way ledges between and on lanes. There are none in desert or volcanic.
- `world: [x, y]`: where the route samples the shared height map (`height.py`). Change it to get a different hill and step layout without changing the seed.

**Secrets and places**
- `hidden_paths` (0–6): walk-through or HM-gated trails.
- `hidden_items` (0–12)
- `lore_spots`: at most 1 in practice.
- `special_areas`: archetype names, species names, or `auto`. Each `auto` entry becomes an area only with probability `special_area_chance`, and only where there is a hidden site at least 20 tiles from every exit. Most routes end up with none.

**Map header only (no visual change)**
- `weather` (e.g. `WEATHER_RAIN`, `WEATHER_SANDSTORM`), `music`, and `tileset`.
- `tileset` picks the learned model (for example `general_mauville`). The default comes from `BIOME_TILESET`.

## Biome maturity (be honest with the user)

- **forest**: the most finished. Dense maze lanes, bend ponds, ledges.
- **meadow, riverland**: tested. **Known bug**: the road class `p` is tiled with General's red roof-brick metatiles (around 8–10) by the fortree model. Real Hoenn dirt roads are classed as sand `d`.
  - The quick workaround is `road: false`.
  - The real fix is a palette or remap change: road → `d` in `stages.BIOME_CODES`, or a `p`→`d` remap before the learned tiler.
  - Say which one you did.
- **riverland**: rivers come out as straight east–west bars plus thin north–south streams. There is no meandering river spine yet.
- **plains**: open fields (Kanto Route 1 / Ash Gray feel). The lane maze is opened into grass with tall-grass plots and a few groves; the map's edge rises into a grassy verge: a tree line, raised grass (class `o`, unwalkable scenery) with a few bushes and small trees, and a low ledge lip facing the field (open at the exits); rock only on a side the region block puts against mountains. At most 0-2 short one-way steps from the height map. Tall grass is soft patches (6-40 cells) plus strips along the road (`pokeroutes/plains.py`, `pokeroutes/plains_grass.py`). Expect 60–75% walkable, over the `max_walkable` budget on purpose.
- **desert**: Route 111 style rock walls, sand and boulders. Close in feel.
- **beach, mountain, volcanic**: rough. Beach has no shoreline foam and leaves stray teal cells. Volcanic uses Lavaridge's red rock (`BIOME_TILESET`), not Route 113's ash.

## Recipes (real knobs only; verify with 3+ seeds)

- **Maze forest (Petalburg Woods / Kalos Route 20)**: the strongest look.
  - `biome: forest`, `meander: 0.7–0.9`, `max_lanes: 3`, `merge_chance: 0.2`, `dead_end_chance: 0.4`, `dead_ends: 2–3`
  - `hidden_paths: 3`, `max_walkable: 0.32–0.36`
- **Starter route (Route 101 / Kanto 1)**:
  - Small `size` (around 24x30), `meander: 0.3`, `max_lanes: 1–2`, `dead_ends: 0`, `hidden_paths: 0`, `lakes: 0`, `tall_grass: 0.4`
  - `biome: meadow` with `road: false` until the brick bug is fixed. The output is still denser and darker than the real route.
- **Desert (Route 111)**: `biome: desert`, a long north–south `size` (around 40x100), `meander: 0.5`, `tall_grass: 0.1`, `weather: WEATHER_SANDSTORM`.
  - Missing: grassy ends, stairs, buildings.
- **River route (Route 119 / 120)**: weak today.
  - Use `biome: riverland`, `tall_grass: 0.5–0.6`, `ledge_chance: 0.5`, `weather: WEATHER_RAIN`, and `road: false`.
  - Missing generator features: a meandering river spine with banks, cliff terraces with stairs outside mountain, long bridges, hedge-row grass corridors, landmark buildings.
  - Don't promise the look; offer to build the missing stage instead.
- **Ash road (Route 113)**: not reachable with knobs, because the volcanic biome uses the wrong tileset. A `general_fallarbor` model exists, but it draws cliff interiors as brick.

## Making it look richer ("block vomit")

- **Where variety is missing today**:
  - The learned tiler (`autotile.Autotiler.tile`, temperature 0.35) mostly picks the most common metatile for each context.
  - `ground.texture_open_ground` only varies plain open grass (light-grass and dirt patches, flower clumps).
- **The biggest visual gap is trees**: generated maps reuse one tree metatile for about 70% or more of tree cells, against about 40% in real routes, and they have more tree cells overall.
- **How to add variety**:
  - Swap in variants with low-frequency noise zones plus sparse speckle.
  - Keep trees in their 2x2 blocks.
  - Lower tree density with `max_walkable` and by opening up the space between lanes.
- **Compare before and after** with `route_stats` (variety per class) and side-by-side PNGs.

## Regions

- `generate_world` with a seed calls `pokeroutes.region` (terrain, towns, landmarks, a route network) and writes `region_out/<seed>/` (`region.png`, `routes/*.yaml`).
- Each route spec carries a `region` block (role, goal, elevation, water). Generate the routes from those YAMLs.
- `generate_region` takes a list of specs and keeps special areas from repeating and exits lined up.
