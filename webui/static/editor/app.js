/* Region editor: draws an editable region on a canvas; brushes and moves happen
   here, the downstream stages (water, climate, biomes, routes) run on the server.
   Pure logic lives in editor/logic.js (window.ED) and editor/map-grid-logic.js (window.MAP_GRID).

   Embeddable: PREditor.init(container, opts) builds the editor inside `container`
   and returns its actions (undo, redo, save, load, generate, exportSpecs,
   recompute, setTool, toggleLayer, fit, openInBench, set_editor_mode, set_route_opener,
   getDoc, destroy), so a host page or shell can drive it from its own menus.
   opts: {seed, load, keys: true, mode: "geography" | "region" | "story" (default: every tool), onOpenRoute}.
   The modes are the steps of laying out a game: geography -> route design A (region: towns and
   routes on the terrain) -> story (beats by hand: what happens where, which field move it gives)
   -> back on the map, each route's gate (what it needs, which beat opens it) in its inspector. */
(function (global) {
  "use strict";
  const ED = global.ED;
  const MAP_GRID = global.MAP_GRID;
  const MARKUP = `<div class="ed">
  <header class="ed-top">
    <h1 data-ed="title">region editor</h1>
    <div class="grp grp-generate">
      <label class="inl">seed <input data-ed="seed" type="number" min="0" value="1"></label>
      <button type="button" class="small" data-ed="genBtn">Generate</button>
      <button type="button" class="small" data-ed="diceBtn" title="Random seed and generate">New random</button>
      <select data-ed="geoSel" aria-label="Geography" title="What kind of land a new region gets">
        <option value="">any geography</option>
        <option value="continent">continent</option>
        <option value="coast">coast</option>
        <option value="peninsula">peninsula</option>
        <option value="islands">islands</option>
        <option value="archipelago">archipelago</option>
        <option value="inland">inland lakes</option>
        <option value="crescent">crescent bay</option>
      </select>
    </div>
    <div class="grp">
      <select data-ed="loadSel" aria-label="Saved regions"><option value="">saved regions...</option></select>
      <button type="button" class="small" data-ed="loadBtn">Load</button>
    </div>
    <div class="grp">
      <label class="inl">name <input data-ed="docName" type="text" maxlength="40" value="seed-1"></label>
      <button type="button" class="small" data-ed="saveBtn" title="Ctrl/Cmd + S">Save</button>
      <button type="button" class="small" data-ed="exportBtn" title="region.json + one YAML spec per route">Export specs</button>
    </div>
    <div class="grp">
      <button type="button" class="small" data-ed="undoBtn" title="Ctrl/Cmd + Z">Undo</button>
      <button type="button" class="small" data-ed="redoBtn" title="Ctrl/Cmd + Shift + Z">Redo</button>
      <span class="hist" data-ed="histInfo"></span>
    </div>
    <nav class="links"><a href="/region.html">region</a><a href="/">route bench</a><button type="button" class="small" data-ed="helpBtn" title="?">?</button></nav>
  </header>

  <nav class="rail" data-ed="rail" aria-label="Tools">
    <button type="button" data-edtool="select" class="on"><b>V</b>select</button>
    <hr data-modes="geography">
    <button type="button" data-edtool="raise"><b>R</b>raise</button>
    <button type="button" data-edtool="lower"><b>L</b>lower</button>
    <button type="button" data-edtool="flatten"><b>F</b>flatten</button>
    <button type="button" data-edtool="smooth"><b>S</b>smooth</button>
    <hr data-modes="geography">
    <button type="button" data-edtool="biome"><b>B</b>biome</button>
    <button type="button" data-edtool="water"><b>W</b>coast</button>
    <hr data-modes="">
    <button type="button" data-edtool="route"><b>D</b>route</button>
    <button type="button" data-edtool="town"><b>T</b>town</button>
    <button type="button" data-edtool="erase"><b>E</b>eraser</button>
  </nav>

  <main class="ed-map" data-ed="wrap">
    <canvas data-ed="cv"></canvas>
    <div class="toolbar">
      <div class="seg" role="group" aria-label="Layers">
        <button type="button" class="tog on" data-layer="height" title="1">Height</button>
        <button type="button" class="tog on" data-layer="biome" title="2">Biome</button>
        <button type="button" class="tog on" data-layer="water" title="3">Water</button>
        <button type="button" class="tog on" data-layer="routes" title="4">Routes</button>
        <button type="button" class="tog on" data-layer="labels" title="5">Labels</button>
        <button type="button" class="tog on" data-layer="townmap" title="6">Town map</button>
      </div>
      <div class="seg seg-zoom" role="group" aria-label="Zoom">
        <button type="button" data-ed="zOut" aria-label="Zoom out">-</button>
        <span data-ed="zLbl">1x</span>
        <button type="button" data-ed="zIn" aria-label="Zoom in">+</button>
        <button type="button" data-ed="zFit">Fit</button>
      </div>
    </div>
    <div class="busy" data-ed="busy" hidden><span class="bar"></span><span data-ed="busyText">working</span></div>
    <div class="status" data-ed="status"></div>
    <div class="toast" data-ed="toast" hidden></div>
  </main>

  <aside class="ed-side" data-ed="side">
    <section>
      <h2>Stages</h2>
      <ul class="stages" data-ed="stages"></ul>
    </section>
    <section data-ed="toolOpts">
      <h2 data-ed="toolTitle">Select</h2>
      <div data-ed="toolBody"></div>
    </section>
    <section data-ed="storySection" hidden>
      <h2 data-ed="storyTitle">Story</h2>
      <div data-ed="storyBody"></div>
    </section>
    <section data-ed="routeListSection" hidden>
      <h2>Routes</h2>
      <div class="route-list" data-ed="routeList"></div>
    </section>
    <section>
      <h2 data-ed="inspTitle">Selection</h2>
      <div data-ed="insp"><p class="muted">Click a route, town or landmark.</p></div>
    </section>
    <section>
      <h2>Log</h2>
      <ol class="log" data-ed="log"></ol>
    </section>
  </aside>
</div>

<div class="help" data-ed="help" hidden role="dialog" aria-label="Keyboard shortcuts">
  <div class="help-box">
    <h2>Keys</h2>
    <dl>
      <dt>V R L F S</dt><dd>select, raise, lower, flatten, smooth</dd>
      <dt>B W</dt><dd>paint biome, coast (land / sea / lake)</dd>
      <dt>D T E</dt><dd>Map brushes: route, town, eraser; Esc cancels a stroke</dd>
      <dt>[ ]</dt><dd>brush smaller / bigger</dd>
      <dt>Alt + drag</dt><dd>raise lowers, lower raises</dd>
      <dt>Space + drag</dt><dd>pan (right or middle drag too)</dd>
      <dt>wheel, + - 0</dt><dd>zoom, fit</dd>
      <dt>1 - 6</dt><dd>layers: height, biome, water, routes, labels, town-map style</dd>
      <dt>Ctrl Z / Ctrl Shift Z</dt><dd>undo / redo (100 steps)</dd>
      <dt>C</dt><dd>recompute every stale stage</dd>
      <dt>Del</dt><dd>erase the selected route or town</dd>
      <dt>double-click</dt><dd>open a route in the route bench</dd>
      <dt>Ctrl S</dt><dd>save</dd>
      <dt>Esc</dt><dd>clear selection, close this</dd>
    </dl>
  </div>
</div>`;

  function init(root, opts) {
  opts = opts || {};
  root.innerHTML = MARKUP;
  // Standalone, everything lives under root. Docked in a host (opts.host, the shell), only the map
  // stays here; tools, layers/places and side panels move into the host's toolbar, hierarchy and
  // inspector, and the rest of the chrome is kept detached so its wiring still works.
  const host = opts.host || null;
  const parts = [root];
  const $ = (id) => { for (const p of parts) { const e = p.querySelector(`[data-ed="${id}"]`); if (e) return e; } return null; };
  const $$ = (sel) => parts.flatMap((p) => [...p.querySelectorAll(sel)]);
  const collapsed_tree_groups = new Set();
  // icons for the docked toolbar (dock_into_host); same look as the Scene tool icons.
  const TOOL_ICONS = {
    select: '<path d="M4 2l8 6.2-3.6.5 2 4.1-1.6.8-2-4.1L4 12z" fill="currentColor"/>',
    raise: '<path d="M8 2.5l3 3.5H9v6.5H7V6H4.5z" fill="currentColor"/><rect x="3" y="13.5" width="10" height="1.2" fill="currentColor"/>',
    lower: '<rect x="3" y="1.3" width="10" height="1.2" fill="currentColor"/><path d="M8 13.5l-3-3.5H7V3.5h2V10h2.5z" fill="currentColor"/>',
    flatten: '<rect x="2" y="7.4" width="12" height="1.2" fill="currentColor"/><path d="M8 2l2 2.3H9V6.4H7V4.3H6z" fill="currentColor"/><path d="M8 14l-2-2.3h1.7V9.6h2v2.1H10z" fill="currentColor"/>',
    smooth: '<path d="M2 6c1.3-1.6 2.7-1.6 4 0s2.7 1.6 4 0 2.7-1.6 4 0" stroke="currentColor" stroke-width="1.3" fill="none" stroke-linecap="round"/><path d="M2 10.5c1.3-1.6 2.7-1.6 4 0s2.7 1.6 4 0 2.7-1.6 4 0" stroke="currentColor" stroke-width="1.3" fill="none" stroke-linecap="round" opacity=".5"/>',
    biome: '<path d="M8 2a6 6 0 100 12c.9 0 1.3-.5 1.3-1.1 0-.3-.1-.5-.3-.7a.9.9 0 01-.3-.7c0-.6.5-1 1.1-1H11a3 3 0 003-3c0-3-2.7-5.5-6-5.5z" fill="currentColor"/><circle cx="5.3" cy="7.2" r="1" fill="#3a3a3a"/><circle cx="7.6" cy="4.8" r="1" fill="#3a3a3a"/><circle cx="10.2" cy="6.6" r="1" fill="#3a3a3a"/>',
    water: '<path d="M2 3h12v6.5c-1.3 0-1.3 1.6-2.6 1.6S9.8 9.5 8.5 9.5 7.2 11.1 5.9 11.1 4.6 9.5 3.3 9.5 2 11.1 2 11.1z" fill="currentColor"/>',
    route: '<path d="M2.5 13.5V8h11V2.5" stroke="currentColor" stroke-width="2.2" fill="none" stroke-linejoin="round"/><circle cx="2.5" cy="13.5" r="1.6" fill="currentColor"/><circle cx="13.5" cy="2.5" r="1.6" fill="currentColor"/>',
    town: '<rect x="2" y="2" width="5.5" height="5.5" rx="1.4" fill="currentColor"/><rect x="8.5" y="2" width="5.5" height="5.5" rx="1.4" fill="currentColor"/><rect x="2" y="8.5" width="5.5" height="5.5" rx="1.4" fill="currentColor"/><rect x="8.5" y="8.5" width="5.5" height="5.5" rx="1.4" fill="currentColor" opacity=".35"/>',
    erase: '<path d="M9.2 2.6l4.2 4.2-6.6 6.6H3.9L2.6 12.1a1.4 1.4 0 010-2z" fill="currentColor"/><path d="M5.5 6.3l4.2 4.2" stroke="#3a3a3a" stroke-width="1.2"/><rect x="8" y="12.6" width="6" height="1.2" fill="currentColor"/>',
  };
  if (host) dock_into_host();
  const listeners = [];
  const on = (target, type, fn, o) => { target.addEventListener(type, fn, o); listeners.push([target, type, fn, o]); };
  const S = 5; // base raster pixels per region cell
  const STORE_KEY = "pokeroutes-webui-v1"; // the bench's saved params
  const REGION_KEY = "pokeroutes-region-spec"; // region-bridge.js reads this
  const TOOLS = {
    select: "Select", raise: "Raise", lower: "Lower", flatten: "Flatten", smooth: "Smooth",
    biome: "Paint biome", water: "Coast", route: "Route", town: "Town", erase: "Eraser",
  };
  const MAP_BRUSHES = new Set(["route", "town", "erase"]);
  const BRUSH_TOOLS = new Set(["raise", "lower", "flatten", "smooth", "biome", "water"]);
  const SPEC_FIELDS = [
    ["meander", 0.05], ["max_lanes", 1], ["dead_ends", 1], ["lakes", 1], ["tall_grass", 0.05], ["ledge_chance", 0.05],
    ["hidden_paths", 1], ["hidden_items", 1],
  ];
  const SPEC_FIELD_LABELS = { meander: "Winding", max_lanes: "Paths to the exit", dead_ends: "Side areas", lakes: "Water", tall_grass: "Tall grass",
    ledge_chance: "Ledges", hidden_paths: "Hidden paths", hidden_items: "Hidden items" };
  const SPEC_DEFAULTS = { max_lanes: 3, ledge_chance: 0.35 };
  const WEATHERS = ["WEATHER_SUNNY", "WEATHER_RAIN", "WEATHER_SHADE", "WEATHER_SANDSTORM", "WEATHER_VOLCANIC_ASH", "WEATHER_FOG_HORIZONTAL"];
  const LM_RGB = {
    volcano: "#e65028", trench: "#285ac8", seacave: "#3c78dc", den: "#3cc86e", pillar: "#3cc86e", islands: "#c8dcff",
    whirlpool: "#96c8ff", tower: "#f0be3c", burn: "#c8963c", station: "#ffe68c", summit: "#f0f0f0", lake: "#96d2ff",
    marsh: "#aadcaa", dunes: "#fadc96", deepwood: "#8cdc8c",
  };
  const tools_shown_in_each_editor_mode = {
    geography: ["select", "raise", "lower", "flatten", "smooth", "biome", "water"],
    region: ["select", "route", "town", "erase"],
    story: ["select"],
  };
  const place_kinds_selectable_in_each_editor_mode = { geography: ["landmark"], region: ["city", "route"], story: ["city", "route"] };
  const editor_title_for_each_mode = { geography: "geography editor", region: "map", story: "story" };
  const modes_that_move_places = new Set([null, "geography", "region"]);
  const HM_LABEL = { "": "nothing", cut: "Cut", surf: "Surf", strength: "Strength", rock_smash: "Rock Smash", waterfall: "Waterfall", dive: "Dive" };
  const margin_around_framed_route_in_cells = 6;
  let open_route_spec_in_host = opts.onOpenRoute || null;

  const st = {
    doc: null, hist: new ED.History(100), view: { z: 6, ox: 0, oy: 0 },
    mode: null, tool: "select", brush: 1, strength: 0.03, biome: "f", water: "sea",
    layers: { height: true, biome: true, water: true, routes: true, labels: true, townmap: true },
    sel: null, hover: null, drag: null, space: false, busy: false, unsaved: false, cursor: null,
    open_route: null,
  };
  const cv = $("cv"), ctx = cv.getContext("2d"), wrap = $("wrap");
  const base = document.createElement("canvas"), bctx = base.getContext("2d");
  let baseImg = null, shade = null, riverSegs = null, sea_distance = null, town_map_river_segments = null;
  const RELIEF_EXAGGERATION = 14;

  // ------------------------------------------------------------ server
  async function api(url, body, raw) {
    const r = await fetch(url, body === undefined ? undefined : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (raw && r.ok) return r;
    let j;
    try { j = await r.json(); } catch (e) { throw new Error(`${r.status} ${r.statusText}`); }
    if (!r.ok || j.error) throw new Error(j.error || r.status);
    return j;
  }
  async function busy(text, fn) {
    if (st.busy) return;
    st.busy = true;
    $("busy").hidden = false;
    $("busyText").textContent = text;
    try { return await fn(); } catch (e) { toast(e.message, true); } finally { st.busy = false; $("busy").hidden = true; }
  }
  let toastT = 0;
  function toast(msg, err) {
    if (host) { host.log(msg, !!err); return; }
    const t = $("toast");
    t.textContent = msg;
    t.className = "toast" + (err ? " err" : "");
    t.hidden = false;
    clearTimeout(toastT);
    toastT = setTimeout(() => { t.hidden = true; }, err ? 7000 : 3200);
  }

  // ------------------------------------------------------------ loading a document
  function setDoc(wire, keepView) {
    const doc = ED.decodeDoc(wire);
    const resized = !st.doc || st.doc.W !== doc.W || st.doc.H !== doc.H;
    st.doc = doc;
    if (doc.name) $("docName").value = doc.name;
    $("seed").value = doc.seed;
    if (st.sel && !findSel(st.sel)) st.sel = null;
    rebuildBase();
    if (resized || !keepView) fit();
    refreshAll();
  }
  async function generate() {
    const seed = Math.max(0, parseInt($("seed").value, 10) || 0);
    if (st.unsaved && !(await ask_before_dropping_edits("generate a new region"))) return;
    await busy(`generating seed ${seed}`, async () => {
      setDoc(await api("/api/editor/generate", Object.assign({ seed }, generate_options($("geoSel").value))));
      st.hist = new ED.History(100);
      st.unsaved = false;
      refreshAll();
    });
  }
  function generate_options(geography_choice) {
    if (geography_choice.startsWith("preset:")) return { preset: geography_choice.slice("preset:".length) };
    if (geography_choice) return { preset: "generic", geography: geography_choice };
    return { preset: "generic" };
  }
  async function ask_before_dropping_edits(what_happens_next) {
    if (!window.Shell || !window.Shell.ask) return true;
    const drop_confirmed = await window.Shell.ask({ title: "Unsaved edits", message: `Drop the unsaved edits and ${what_happens_next}?`, ok_label: "Drop edits" });
    return !!drop_confirmed;
  }
  async function loadSaved(name) {
    if (!name) return;
    if (st.unsaved && !(await ask_before_dropping_edits("load " + name))) return;
    await busy(`loading ${name}`, async () => {
      setDoc(await api("/api/editor/load", { name }));
      st.hist = new ED.History(100);
      st.unsaved = false;
      refreshAll();
      toast(`loaded ${name}`);
    });
  }
  async function listSaved() {
    try {
      const j = await api("/api/editor/list");
      const sel = $("loadSel");
      sel.innerHTML = '<option value="">saved regions...</option>';
      for (const r of j.regions) {
        const o = document.createElement("option");
        o.value = r.name;
        o.textContent = r.name + (r.edited ? "  (edited)" : "");
        sel.appendChild(o);
      }
    } catch (e) { /* the list is a nicety */ }
  }
  async function save() {
    if (!st.doc) return;
    const name = $("docName").value.trim() || `seed-${st.doc.seed}`;
    st.doc.name = name;
    await busy("saving", async () => {
      const j = await api("/api/editor/save", { doc: ED.encodeDoc(st.doc), name });
      st.unsaved = false;
      $("docName").value = j.name;
      st.doc.name = j.name;
      toast(`saved region_out/${j.name}/ (${j.routes} route specs)`);
      logLine(`saved to region_out/${j.name}`);
      listSaved();
    });
  }
  async function exportSpecs() {
    if (!st.doc) return;
    st.doc.name = $("docName").value.trim() || st.doc.name;
    await busy("exporting specs", async () => {
      const r = await api("/api/editor/export", { doc: ED.encodeDoc(st.doc) }, true);
      if (!r.ok) throw new Error((await r.json()).error || r.status);
      const blob = await r.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${st.doc.name || "region"}_specs.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    });
  }

  // ------------------------------------------------------------ edits + history
  function commit(label, cause) {
    st.unsaved = true;
    if (cause) st.doc.stale = ED.markStale(st.doc.stale, cause);
    refreshAll(label);
  }
  function undo() {
    if (st.busy) return;
    const l = st.hist.undo(st.doc);
    if (l == null) return;
    afterHistory("undid " + l);
  }
  function redo() {
    if (st.busy) return;
    const l = st.hist.redo(st.doc);
    if (l == null) return;
    afterHistory("redid " + l);
  }
  function afterHistory(msg) {
    st.unsaved = true;
    if (st.sel && !findSel(st.sel)) st.sel = null;
    rebuildBase();
    refreshAll();
    toast(msg);
  }
  async function recompute(button) {
    if (!st.doc) return;
    const stages = ED.stagesFor(button, st.doc.stale);
    if (!stages.length) { toast("nothing stale"); return; }
    await busy("recomputing " + stages.join(", "), async () => {
      const t0 = performance.now();
      const out = await api("/api/editor/recompute", { doc: ED.encodeDoc(st.doc), stages });
      st.hist.begin(st.doc, "recompute " + stages.join(" + "));
      out.name = st.doc.name;
      setDoc(out, true);
      // stages after the last one run stay stale (the server only clears what it ran)
      st.unsaved = true;
      refreshAll();
      toast(`recomputed ${stages.join(", ")} in ${((performance.now() - t0) / 1000).toFixed(1)} s`);
    });
  }
  async function generate_map() {
    if (!st.doc) return;
    await busy("generating towns and routes", async () => {
      const t0 = performance.now();
      const out = await api("/api/editor/generate-map", { doc: ED.encodeDoc(st.doc) });
      st.hist.begin(st.doc, "generate map");
      out.name = st.doc.name;
      setDoc(out, true);
      st.unsaved = true;
      refreshAll();
      toast(`map: ${(out.cities || []).length} towns, ${(out.routes || []).length} routes in ${((performance.now() - t0) / 1000).toFixed(1)} s`);
    });
  }
  async function paint_map_cells(cells, brush, label) {
    if (!cells.length) return;
    await busy("painting the map", async () => {
      const out = await api("/api/editor/paint-map", { doc: ED.encodeDoc(st.doc), cells, brush });
      st.hist.begin(st.doc, label);
      out.name = st.doc.name;
      setDoc(out, true);
      st.unsaved = true;
      refreshAll();
    });
  }

  // ------------------------------------------------------------ base raster
  function rebuildBase() {
    const d = st.doc;
    base.width = d.W * S;
    base.height = d.H * S;
    baseImg = bctx.createImageData(base.width, base.height);
    shade = ED.shadeGrid(d.grids.height, d.W, d.H, RELIEF_EXAGGERATION);
    sea_distance = town_map_shown() ? ED.sea_distance_to_land(d, 3) : null;
    town_map_river_segments = town_map_shown() ? ED.river_segments_by_cell(d) : null;
    paint_base_cells(d, null);
    bctx.putImageData(baseImg, 0, 0);
    riverSegs = null;
    draw();
  }
  function updateBase(rect) {
    const d = st.doc;
    const r = [Math.max(0, rect[0] - 2), Math.max(0, rect[1] - 2), Math.min(d.W, rect[2] + 2), Math.min(d.H, rect[3] + 2)];
    shade = ED.shadeGrid(d.grids.height, d.W, d.H, RELIEF_EXAGGERATION);
    if (town_map_shown()) sea_distance = ED.sea_distance_to_land(d, 3);
    if (town_map_shown()) town_map_river_segments = ED.river_segments_by_cell(d);
    paint_base_cells(d, r);
    bctx.putImageData(baseImg, 0, 0, r[0] * S, r[1] * S, (r[2] - r[0]) * S, (r[3] - r[1]) * S);
    riverSegs = null;
    draw();
  }
  function town_map_shown() { return !!st.layers.townmap && st.mode !== "geography"; }
  function paint_base_cells(d, cell_rect) {
    if (town_map_shown()) ED.renderTownMap(d, st.layers, S, baseImg.data, cell_rect, shade, sea_distance, town_map_river_segments);
    else ED.renderBase(d, st.layers, S, baseImg.data, cell_rect, shade);
  }
  function rivers() {
    if (riverSegs) return riverSegs;
    const d = st.doc, g = d.grids, W = d.W;
    const segs = [];
    let amax = 1;
    for (let i = 0; i < g.river.length; i++) if (g.river[i] && g.flow[i] > amax) amax = g.flow[i];
    for (let i = 0; i < g.river.length; i++) {
      if (!g.river[i]) continue;
      const x = i % W, y = (i / W) | 0;
      const wdt = 0.18 + 0.5 * Math.sqrt(Math.min(1, (g.flow[i] || 0) / amax));
      const r = g.recv ? g.recv[i] : -1;
      let to = null;
      if (r >= 0 && (g.river[r] || g.lake[r] || !g.land[r]) && Math.abs((r % W) - x) <= 1) to = [r % W, (r / W) | 0];
      if (!to) { // no downhill neighbour on the river: join 4-neighbours that are river too
        for (const [dx, dy] of [[1, 0], [0, 1]]) {
          const j = (y + dy) * W + x + dx;
          if (x + dx < W && y + dy < d.H && g.river[j]) segs.push([x, y, x + dx, y + dy, wdt]);
        }
        segs.push([x, y, x, y, wdt]);
        continue;
      }
      segs.push([x, y, to[0], to[1], wdt]);
    }
    riverSegs = segs;
    return segs;
  }

  // ------------------------------------------------------------ view
  function resize() {
    const dpr = window.devicePixelRatio || 1;
    const r = wrap.getBoundingClientRect();
    cv.width = Math.max(1, Math.round(r.width * dpr));
    cv.height = Math.max(1, Math.round(r.height * dpr));
    draw();
  }
  function fit() {
    if (!st.doc) return;
    const b = wrap.getBoundingClientRect();
    const top = host ? 0 : 44; // standalone, the layer bar floats over the top of the map
    const z = Math.max(1, Math.min((b.width - 40) / st.doc.W, (b.height - 46 - top) / st.doc.H));
    st.view = { z, ox: (b.width - st.doc.W * z) / 2, oy: top + (b.height - top - st.doc.H * z) / 2 };
    draw();
  }
  function zoomAt(f, sx, sy) {
    const v = st.view;
    const z = Math.max(1, Math.min(48, v.z * f));
    const mx = (sx - v.ox) / v.z, my = (sy - v.oy) / v.z;
    st.view = { z, ox: sx - mx * z, oy: sy - my * z };
    draw();
  }
  function toCell(sx, sy) { return { fx: (sx - st.view.ox) / st.view.z, fy: (sy - st.view.oy) / st.view.z }; }
  function local(ev) { const b = wrap.getBoundingClientRect(); return [ev.clientX - b.left, ev.clientY - b.top]; }

  // ------------------------------------------------------------ drawing
  let raf = 0;
  function draw() { if (!raf) raf = requestAnimationFrame(() => { raf = 0; paint(); }); }
  function rgb(c, a) { return a == null ? `rgb(${c[0]},${c[1]},${c[2]})` : `rgba(${c[0]},${c[1]},${c[2]},${a})`; }

  function paint() {
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, cv.width, cv.height);
    $("zLbl").textContent = `${(st.view.z / 6).toFixed(st.view.z < 6 ? 2 : 1)}x`;
    if (host && host.onView) host.onView();
    const d = st.doc;
    if (!d) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const { z, ox, oy } = st.view;
    ctx.imageSmoothingEnabled = z < S;
    ctx.fillStyle = "rgba(0,0,0,0.35)";
    ctx.fillRect(ox + 4, oy + 4, d.W * z, d.H * z);
    ctx.drawImage(base, ox, oy, d.W * z, d.H * z);
    const X = (x) => ox + x * z, Y = (y) => oy + y * z;

    const town_map = town_map_shown();
    if (town_map) draw_map_grid_lines(d, X, Y, z);
    if (st.layers.water && !town_map) {
      ctx.strokeStyle = "rgb(70,130,200)";
      ctx.lineCap = "round";
      for (const [x0, y0, x1, y1, w] of rivers()) {
        ctx.lineWidth = Math.max(1, w * z);
        ctx.beginPath();
        ctx.moveTo(X(x0 + 0.5), Y(y0 + 0.5));
        ctx.lineTo(X(x1 + 0.5) + (x0 === x1 && y0 === y1 ? 0.01 : 0), Y(y1 + 0.5));
        ctx.stroke();
      }
    }
    if (st.layers.routes) (town_map ? draw_town_map_routes : drawRoutes)(d, X, Y, z);
    (town_map ? draw_town_map_landmarks : drawLandmarks)(d, X, Y, z);
    if (st.mode !== "geography") (town_map ? draw_town_map_towns : drawCities)(d, X, Y, z);
    if (st.layers.labels) (town_map ? draw_town_map_labels : drawLabels)(d, X, Y, z);
    if (st.mode === "story") drawBeats(d, X, Y, z);
    if (st.mode !== "geography") draw_route_open_in_routes_view(d, X, Y, z);
    drawToolOverlay(d, X, Y, z);
  }

  // the route the Routes view has open: a bright frame and its name, so it can be found while walking exits
  function draw_route_open_in_routes_view(d, X, Y, z) {
    const open_route = st.open_route && ED.routeByName(d, st.open_route);
    if (!open_route) return;
    const [x0, y0, x1, y1] = open_route.rect;
    ctx.save();
    ctx.lineWidth = 4;
    ctx.strokeStyle = "rgba(20,20,20,0.55)";
    ctx.strokeRect(X(x0) - 2, Y(y0) - 2, (x1 - x0) * z + 4, (y1 - y0) * z + 4);
    ctx.lineWidth = 2;
    ctx.strokeStyle = "#ffd65c";
    ctx.strokeRect(X(x0) - 2, Y(y0) - 2, (x1 - x0) * z + 4, (y1 - y0) * z + 4);
    ctx.restore();
  }

  function is_cell_rect_in_view(cell_rect) {
    const map_bounds = wrap.getBoundingClientRect();
    const [x0, y0, x1, y1] = cell_rect;
    const left = st.view.ox + x0 * st.view.z, top = st.view.oy + y0 * st.view.z;
    const right = st.view.ox + x1 * st.view.z, bottom = st.view.oy + y1 * st.view.z;
    const is_inside = left >= 0 && top >= 0 && right <= map_bounds.width && bottom <= map_bounds.height;
    return is_inside;
  }

  function center_cell_rect_in_view(cell_rect) {
    const map_bounds = wrap.getBoundingClientRect();
    const [x0, y0, x1, y1] = cell_rect;
    st.view = Object.assign({}, st.view, {
      ox: map_bounds.width / 2 - (x0 + x1) / 2 * st.view.z,
      oy: map_bounds.height / 2 - (y0 + y1) / 2 * st.view.z,
    });
  }

  function set_open_route(route_name) {
    st.open_route = route_name || null;
    const open_route = st.doc && st.open_route && ED.routeByName(st.doc, st.open_route);
    if (open_route && wrap.getBoundingClientRect().width && !is_cell_rect_in_view(open_route.rect)) center_cell_rect_in_view(open_route.rect);
    draw();
  }

  function place_rect_by_name(d, name) {
    const c = d.cities.find((o) => o.name === name);
    if (c) return c.rect;
    const r = ED.routeByName(d, name);
    return r ? r.rect : null;
  }
  // numbered story beats on their places
  function drawBeats(d, X, Y, z) {
    const fs = Math.round(Math.max(10, Math.min(16, z * 1.5)));
    const stacked = {};
    (d.story || []).forEach((b, i) => {
      const rect = place_rect_by_name(d, b.at);
      if (!rect) return;
      const k = stacked[b.at] = (stacked[b.at] || 0) + 1;
      const cx = X(rect[0]) + fs * (1.1 * k), cy = Y(rect[1]) + fs * 0.2;
      ctx.fillStyle = "#ffd65c";
      ctx.strokeStyle = "#141414";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(cx, cy, fs * 0.62, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      ctx.font = `600 ${fs * 0.8}px "Pixelify Sans", ui-monospace, monospace`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillStyle = "#141414";
      ctx.fillText(String(i + 1), cx, cy + 0.5);
    });
  }

  function drawRoutes(d, X, Y, z) {
    const selR = st.sel && st.sel.kind === "route" ? st.sel.id : null;
    const hovR = st.hover && st.hover.kind === "route" ? st.hover.id : null;
    for (const r of d.routes) {
      const [x0, y0, x1, y1] = r.rect;
      const c = ED.ROLE_RGB[r.role] || [255, 255, 255];
      const on = r.name === selR, hov = r.name === hovR;
      ctx.fillStyle = rgb(c, on ? 0.24 : hov ? 0.18 : 0.12);
      ctx.fillRect(X(x0), Y(y0), (x1 - x0) * z, (y1 - y0) * z);
      ctx.lineWidth = on ? 2.5 : 1.5;
      ctx.strokeStyle = rgb(c, on ? 1 : 0.85);
      if (r.pinned) ctx.setLineDash([]); else ctx.setLineDash([Math.max(3, z * 0.8), Math.max(2, z * 0.5)]);
      ctx.strokeRect(X(x0) + 0.75, Y(y0) + 0.75, (x1 - x0) * z - 1.5, (y1 - y0) * z - 1.5);
      ctx.setLineDash([]);
    }
    ctx.lineJoin = "round";
    for (const r of d.routes) {
      const p = r.path || [];
      if (p.length < 2) continue;
      ctx.strokeStyle = "rgba(20,20,20,0.45)";
      ctx.lineWidth = Math.max(2, z * 0.42) + 2;
      ctx.beginPath();
      p.forEach(([x, y], k) => (k ? ctx.lineTo(X(x + 0.5), Y(y + 0.5)) : ctx.moveTo(X(x + 0.5), Y(y + 0.5))));
      ctx.stroke();
      ctx.strokeStyle = "rgba(255,255,255,0.9)";
      ctx.lineWidth = Math.max(2, z * 0.42);
      ctx.stroke();
    }
    for (const r of d.routes) {
      const on = r.name === selR;
      r.exits.forEach((e, k) => {
        const [ex, ey] = e.cell;
        const hot = on && st.hover && st.hover.kind === "exit" && st.hover.k === k;
        const pad = on ? Math.max(1.5, z * 0.15) : 0;
        ctx.fillStyle = hot ? "#ffd65c" : "#fff";
        ctx.strokeStyle = "#141414";
        ctx.lineWidth = 1.25;
        ctx.fillRect(X(ex) - pad, Y(ey) - pad, z + 2 * pad, z + 2 * pad);
        ctx.strokeRect(X(ex) - pad, Y(ey) - pad, z + 2 * pad, z + 2 * pad);
      });
      if (r.goal) {
        ctx.strokeStyle = "#fff";
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(X(r.goal[0] + 0.5), Y(r.goal[1] + 0.5), z * 1.4, 0, Math.PI * 2);
        ctx.stroke();
      }
    }
  }

  // ------------------------------------------------------------ town-map style (like the Sinnoh town map)
  const SPECIAL_SPOT_KINDS = new Set(["den", "marsh", "deepwood", "islands", "burn", "dunes", "lake", "trench"]);
  const TOWN_MAP_INK = "#2c2c34";
  function trace_polyline(points, X, Y) {
    ctx.beginPath();
    points.forEach(([x, y], k) => (k ? ctx.lineTo(X(x), Y(y)) : ctx.moveTo(X(x), Y(y))));
  }
  function trace_rounded_rect(x, y, width, height, radius) {
    const r = Math.min(radius, width / 2, height / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + width, y, x + width, y + height, r);
    ctx.arcTo(x + width, y + height, x, y + height, r);
    ctx.arcTo(x, y + height, x, y, r);
    ctx.arcTo(x, y, x + width, y, r);
    ctx.closePath();
  }
  function metro_line_width(z) { return Math.max(3, Math.min(40, z * 1.5)); }
  function trace_metro_line(points, X, Y, corner_radius) {
    ctx.beginPath();
    ctx.moveTo(X(points[0][0]), Y(points[0][1]));
    for (let k = 1; k < points.length - 1; k++) ctx.arcTo(X(points[k][0]), Y(points[k][1]), X(points[k + 1][0]), Y(points[k + 1][1]), corner_radius);
    const last = points[points.length - 1];
    ctx.lineTo(X(last[0]), Y(last[1]));
  }
  function stroke_metro_lines(route_lines, X, Y, width, casing, colour_of_route) {
    const corner_radius = width * 1.6;
    for (const { route, lines } of route_lines) {
      ctx.strokeStyle = route.is_selected ? "#ffd65c" : route.is_hovered ? "#fff6d8" : "rgba(255,255,255,0.96)";
      ctx.lineWidth = width + 2 * casing + (route.is_selected ? 2 : 0);
      for (const line of lines) { trace_metro_line(line, X, Y, corner_radius); ctx.stroke(); }
    }
    for (const { route, lines } of route_lines) {
      ctx.strokeStyle = colour_of_route[route.name];
      ctx.lineWidth = width;
      ctx.setLineDash(route.mode === "sea" ? [width * 1.1, width * 0.7] : []);
      for (const line of lines) { trace_metro_line(line, X, Y, corner_radius); ctx.stroke(); }
    }
    ctx.setLineDash([]);
  }
  function draw_metro_terminus(grid, route, X, Y, width, colour) {
    const terminus = MAP_GRID.terminus_of_route(grid, route);
    if (!terminus) return;
    const [x, y] = terminus.point, [across_x, across_y] = terminus.across;
    const half = width * 1.25;
    ctx.strokeStyle = "rgba(255,255,255,0.96)";
    ctx.lineWidth = width * 0.9 + 4;
    ctx.beginPath();
    ctx.moveTo(X(x) - across_x * half, Y(y) - across_y * half);
    ctx.lineTo(X(x) + across_x * half, Y(y) + across_y * half);
    ctx.stroke();
    ctx.strokeStyle = colour;
    ctx.lineWidth = width * 0.9;
    ctx.stroke();
  }
  function draw_town_map_routes(d, X, Y, z) {
    const selected_name = st.sel && st.sel.kind === "route" ? st.sel.id : null;
    const hovered_name = st.hover && st.hover.kind === "route" ? st.hover.id : null;
    const grid = map_grid();
    const width = metro_line_width(z);
    const colour_of_route = MAP_GRID.route_line_colours(d);
    const route_lines = d.routes.map((route) => ({
      route: Object.assign({ is_selected: route.name === selected_name, is_hovered: route.name === hovered_name }, route),
      lines: MAP_GRID.metro_lines_of_route(grid, route).filter((line) => line.length > 1),
    }));
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    stroke_metro_lines(route_lines, X, Y, width, Math.max(1.5, width * 0.28), colour_of_route);
    ctx.lineCap = "butt";
    for (const route of d.routes) draw_metro_terminus(grid, route, X, Y, width, colour_of_route[route.name]);
  }
  function station_inset(grid) { return grid.cell * 0.2; }
  function trace_station_pieces(pieces, X, Y, z, grow) {
    ctx.beginPath();
    for (const [left, top, right, bottom] of pieces) {
      const x = X(left) - grow, y = Y(top) - grow, width = (right - left) * z + 2 * grow, height = (bottom - top) * z + 2 * grow;
      const radius = Math.min(width, height) * 0.22;
      ctx.moveTo(x + radius, y);
      ctx.arcTo(x + width, y, x + width, y + height, radius);
      ctx.arcTo(x + width, y + height, x, y + height, radius);
      ctx.arcTo(x, y + height, x, y, radius);
      ctx.arcTo(x, y, x + width, y, radius);
      ctx.closePath();
    }
  }
  function draw_town_map_town(c, X, Y, z) {
    const grid = map_grid();
    const pieces = MAP_GRID.station_pieces_of_town(grid, c, station_inset(grid));
    if (!pieces.length) return;
    const is_selected = st.sel && st.sel.kind === "city" && st.sel.id === c.id;
    const ring = Math.max(2, grid.cell * z * 0.07);
    trace_station_pieces(pieces, X, Y, z, ring + (is_selected ? 2 : 0));
    ctx.fillStyle = is_selected ? "#ffd65c" : TOWN_MAP_INK;
    ctx.fill();
    trace_station_pieces(pieces, X, Y, z, 0);
    ctx.fillStyle = "#ffffff";
    ctx.fill();
  }
  function draw_town_map_towns(d, X, Y, z) {
    for (const c of d.cities) draw_town_map_town(c, X, Y, z);
  }
  function draw_special_spot(l, X, Y, z, is_selected) {
    const [x0, y0, x1, y1] = l.footprint;
    ctx.beginPath();
    ctx.ellipse(X((x0 + x1) / 2), Y((y0 + y1) / 2), Math.max(5, (x1 - x0) * z / 2), Math.max(5, (y1 - y0) * z / 2), 0, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(112,216,190,0.92)";
    ctx.fill();
    ctx.strokeStyle = is_selected ? "#ffd65c" : "rgb(48,148,126)";
    ctx.lineWidth = is_selected ? 3 : 1.75;
    ctx.stroke();
  }
  function draw_landmark_pill(l, X, Y, z, is_selected) {
    const width = Math.max(11, Math.min(30, z * 2.6)), height = Math.max(6, Math.min(16, z * 1.3));
    const left = X(l.x + 0.5) - width / 2, top = Y(l.y + 0.5) - height / 2;
    trace_rounded_rect(left, top, width, height, height / 2);
    ctx.fillStyle = "#5c9ee8";
    ctx.fill();
    ctx.strokeStyle = is_selected ? "#ffd65c" : "#244e8a";
    ctx.lineWidth = is_selected ? 2.5 : 1.5;
    ctx.stroke();
    trace_rounded_rect(left + height * 0.35, top + height * 0.2, width - height * 0.7, Math.max(1.5, height * 0.2), height * 0.1);
    ctx.fillStyle = "rgba(236,246,255,0.8)";
    ctx.fill();
  }
  function draw_town_map_landmarks(d, X, Y, z) {
    for (const l of d.landmarks) {
      const is_selected = !!st.sel && st.sel.kind === "landmark" && st.sel.id === l.id;
      if (SPECIAL_SPOT_KINDS.has(l.kind) && l.footprint) draw_special_spot(l, X, Y, z, is_selected);
      else draw_landmark_pill(l, X, Y, z, is_selected);
    }
  }
  function town_map_label(text, x, y, size, fill, align) {
    ctx.font = `600 ${size}px "Pixelify Sans", ui-monospace, monospace`;
    ctx.textAlign = align || "center";
    ctx.textBaseline = "middle";
    ctx.lineJoin = "round";
    ctx.lineWidth = Math.max(3, size * 0.32);
    ctx.strokeStyle = "rgba(255,255,250,0.92)";
    ctx.strokeText(text, x, y);
    ctx.fillStyle = fill || TOWN_MAP_INK;
    ctx.fillText(text, x, y);
  }
  function draw_town_map_labels(d, X, Y, z) {
    const size = Math.round(Math.max(10, Math.min(15, z * 1.6)));
    const band = metro_line_width(z);
    if (st.layers.routes) for (const r of d.routes) draw_town_map_route_label(r, X, Y, size, band);
    for (const l of d.landmarks) {
      if (["whirlpool", "station", "pillar"].includes(l.kind)) continue;
      town_map_label(l.name, X(l.x + 0.5), Y(l.y + 0.5) + Math.max(10, z * 1.6), Math.round(size * 0.9), "#1e3c6c");
    }
    const grid = map_grid();
    for (const c of d.cities) {
      const [x0, , x1, y1] = c.rect;
      town_map_label(c.name, X((x0 + x1) / 2), Y(y1 - station_inset(grid)) + size * 0.95, Math.round(size * 1.1));
    }
  }
  function route_gate_summary(r) {
    const needs = ED.routeNeeds(r), after = (r.gate || {}).after;
    return [needs ? "needs " + HM_LABEL[needs] : "", after != null ? "after beat " + (after + 1) : ""].filter(Boolean).join(" · ") || "open";
  }
  function draw_town_map_route_label(r, X, Y, size, band) {
    const anchor = MAP_GRID.label_anchor_of_route(map_grid(), r);
    if (!anchor) return;
    const is_selected = !!st.sel && st.sel.kind === "route" && st.sel.id === r.name;
    const gate = route_gate_summary(r);
    const role = r.role.replace(/_/g, " ") + (r.pinned ? " · pinned" : "");
    const second_line = gate !== "open" ? gate : is_selected ? role : "";
    const second_colour = gate !== "open" ? "#963228" : TOWN_MAP_INK;
    const [x, y] = anchor.point;
    if (anchor.runs_across) {
      town_map_label(r.name, X(x), Y(y) - band / 2 - size * 0.75, size);
      if (second_line) town_map_label(second_line, X(x), Y(y) + band / 2 + size * 0.75, Math.round(size * 0.85), second_colour);
      return;
    }
    const left = X(x) + band / 2 + size * 0.5;
    town_map_label(r.name, left, Y(y) - (second_line ? size * 0.5 : 0), size, TOWN_MAP_INK, "left");
    if (second_line) town_map_label(second_line, left, Y(y) + size * 0.6, Math.round(size * 0.85), second_colour, "left");
  }



  function drawCities(d, X, Y, z) {
    for (const c of d.cities) {
      const [x0, y0, x1, y1] = c.rect;
      const on = st.sel && st.sel.kind === "city" && st.sel.id === c.id;
      ctx.fillStyle = "#c45c46";
      ctx.fillRect(X(x0), Y(y0), (x1 - x0) * z, (y1 - y0) * z);
      // a few roofs so it reads as a town
      ctx.fillStyle = "#963f31";
      let s = c.id.length * 7 + 3;
      for (let k = 0; k < ((x1 - x0) * (y1 - y0)) / 7; k++) {
        s = (s * 9301 + 49297) % 233280;
        const bx = x0 + 0.6 + (s / 233280) * (x1 - x0 - 2.2);
        s = (s * 9301 + 49297) % 233280;
        const by = y0 + 0.6 + (s / 233280) * (y1 - y0 - 2);
        ctx.fillRect(X(bx), Y(by), z * 0.9, z * 0.7);
      }
      ctx.strokeStyle = on ? "#ffd65c" : "#28140f";
      ctx.lineWidth = on ? 3 : 2;
      ctx.strokeRect(X(x0), Y(y0), (x1 - x0) * z, (y1 - y0) * z);
    }
  }

  function drawLandmarks(d, X, Y, z) {
    for (const l of d.landmarks) {
      const col = LM_RGB[l.kind] || "#fff";
      const cx = X(l.x + 0.5), cy = Y(l.y + 0.5);
      const on = st.sel && st.sel.kind === "landmark" && st.sel.id === l.id;
      if (["burn", "den", "islands", "trench"].includes(l.kind)) {
        const [x0, y0, x1, y1] = l.footprint;
        ctx.strokeStyle = col;
        ctx.lineWidth = 1;
        ctx.strokeRect(X(x0), Y(y0), (x1 - x0) * z, (y1 - y0) * z);
      }
      const r = Math.max(3.5, z * (l.kind === "station" ? 0.7 : 1.15));
      ctx.fillStyle = col;
      ctx.strokeStyle = on ? "#ffd65c" : "#141414";
      ctx.lineWidth = on ? 2.5 : 1.25;
      ctx.beginPath();
      if (l.kind === "volcano") { ctx.moveTo(cx, cy - r * 1.4); ctx.lineTo(cx - r * 1.3, cy + r); ctx.lineTo(cx + r * 1.3, cy + r); ctx.closePath(); ctx.fill(); ctx.stroke(); }
      else if (l.kind === "whirlpool") { ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.strokeStyle = col; ctx.lineWidth = 2; ctx.stroke(); }
      else { ctx.rect(cx - r, cy - r, 2 * r, 2 * r); ctx.fill(); ctx.stroke(); }
    }
  }

  function label(text, x, y, size, fill) {
    ctx.font = `600 ${size}px "Pixelify Sans", ui-monospace, monospace`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.lineWidth = 3;
    ctx.strokeStyle = "rgba(16,18,16,0.9)";
    ctx.lineJoin = "round";
    ctx.strokeText(text, x, y);
    ctx.fillStyle = fill || "#f4f2ea";
    ctx.fillText(text, x, y);
  }
  function drawLabels(d, X, Y, z) {
    const fs = Math.round(Math.max(10, Math.min(18, z * 1.7)));
    if (st.layers.routes) {
      for (const r of d.routes) {
        const [x0, y0, x1, y1] = r.rect;
        const cx = X((x0 + x1) / 2), cy = Y((y0 + y1) / 2);
        label(r.name, cx, cy - fs * 0.55, fs);
        const gate = route_gate_summary(r);
        if (gate !== "open") {
          label(gate, cx, cy + fs * 0.55, Math.round(fs * 0.78), "#ffb0a0");
        } else {
          label(r.role.replace(/_/g, " ") + (r.pinned ? " · pinned" : ""), cx, cy + fs * 0.55, Math.round(fs * 0.78), rgb(ED.ROLE_RGB[r.role] || [255, 255, 255]));
        }
      }
    }
    for (const l of d.landmarks) {
      if (["whirlpool", "station", "pillar"].includes(l.kind)) continue;
      label(l.name, X(l.x + 0.5), Y(l.y + 0.5) + Math.max(10, z * 2.4), Math.round(fs * 0.85), LM_RGB[l.kind]);
    }
    for (const c of d.cities) {
      const [x0, y0, x1, y1] = c.rect;
      label(c.name, X((x0 + x1) / 2), Y((y0 + y1) / 2), Math.round(fs * 1.15));
    }
  }

  function brush_radius_in_cells() {
    const brush_radius = st.brush <= 1 ? 0 : st.brush / 2;
    return brush_radius;
  }
  function drawToolOverlay(d, X, Y, z) {
    if (st.cursor && BRUSH_TOOLS.has(st.tool) && !st.space) {
      const { fx, fy } = st.cursor;
      const r = brush_radius_in_cells();
      ctx.strokeStyle = "rgba(255,255,255,0.9)";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(X(Math.floor(fx) + 0.5), Y(Math.floor(fy) + 0.5), Math.max(3, (r + 0.5) * z), 0, Math.PI * 2);
      ctx.stroke();
      ctx.strokeStyle = "rgba(0,0,0,0.5)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(X(Math.floor(fx) + 0.5), Y(Math.floor(fy) + 0.5), Math.max(3, r * z) + 1.5, 0, Math.PI * 2);
      ctx.stroke();
    }
    if (st.drag && st.drag.kind === "paint") { draw_map_stroke_preview(d, X, Y, z); return; }
    if (st.cursor && MAP_BRUSHES.has(st.tool) && !st.space) draw_map_brush_hover(d, X, Y, z);
  }
  // ------------------------------------------------------------ painting the Map grid
  function map_grid() { return MAP_GRID.grid_of_doc(st.doc); }
  function grid_cell_under(c) { return MAP_GRID.grid_cell_at(map_grid(), c.fx, c.fy); }
  function start_map_stroke(c) {
    const cell = grid_cell_under(c);
    st.drag = { kind: "paint", brush: st.tool, cells: cell ? [cell] : [] };
    draw();
  }
  function extend_map_stroke(dr, c) {
    const cell = grid_cell_under(c);
    if (!cell) return;
    dr.cells = MAP_GRID.stroke_with_cell(dr.cells, cell);
    draw();
  }
  function finish_map_stroke(dr) {
    const changed_cells = MAP_GRID.cells_the_brush_changes(map_grid(), dr.cells, dr.brush);
    paint_map_cells(changed_cells, dr.brush, { route: "paint route", town: "paint town", erase: "erase" }[dr.brush]);
  }
  function erase_place(place_kind, place) {
    const cells = MAP_GRID.grid_cells_of_place(st.doc, place);
    st.sel = null;
    paint_map_cells(cells, "erase", "erase " + (place_kind === "city" ? "town" : "route"));
  }
  const BRUSH_PREVIEW_COLOURS = { route: "rgba(44,44,52,0.55)", town: "rgba(255,255,255,0.85)", erase: "rgba(214,64,48,0.5)" };
  function trace_grid_cell(grid, cell, X, Y, z, inset) {
    const size = grid.cell * z;
    trace_rounded_rect(X(cell[0] * grid.cell) + inset, Y(cell[1] * grid.cell) + inset, size - 2 * inset, size - 2 * inset, size * 0.16);
  }
  function draw_map_stroke_preview(d, X, Y, z) {
    const grid = map_grid(), dr = st.drag;
    const inset = grid.cell * z * 0.18;
    for (const cell of dr.cells) {
      trace_grid_cell(grid, cell, X, Y, z, inset);
      ctx.fillStyle = BRUSH_PREVIEW_COLOURS[dr.brush];
      ctx.fill();
      ctx.strokeStyle = dr.brush === "erase" ? "#962820" : TOWN_MAP_INK;
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
    if (dr.brush !== "route" || dr.cells.length < 2) return;
    const points = MAP_GRID.without_points_in_line(dr.cells.map((cell) => MAP_GRID.region_centre_of_grid_cell(grid, cell)));
    ctx.strokeStyle = "rgba(255,255,255,0.95)";
    ctx.lineWidth = metro_line_width(z) * 0.45;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    trace_polyline(points, X, Y);
    ctx.stroke();
    ctx.lineCap = "butt";
  }
  function draw_map_brush_hover(d, X, Y, z) {
    const cell = grid_cell_under(st.cursor);
    if (!cell) return;
    const grid = map_grid();
    trace_grid_cell(grid, cell, X, Y, z, 1.5);
    ctx.strokeStyle = st.tool === "erase" ? "rgba(150,40,32,0.9)" : "rgba(44,44,52,0.75)";
    ctx.lineWidth = 1.5;
    ctx.setLineDash([4, 3]);
    ctx.stroke();
    ctx.setLineDash([]);
  }
  function draw_map_grid_lines(d, X, Y, z) {
    const grid = map_grid();
    if (!grid.width || grid.cell * z < 14) return;
    const painting = MAP_BRUSHES.has(st.tool);
    ctx.strokeStyle = painting ? "rgba(44,44,52,0.16)" : "rgba(44,44,52,0.07)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let column = 0; column <= grid.width; column++) { ctx.moveTo(Math.round(X(column * grid.cell)) + 0.5, Y(0)); ctx.lineTo(Math.round(X(column * grid.cell)) + 0.5, Y(grid.height * grid.cell)); }
    for (let row = 0; row <= grid.height; row++) { ctx.moveTo(X(0), Math.round(Y(row * grid.cell)) + 0.5); ctx.lineTo(X(grid.width * grid.cell), Math.round(Y(row * grid.cell)) + 0.5); }
    ctx.stroke();
  }

  // ------------------------------------------------------------ hit testing (select tool)
  function findSel(sel) {
    if (!st.doc || !sel) return null;
    if (sel.kind === "route") return ED.routeByName(st.doc, sel.id);
    if (sel.kind === "city") return st.doc.cities.find((c) => c.id === sel.id) || null;
    if (sel.kind === "landmark") return st.doc.landmarks.find((l) => l.id === sel.id) || null;
    return null;
  }
  function hitSelect(sx, sy) {
    const d = st.doc;
    const { fx, fy } = toCell(sx, sy);
    const p = ED.placeAt(d, fx, fy);
    if (p && p.kind === "route" && !st.layers.routes) return null;
    if (p && !is_place_kind_selectable_in_current_mode(p.kind)) return null;
    return p ? { kind: p.kind, id: p.id, place: p } : null;
  }

  // ------------------------------------------------------------ pointer input
  wrap.addEventListener("contextmenu", (e) => e.preventDefault());
  wrap.addEventListener("pointerdown", (ev) => {
    if (!st.doc || st.busy || (ev.target.closest && ev.target.closest(".toolbar"))) return;
    const [sx, sy] = local(ev);
    wrap.setPointerCapture(ev.pointerId);
    const c = toCell(sx, sy);
    if (ev.button === 1 || ev.button === 2 || st.space) {
      st.drag = { kind: "pan", sx, sy, ox: st.view.ox, oy: st.view.oy };
      wrap.classList.add("panning");
      return;
    }
    if (BRUSH_TOOLS.has(st.tool)) {
      const s = st.hist.begin(st.doc, TOOLS[st.tool].toLowerCase());
      const i = Math.floor(c.fy) * st.doc.W + Math.floor(c.fx);
      st.drag = { kind: "brush", s, last: [Math.floor(c.fx), Math.floor(c.fy)], alt: ev.altKey, changed: false,
        target: st.doc.grids.height[Math.max(0, Math.min(st.doc.W * st.doc.H - 1, i))], bb: null };
      dab(c.fx, c.fy);
      return;
    }
    if (MAP_BRUSHES.has(st.tool)) {
      start_map_stroke(c);
      return;
    }
    // select tool
    const h = hitSelect(sx, sy);
    if (!h) {
      st.sel = null;
      refreshInspector();
      st.drag = { kind: "pan", sx, sy, ox: st.view.ox, oy: st.view.oy, click: true };
      draw();
      return;
    }
    st.sel = { kind: h.kind, id: h.id };
    refreshInspector();
    if (h.kind === "landmark" && modes_that_move_places.has(st.mode)) {
      st.drag = { kind: "move", sel: st.sel, cx: Math.floor(c.fx), cy: Math.floor(c.fy), s: null };
    }
    draw();
  });

  wrap.addEventListener("pointermove", (ev) => {
    if (!st.doc) return;
    const [sx, sy] = local(ev);
    const c = toCell(sx, sy);
    st.cursor = c;
    const dr = st.drag;
    if (dr && dr.kind === "pan") {
      st.view.ox = dr.ox + sx - dr.sx;
      st.view.oy = dr.oy + sy - dr.sy;
      draw();
      return;
    }
    if (dr && dr.kind === "brush") {
      const nx = Math.floor(c.fx), ny = Math.floor(c.fy);
      const line = ED.lineCells(dr.last[0], dr.last[1], nx, ny).slice(1);
      for (const [x, y] of line) dab(x + 0.5, y + 0.5, true);
      dr.last = [nx, ny];
      status(c);
      draw();
      return;
    }
    if (dr && dr.kind === "paint") {
      extend_map_stroke(dr, c);
      status(c);
      return;
    }
    if (dr && dr.kind === "move") {
      const nx = Math.floor(c.fx), ny = Math.floor(c.fy);
      const dx = nx - dr.cx, dy = ny - dr.cy;
      if (dx || dy) {
        if (!dr.s) dr.s = st.hist.begin(st.doc, "move " + dr.sel.kind);
        const moved = ED.moveLandmark(st.doc, dr.sel.id, dx, dy);
        if (moved) { dr.cx = nx; dr.cy = ny; dr.changed = true; }
        draw();
      }
      return;
    }
    // hover
    let h = null;
    if (st.tool === "select") h = hitSelect(sx, sy);
    const key = (x) => (x ? `${x.kind}:${x.id || ""}` : "");
    const is_painting_tool = BRUSH_TOOLS.has(st.tool) || MAP_BRUSHES.has(st.tool);
    if (key(h) !== key(st.hover) || is_painting_tool) { st.hover = h; draw(); }
    wrap.style.cursor = st.space ? "grab" : is_painting_tool ? "crosshair" :
      h && h.kind === "landmark" && modes_that_move_places.has(st.mode) ? "move" : h ? "pointer" : "default";
    status(c);
  });

  function endDrag() {
    const dr = st.drag;
    st.drag = null;
    wrap.classList.remove("panning");
    if (!dr) return;
    if (dr.kind === "brush") {
      if (!dr.changed) { st.hist.cancel(); return; }
      const cause = { raise: "terrain", lower: "terrain", flatten: "terrain", smooth: "terrain", biome: "biome", water: "coast" }[st.tool];
      commit(TOOLS[st.tool].toLowerCase(), cause);
      return;
    }
    if (dr.kind === "paint") { draw(); finish_map_stroke(dr); return; }
    if (dr.kind === "move") {
      if (dr.s && !dr.changed) { st.hist.cancel(); return; }
      if (dr.changed) commit("move " + dr.sel.kind, "places");
    }
  }
  wrap.addEventListener("pointerup", endDrag);
  wrap.addEventListener("pointercancel", endDrag);
  wrap.addEventListener("pointerleave", () => { st.cursor = null; st.hover = null; $("status").textContent = ""; draw(); });
  wrap.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    const [sx, sy] = local(ev);
    zoomAt(Math.exp(-ev.deltaY * 0.0015), sx, sy);
  }, { passive: false });
  wrap.addEventListener("dblclick", (ev) => {
    if (!st.doc || st.tool !== "select" || !is_place_kind_selectable_in_current_mode("route")) return;
    const [sx, sy] = local(ev);
    const { fx, fy } = toCell(sx, sy);
    const p = ED.placeAt(st.doc, fx, fy, { noLandmarks: true });
    if (p && p.kind === "route") openInBench(p.obj);
  });

  function dab(fx, fy, moving) {
    const d = st.doc, dr = st.drag;
    const cells = ED.brushCells(d.W, d.H, Math.floor(fx), Math.floor(fy), brush_radius_in_cells());
    if (!cells.length) return;
    const s = dr.s, alt = dr.alt;
    const k = st.strength;
    switch (st.tool) {
      case "raise": ED.brushRaise(d, s, cells, alt ? -k : k); break;
      case "lower": ED.brushRaise(d, s, cells, alt ? k : -k); break;
      case "flatten": ED.brushFlatten(d, s, cells, dr.target, 0.35); break;
      case "smooth": ED.brushSmooth(d, s, cells, 0.5); break;
      case "biome": ED.paintBiome(d, s, cells, st.biome); break;
      case "water": ED.paintWater(d, s, cells, st.water); break;
    }
    dr.changed = true;
    void moving;
    updateBase(ED.bbox(cells, d.W));
  }

  function status(c) {
    const d = st.doc;
    const x = Math.floor(c.fx), y = Math.floor(c.fy);
    if (x < 0 || y < 0 || x >= d.W || y >= d.H) { $("status").textContent = ""; return; }
    const i = y * d.W + x, g = d.grids;
    const b = String.fromCharCode(g.biome[i]);
    const bits = [`x ${x}  y ${y}`, `h ${g.height[i].toFixed(3)}`, ED.BIOME_NAMES[b] || b];
    if (g.tiers) bits.push(`tier ${g.tiers[i]}`);
    if (g.river[i]) bits.push("river");
    if (g.paint_biome[i] || g.paint_water[i]) bits.push("hand-painted");
    $("status").textContent = bits.join("  ·  ");
  }

  // ------------------------------------------------------------ bench drill-down
  async function openInBench(r) {
    await busy("opening " + r.name, async () => {
      const spec = await api("/api/editor/spec", { doc: ED.encodeDoc(st.doc), id: r.id });
      if (open_route_spec_in_host) { open_route_spec_in_host(spec); return; }
      try {
        localStorage.setItem(STORE_KEY, JSON.stringify(ED.benchParams(spec)));
        localStorage.setItem(REGION_KEY, JSON.stringify({ name: spec.name, region: spec.region, endings: spec.endings || [] }));
      } catch (e) { toast("browser storage is blocked; the bench opens with its own settings", true); }
      window.open(`/?region=${encodeURIComponent((st.doc.name || st.doc.seed) + "/" + spec.id)}`, "_blank");
    });
  }

  // ------------------------------------------------------------ side panel
  function refreshAll(label) {
    refreshStages();
    refreshTool();
    refreshInspector();
    refreshHistory();
    refreshLog();
    if (host) { /* the host owns the page title */ }
    else if (label) document.title = "* poke-routes region editor";
    else if (!st.unsaved) document.title = "poke-routes region editor";
    draw();
  }
  function refreshHistory() {
    $("undoBtn").disabled = !st.hist.undos.length;
    $("redoBtn").disabled = !st.hist.redos.length;
    $("histInfo").textContent = st.hist.undos.length ? `${st.hist.undos.length} steps` : "";
  }
  function refreshLog() {
    if (host) return; // docked: toasts go to the host's console instead
    const ol = $("log");
    ol.innerHTML = "";
    for (const l of (st.doc ? st.doc.log : []).slice(-14).reverse()) {
      const li = document.createElement("li");
      li.textContent = l;
      ol.appendChild(li);
    }
  }
  function logLine(t) { if (st.doc) { st.doc.log = (st.doc.log || []).concat([t]); refreshLog(); } }

  function is_stage_shown_in_current_mode(stage_name) {
    if (st.mode === "geography") return stage_name !== "routes";
    if (st.mode === "region" || st.mode === "story") return stage_name === "routes";
    return true;
  }
  function refreshStages() {
    const ul = $("stages");
    ul.innerHTML = "";
    const names = { water: "water: lakes, rivers", climate: "climate", biomes: "biomes, tiers", routes: "routes, roles" };
    for (const s of ED.STAGES) {
      if (!is_stage_shown_in_current_mode(s) || !(st.doc && st.doc.stale.includes(s))) continue;
      const li = document.createElement("li");
      li.className = "stale";
      li.innerHTML = `<span><span class="nm"></span></span><button type="button" class="small">Recompute</button>`;
      li.querySelector(".nm").textContent = names[s];
      const b = li.querySelector("button");
      b.title = s === "routes" ? "Re-lays every unpinned route (C)" : "Runs this stage and the ones after it (C)";
      b.onclick = () => recompute(s);
      ul.appendChild(li);
    }
    focus_side_folds();
  }

  function refreshTool() {
    $$("[data-edtool]").forEach((b) => b.classList.toggle("on", b.dataset.edtool === st.tool));
    $("toolTitle").textContent = TOOLS[st.tool];
    const box = $("toolBody");
    box.innerHTML = "";
    const add = (html) => { const d = document.createElement("div"); d.className = "opt"; d.innerHTML = html; box.appendChild(d); return d; };
    focus_side_folds();
    if (!BRUSH_TOOLS.has(st.tool)) return;
    const d = add(`<div class="slider"><label for="ed-bsz"><span>Brush size</span><output>${st.brush}</output></label><input type="range" id="ed-bsz" min="1" max="16" step="1" value="${st.brush}"></div>`);
    d.querySelector("input").oninput = (e) => { st.brush = +e.target.value; d.querySelector("output").textContent = st.brush; draw(); };
    if (["raise", "lower"].includes(st.tool)) {
      const d = add(`<div class="slider"><label for="ed-bst"><span>Strength</span><output>${st.strength.toFixed(3)}</output></label><input type="range" id="ed-bst" min="0.005" max="0.12" step="0.005" value="${st.strength}"></div>`);
      d.querySelector("input").oninput = (e) => { st.strength = +e.target.value; d.querySelector("output").textContent = st.strength.toFixed(3); };
    }
    if (st.tool === "water") {
      const d = add('<div class="segrow"></div>');
      for (const m of ["land", "sea", "lake"]) {
        const b = document.createElement("button");
        b.type = "button"; b.textContent = m; b.className = st.water === m ? "on" : "";
        b.onclick = () => { st.water = m; refreshTool(); };
        d.firstChild.appendChild(b);
      }
    }
  }

  function el(tag, attrs, text) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
    if (text != null) e.textContent = text;
    return e;
  }
  function row(labelText, input, over, onReset) {
    const d = el("div", { class: "row" + (over ? " ovr" : "") });
    d.append(el("span", {}, labelText), input);
    if (over && onReset) {
      const b = el("button", { type: "button", class: "rst", title: "back to the derived value" }, "×");
      b.onclick = onReset;
      d.append(b);
    }
    return d;
  }
  // one undo step per inspector change
  function edit(label, fn, cause) {
    if (st.busy) return;
    st.hist.begin(st.doc, label);
    if (fn() === false) { st.hist.cancel(); refreshInspector(); return; }
    commit(label, cause);
  }

  function refreshInspector() {
    refresh_route_list();
    refresh_story();
    refresh_tree();
    const box = $("insp");
    box.innerHTML = "";
    const o = findSel(st.sel);
    show_selection_panel_for(o);
    focus_side_folds();
    if (!o) return;
    if (st.mode === "story") return inspect_place_for_story(box, o);
    if (st.sel.kind === "route") return inspectRoute(box, o);
    const isCity = st.sel.kind === "city";
    box.append(el("h3", {}, o.name));
    box.append(el("p", { class: "sub" }, isCity ? `${o.size || "town"} · ${o.half || ""} · gym ${o.gym || "-"}` : `${o.kind}${o.legendary ? " · " + o.legendary : ""}`));
    const nm = el("input", { type: "text", maxlength: "40" });
    nm.value = o.name;
    nm.onchange = () => edit("rename", () => ED.renamePlace(st.doc, st.sel.kind, o.id, nm.value));
    box.append(row("Name", nm));
    const town_cell_count = (o.grid_cells || []).length;
    const pos = isCity ? `${town_cell_count} map cell${town_cell_count === 1 ? "" : "s"}` : `at ${o.x}, ${o.y}`;
    box.append(el("p", { class: "sub" }, pos));
    if (o.story || o.note) box.append(el("p", { class: "story" }, o.story || o.note));
  }
  function show_selection_panel_for(selected_object) {
    const selection_title = selected_object ? ({ route: "Route", city: "Town", landmark: "Landmark" }[st.sel.kind] || "Selection") : "Selection";
    const title_element = $("inspTitle");
    if (title_element) title_element.textContent = selection_title;
  }
  // Docked, the inspector shows only what belongs to the focused thing: the active brush's settings
  // while a brush is in hand, else the selected town, route or landmark, else the region itself.
  function side_fold(fold_name) {
    if (!host) return null;
    return host.side.querySelector(`[data-side-fold="${fold_name}"]`);
  }
  function focus_side_folds() {
    if (!host || !host.side.childElementCount) return;
    const brush_in_hand = BRUSH_TOOLS.has(st.tool);
    const has_selection = !!findSel(st.sel);
    const has_stale_stage = !!st.doc && st.doc.stale.some(is_stage_shown_in_current_mode);
    const visible_folds = { region: !brush_in_hand && !has_selection, selection: !brush_in_hand && has_selection, tool: brush_in_hand, story: false, stages: has_stale_stage };
    for (const [fold_name, is_visible] of Object.entries(visible_folds)) {
      const fold_element = side_fold(fold_name);
      if (fold_element) fold_element.hidden = !is_visible;
    }
  }

  function inspectRoute(box, r) {
    const spec = r.spec || {};
    const ov = r.spec_overrides || {};
    box.append(el("h3", {}, r.name));
    box.append(el("p", { class: "sub" }, `${r.shape || ""} · ${r.mode} · link ${r.link || "-"}`));
    const nm = el("input", { type: "text", maxlength: "40" });
    nm.value = r.name;
    nm.onchange = () => edit("rename route", () => {
      const ok = ED.renameRoute(st.doc, r.name, nm.value);
      if (ok) st.sel.id = nm.value.trim();
      else toast("that name is taken or empty", true);
      return ok;
    });
    box.append(row("Name", nm));
    const pin = el("input", { type: "checkbox", id: "ed-pin" });
    pin.checked = !!r.pinned;
    pin.onchange = () => edit(pin.checked ? "pin route" : "unpin route", () => { r.pinned = pin.checked; if (!r.pinned) delete r.pinned; });
    const pr = el("div", { class: "check", title: "Recompute leaves a pinned route alone" });
    pr.append(pin, el("label", { for: "ed-pin" }, "Pinned"));
    box.append(pr);

    const role = el("select");
    for (const k of ED.ROLES) { const op = el("option", { value: k }, k.replace(/_/g, " ")); if (k === r.role) op.selected = true; role.append(op); }
    role.onchange = () => edit("set role", () => { r.role = role.value; r.manual = Object.assign({}, r.manual, { role: role.value }); r.pinned = true; });
    box.append(row("Role", role, r.manual && r.manual.role, () => edit("reset role", () => { delete r.manual.role; })));

    const bio = el("select");
    const cur = ov.biome || spec.biome;
    for (const b of ED.SPEC_BIOMES) { const op = el("option", { value: b }, b); if (b === cur) op.selected = true; bio.append(op); }
    bio.onchange = () => edit("set biome", () => ED.setSpecField(r, "biome", bio.value));
    box.append(row("Biome", bio, "biome" in ov, () => edit("reset biome", () => ED.setSpecField(r, "biome", undefined))));

    const wc = r.rect[2] - r.rect[0], hc = r.rect[3] - r.rect[1];
    const route_cell_count = (r.grid_cells || []).length;
    box.append(el("p", { class: "sub" }, `${route_cell_count} map cell${route_cell_count === 1 ? "" : "s"} · ${wc * ED.CELL} x ${hc * ED.CELL} metatiles`));

    for (const [k, step] of SPEC_FIELDS) {
      const v = k in ov ? ov[k] : spec[k] != null ? spec[k] : SPEC_DEFAULTS[k];
      if (v == null) continue;
      const inp = el("input", { type: "number", step: String(step), min: "0", value: String(v) });
      inp.onchange = () => edit("set " + k, () => {
        const n = step < 1 ? Math.max(0, Math.min(1, parseFloat(inp.value))) : Math.max(0, parseInt(inp.value, 10));
        if (!isFinite(n)) return false;
        ED.setSpecField(r, k, n);
      });
      box.append(row(SPEC_FIELD_LABELS[k] || k.replace(/_/g, " "), inp, k in ov, () => edit("reset " + k, () => ED.setSpecField(r, k, undefined))));
    }
    const wsel = el("select");
    const wcur = ov.weather || spec.weather;
    for (const w of WEATHERS) { const op = el("option", { value: w }, w.replace("WEATHER_", "").toLowerCase().replace(/_/g, " ")); if (w === wcur) op.selected = true; wsel.append(op); }
    wsel.onchange = () => edit("set weather", () => ED.setSpecField(r, "weather", wsel.value));
    box.append(row("Weather", wsel, "weather" in ov, () => edit("reset weather", () => ED.setSpecField(r, "weather", undefined))));

    box.append(el("h2", { style: "margin-top:12px" }, "Gate"));
    append_route_gate_controls(box, r);

    box.append(el("h2", { style: "margin-top:12px" }, "Exits"));
    const ul = el("ul", { class: "exits2" });
    r.exits.forEach((e) => {
      const li = el("li");
      li.append(el("span", {}, e.side), el("span", { class: "to" }, "to " + (e.to || "nothing")));
      ul.append(li);
    });
    box.append(ul);
    const warns = ED.routeWarnings(st.doc, r);
    if (warns.length) {
      const wl = el("ul", { class: "warns" });
      for (const w of warns) wl.append(el("li", {}, w));
      box.append(wl);
    }
    const btns = el("div", { class: "btns" });
    const ob = el("button", { type: "button", class: "small", title: "Double-click the route on the map" }, "Open in Routes");
    ob.onclick = () => openInBench(r);
    btns.append(ob);
    box.append(btns);
    if (spec.error) box.append(el("p", { class: "warns" }, "spec: " + spec.error));
  }

  // ------------------------------------------------------------ story and route gates
  function hm_select(value, with_derived) {
    const s = el("select");
    if (with_derived) s.append(el("option", { value: "derived" }, "derived"));
    for (const k of [""].concat(ED.HMS)) {
      const op = el("option", { value: k }, HM_LABEL[k]);
      if (k === value) op.selected = true;
      s.append(op);
    }
    return s;
  }
  function beat_select(value, none_label) {
    const s = el("select");
    s.append(el("option", { value: "" }, none_label));
    (st.doc.story || []).forEach((b, i) => {
      const op = el("option", { value: String(i) }, `${i + 1} ${b.name}`);
      if (i === value) op.selected = true;
      s.append(op);
    });
    return s;
  }
  function inspect_place_for_story(box, o) {
    box.append(el("h3", {}, o.name));
    const here = (st.doc.story || []).map((b, i) => [b, i]).filter(([b]) => b.at === o.name);
    box.append(el("p", { class: "sub" }, here.length ? "beats here: " + here.map(([b, i]) => `${i + 1} ${b.name}`).join(", ") : "no beats here yet"));
    const nm = el("input", { type: "text", maxlength: "40", placeholder: "e.g. Badge 1, rival fight" });
    const hm = hm_select("");
    box.append(row("Beat", nm), row("Gives", hm));
    const add = el("button", { type: "button", class: "small" }, "Add beat here");
    add.onclick = () => edit("add beat", () => {
      st.doc.story = (st.doc.story || []).concat([{ name: nm.value.trim() || `Beat ${(st.doc.story || []).length + 1}`, at: o.name, unlocks: hm.value }]);
    });
    const btns = el("div", { class: "btns" });
    btns.append(add);
    box.append(btns);
  }
  function refresh_story() {
    const section = $("storySection");
    section.hidden = st.mode !== "story" || !st.doc;
    if (section.hidden) return;
    const box = $("storyBody");
    box.innerHTML = "";
    const story = st.doc.story || [];
    $("storyTitle").textContent = "Story beats";
    if (!story.length) box.append(el("p", { class: "hint" }, "No beats yet. Pick a town or route on the map and add one. Beat 1 is where the game starts."));
    const ol = el("ol", { class: "beats" });
    story.forEach((b, i) => {
      const li = el("li");
      const nm = el("input", { type: "text", maxlength: "40", value: b.name, "aria-label": `beat ${i + 1} name` });
      nm.onchange = () => edit("rename beat", () => { b.name = nm.value.trim() || b.name; });
      const hm = hm_select(b.unlocks || "");
      hm.title = "field move this beat gives";
      hm.onchange = () => edit("set beat move", () => { b.unlocks = hm.value; });
      const mv = (dk) => () => edit("move beat", () => ED.moveBeat(st.doc, i, i + dk));
      const up = el("button", { type: "button", class: "small", title: "earlier" }, "↑");
      up.onclick = mv(-1);
      const dn = el("button", { type: "button", class: "small", title: "later" }, "↓");
      dn.onclick = mv(1);
      const rm = el("button", { type: "button", class: "small", title: "remove" }, "×");
      rm.onclick = () => edit("remove beat", () => ED.removeBeat(st.doc, i));
      const at = el("span", { class: "at" }, "at " + b.at);
      li.append(nm, hm, up, dn, rm, at);
      ol.append(li);
    });
    box.append(ol);
    const warns = ED.storyWarnings(st.doc);
    if (warns.length) {
      const wl = el("ul", { class: "warns" });
      for (const w of warns) wl.append(el("li", {}, w));
      box.append(wl);
    }
  }
  function append_route_gate_controls(box, r) {
    const g = r.gate || {};
    const hm = hm_select(g.needs != null ? g.needs : "derived", true);
    hm.querySelector('option[value="derived"]').textContent = "derived (" + HM_LABEL[r.mode === "sea" ? "surf" : ""] + ")";
    hm.onchange = () => edit("set route gate", () => set_gate(r, "needs", hm.value === "derived" ? undefined : hm.value));
    box.append(row("Needs", hm, g.needs != null, () => edit("reset gate", () => set_gate(r, "needs", undefined))));
    hm.title = "The field move needed to get onto the route";
    if (!(st.doc.story || []).length && g.after == null) return;
    const after = beat_select(g.after != null ? g.after : -1, "from the start");
    after.onchange = () => edit("set route opens", () => set_gate(r, "after", after.value === "" ? undefined : +after.value));
    box.append(row("Opens after", after, g.after != null, () => edit("reset gate", () => set_gate(r, "after", undefined))));
  }
  function set_gate(r, k, v) {
    r.gate = Object.assign({}, r.gate);
    if (v === undefined) delete r.gate[k]; else r.gate[k] = v;
    if (!Object.keys(r.gate).length) delete r.gate;
    r.pinned = true;
  }

  function deleteSelected() {
    if (!st.sel || !["region", null].includes(st.mode) || !["city", "route"].includes(st.sel.kind)) return;
    const selected_place = findSel(st.sel);
    if (selected_place) erase_place(st.sel.kind, selected_place);
  }

  function refresh_route_list() {
    const route_list_box = $("routeList");
    route_list_box.innerHTML = "";
    if (st.mode !== "region" || !st.doc) return;
    for (const route of st.doc.routes) route_list_box.append(make_route_list_button(route));
  }
  function make_route_list_button(route) {
    const is_selected_route = !!st.sel && st.sel.kind === "route" && st.sel.id === route.name;
    const route_button = el("button", { type: "button", class: "small" + (is_selected_route ? " on" : "") }, route.name);
    route_button.onclick = () => fly_to_route(route);
    return route_button;
  }
  function fly_to_route(route) {
    st.sel = { kind: "route", id: route.name };
    frame_cell_rect_in_view(route.rect);
    refreshInspector();
  }
  function frame_cell_rect_in_view(cell_rect) {
    const [left_cell, top_cell, right_cell, bottom_cell] = cell_rect;
    const map_bounds = wrap.getBoundingClientRect();
    const framed_width_in_cells = right_cell - left_cell + 2 * margin_around_framed_route_in_cells;
    const framed_height_in_cells = bottom_cell - top_cell + 2 * margin_around_framed_route_in_cells;
    const zoom_in_pixels_per_cell = Math.max(1, Math.min(48, (map_bounds.width - 40) / framed_width_in_cells, (map_bounds.height - 90) / framed_height_in_cells));
    st.view = {
      z: zoom_in_pixels_per_cell,
      ox: map_bounds.width / 2 - (left_cell + right_cell) / 2 * zoom_in_pixels_per_cell,
      oy: 22 + map_bounds.height / 2 - (top_cell + bottom_cell) / 2 * zoom_in_pixels_per_cell,
    };
    draw();
  }

  function is_tool_available_in_current_mode(tool_name) {
    if (!st.mode) return true;
    const is_tool_available = tools_shown_in_each_editor_mode[st.mode].includes(tool_name);
    return is_tool_available;
  }
  function is_place_kind_selectable_in_current_mode(place_kind) {
    if (!st.mode) return true;
    const is_place_kind_selectable = place_kinds_selectable_in_each_editor_mode[st.mode].includes(place_kind);
    return is_place_kind_selectable;
  }
  function is_rail_element_shown_in_current_mode(rail_element) {
    if (!st.mode) return true;
    if (rail_element.dataset.edtool) return is_tool_available_in_current_mode(rail_element.dataset.edtool);
    const is_rail_element_shown = (rail_element.dataset.modes || "").split(" ").includes(st.mode);
    return is_rail_element_shown;
  }
  function set_editor_mode(mode_name) {
    if (mode_name === "gates") mode_name = "region";
    st.mode = tools_shown_in_each_editor_mode[mode_name] ? mode_name : null;
    for (const rail_element of $$(".rail > *, .ed-tools > *")) rail_element.hidden = !is_rail_element_shown_in_current_mode(rail_element);
    $("title").textContent = editor_title_for_each_mode[st.mode] || "region editor";
    $("routeListSection").hidden = st.mode !== "region";
    for (const geography_only_row of document.querySelectorAll("[data-geography-only]")) geography_only_row.hidden = st.mode !== "geography" && st.mode !== null;
    $$('[data-layer="routes"]')[0].hidden = st.mode === "geography";
    $$('[data-layer="townmap"]')[0].hidden = st.mode === "geography";
    st.layers.routes = st.mode !== "geography";
    st.layers.townmap = st.mode !== "geography";
    $$("[data-layer]").forEach((b) => b.classList.toggle("on", st.layers[b.dataset.layer]));
    if (!is_tool_available_in_current_mode(st.tool)) st.tool = "select";
    if (st.sel && !is_place_kind_selectable_in_current_mode(st.sel.kind)) st.sel = null;
    st.hover = null;
    refreshTool();
    refreshInspector();
    refreshStages();
    if (st.doc) rebuildBase();
    draw();
  }

  // ------------------------------------------------------------ docked in a host shell
  // Runs before the rest of init: only uses root, parts, host and el.
  function dock_into_host() {
    const top = root.querySelector(".ed-top"), rail = root.querySelector(".rail");
    const side = root.querySelector(".ed-side"), layer_bar = root.querySelector(".ed-map > .toolbar");
    for (const n of [top, rail, side, layer_bar]) n.remove();
    root.querySelector(".ed").classList.add("docked");
    root.querySelector(".busy .bar").className = "spin";
    parts.push(host.tools, host.side, top, side, layer_bar);

    // tools: the shell toolbar's tool group, same button look as the Scene tools
    host.tools.innerHTML = "";
    host.tools.classList.add("ed-tools");
    for (const b of [...rail.children]) {
      if (b.tagName === "HR") { host.tools.append(el("span", { class: "tb-sep", "data-modes": b.dataset.modes || "" })); continue; }
      const key = b.querySelector("b").textContent, name = b.lastChild.textContent;
      const label = name[0].toUpperCase() + name.slice(1);
      const btn = el("button", { type: "button", class: "tb-btn", "data-edtool": b.dataset.edtool, title: `${label} (${key})`, "aria-label": label });
      btn.innerHTML = `<svg viewBox="0 0 16 16" aria-hidden="true">${TOOL_ICONS[b.dataset.edtool] || ""}</svg>`;
      host.tools.append(btn);
    }

    // side panel: the shell inspector's foldouts. Region (name, seed) first, then selection, tool, story, stages.
    host.side.innerHTML = "";
    const fold = (title, nodes, ed_id, title_ed_id) => {
      const d = el("details", { class: "fold", open: "" });
      if (ed_id) d.dataset.ed = ed_id;
      const summary = el("summary", {}, title);
      if (title_ed_id) summary.dataset.ed = title_ed_id;
      const body = el("div", { class: "fold-body" });
      body.append(...nodes);
      d.append(summary, body);
      return d;
    };
    const prop = (label_text, ...controls) => {
      const p = el("div", { class: "prop" });
      const inline = el("span", { class: "inline" });
      inline.append(...controls);
      p.append(el("label", {}, label_text), inline);
      return p;
    };
    const name_input = $("docName"), seed_input = $("seed");
    name_input.className = "grow";
    seed_input.className = "num grow";
    const geography_select = $("geoSel");
    geography_select.className = "grow";
    geography_select.title = "What kind of land Generate makes";
    const seed_row = prop("Seed", seed_input), geography_row = prop("Geography", geography_select);
    seed_row.dataset.geographyOnly = "true";
    geography_row.dataset.geographyOnly = "true";
    const region_fold = fold("Region", [prop("Name", name_input), seed_row, geography_row]);
    region_fold.dataset.sideFold = "region";
    host.side.append(region_fold);
    const section_of = (id) => side.querySelector(`[data-ed="${id}"]`).closest("section");
    const as_fold = (section) => {
      const h = section.querySelector("h2");
      const d = fold(h.textContent, [], section.dataset.ed, h.dataset.ed);
      h.remove();
      d.hidden = section.hidden;
      d.lastChild.append(...section.childNodes);
      return d;
    };
    const side_fold_names = { insp: "selection", toolBody: "tool", storyBody: "story", stages: "stages" };
    for (const [id, fold_name] of Object.entries(side_fold_names)) {
      const side_fold_element = as_fold(section_of(id));
      side_fold_element.dataset.sideFold = fold_name;
      host.side.append(side_fold_element);
    }
  }

  // Hierarchy (docked only): the places this mode can select, then the layers with eye toggles.
  function refresh_tree() {
    if (!host || !host.tree) return;
    const ul = host.tree;
    const scroll = ul.parentElement ? ul.parentElement.scrollTop : 0;
    ul.innerHTML = "";
    if (!st.doc) { ul.append(el("li", { class: "empty" }, "Generating...")); return; }
    const q = (host.filter ? host.filter() : "").trim().toLowerCase();
    const row = (depth, label, meta) => {
      const r = el("div", { class: "row" });
      r.style.paddingLeft = 4 + depth * 12 + "px";
      r.append(el("span", { class: "tw" }, ""), el("span", { class: "lb" }, label));
      if (meta != null) r.append(el("span", { class: "meta" }, String(meta)));
      return r;
    };
    const group = (key, label, items) => {
      const shown = items.filter((it) => !q || it.label.toLowerCase().includes(q));
      if (q && !shown.length) return;
      const open = !!q || !collapsed_tree_groups.has(key);
      const li = el("li", { class: "node" + (open ? "" : " collapsed") });
      const r = row(0, label, items.length);
      r.querySelector(".lb").style.fontWeight = "500";
      const tw = r.querySelector(".tw");
      tw.textContent = items.length ? "▶" : "";
      if (open) tw.classList.add("open");
      r.onclick = () => { if (collapsed_tree_groups.has(key)) collapsed_tree_groups.delete(key); else collapsed_tree_groups.add(key); refresh_tree(); };
      li.append(r);
      const sub = el("ul");
      for (const it of shown) {
        const c = el("li", { class: "node" + (it.on ? " sel" : "") });
        const cr = row(1, it.label, it.meta);
        if (it.color) {
          const ic = el("span", { class: "ic" + (it.icon_class ? " " + it.icon_class : "") });
          ic.style.background = it.color;
          cr.insertBefore(ic, cr.querySelector(".lb"));
        }
        if (it.eye) {
          const b = el("button", { type: "button", class: "eye" + (it.eye.on ? " on" : ""), "aria-label": (it.eye.on ? "Hide " : "Show ") + it.label });
          b.innerHTML = it.eye.on ? host.icons.eye : host.icons.eye_off;
          b.onclick = (ev) => { ev.stopPropagation(); it.eye.toggle(); };
          cr.append(b);
        }
        cr.onclick = it.run;
        if (it.frame) cr.ondblclick = it.frame;
        c.append(cr);
        sub.append(c);
      }
      li.append(sub);
      ul.append(li);
    };
    const pick = (kind, id) => () => { st.sel = { kind, id }; refreshInspector(); draw(); };
    const frame = (rect) => () => frame_cell_rect_in_view(rect);
    const is_on = (kind, id) => !!st.sel && st.sel.kind === kind && st.sel.id === id;
    if (is_place_kind_selectable_in_current_mode("landmark")) {
      group("landmarks", "Landmarks", st.doc.landmarks.map((l) => {
        const rect = l.footprint || [l.x - 3, l.y - 3, l.x + 4, l.y + 4];
        return { label: l.name, meta: l.kind, color: LM_RGB[l.kind] || "#fff", icon_class: "dot", on: is_on("landmark", l.id), run: pick("landmark", l.id), frame: frame(rect) };
      }));
    }
    if (is_place_kind_selectable_in_current_mode("city")) {
      group("cities", "Towns", st.doc.cities.map((c) => ({ label: c.name, meta: c.size, color: "#c45c46", on: is_on("city", c.id), run: pick("city", c.id), frame: frame(c.rect) })));
    }
    if (is_place_kind_selectable_in_current_mode("route")) {
      group("routes", "Routes", st.doc.routes.map((r) => ({ label: r.name, meta: r.role ? r.role.replace(/_/g, " ") : "", color: "#e8d27a", icon_class: "line", on: is_on("route", r.name), run: pick("route", r.name), frame: () => fly_to_route(r) })));
    }
    const layer_names = { height: "Height", biome: "Biome", water: "Water", routes: "Routes", labels: "Labels", townmap: "Town-map style" };
    group("layers", "Layers", Object.keys(layer_names).filter((k) => (k !== "routes" && k !== "townmap") || st.mode !== "geography").map((k) => (
      { label: layer_names[k], eye: { on: st.layers[k], toggle: () => toggleLayer(k) }, run: () => toggleLayer(k) })));
    if (ul.parentElement) ul.parentElement.scrollTop = scroll;
  }

  // ------------------------------------------------------------ toolbar + keys
  function setTool(t) {
    st.tool = t;
    st.hover = null;
    refreshTool();
    draw();
  }
  function toggleLayer(k) {
    st.layers[k] = !st.layers[k];
    $$("[data-layer]").forEach((b) => b.classList.toggle("on", st.layers[b.dataset.layer]));
    if (["height", "biome", "water", "townmap"].includes(k)) rebuildBase(); else draw();
    refresh_tree();
  }
  $$("[data-edtool]").forEach((b) => { b.onclick = () => setTool(b.dataset.edtool); });
  $$("[data-layer]").forEach((b) => { b.onclick = () => toggleLayer(b.dataset.layer); });
  $("zIn").onclick = () => { const b = wrap.getBoundingClientRect(); zoomAt(1.25, b.width / 2, b.height / 2); };
  $("zOut").onclick = () => { const b = wrap.getBoundingClientRect(); zoomAt(0.8, b.width / 2, b.height / 2); };
  $("zFit").onclick = fit;
  $("genBtn").onclick = generate;
  $("diceBtn").onclick = () => { $("seed").value = Math.floor(Math.random() * 100000); generate(); };
  $("seed").addEventListener("keydown", (e) => { if (e.key === "Enter") generate(); });
  $("loadBtn").onclick = () => loadSaved($("loadSel").value);
  $("saveBtn").onclick = save;
  $("exportBtn").onclick = exportSpecs;
  $("undoBtn").onclick = undo;
  $("redoBtn").onclick = redo;
  $("helpBtn").onclick = () => { $("help").hidden = !$("help").hidden; };
  $("help").onclick = (e) => { if (e.target === $("help")) $("help").hidden = true; };
  $("docName").onchange = () => { if (st.doc) { st.doc.name = $("docName").value.trim(); st.unsaved = true; } };

  const KEY_TOOLS = { v: "select", r: "raise", l: "lower", f: "flatten", s: "smooth", b: "biome", w: "water", d: "route", t: "town", e: "erase" };
  on(document, "keydown", (ev) => {
    if (opts.keys === false || !root.isConnected || root.closest("[hidden]")) return; // a host can hide or park the editor
    if (host && host.is_active && !host.is_active()) return; // docked beside another view that has the focus
    if (ev.defaultPrevented) return; // the host's own shortcut took it
    const mod = ev.ctrlKey || ev.metaKey;
    if (host && mod) return; // docked: save / undo / redo come through the host's menus
    if (mod && ev.key.toLowerCase() === "s") { ev.preventDefault(); save(); return; }
    if (ev.target && ev.target.closest && ev.target.closest("input, select, textarea")) return;
    if (mod && ev.key.toLowerCase() === "z") { ev.preventDefault(); ev.shiftKey ? redo() : undo(); return; }
    if (mod && ev.key.toLowerCase() === "y") { ev.preventDefault(); redo(); return; }
    if (mod || ev.altKey && ev.key !== "Alt") return;
    if (ev.key === " ") { ev.preventDefault(); st.space = true; wrap.style.cursor = "grab"; return; }
    if (ev.key === "Escape" && st.drag && st.drag.kind === "paint") { st.drag = null; draw(); return; }
    if (ev.key === "?") { $("help").hidden = !$("help").hidden; return; }
    if (ev.key === "Escape") { $("help").hidden = true; st.sel = null; refreshInspector(); draw(); return; }
    if (!st.doc) return;
    const k = ev.key.toLowerCase();
    if (KEY_TOOLS[k]) { if (is_tool_available_in_current_mode(KEY_TOOLS[k])) setTool(KEY_TOOLS[k]); return; }
    if (ev.key === "[") { st.brush = Math.max(1, st.brush - 1); refreshTool(); draw(); return; }
    if (ev.key === "]") { st.brush = Math.min(16, st.brush + 1); refreshTool(); draw(); return; }
    if ("123456".includes(ev.key)) { toggleLayer(["height", "biome", "water", "routes", "labels", "townmap"][+ev.key - 1]); return; }
    if (ev.key === "+" || ev.key === "=") { $("zIn").onclick(); return; }
    if (ev.key === "-") { $("zOut").onclick(); return; }
    if (ev.key === "0") { fit(); return; }
    if (k === "c") { recompute("stale"); return; }
    if (ev.key === "Delete" || ev.key === "Backspace") { ev.preventDefault(); deleteSelected(); }
  });
  on(document, "keyup", (ev) => { if (ev.key === " ") { st.space = false; wrap.style.cursor = ""; } });

  // ------------------------------------------------------------ boot
  set_editor_mode(opts.mode);
  const ro = new ResizeObserver(resize);
  ro.observe(wrap);
  resize();
  refreshAll();
  listSaved();
  if (document.fonts && document.fonts.load) document.fonts.load('600 14px "Pixelify Sans"').then(draw, () => {});
  if (opts.load) loadSaved(opts.load);
  else if (opts.seed !== null) { if (opts.seed != null) $("seed").value = opts.seed; generate(); }

  return {
    undo, redo, save, generate: (seed) => { if (seed != null) $("seed").value = seed; return generate(); },
    load: loadSaved, listSaved, exportSpecs, recompute, setTool, toggleLayer, fit, openInBench,
    set_editor_mode, set_route_opener: (route_opener) => { open_route_spec_in_host = route_opener; },
    deleteSelected, help: () => { $("help").hidden = !$("help").hidden; },
    generate_map, set_open_route,
    set_paint_biome: (biome_code) => { st.biome = biome_code; setTool("biome"); refreshTool(); },
    paint_biome: () => st.biome,
    tools: Object.keys(TOOLS), layers: () => Object.assign({}, st.layers),
    zoomIn: () => $("zIn").onclick(), zoomOut: () => $("zOut").onclick(), getZoomPct: () => Math.round(st.view.z / 6 * 100),
    getDoc: () => st.doc, isUnsaved: () => st.unsaved,
    can_undo: () => st.hist.undos.length > 0, can_redo: () => st.hist.redos.length > 0,
    saved_names: () => [...$("loadSel").options].map((o) => o.value).filter(Boolean), refresh_tree, select: (sel) => { st.sel = sel; refreshInspector(); draw(); },
    destroy() {
      ro.disconnect();
      for (const [t, type, fn, o] of listeners) t.removeEventListener(type, fn, o);
      root.innerHTML = "";
    },
  };
  }

  global.PREditor = { init };
})(typeof window !== "undefined" ? window : globalThis);
