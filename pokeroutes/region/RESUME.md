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
- Seeds checked: 1, 3, 5, 7, 11. Seed 1 is the reference render.
