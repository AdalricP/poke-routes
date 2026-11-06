# pokeroutes.region: state

Run: `python3 -m pokeroutes.region generate --seed 1 [--screens DIR] [--no-strip]` (~2-4 s).
Writes region_out/<seed>/: region.png (map + legend), region_map.png (bare, for the web page),
stages.png (6-panel strip), region.json, routes/<Id>.yaml (one poke-routes spec per route).

Modules: world.py (state), terrain.py (coast with two halves + strait, fBm + domain warp,
stream-power + thermal erosion, priority-flood lakes, flow accumulation rivers, westerly
rain shadow climate, biomes), legends.py (Glutberg cone/lava/ash plume, Deep Rinne trench +
storm coast + Brandung Caves, Romowe ring valley + pillar, Silver Skerries + whirlpools,
Brandwald + Gold Feather Tower + beast stations), settle.py (4 cities), network.py (A* with
turn penalty, rects grown flush to neighbours, exits, roles, names, wild dead ends),
export.py (YAML specs), render.py, cli.py, pipeline.py.

Web: webui/region_api.py + static/region.html, region.js, region-bridge.js; 3-line hook in
server.py. /region.html -> click a route -> bench opens pre-filled; the bridge adds `region`
to /api/generate.

Known gaps / next:
- RouteSpec keeps `region` now. The generator uses role/shape, goal and elevation.tiers; not yet
  water.grid, rivers, waterfalls, coast, biome grid/gradient, path or landmarks.
- Some seeds: Romowe gate ends up inside the Pilsau-Kaup route (logged; route lists it).
- Rects near crowded spots (around the capital) can be small; labels overlap there.
- 2026-11-03: the coast used to be fixed ellipses, so every seed gave the same two islands.
  Now lobe count/placement, strait, spit arc, Nehrung size and coast roughness come from the
  seed (lore layout kept). Seeds 1-60 all build; ~1 in 8 drops an optional link
  (often the Huettenau-Pilsau loop), seed 31 loses Pilsau-Kaup. Worth a look.
- 2026-11-06: the owner asked why the geography was always the same map. Default is now a
  generic generator (geography.py): the seed picks continent / coast / peninsula / islands /
  archipelago / inland lakes / crescent and shapes it; no legendaries, no towns, so no routes
  until towns are placed in the editor (routes follow the shortest tree between them).
  The old map, legends and four towns are the opt-in `--preset arklarna`, unchanged (seed 1
  and 7 builds matched the old hashes). `w.half` became `w.landmass`; `w.strait` is None
  outside the preset. Open: land touching the map edge drains off-map; sea links between towns
  on different islands rarely find a way.
