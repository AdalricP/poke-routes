# Architecture

```
                 region generator (optional)
  pokeroutes/region/
  terrain.py   fBm + domain warp, erosion, lakes, rivers, climate, biomes
  legends.py   legendary landmarks carved into the terrain
  settle.py    cities
  network.py   route graph: rects, exits, roles, names
  export.py    one spec YAML per route  ──────────────┐
                                                      │
  examples/*.yaml ───────────────────────────────────►│
                                                      ▼
                                   spec.py   RouteSpec (size, biome, exits, counts)
                                                      │
                                                      ▼
  generate.py  STAGES, each edits route.cls and saves a snapshot
  ┌───────────────────────────────────────────────────────────────────┐
  │ base ─ skeleton ─ branches ─ clearings ─ specials ─ water ─ road  │
  │ ─ tall grass ─ ledges ─ hidden ─ dressing ─ cleanup ─ connect     │
  └───────────────────────────────────────────────────────────────────┘
     ▲ braid.py   plans the skeleton: spine, nodes, lanes (u, v frame)
     ▲ stages.py  carves it; biome recipes, elevation tiers, HM obstacles
     ▲ places.py  special areas and hidden things
     ▲ areas/     recipes, stamps, overworld Pokémon, events, sub-maps
                                                      │
                     Route: cls grid + zones + events │
                                                      ▼
             ┌──────────────────────────┬─────────────────────────────┐
             │ autotile.py (learned)    │ ruletile.py (rule-based)    │
             │ model from learn.py,     │ class table for a custom    │
             │ models/<biome>.json      │ tileset (ARK_ROM)           │
             └────────────┬─────────────┴──────────────┬──────────────┘
                          ▼                            ▼
                     Layout: metatile, collision, elevation per cell
                                                      │
                                                      ▼
  export.py   data/layouts/<Id>/{map.bin, border.bin, layouts_entry.json}
              data/maps/<Id>/{map.json, scripts.inc}, flags, exits
              scene.json, report.json, plan.png, stages.png, emerald.png
```

## The pieces

### Spec

`spec.py` holds `RouteSpec`, a dataclass with defaults for every field. A YAML file only sets what differs. The region generator writes the same YAML, plus a `region` block (role, goal, elevation tiers) that the stages read when it is there.

### Route

`generate.Route` is the shared state: `cls`, a 2D array of one-character terrain classes (`f` tree, `g` grass, `p` road, `t` tall grass, `w` water, `r` rock, `s` ledge, and the extra codes in `classify.py`), plus zones (lanes, clearings, special areas, lakes) and events (signs, items, cut trees, NPCs). Each stage calls `route.snap(name)`, which is what `--stages` and the web UI play back.

### Skeleton

`braid.plan` runs once and carves nothing. It lays the spine from the entry exit to the far exit as one straight leg or an L or U, puts nodes on it, and plans the lanes between nodes as orthogonal polylines in a per-leg (u, v) frame. An owner grid checks that new lanes touch other structures only at their own junctions and keep `GAP` trees from everything else. `min_run` sets the shortest straight run between turns.

### Special areas

`places.py` picks areas for the route and finds sites for them. `areas/` builds each one from a recipe of stamps (split oak, fallen log, rock ring, pond, ...). It also returns the access gate (cut tree, hidden gap, ledge, NPC block, boulder), the overworld Pokémon as `OBJ_EVENT_GFX_SPECIES` object events with stub scripts, and the flags to allocate. Areas are matched to habitats from a regional Pokédex file. Without one, built-in shapes are used.

### Tiling

`learn.py` walks every pret layout with the target primary tileset. For each cell it records which metatile appears for each 3x3 class neighbourhood, which metatiles sit next to each other, and how often each metatile is blocked. `autotile.py` starts each cell from the neighbourhood statistics, backing off to a plus shape and then the class alone, and runs a few sweeps of iterated conditional modes so neighbours form pairs that occur in the corpus. `models/index.json` maps biomes to models. `ruletile.py` covers tilesets no map uses yet, with blob47 edges, 2x2 trees and ledge rows.

### Export

`export.py` writes Porymap-format files for pokeemerald-expansion. Connection offsets between routes generated together come from `region_offsets`. The rest are left at 0 for an installer to fill in.

### Web UI

`webui/server.py` is a stdlib HTTP server. `POST /api/generate` runs the stages, tiles every snapshot in a worker pool (`fasttile.py`), and returns the class grids and metatile ids. The page draws them from `/api/atlas.png`. `GET /api/export` returns a zip of the export. `region_api.py` serves `/region.html` and passes a route's region block to the generator.
