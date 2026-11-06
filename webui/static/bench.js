/* Route bench inside the editor shell: spec inspector -> /api/generate -> stage canvases
   drawn from the metatile atlas, plus the hierarchy, the geography / region editor,
   file operations and the menu model. Shell services come from shell.js (window.Shell),
   pure helpers from logic.js (window.PR) and shell-logic.js (window.SL). */
(function () {
  "use strict";
  const PR = window.PR, SL = window.SL, Shell = window.Shell;
  const $ = (id) => document.getElementById(id);
  const el = Shell.el;
  const log = Shell.log;
  const CELL = 16;
  const FADE_MS = 220;
  const FLASH_MS = 1100; // changed cells glow yellow after stepping to a stage, then fade
  const LAND_RATES = [20, 20, 10, 10, 10, 10, 5, 5, 4, 4, 1, 1];
  const WATER_RATES = [60, 30, 5, 4, 1];
  const STORE_KEY = "pokeroutes-webui-v1";       // last params (also written by the region / world editors)
  const REGION_KEY = "pokeroutes-region-spec";    // region block of a route opened from a region
  const PREFS_KEY = "pokeroutes-shell-prefs-v1";
  const VIEW_KEY = "pokeroutes-shell-view-v1";
  // Brushes come from the route's paint palette (GET /api/paint-palette, webui/paint_palette.py):
  // every class its tiler draws, its objects and its exact tiles. One "ledge" brush covers every
  // ledge direction; state.ledgeDir picks the class.
  const LEDGE_BRUSH = SL.LEDGE_BRUSH;
  const store = Shell.store;

  const sget = (k) => { try { return JSON.parse(store.getItem(k) || "null"); } catch (e) { return null; } };
  const sset = (k, v) => { try { store.setItem(k, JSON.stringify(v)); } catch (e) { /* blocked storage */ } };
  const sdel = (k) => { try { store.removeItem(k); } catch (e) { /* blocked storage */ } };

  // ------------------------------------------------------------ preferences
  const PREF_DEFAULTS = { autogen: true, playOnGenerate: false, stageMs: 900, fade: true, ui: "12", fit: "resize", loop: false, tiler: "ark" };
  const prefs = Object.assign({}, PREF_DEFAULTS, sget(PREFS_KEY) || {});
  if (!prefs.tiler_picked_by_hand) prefs.tiler = PREF_DEFAULTS.tiler;
  function savePrefs() { sset(PREFS_KEY, prefs); }
  function applyPrefs() {
    document.documentElement.dataset.ui = prefs.ui === "13" ? "13" : "12";
    $("tbTiler").value = prefs.tiler;
  }

  // ------------------------------------------------------------ state
  const viewSaved = sget(VIEW_KEY) || {};
  const state = {
    atlases: {}, result: null,
    stage: 0, prevStage: null, fadeStart: 0,
    tileCanvases: [], classCanvases: [], changedMasks: [], stageInfo: [], animCells: [], flashStart: 0,
    view: { zoom: 1, ox: 0, oy: 0 },
    layer: viewSaved.layer === "classes" ? "classes" : "tiles",
    ov: Object.assign({ areas: true, events: true, grid: false, markings: true }, viewSaved.ov || {}),
    hover: null, hoverArea: null, sel: null,
    playing: false, timer: null, busy: false,
    specials: [], exits: [], tab: "dex",
    tool: "select", paint: { cells: new Map(), busy: false }, ledgeDir: "s", brush: "g", palette: null, palettes: {},
    region: sget(REGION_KEY),      // {name, region, endings} or null
    file: { handle: null, name: "" },
    saved: "", generated: "",
    expanded: new Set(["route", "g:exits", "g:lanes", "g:special", "g:layers"]),
    route_trail: [],           // names of the region routes walked through, for Back
    connection_labels: [],     // screen boxes of the exit labels drawn on the Scene: {x, y, w, h, i}
  };
  const undoStack = new SL.History(150, 800);
  function saveView() { sset(VIEW_KEY, { layer: state.layer, ov: state.ov }); }

  // ------------------------------------------------------------ inspector controls
  // Built from /api/spec-schema (RouteSpec's dataclass fields), so new generator
  // parameters show up without touching this file.
  const FALLBACK_SCHEMA = { fields: [
    { name: "biome", kind: "str", default: "forest", group: "route", choices: ["forest", "meadow", "riverland"] },
    { name: "width", kind: "int", default: 48, group: "route", min: 24, max: 96, step: 2 },
    { name: "height", kind: "int", default: 80, group: "route", min: 24, max: 112, step: 2 },
    { name: "meander", kind: "float", default: 0.6, group: "shape", min: 0, max: 1, step: 0.05 },
    { name: "branches", kind: "int", default: 2, group: "shape", min: 0, max: 8 },
    { name: "loops", kind: "int", default: 1, group: "shape", min: 0, max: 4 },
    { name: "dead_ends", kind: "int", default: 1, group: "shape", min: 0, max: 6 },
    { name: "clearings", kind: "int", default: 3, group: "shape", min: 0, max: 8 },
    { name: "lakes", kind: "int", default: 1, group: "shape", min: 0, max: 4 },
    { name: "tall_grass", kind: "float", default: 0.35, group: "shape", min: 0, max: 0.9, step: 0.05 },
    { name: "road", kind: "bool", default: true, group: "shape", label: "Dirt road" },
    { name: "hidden_paths", kind: "int", default: 2, group: "secrets", min: 0, max: 6 },
    { name: "hidden_items", kind: "int", default: 4, group: "secrets", min: 0, max: 12 },
  ], exit_fields: [] };
  const FIRST_RUN = { width: 64, height: 72, meander: 0.7, branches: 3, dead_ends: 2, clearings: 4, tall_grass: 0.3 };
  const DEFAULT_EXITS = [{ side: "south", at: 0.45, width: 2 }, { side: "north", at: 0.6, width: 2 }, { side: "east", at: 0.35, width: 3 }];
  const DEFAULT_SPECIALS = ["auto", "auto", "pond"];
  const controls = {};
  let schema = FALLBACK_SCHEMA;
  let fid = 0;
  let pendingGenerate = false;

  const SHORT_LABELS = { road: "Dirt road" }; // the server's hint text is a sentence; the Inspector wants a name
  function labelOf(f) {
    if (SHORT_LABELS[f.name]) return SHORT_LABELS[f.name];
    if (f.label) return f.label.charAt(0).toUpperCase() + f.label.slice(1);
    const t = f.name.replace(/_/g, " ");
    return t.charAt(0).toUpperCase() + t.slice(1);
  }

  function makeControl(f, value) {
    const row = el("div", "prop");
    const id = "f-" + f.name + "-" + (++fid);
    const lab = el("label", "", labelOf(f));
    lab.htmlFor = id;
    lab.title = f.name;
    row.append(lab);
    let get, set;
    if (f.kind === "bool") {
      row.classList.add("check-prop");
      const inp = Object.assign(el("input"), { type: "checkbox", id });
      inp.addEventListener("change", () => edited(f.name));
      row.append(inp);
      get = () => inp.checked;
      set = (v) => { inp.checked = !!v; };
    } else if (f.choices) {
      const sel = Object.assign(el("select"), { id });
      f.choices.forEach((c) => sel.append(Object.assign(el("option", "", c), { value: c })));
      sel.addEventListener("change", () => edited(f.name));
      row.append(sel);
      get = () => sel.value;
      set = (v) => { if (f.choices.includes(v)) sel.value = v; };
    } else if (f.kind === "int" || f.kind === "float") {
      const step = f.step != null ? f.step : f.kind === "int" ? 1 : 0.05;
      const box = el("div", "slider");
      const rng = Object.assign(el("input"), { type: "range", min: f.min, max: f.max, step, id });
      const num = Object.assign(el("input"), { type: "number", min: f.min, max: f.max, step });
      num.setAttribute("aria-label", labelOf(f) + " value");
      const fmt = (v) => (step < 1 ? Number(v).toFixed(2) : String(v));
      rng.addEventListener("input", () => { num.value = fmt(rng.value); sizeWarn(); edited(f.name, true); });
      num.addEventListener("change", () => {
        let v = Number(num.value);
        if (!Number.isFinite(v)) v = Number(rng.value);
        v = Math.max(f.min, Math.min(f.max, v));
        rng.value = v;
        num.value = fmt(rng.value);
        sizeWarn();
        edited(f.name);
      });
      box.append(rng, num);
      row.append(box);
      get = () => (f.kind === "int" ? parseInt(rng.value, 10) : parseFloat(rng.value));
      set = (v) => { rng.value = v; num.value = fmt(rng.value); };
    } else if (f.kind === "range") {
      const box = el("div", "pair");
      const lo = f.min != null ? f.min : 0, hi = f.max != null ? f.max : 100;
      const a = Object.assign(el("input"), { type: "number", min: lo, max: hi, id });
      const b = Object.assign(el("input"), { type: "number", min: lo, max: hi });
      a.setAttribute("aria-label", labelOf(f) + " low");
      b.setAttribute("aria-label", labelOf(f) + " high");
      a.addEventListener("change", () => edited(f.name));
      b.addEventListener("change", () => edited(f.name));
      box.append(a, b);
      row.append(box);
      get = () => [Number(a.value), Number(b.value)].sort((x, y) => x - y);
      set = (v) => { if (Array.isArray(v)) { a.value = v[0]; b.value = v[1]; } };
    } else {
      const inp = Object.assign(el("input"), { type: "text", id });
      inp.addEventListener("change", () => edited(f.name));
      row.append(inp);
      get = () => inp.value;
      set = (v) => { inp.value = v == null ? "" : v; };
    }
    set(value);
    return { el: row, get, set, field: f };
  }

  const GROUPS = ["route", "shape", "secrets", "encounters", "other"];
  function buildControls(saved) {
    for (const g of GROUPS) $("g-" + g).innerHTML = "";
    for (const k of Object.keys(controls)) delete controls[k];
    for (const f of schema.fields) {
      if (f.name === "seed") continue; // has its own box with the dice
      const v = saved && saved[f.name] != null ? saved[f.name] : FIRST_RUN[f.name] != null && !saved ? FIRST_RUN[f.name] : f.default;
      const c = makeControl(f, v);
      ($("g-" + f.group) || $("g-other")).append(c.el);
      controls[f.name] = c;
    }
    $("sec-other").hidden = !$("g-other").children.length;
    $("sec-encounters").hidden = !$("g-encounters").children.length;
    sizeWarn();
  }

  function sizeWarn() {
    if (!controls.width || !controls.height) return;
    $("sizewarn").hidden = PR.gbaSizeOk(controls.width.get(), controls.height.get());
  }

  function renderChips() {
    const ul = $("chips");
    ul.innerHTML = "";
    if (!state.specials.length) { ul.append(el("li", "none", "none")); return; }
    state.specials.forEach((s, i) => {
      const li = el("li", "", s);
      const b = el("button", "", "×");
      b.type = "button";
      b.setAttribute("aria-label", "Remove " + s);
      b.onclick = () => { state.specials.splice(i, 1); renderChips(); edited("special_areas"); };
      li.append(b);
      ul.append(li);
    });
  }
  function addSpecial() {
    const v = $("areaInput").value.trim();
    if (!v || state.specials.length >= 6) return;
    state.specials.push(v);
    $("areaInput").value = "";
    renderChips();
    edited("special_areas");
  }

  function renderExits() {
    const ol = $("exits");
    ol.innerHTML = "";
    state.exits.forEach((e, i) => {
      const li = el("li");
      const sel = el("select");
      sel.setAttribute("aria-label", `Exit ${i + 1} side`);
      ["north", "south", "east", "west"].forEach((s) => sel.append(Object.assign(el("option", "", s), { value: s, selected: s === e.side })));
      const rng = Object.assign(el("input"), { type: "range", min: 0, max: 1, step: 0.05, value: e.at, title: "Position along the edge" });
      rng.setAttribute("aria-label", `Exit ${i + 1} position`);
      const num = Object.assign(el("input"), { type: "number", min: 1, max: 6, value: e.width, title: "Width" });
      num.setAttribute("aria-label", `Exit ${i + 1} width`);
      const x = el("button", "x", "×");
      x.type = "button";
      x.disabled = state.exits.length <= 1;
      x.setAttribute("aria-label", `Remove exit ${i + 1}`);
      if (e.to) li.title = "to " + e.to;
      sel.onchange = () => { e.side = sel.value; edited("exits"); };
      rng.oninput = () => { e.at = +rng.value; edited("exits:" + i, true); };
      num.onchange = () => { e.width = Math.max(1, Math.min(6, parseInt(num.value, 10) || 3)); num.value = e.width; edited("exits"); };
      x.onclick = () => { state.exits.splice(i, 1); renderExits(); edited("exits"); };
      li.append(sel, rng, num, x);
      ol.append(li);
    });
    $("exitAdd").disabled = state.exits.length >= 4;
  }

  function params() {
    const p = {
      name: $("name").value.trim() || "Testweg",
      seed: Math.max(0, parseInt($("seed").value, 10) || 0),
      special_areas: state.specials.slice(),
      exits: state.exits.map((e) => ({ ...e })),
    };
    for (const [k, c] of Object.entries(controls)) p[k] = c.get();
    return p;
  }

  function setParams(p) {
    if (p.name != null) $("name").value = p.name;
    if (p.seed != null) $("seed").value = p.seed;
    for (const [k, c] of Object.entries(controls)) if (p[k] != null) c.set(p[k]);
    if (Array.isArray(p.special_areas)) state.specials = p.special_areas.slice(0, 6);
    if (Array.isArray(p.exits) && p.exits.length >= 1) state.exits = p.exits.map((e) => ({ ...e }));
    renderChips();
    renderExits();
    sizeWarn();
    lastParams = params();
    updateTitle();
  }

  // ------------------------------------------------------------ undo
  let lastParams = null;
  function edited(key, live) {
    const after = params();
    undoStack.push({ kind: "spec", before: lastParams, after, key: live ? key : key + ":" + Date.now(), t: Date.now() });
    lastParams = after;
    sset(STORE_KEY, after);
    updateTitle();
    autoGenerate();
  }
  // Inspector edits regenerate on their own after a short pause (Preferences > Generate on edit).
  let autoTimer = null;
  function autoGenerate() {
    if (!prefs.autogen) return;
    clearTimeout(autoTimer);
    autoTimer = setTimeout(runAutoGenerate, 400);
  }
  function runAutoGenerate() {
    if (state.busy) { autoTimer = setTimeout(runAutoGenerate, 400); return; }
    if (state.generated === JSON.stringify(params())) return;
    generate({ auto: true });
  }
  // Replace every field at once (open, paste, new, region) as one undo step.
  function replaceParams(p, label) {
    const before = params();
    setParams(p);
    undoStack.push({ kind: "spec", before, after: params(), label: label || "spec" });
    sset(STORE_KEY, params());
  }

  async function undo() {
    const e = undoStack.undo();
    if (!e) { Shell.status("Nothing to undo"); return; }
    await applyEntry(e, "before");
    Shell.status("Undid " + entryLabel(e));
  }
  async function redo() {
    const e = undoStack.redo();
    if (!e) { Shell.status("Nothing to redo"); return; }
    await applyEntry(e, "after");
    Shell.status("Redid " + entryLabel(e));
  }
  function entryLabel(e) {
    if (e.kind === "paint") return `paint (${e.cells.length} cells)`;
    const k = String(e.key || e.label || "").split(":")[0];
    return k ? `change to ${k.replace(/_/g, " ")}` : "change";
  }
  async function applyEntry(e, side) {
    if (e.kind === "spec") {
      setParams(e[side]);
      sset(STORE_KEY, params());
      autoGenerate();
    } else if (e.kind === "paint") {
      if (state.result && state.result.id === e.id && await retile(side === "before" ? e.old : e.cells, true)) storePaint(state.result, e[side]);
    }
  }

  // ------------------------------------------------------------ server
  async function fetchJSON(url, opts) {
    const r = await fetch(url, opts);
    let body;
    try { body = await r.json(); } catch (e) { throw new Error(`${r.status} ${r.statusText}`); }
    if (!r.ok || body.error) throw new Error(body.error || `${r.status}`);
    return body;
  }
  const postJSON = (url, body) => fetchJSON(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

  async function atlasFor(url) {
    if (state.atlases[url]) return state.atlases[url];
    const meta = await fetchJSON(url);
    const img = new Image();
    img.src = meta.png;
    await img.decode();
    state.atlases[url] = { meta, img };
    return state.atlases[url];
  }

  async function loadSchema() {
    try {
      const sc = await fetchJSON("/api/spec-schema");
      if (sc && Array.isArray(sc.fields) && sc.fields.length) schema = sc;
    } catch (e) { log("warn", "Could not load the spec schema; using built-in fields", e.message); }
  }

  async function loadOptions() {
    try {
      const o = await fetchJSON("/api/options");
      const dl = $("areaList");
      dl.innerHTML = "";
      ["auto", ...o.archetypes, ...o.species].forEach((v) => dl.append(Object.assign(el("option"), { value: v })));
    } catch (e) { /* the datalist is a nicety */ }
  }

  async function loadTilers() {
    try {
      const t = await fetchJSON("/api/tilers");
      for (const x of t.tilers) {
        const o = $("tbTiler").querySelector(`option[value="${x.id}"]`);
        if (o) { o.disabled = !x.ok; o.title = x.ok ? x.label : x.error; }
        if (!x.ok && prefs.tiler === x.id) { prefs.tiler = "learned"; applyPrefs(); }
      }
      state.tilers = t.tilers;
    } catch (e) { state.tilers = null; }
  }

  // ------------------------------------------------------------ generate
  async function generate(opts) {
    if (state.busy) { pendingGenerate = true; return; }
    const p = params();
    sset(STORE_KEY, p);
    const body = Object.assign({}, p, { tiler: prefs.tiler });
    if (state.region && state.region.region && state.region.name === p.name) {
      body.region = state.region.region;
      if (state.region.endings && state.region.endings.length) body.endings = state.region.endings;
    }
    setBusy(true, "generating");
    stopPlay();
    const t0 = performance.now();
    try {
      const res = await postJSON("/api/generate", body);
      setBusy(true, "drawing tiles");
      const atlas = await atlasFor(res.atlas || "/api/atlas");
      await new Promise((r) => requestAnimationFrame(r));
      const resized = !state.result || state.result.width !== res.width || state.result.height !== res.height;
      state.result = res;
      state.atlas = atlas;
      await load_paint_palette(res);
      state.sel = null;
      undoStack.filter((e) => e.kind !== "paint");
      buildCanvases();
      state.stage = res.stages.length - 1;
      state.prevStage = null;
      renderInfo();
      renderTree();
      renderSelection();
      if (resized || prefs.fit === "always") fit();
      $("sceneMsg").hidden = true;
      state.generated = JSON.stringify(p);
      sset(SL.RECENT_KEY, SL.pushRecent(sget(SL.RECENT_KEY), p, 10, Date.now()));
      reportToConsole(res, (performance.now() - t0) / 1000);
      // a fresh map starts unpainted; only the page-load generate puts the last paint back
      if (opts && opts.keepPaint) { updatePaintCount(); await reapplyPaint(); } else storePaint(res, {});
      if (prefs.playOnGenerate && !(opts && opts.auto) && res.stages.length > 1) { setStage(0, false); startPlay(); }
      else setStage(res.stages.length - 1, false);
      updateTitle();
    } catch (e) {
      $("sceneMsg").hidden = !!state.result;
      log("error", "Generate failed: " + e.message);
    } finally {
      setBusy(false);
      if (pendingGenerate) { pendingGenerate = false; generate(); }
    }
  }

  function regenerate() {
    $("seed").value = Math.floor(Math.random() * 100000);
    edited("seed");
    generate();
  }

  function reportToConsole(r, rt) {
    const t = r.timing || {};
    log("info", `Generated ${r.name} (seed ${r.seed}, ${r.width}×${r.height}, ${r.biome || "?"}, ${r.tiler === "ark" ? "Ark" : "learned"} tiles) in ${rt.toFixed(1)} s`,
      `server ${(t.total || 0).toFixed(2)} s: generate ${(t.generate || 0).toFixed(2)}, final tiling ${(t.tile_final || 0).toFixed(2)}, stage tiling ${(t.tile_stages || 0).toFixed(2)}\n` +
      `${r.stages.length} stages: ${r.stages.map((s) => s.name).join(", ")}`);
    for (const w of r.warnings || []) log("warn", w);
    if (r.log && r.log.length) log("info", `Generator notes (${r.log.length})`, r.log.join("\n"));
    const rows = reportRows(r);
    if (rows.length) log("info", "Generation report", rows.map(([k, v]) => `${k.padEnd(18)} ${v}`).join("\n"));
    if (!r.exportable) log("warn", `${r.width}×${r.height} is too big for the GBA map buffer; Porymap export is disabled`);
  }

  function setBusy(on, text) {
    state.busy = on;
    $("busy").hidden = !on;
    if (text) $("busyText").textContent = text;
    $("tbGenerate").disabled = on;
  }

  // ------------------------------------------------------------ canvases
  function drawStage(i, prev, prevTiles) {
    const r = state.result;
    const W = r.width, H = r.height;
    const st = r.stages[i];
    const { meta, img } = state.atlas;
    const idx = meta.index, cols = meta.columns;
    const c = document.createElement("canvas");
    c.width = W * CELL;
    c.height = H * CELL;
    const g = c.getContext("2d");
    if (prev) g.drawImage(prev, 0, 0); // start from the previous stage; only redraw what changed
    let changed = 0;
    const mask = document.createElement("canvas");
    mask.width = W;
    mask.height = H;
    const mg = mask.getContext("2d");
    mg.fillStyle = "#ffde59";
    for (let k = 0; k < st.tiles.length; k++) {
      const m = st.tiles[k];
      if (prevTiles && prevTiles[k] === m) continue;
      changed++;
      if (prevTiles) mg.fillRect(k % W, Math.floor(k / W), 1, 1);
      const x = (k % W) * CELL, y = Math.floor(k / W) * CELL;
      const a = idx[m];
      if (a == null) { g.fillStyle = "#ff00ff"; g.fillRect(x, y, CELL, CELL); }
      else g.drawImage(img, (a % cols) * CELL, Math.floor(a / cols) * CELL, CELL, CELL, x, y, CELL, CELL);
    }
    const cc = document.createElement("canvas");
    cc.width = W;
    cc.height = H;
    const cg = cc.getContext("2d");
    const data = cg.createImageData(W, H);
    for (let y = 0; y < H; y++) {
      const row = st.cls[y];
      for (let x = 0; x < W; x++) {
        const hex = PR.CLASS_COLORS[row[x]] || "#ff00ff";
        const o = (y * W + x) * 4;
        data.data[o] = parseInt(hex.slice(1, 3), 16);
        data.data[o + 1] = parseInt(hex.slice(3, 5), 16);
        data.data[o + 2] = parseInt(hex.slice(5, 7), 16);
        data.data[o + 3] = 255;
      }
    }
    cg.putImageData(data, 0, 0);
    // every cell drawn with an animated metatile (water, flowers, waterfalls...), so
    // tickAnim() can redraw just those few cells instead of the whole stage each frame
    const anim = [];
    if (meta.anim) {
      const breeze_metatiles = new Set(meta.breeze || []);
      for (let k = 0; k < st.tiles.length; k++) {
        const m = st.tiles[k];
        if (!meta.anim[m]) continue;
        const cell_x = k % W, cell_y = Math.floor(k / W);
        const phase = breeze_metatiles.has(m) ? breeze_phase_for_tree_block(cell_x, cell_y) : 0;
        anim.push({ x: cell_x * CELL, y: cell_y * CELL, mid: m, phase, shown_atlas_cell: -1 });
      }
    }
    return { tiles: c, classes: cc, mask: prevTiles ? mask : null, changed: prevTiles ? changed : st.tiles.length, anim };
  }

  function buildCanvases() {
    const r = state.result;
    state.tileCanvases = [];
    state.classCanvases = [];
    state.changedMasks = [];
    state.stageInfo = [];
    state.animCells = [];
    let prev = null, prevTiles = null;
    r.stages.forEach((st, i) => {
      const d = drawStage(i, prev, prevTiles);
      state.tileCanvases.push(d.tiles);
      state.classCanvases.push(d.classes);
      state.changedMasks.push(d.mask);
      state.animCells.push(d.anim);
      const pz = i ? r.stages[i - 1].zones : 0, pe = i ? r.stages[i - 1].events : 0;
      state.stageInfo.push({ changed: d.changed, zones: st.zones - pz, events: st.events - pe });
      prev = d.tiles;
      prevTiles = st.tiles;
    });
  }

  // ------------------------------------------------------------ tile animation
  // Plays the handful of metatiles that animate in-game (water, flowers, waterfalls) so the
  // scene isn't a dead frame. Only touches the few cells that actually animate.
  const ANIM_MS = 200;
  const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const tile_animation_speeds = [0.25, 0.5, 1, 2, 4];
  const tile_animation = { is_playing: !reduceMotion, speed_index: 2 };
  let animLast = 0, animFrame = 0;
  function tickAnim(t) {
    requestAnimationFrame(tickAnim);
    if (!tile_animation.is_playing || t - animLast < ANIM_MS / tile_animation_speeds[tile_animation.speed_index]) return;
    animLast = t;
    animFrame++;
    draw_tile_animation_frame();
  }
  function draw_tile_animation_frame() {
    const cells = state.animCells[state.stage];
    if (!state.atlas || !cells || !cells.length) return;
    const { meta, img } = state.atlas;
    const g = state.tileCanvases[state.stage].getContext("2d");
    for (const c of cells) {
      const seq = [meta.index[c.mid], ...meta.anim[c.mid]];
      const a = seq[(animFrame + c.phase) % seq.length];
      if (a === c.shown_atlas_cell) continue;
      c.shown_atlas_cell = a;
      g.clearRect(c.x, c.y, CELL, CELL);
      g.drawImage(img, (a % meta.columns) * CELL, Math.floor(a / meta.columns) * CELL, CELL, CELL, c.x, c.y, CELL, CELL);
    }
    draw();
  }
  const breeze_wave_ticks_per_tree_block_step = 3;
  const breeze_phase_jitter_in_ticks = 4;
  function breeze_phase_for_tree_block(cell_x, cell_y) {
    const tree_block_x = Math.floor(cell_x / 2), tree_block_y = Math.floor(cell_y / 2);
    const tree_block_hash = ((tree_block_x * 73856093) ^ (tree_block_y * 19349663)) >>> 0;
    const breeze_phase_in_ticks = (tree_block_x + tree_block_y) * breeze_wave_ticks_per_tree_block_step + (tree_block_hash % breeze_phase_jitter_in_ticks);
    return breeze_phase_in_ticks;
  }
  requestAnimationFrame(tickAnim);
  function toggle_tile_animation() {
    tile_animation.is_playing = !tile_animation.is_playing;
    sync_tile_animation_buttons();
  }
  function step_tile_animation(frame_delta) {
    tile_animation.is_playing = false;
    animFrame = Math.max(0, animFrame + frame_delta);
    draw_tile_animation_frame();
    sync_tile_animation_buttons();
  }
  function change_tile_animation_speed(speed_step) {
    tile_animation.speed_index = Math.max(0, Math.min(tile_animation_speeds.length - 1, tile_animation.speed_index + speed_step));
    sync_tile_animation_buttons();
    Shell.status(`Tile animation speed ${tile_animation_speeds[tile_animation.speed_index]}×`);
  }
  function sync_tile_animation_buttons() {
    const play_button = $("tbPlay");
    play_button.classList.toggle("playing", tile_animation.is_playing);
    play_button.setAttribute("aria-label", tile_animation.is_playing ? "Pause tile animation" : "Play tile animation");
    play_button.title = (tile_animation.is_playing ? "Pause" : "Play") + " tile animation (Space)";
    const current_speed = tile_animation_speeds[tile_animation.speed_index];
    $("tbPrev").title = `Slower animation, now ${current_speed}× (Left arrow)`;
    $("tbNext").title = `Faster animation, now ${current_speed}× (Right arrow)`;
  }

  // ------------------------------------------------------------ scene view
  const wrap = $("viewScene");
  const canvas = $("map");
  const ctx = canvas.getContext("2d");
  wrap.tabIndex = 0;
  wrap.setAttribute("aria-label", "Scene view");

  function resize() {
    const dpr = window.devicePixelRatio || 1;
    const r = wrap.getBoundingClientRect();
    if (!r.width || !r.height) return;
    canvas.width = Math.max(1, Math.round(r.width * dpr));
    canvas.height = Math.max(1, Math.round(r.height * dpr));
    draw();
  }

  function fit() {
    const r = state.result;
    if (!r) return;
    const box = wrap.getBoundingClientRect();
    const z = PR.fitZoom(r.width * CELL, r.height * CELL, box.width, box.height, 16);
    state.view = {
      zoom: z,
      ox: Math.round((box.width - r.width * CELL * z) / 2),
      oy: Math.round(Math.max(16, (box.height - r.height * CELL * z) / 2)),
    };
    draw();
  }

  function zoomBy(dir, sx, sy) {
    const box = wrap.getBoundingClientRect();
    if (sx == null) { sx = box.width / 2; sy = box.height / 2; }
    const nz = PR.stepZoom(state.view.zoom, dir);
    if (nz === state.view.zoom) return;
    state.view = PR.zoomAt(state.view, nz, sx, sy);
    draw();
  }
  function zoomTo(z) {
    const box = wrap.getBoundingClientRect();
    state.view = PR.zoomAt(state.view, z, box.width / 2, box.height / 2);
    draw();
  }

  function centerOn(bbox) {
    const box = wrap.getBoundingClientRect();
    const [x0, y0, x1, y1] = bbox;
    const w = (x1 - x0 + 6) * CELL, h = (y1 - y0 + 6) * CELL;
    const z = Math.max(state.view.zoom, Math.min(PR.fitZoom(w, h, box.width, box.height, 40), 4));
    const cx = ((x0 + x1) / 2) * CELL, cy = ((y0 + y1) / 2) * CELL;
    state.view = { zoom: z, ox: Math.round(box.width / 2 - cx * z), oy: Math.round(box.height / 2 - cy * z) };
    draw();
  }

  // pointer: select / pan / paint / stamp
  let drag = null;
  let wheelAcc = 0;
  let spaceDown = false;
  const pt = (ev) => { const b = wrap.getBoundingClientRect(); return { sx: ev.clientX - b.left, sy: ev.clientY - b.top }; };

  wrap.addEventListener("pointerdown", (ev) => {
    wrap.focus({ preventScroll: true });
    const { sx, sy } = pt(ev);
    const panBtn = ev.button === 1 || ev.button === 2 || spaceDown || state.tool === "pan";
    wrap.setPointerCapture(ev.pointerId);
    if (!panBtn && (state.tool === "paint" || state.tool === "stamp") && ev.button === 0) {
      if (!canPaint(true) || state.paint.busy) { drag = null; return; }
      drag = { mode: "paint" };
      if (state.stage !== state.result.stages.length - 1) { stopPlay(); setStage(state.result.stages.length - 1, false); }
      state.paint.cells = new Map();
      state.paint.last = null;
      paintAt(sx, sy);
      return;
    }
    drag = { mode: "pan", x: ev.clientX, y: ev.clientY, ox: state.view.ox, oy: state.view.oy, moved: false, select: !panBtn && ev.button === 0 };
  });
  wrap.addEventListener("pointermove", (ev) => {
    const { sx, sy } = pt(ev);
    if (drag && drag.mode === "paint") { paintAt(sx, sy); hoverAt(sx, sy); return; }
    if (drag) {
      const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
      if (!drag.moved && Math.abs(dx) + Math.abs(dy) > 3) { drag.moved = true; wrap.classList.add("dragging"); }
      if (drag.moved) {
        state.view.ox = drag.ox + dx;
        state.view.oy = drag.oy + dy;
        hideTip();
        draw();
      }
      return;
    }
    hoverAt(sx, sy);
  });
  wrap.addEventListener("pointerup", (ev) => {
    const d = drag;
    drag = null;
    wrap.classList.remove("dragging");
    if (!d) return;
    if (d.mode === "paint") { commitPaint(); return; }
    if (d.select && !d.moved) { const { sx, sy } = pt(ev); pickAt(sx, sy); }
  });
  wrap.addEventListener("pointercancel", () => { drag = null; wrap.classList.remove("dragging"); state.paint.cells = new Map(); draw(); });
  wrap.addEventListener("contextmenu", (ev) => ev.preventDefault());
  wrap.addEventListener("dblclick", () => { if (state.sel) focusSel(); });
  wrap.addEventListener("pointerleave", () => { state.hover = null; state.hoverArea = null; hideTip(); $("stCell").textContent = ""; draw(); });
  wrap.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    const { sx, sy } = pt(ev);
    if (ev.ctrlKey || ev.metaKey || Math.abs(ev.deltaY) >= Math.abs(ev.deltaX)) {
      wheelAcc += ev.deltaY;
      if (Math.abs(wheelAcc) < 40) return;
      zoomBy(wheelAcc < 0 ? 1 : -1, sx, sy);
      wheelAcc = 0;
    } else {
      state.view.ox -= ev.deltaX;
      draw();
    }
  }, { passive: false });
  wrap.addEventListener("keydown", (ev) => { if (ev.code === "Space") spaceDown = true; });
  window.addEventListener("keyup", (ev) => { if (ev.code === "Space") spaceDown = false; });

  function cellOf(sx, sy) {
    const r = state.result;
    if (!r) return null;
    const c = PR.cellAt(state.view, sx, sy, CELL);
    return c.x >= 0 && c.y >= 0 && c.x < r.width && c.y < r.height ? c : null;
  }

  function hoverAt(sx, sy) {
    const r = state.result;
    if (!r) return;
    const c = cellOf(sx, sy);
    state.hover = c;
    const zones = PR.zonesAt(r, state.stage);
    const area = c && state.ov.areas && state.tool === "select" ? PR.specialAt(zones, c.x, c.y) : null;
    const changed = area !== state.hoverArea;
    state.hoverArea = area;
    if (c) {
      const st = r.stages[state.stage];
      const k = st.cls[c.y][c.x];
      const m = st.tiles[c.y * r.width + c.x];
      $("stCell").textContent = `x ${c.x}  y ${c.y}  ${PR.CLASS_NAMES[k] || k}  0x${m.toString(16).toUpperCase().padStart(3, "0")}`;
    } else $("stCell").textContent = "";
    wrap.classList.toggle("over-connection", state.tool === "select" && !!connection_label_at(sx, sy));
    if (area) showTip(area, sx, sy); else hideTip();
    if (changed || state.tool !== "select") draw();
  }

  function showTip(z, sx, sy) {
    const tip = $("tip");
    const a = z.area || {};
    tip.innerHTML = "";
    tip.append(el("h3", "", z.name));
    tip.append(el("div", "sp", (a.species ? a.species : "no resident species") + (a.archetype ? ` · ${a.archetype}` : "") +
      (a.shiny_boost > 1 ? ` · shiny x${a.shiny_boost}` : "")));
    if (a.description) tip.append(el("p", "", a.description));
    tip.hidden = false;
    const box = wrap.getBoundingClientRect();
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    let x = sx + 16, y = sy + 16;
    if (x + tw > box.width - 8) x = sx - tw - 16;
    if (y + th > box.height - 8) y = sy - th - 16;
    tip.style.left = Math.max(8, x) + "px";
    tip.style.top = Math.max(8, y) + "px";
  }
  function hideTip() { $("tip").hidden = true; }

  // What is under a cell, most specific first.
  function pickAt(sx, sy) {
    const label = connection_label_at(sx, sy);
    if (label) { follow_connection(label.i); return; }
    const r = state.result;
    const c = r && cellOf(sx, sy);
    if (!c || !state.ov.markings) { select(null); return; }
    const zones = PR.zonesAt(r, state.stage);
    const nz = zones.length;
    const evs = PR.eventsAt(r, state.stage);
    let hit = null;
    if (state.ov.events) {
      const i = evs.findIndex((e) => e.x === c.x && e.y === c.y);
      if (i >= 0) hit = { type: "event", i };
    }
    if (!hit && state.ov.areas) {
      const z = PR.specialAt(zones, c.x, c.y);
      if (z) hit = { type: "zone", i: r.zones.indexOf(z) };
    }
    if (!hit) {
      const i = (r.exits || []).findIndex((e) => e.cells.some(([y, x]) => x === c.x && y === c.y));
      if (i >= 0) hit = { type: "exit", i };
    }
    if (!hit) {
      for (let i = nz - 1; i >= 0; i--) {
        const z = r.zones[i];
        if (PR.PATH_KINDS.includes(z.kind) && z.cells.some(([y, x]) => x === c.x && y === c.y)) { hit = { type: "zone", i }; break; }
      }
    }
    if (!hit) {
      for (let i = nz - 1; i >= 0; i--) {
        const z = r.zones[i];
        if (!z.bbox || z.kind === "special") continue;
        const [x0, y0, x1, y1] = z.bbox;
        if (c.x >= x0 && c.x < x1 && c.y >= y0 && c.y < y1) { hit = { type: "zone", i }; break; }
      }
    }
    select(hit || { type: "route" });
  }

  const route_effect_presets = {
    leaves: {
      particle_count: 70, shape: "leaf", colours: ["#d8692c", "#b9381f", "#e9a63a", "#c4822b", "#9c2f1c"],
      fall_speed_in_map_pixels_per_second: [14, 26], drift_speed_in_map_pixels_per_second: [6, 16],
      size_in_map_pixels: [2, 3], sway_amplitude_in_map_pixels: 10, sway_frequency_in_hertz: [0.4, 0.9],
    },
    snow: {
      particle_count: 120, shape: "flake", colours: ["#ffffff", "#f1f6ff", "#dde7f4"],
      fall_speed_in_map_pixels_per_second: [9, 18], drift_speed_in_map_pixels_per_second: [-3, 3],
      size_in_map_pixels: [1, 2], sway_amplitude_in_map_pixels: 6, sway_frequency_in_hertz: [0.2, 0.5],
    },
    rain: {
      particle_count: 140, shape: "streak", colours: ["rgba(214,228,255,0.8)", "rgba(176,204,246,0.65)"],
      fall_speed_in_map_pixels_per_second: [190, 240], drift_speed_in_map_pixels_per_second: [-58, -48],
      size_in_map_pixels: [1, 1], sway_amplitude_in_map_pixels: 0, sway_frequency_in_hertz: [0, 0],
    },
    ash: {
      particle_count: 90, shape: "fleck", colours: ["#8b8681", "#a6a19b", "#6f6b67", "#c0bbb4"],
      fall_speed_in_map_pixels_per_second: [4, 10], drift_speed_in_map_pixels_per_second: [3, 9],
      size_in_map_pixels: [1, 2], sway_amplitude_in_map_pixels: 7, sway_frequency_in_hertz: [0.15, 0.35],
    },
  };
  const rain_streak_length_in_map_pixels = 6;
  const longest_particle_frame_in_seconds = 0.1;
  const largest_particle_pixel_scale = 4;
  const particle_canvas = el("canvas");
  particle_canvas.setAttribute("aria-hidden", "true");
  particle_canvas.style.pointerEvents = "none";
  canvas.after(particle_canvas);
  const particle_context = particle_canvas.getContext("2d");
  const route_effect_particles = { effect: "none", particles: [], last_time_in_milliseconds: 0, is_canvas_clear: true };

  function current_route_effect() {
    const effect_control = controls.effect;
    const selected_effect = effect_control ? effect_control.get() : "none";
    return selected_effect;
  }
  function random_between(range_of_values) {
    const value_in_range = range_of_values[0] + Math.random() * (range_of_values[1] - range_of_values[0]);
    return value_in_range;
  }
  function random_colour_of(preset) {
    const colour = preset.colours[Math.floor(Math.random() * preset.colours.length)];
    return colour;
  }
  function new_particle_for(preset, should_start_anywhere) {
    const particle = {
      x_fraction: Math.random(),
      y_fraction: should_start_anywhere ? Math.random() : -0.02,
      fall_speed: random_between(preset.fall_speed_in_map_pixels_per_second),
      drift_speed: random_between(preset.drift_speed_in_map_pixels_per_second),
      size: Math.round(random_between(preset.size_in_map_pixels)),
      sway_frequency: random_between(preset.sway_frequency_in_hertz),
      sway_phase: Math.random() * Math.PI * 2,
      colour: random_colour_of(preset),
    };
    return particle;
  }
  function particles_for_effect(effect) {
    const preset = route_effect_presets[effect];
    if (!preset) return [];
    const fresh_particles = Array.from({ length: preset.particle_count }, () => new_particle_for(preset, true));
    return fresh_particles;
  }
  function visible_map_rectangle(view_width, view_height) {
    const route_result = state.result;
    const { zoom, ox, oy } = state.view;
    const left = Math.max(0, ox), top = Math.max(0, oy);
    const right = Math.min(view_width, ox + route_result.width * CELL * zoom), bottom = Math.min(view_height, oy + route_result.height * CELL * zoom);
    const visible_rectangle = { left, top, width: right - left, height: bottom - top };
    return visible_rectangle;
  }
  function particle_pixel_scale() {
    const pixel_scale = Math.max(1, Math.min(largest_particle_pixel_scale, Math.round(state.view.zoom)));
    return pixel_scale;
  }
  function wrap_fraction(fraction) {
    const wrapped_fraction = fraction - Math.floor(fraction);
    return wrapped_fraction;
  }
  function advance_particle(particle, preset, elapsed_in_seconds, rectangle, pixel_scale) {
    particle.sway_phase += particle.sway_frequency * elapsed_in_seconds * Math.PI * 2;
    const sway_speed = Math.cos(particle.sway_phase) * preset.sway_amplitude_in_map_pixels * particle.sway_frequency;
    particle.x_fraction = wrap_fraction(particle.x_fraction + (particle.drift_speed + sway_speed) * pixel_scale * elapsed_in_seconds / rectangle.width);
    particle.y_fraction += particle.fall_speed * pixel_scale * elapsed_in_seconds / rectangle.height;
    if (particle.y_fraction <= 1.02) return;
    Object.assign(particle, new_particle_for(preset, false));
  }
  function draw_particle(particle, preset, rectangle, pixel_scale) {
    const screen_x = Math.round(rectangle.left + particle.x_fraction * rectangle.width);
    const screen_y = Math.round(rectangle.top + particle.y_fraction * rectangle.height);
    const square_size = particle.size * pixel_scale;
    particle_context.fillStyle = particle.colour;
    switch (preset.shape) {
      case "streak":
        draw_rain_streak(screen_x, screen_y, particle, pixel_scale);
        break;
      case "leaf": {
        const tumble_width = Math.abs(Math.sin(particle.sway_phase)) > 0.35 ? square_size : pixel_scale;
        particle_context.fillRect(screen_x, screen_y, tumble_width, square_size);
        break;
      }
      default:
        particle_context.fillRect(screen_x, screen_y, square_size, square_size);
    }
  }
  function draw_rain_streak(screen_x, screen_y, particle, pixel_scale) {
    const slant_per_pixel_of_fall = particle.drift_speed / particle.fall_speed;
    for (let streak_step = 0; streak_step < rain_streak_length_in_map_pixels; streak_step++) {
      const step_x = Math.round(screen_x - streak_step * slant_per_pixel_of_fall * pixel_scale);
      particle_context.fillRect(step_x, screen_y - streak_step * pixel_scale, pixel_scale, pixel_scale);
    }
  }
  function clear_route_effect_canvas() {
    if (route_effect_particles.is_canvas_clear) return;
    particle_context.setTransform(1, 0, 0, 1, 0, 0);
    particle_context.clearRect(0, 0, particle_canvas.width, particle_canvas.height);
    route_effect_particles.is_canvas_clear = true;
  }
  function size_particle_canvas_to_scene() {
    if (particle_canvas.width === canvas.width && particle_canvas.height === canvas.height) return;
    particle_canvas.width = canvas.width;
    particle_canvas.height = canvas.height;
    route_effect_particles.is_canvas_clear = true;
  }
  function should_show_route_effect(effect) {
    const has_preset = !!route_effect_presets[effect];
    const is_route_on_screen = !!state.result && state.tileCanvases.length > 0 && Shell.layout.center === "scene" && !wrap.hidden;
    return has_preset && is_route_on_screen;
  }
  function tick_route_effect(time_in_milliseconds) {
    requestAnimationFrame(tick_route_effect);
    const elapsed_in_seconds = Math.min(longest_particle_frame_in_seconds, (time_in_milliseconds - route_effect_particles.last_time_in_milliseconds) / 1000);
    route_effect_particles.last_time_in_milliseconds = time_in_milliseconds;
    const effect = current_route_effect();
    if (!should_show_route_effect(effect)) { clear_route_effect_canvas(); return; }
    if (effect !== route_effect_particles.effect) {
      route_effect_particles.effect = effect;
      route_effect_particles.particles = particles_for_effect(effect);
    }
    draw_route_effect_frame(route_effect_presets[effect], tile_animation.is_playing ? elapsed_in_seconds * tile_animation_speeds[tile_animation.speed_index] : 0);
  }
  function draw_route_effect_frame(preset, elapsed_in_seconds) {
    const device_pixel_ratio = window.devicePixelRatio || 1;
    size_particle_canvas_to_scene();
    const view_box = wrap.getBoundingClientRect();
    const rectangle = visible_map_rectangle(view_box.width, view_box.height);
    particle_context.setTransform(1, 0, 0, 1, 0, 0);
    particle_context.clearRect(0, 0, particle_canvas.width, particle_canvas.height);
    route_effect_particles.is_canvas_clear = false;
    if (rectangle.width <= 0 || rectangle.height <= 0) return;
    particle_context.setTransform(device_pixel_ratio, 0, 0, device_pixel_ratio, 0, 0);
    particle_context.save();
    particle_context.beginPath();
    particle_context.rect(rectangle.left, rectangle.top, rectangle.width, rectangle.height);
    particle_context.clip();
    const pixel_scale = particle_pixel_scale();
    for (const particle of route_effect_particles.particles) {
      advance_particle(particle, preset, elapsed_in_seconds, rectangle, pixel_scale);
      draw_particle(particle, preset, rectangle, pixel_scale);
    }
    particle_context.restore();
  }
  requestAnimationFrame(tick_route_effect);

  // ------------------------------------------------------------ drawing
  let raf = 0;
  function draw() {
    if (raf) return;
    raf = requestAnimationFrame(() => { raf = 0; paint(); });
  }

  function paint() {
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    $("tbZoom").textContent = `${Math.round(state.view.zoom * 100)}%`;
    const r = state.result;
    if (!r || !state.tileCanvases.length) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    let t = 1;
    if (state.prevStage != null && prefs.fade) {
      t = Math.min(1, (performance.now() - state.fadeStart) / FADE_MS);
      if (t >= 1) state.prevStage = null;
    } else state.prevStage = null;
    paintScene(ctx, state.view, { fade: t, prev: state.prevStage, interactive: true });
    if (t < 1 || flashLeft() > 0) draw();
  }

  // Draws the map + overlays into any 2D context (the scene view, and File > Export PNG).
  function paintScene(g, view, opt) {
    const r = state.result;
    const { zoom, ox, oy } = view;
    const W = r.width * CELL * zoom, H = r.height * CELL * zoom;
    const layer = state.layer === "classes" ? state.classCanvases : state.tileCanvases;
    g.imageSmoothingEnabled = false;
    if (opt.interactive) { g.fillStyle = "rgba(0,0,0,0.35)"; g.fillRect(ox + 2, oy + 2, W, H); }
    if (opt.prev != null && opt.fade < 1) g.drawImage(layer[opt.prev], ox, oy, W, H);
    g.globalAlpha = opt.fade == null ? 1 : opt.fade;
    g.drawImage(layer[state.stage], ox, oy, W, H);
    g.globalAlpha = 1;
    const mask = state.changedMasks[state.stage], fl = flashLeft();
    if (opt.interactive && mask && fl > 0) { g.globalAlpha = 0.65 * fl; g.drawImage(mask, ox, oy, W, H); g.globalAlpha = 1; }
    const s = CELL * zoom;
    if (state.ov.grid && s >= 8) drawGrid(g, r, ox, oy, s);
    const zones = PR.zonesAt(r, state.stage);
    if (state.ov.markings && state.ov.events) drawEvents(g, PR.eventsAt(r, state.stage), ox, oy, s);
    if (state.ov.markings && state.ov.areas) drawAreas(g, zones, ox, oy, s);
    if (!opt.interactive) return;
    if (state.ov.markings) drawSelection(g, ox, oy, s);
    if (state.ov.markings) draw_connection_labels(g, ox, oy, s); else state.connection_labels = [];
    if (state.paint.cells.size) {
      g.globalAlpha = 0.7;
      for (const [k, value] of state.paint.cells) {
        const [x, y] = k.split(",").map(Number);
        draw_paint_value(g, x, y, value, ox + x * s, oy + y * s, s);
      }
      g.globalAlpha = 1;
    }
    if (state.hover && !state.hoverArea && !state.paint.cells.size) draw_brush_preview_under_cursor(g, ox, oy, s);
    if (state.hover && !state.hoverArea) {
      const cells = hoverCells();
      g.strokeStyle = "rgba(255,255,255,0.9)";
      g.lineWidth = 1;
      for (const [x, y] of cells) g.strokeRect(ox + x * s + 0.5, oy + y * s + 0.5, s - 1, s - 1);
    }
  }

  function hoverCells() {
    const h = state.hover, r = state.result;
    if (state.tool === "paint") return brush_cells_at(h.x, h.y).map(([x, y]) => [x, y]);
    if (state.tool === "stamp") return SL.stampCells($("stampKind").value, h.x, h.y, r.width, r.height);
    return [[h.x, h.y]];
  }

  function drawGrid(g, r, ox, oy, s) {
    g.strokeStyle = "rgba(0,0,0,0.18)";
    g.lineWidth = 1;
    g.beginPath();
    for (let x = 0; x <= r.width; x++) { g.moveTo(ox + x * s + 0.5, oy); g.lineTo(ox + x * s + 0.5, oy + r.height * s); }
    for (let y = 0; y <= r.height; y++) { g.moveTo(ox, oy + y * s + 0.5); g.lineTo(ox + r.width * s, oy + y * s + 0.5); }
    g.stroke();
  }

  function pathLines(g, z, ox, oy, s) {
    for (const line of PR.polylines(z.cells)) {
      g.beginPath();
      line.forEach(([y, x], i) => {
        const px = ox + (x + 0.5) * s, py = oy + (y + 0.5) * s;
        if (i) g.lineTo(px, py); else g.moveTo(px, py);
      });
      g.stroke();
    }
  }

  function drawEvents(g, events, ox, oy, s) {
    const rad = Math.max(3.5, Math.min(7, s * 0.3));
    for (const e of events) {
      const cx = ox + (e.x + 0.5) * s, cy = oy + (e.y + 0.5) * s;
      g.lineWidth = 1.5;
      g.strokeStyle = "#1b1a17";
      if (e.kind === "hidden_item") {
        g.beginPath(); g.arc(cx, cy, rad, Math.PI, 0); g.closePath(); g.fillStyle = "#e0453a"; g.fill();
        g.beginPath(); g.arc(cx, cy, rad, 0, Math.PI); g.closePath(); g.fillStyle = "#f5f2ea"; g.fill();
        g.beginPath(); g.arc(cx, cy, rad, 0, Math.PI * 2); g.moveTo(cx - rad, cy); g.lineTo(cx + rad, cy); g.stroke();
      } else if (e.kind === "cut_tree") {
        g.beginPath();
        g.moveTo(cx, cy - rad * 1.1); g.lineTo(cx + rad, cy + rad * 0.8); g.lineTo(cx - rad, cy + rad * 0.8); g.closePath();
        g.fillStyle = "#6fcf6a"; g.fill(); g.stroke();
      } else {
        g.beginPath();
        for (let i = 0; i < 6; i++) {
          const a = Math.PI / 6 + (i * Math.PI) / 3;
          const px = cx + Math.cos(a) * rad, py = cy + Math.sin(a) * rad;
          if (i) g.lineTo(px, py); else g.moveTo(px, py);
        }
        g.closePath(); g.fillStyle = "#b9b2a3"; g.fill(); g.stroke();
      }
    }
  }

  function drawAreas(g, zones, ox, oy, s) {
    const fs = Math.round(Math.max(11, Math.min(14, s * 0.7)));
    g.font = `600 ${fs}px Inter, system-ui, sans-serif`;
    g.textBaseline = "middle";
    const selZone = state.sel && state.sel.type === "zone" ? state.result.zones[state.sel.i] : null;
    for (const z of zones) {
      if (z.kind !== "special" || !z.bbox) continue;
      const [x0, y0, x1, y1] = z.bbox;
      const hot = z === state.hoverArea || z === selZone;
      const X = ox + x0 * s, Y = oy + y0 * s, Wd = (x1 - x0) * s, Ht = (y1 - y0) * s;
      g.fillStyle = hot ? "rgba(240,169,74,0.16)" : "rgba(240,169,74,0.06)";
      g.fillRect(X, Y, Wd, Ht);
      g.strokeStyle = "rgba(0,0,0,0.45)";
      g.lineWidth = 3.5;
      g.strokeRect(X + 0.5, Y + 0.5, Wd, Ht);
      g.strokeStyle = hot ? "#ffd27a" : "#f0a94a";
      g.lineWidth = hot ? 2.5 : 1.75;
      g.strokeRect(X + 0.5, Y + 0.5, Wd, Ht);
      const tw = g.measureText(z.name).width;
      const lh = fs + 6;
      const ly = Y - lh - 2 < 0 ? Y + Ht + 2 : Y - lh - 2;
      g.fillStyle = hot ? "#ffd27a" : "rgba(28,28,28,0.88)";
      g.fillRect(X, ly, tw + 10, lh);
      g.fillStyle = hot ? "#1c1a16" : "#eeeeee";
      g.fillText(z.name, X + 5, ly + lh / 2 + 1);
    }
  }

  function drawSelection(g, ox, oy, s) {
    const sel = state.sel, r = state.result;
    if (!sel || !r) return;
    g.save();
    g.strokeStyle = "#4d90d6";
    g.lineWidth = 2;
    if (sel.type === "zone") {
      const z = r.zones[sel.i];
      if (!z) { g.restore(); return; }
      if (PR.PATH_KINDS.includes(z.kind)) {
        const lw = Math.max(2, Math.min(6, s * 0.2));
        g.lineJoin = "round"; g.lineCap = "round";
        g.strokeStyle = "rgba(255,255,255,0.95)"; g.lineWidth = lw + 5; pathLines(g, z, ox, oy, s);
        g.strokeStyle = "#4d90d6"; g.lineWidth = lw + 1; pathLines(g, z, ox, oy, s);
      } else {
        const bb = z.bbox || bboxOf(z.cells);
        g.setLineDash([6, 4]);
        g.strokeRect(ox + bb[0] * s - 1.5, oy + bb[1] * s - 1.5, (bb[2] - bb[0]) * s + 3, (bb[3] - bb[1]) * s + 3);
      }
    } else if (sel.type === "event") {
      const e = r.events[sel.i];
      if (e) { g.beginPath(); g.arc(ox + (e.x + 0.5) * s, oy + (e.y + 0.5) * s, Math.max(7, s * 0.6), 0, Math.PI * 2); g.stroke(); }
    } else if (sel.type === "exit") {
      const x = r.exits[sel.i];
      if (x && x.cells.length) {
        const bb = bboxOf(x.cells);
        g.strokeRect(ox + bb[0] * s - 1.5, oy + bb[1] * s - 1.5, (bb[2] - bb[0]) * s + 3, (bb[3] - bb[1]) * s + 3);
      }
    } else if (sel.type === "route") {
      g.strokeStyle = "rgba(77,144,214,0.8)";
      g.lineWidth = 1;
      g.strokeRect(ox - 1.5, oy - 1.5, r.width * s + 3, r.height * s + 3);
    }
    g.restore();
  }

  function bboxOf(cells) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const [y, x] of cells) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x + 1); y1 = Math.max(y1, y + 1); }
    return [x0, y0, x1, y1];
  }

  // ------------------------------------------------------------ paint / stamp (POST /api/retile)
  function canPaint(loud) {
    const r = state.result;
    if (!r) { if (loud) log("warn", "Generate a route before painting."); return false; }
    return true;
  }
  // One ledge brush, rotated with the toolbar widget or the R key, instead of a
  // separate tile per direction.
  function updateLedgeUI() {
    const ledge_codes = SL.palette_ledge_codes(state.palette);
    if (ledge_codes.length && !ledge_codes.includes(state.ledgeDir)) state.ledgeDir = ledge_codes[0];
    $("ledgeRot").hidden = state.brush !== LEDGE_BRUSH;
    $("ledgeRot").querySelectorAll(".ledge-dir-btn").forEach((b) => {
      b.hidden = !ledge_codes.includes(b.dataset.dir);
      b.classList.toggle("on", b.dataset.dir === state.ledgeDir);
    });
    $("brushSize").hidden = !["terrain", "ledge"].includes(SL.brush_kind(state.brush).kind);
  }
  function rotateLedgeDir() {
    state.ledgeDir = SL.next_ledge_code(state.palette, state.ledgeDir);
    updateLedgeUI();
    if (!$("leftTiles").hidden) render_paint_palette();
  }
  function paintAt(sx, sy) {
    const r = state.result;
    const c = cellOf(sx, sy);
    if (!c) { state.paint.last = null; return; }
    if (state.tool === "stamp") {
      if (state.paint.last && state.paint.last.x === c.x && state.paint.last.y === c.y) return;
      for (const [x, y, k] of SL.stampCells($("stampKind").value, c.x, c.y, r.width, r.height)) state.paint.cells.set(`${x},${y}`, k);
    } else {
      const stroke_points = state.paint.last ? SL.lineCells(state.paint.last, c).slice(1) : [c];
      for (const stroke_point of stroke_points) add_brush_cells_to_stroke(brush_cells_at(stroke_point.x, stroke_point.y));
    }
    state.paint.last = c;
    draw();
  }
  async function commitPaint() {
    const r = state.result;
    const fin = r.stages[r.stages.length - 1];
    const stroke = [...state.paint.cells].map(([k, cls]) => [...k.split(",").map(Number), cls]);
    const cells = [], old = [];
    const class_codes = SL.palette_class_codes(state.palette);
    const plain = (k) => (class_codes.includes(k) ? k : "g");
    const remembered = savedPaint(r);
    const isTree = (x, y) => String(remembered[`${x},${y}`] || "").includes("#");
    for (const [x, y, k] of stroke.filter((c) => c[2].includes("#"))) {
      const was = fin.cls[y][x] + "#" + fin.tiles[y * r.width + x];
      if (was === k) continue;
      const [was_class, was_tile] = was.split("#");
      cells.push([x, y, k]);
      old.push([x, y, (was_class === "o" ? "o" : plain(was_class)) + "#" + was_tile]);
    }
    const class_stroke = stroke.filter((c) => !c[2].includes("#"));
    // the tidy pass leaves exact tiles and objects alone (a 1-wide tree is not a stray tree wall)
    const tidy = SL.tidyPaint(fin.cls, class_stroke).filter(([x, y]) => !isTree(x, y));
    for (const [x, y, cls] of class_stroke.concat(tidy)) {
      const was = fin.cls[y][x];
      if (was === cls && !isTree(x, y)) continue;
      cells.push([x, y, cls]);
      old.push([x, y, plain(was)]);
    }
    if (!cells.length) { state.paint.cells = new Map(); draw(); return; }
    const before = savedPaint(r);
    state.paint.busy = true;
    try {
      const ok = await retile(cells);
      if (!ok) return;
      const after = SL.mergePaint(before, cells);
      storePaint(r, after);
      undoStack.push({ kind: "paint", id: r.id, cells, old, before, after });
    } finally {
      state.paint.busy = false;
    }
  }
  // paint memory per route name + size (see SL.paintKey)
  function savedPaint(r) { const all = sget(SL.PAINT_KEY) || {}; return (r && all[SL.paintKey(r)]) || {}; }
  function storePaint(r, map) {
    const all = sget(SL.PAINT_KEY) || {};
    if (map && Object.keys(map).length) all[SL.paintKey(r)] = map; else delete all[SL.paintKey(r)];
    sset(SL.PAINT_KEY, all);
    updatePaintCount();
  }
  function updatePaintCount() {
    const n = state.result ? Object.keys(savedPaint(state.result)).length : 0;
    $("paintN").textContent = n ? `${n} painted` : "";
  }
  // a new route with remembered paint: put it back
  async function reapplyPaint() {
    const r = state.result;
    const cells = SL.paintList(savedPaint(r));
    if (!cells.length) return;
    if (await retile(cells, true)) log("info", `Put back ${cells.length} painted cells on ${r.name} (Generate starts it fresh)`);
  }
  async function retile(cells, quiet) {
    const r = state.result;
    if (!r) return false;
    try {
      const res = await postJSON("/api/retile", { id: r.id, cells: SL.expand_paint_cells(cells) });
      if (state.result !== r) return false; // a new route arrived meanwhile
      const last = r.stages.length - 1;
      r.stages[last].cls = res.cls;
      r.stages[last].tiles = res.tiles;
      const d = drawStage(last, last ? state.tileCanvases[last - 1] : null, last ? r.stages[last - 1].tiles : null);
      state.tileCanvases[last] = d.tiles;
      state.classCanvases[last] = d.classes;
      state.animCells[last] = d.anim;
      if (!quiet) log("info", `Painted ${res.painted} cells; re-tiled ${res.retiled}`);
      return true;
    } catch (e) {
      const missing = /not found|404/.test(e.message);
      log("error", missing ? "Painting needs the /api/retile endpoint, which this server doesn't have yet." : "Paint failed: " + e.message);
      return false;
    } finally {
      state.paint.cells = new Map();
      draw();
    }
  }

  function setTool(t) {
    state.tool = t;
    document.querySelectorAll("[data-tool]").forEach((b) => { b.classList.toggle("on", b.dataset.tool === t); b.setAttribute("aria-pressed", b.dataset.tool === t); });
    $("paintOpts").hidden = t !== "paint";
    $("stampOpts").hidden = t !== "stamp";
    wrap.className = "view scene tool-" + t;
    if (t === "paint" || t === "stamp") { canPaint(true); updatePaintCount(); }
    if (t === "paint") { updateLedgeUI(); show_left_tab("tiles"); }
    hideTip();
    draw();
  }

  // ------------------------------------------------------------ selection
  function select(sel) {
    state.sel = sel;
    renderTree();
    renderSelection();
    draw();
  }

  function selBBox(sel) {
    const r = state.result;
    if (!sel || !r) return null;
    if (sel.type === "zone") { const z = r.zones[sel.i]; return z && (z.bbox || bboxOf(z.cells)); }
    if (sel.type === "event") { const e = r.events[sel.i]; return e && [e.x - 2, e.y - 2, e.x + 3, e.y + 3]; }
    if (sel.type === "exit") { const x = r.exits[sel.i]; return x && x.cells.length ? bboxOf(x.cells) : null; }
    return null;
  }
  function presentAt(sel) {
    const r = state.result;
    const st = r.stages[state.stage];
    if (sel.type === "zone") return sel.i < st.zones;
    if (sel.type === "event") return sel.i < st.events;
    return true;
  }
  function focusSel() {
    const r = state.result;
    if (!r) return;
    if (!state.sel || state.sel.type === "route") { fit(); return; }
    if (!presentAt(state.sel)) { stopPlay(); setStage(r.stages.length - 1, false); }
    const bb = selBBox(state.sel);
    if (bb) centerOn(bb);
  }

  const KIND_NAMES = { main: "Main lane", branch: "Branch", loop: "Loop", dead_end: "Dead end", hidden: "Hidden trail", trail: "Area trail",
    special: "Special area", clearing: "Clearing", lake: "Lake", hidden_item: "Hidden item", cut_tree: "Cut tree", rock_smash: "Smashable rock",
    sealed_gate: "Sealed stone (opened by a lever)", gate_switch: "Hidden lever" };
  const kname = (k) => KIND_NAMES[k] || String(k).replace(/_/g, " ");

  function renderSelection() {
    const box = $("selBody");
    box.innerHTML = "";
    const sel = state.sel, r = state.result;
    if (!sel || !r) { box.append(el("p", "muted", "Nothing selected. Click something in the Scene or Hierarchy.")); return; }
    const title = (txt, color, cls) => {
      const h = el("div", "sel-title");
      const ic = el("span", "ic" + (cls ? " " + cls : ""));
      ic.style.background = color || "#888";
      h.append(ic, el("span", "", txt));
      box.append(h);
    };
    const kv = (rows) => {
      const dl = el("dl", "kv");
      for (const [k, v] of rows) { if (v == null || v === "") continue; dl.append(el("dt", "", k), el("dd", "", String(v))); }
      box.append(dl);
    };
    const actions = () => {
      const a = el("div", "sel-actions");
      const f = el("button", "btn", "Frame");
      f.type = "button";
      f.title = "Frame in the Scene view (F)";
      f.onclick = focusSel;
      a.append(f);
      box.append(a);
    };
    if (sel.type === "route") {
      title(r.name, "#4d90d6");
      kv([["id", r.route_id], ["seed", r.seed], ["size", `${r.width} × ${r.height}`], ["biome", r.biome], ["tiler", r.tiler === "ark" ? "Ark rule tiles" : `learned (${r.tileset || "general_fortree"})`],
        ["stages", r.stages.length], ["zones", r.zones.length], ["events", r.events.length], ["exportable", r.exportable ? "yes" : "no (too big)"]]);
      return;
    }
    if (sel.type === "zone") {
      const z = r.zones[sel.i];
      if (!z) return;
      const isPath = PR.PATH_KINDS.includes(z.kind);
      title(z.name || kname(z.kind), isPath ? PR.PATH_COLORS[z.kind] : z.kind === "special" ? "#f0a94a" : z.kind === "lake" ? PR.CLASS_COLORS.w : PR.CLASS_COLORS.g);
      const bb = z.bbox || bboxOf(z.cells);
      const added = r.stages.findIndex((st) => st.zones > sel.i);
      kv([["kind", kname(z.kind)], ["cells", z.cells.length], ["bounds", `x ${bb[0]}-${bb[2] - 1}, y ${bb[1]}-${bb[3] - 1}`],
        ["ledge", z.ledge ? z.ledge.join(", ") : null], ["added in", added >= 0 ? `stage ${added + 1} (${r.stages[added].name})` : null]]);
      if (z.area) {
        const a = z.area;
        kv([["species", a.species || "none"], ["archetype", a.archetype], ["shiny", a.shiny_boost > 1 ? `x${a.shiny_boost}` : null],
          ["habitats", Array.isArray(a.habitats) ? a.habitats.join(", ") : a.habitats]]);
        if (a.description) box.append(el("p", "sel-desc", a.description));
        const table = (r.encounters && r.encounters.areas || {})[a.id];
        if (table && table.length) {
          box.append(heading("Encounters here"));
          box.append(encTable(PR.encounterRows(table).map((x) => [nameCell(x.name, x.shiny_boost > 1 ? `shiny x${x.shiny_boost}` : x.rarity), `${x.levels[0]}-${x.levels[1]}`, pctCell(x.pct)]),
            ["species", "lv", "share"]));
        }
      }
      actions();
      return;
    }
    if (sel.type === "event") {
      const e = r.events[sel.i];
      if (!e) return;
      title(kname(e.kind), e.kind === "hidden_item" ? "#e0453a" : e.kind === "cut_tree" ? "#6fcf6a" : "#b9b2a3");
      const rows = [["kind", e.kind], ["cell", `x ${e.x}, y ${e.y}`]];
      for (const [k, v] of Object.entries(e)) if (!["kind", "x", "y"].includes(k)) rows.push([k.replace(/_/g, " "), v]);
      kv(rows);
      actions();
      return;
    }
    if (sel.type === "exit") {
      const x = r.exits[sel.i];
      if (!x) return;
      title(`Exit ${sel.i + 1}: ${x.side}`, "#9cc6f0");
      const sp = state.exits[sel.i];
      const connection = scene_connections()[sel.i];
      kv([["side", x.side], ["to", x.to || "nothing (open edge)"], ["leads to", connection && connection.name], ["width", x.cells.length],
        ["position", sp ? sp.at : null], ["role", sel.i < 2 ? "joined by the main road" : "extra exit"]]);
      actions();
      if (connection) box.querySelector(".sel-actions").append(follow_button(follow_action(sel.i, connection), "btn go"));
    }
  }

  // ------------------------------------------------------------ hierarchy
  const EYE = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3.5C4.4 3.5 2 6.6 1.3 8c.7 1.4 3.1 4.5 6.7 4.5s6-3.1 6.7-4.5C14 6.6 11.6 3.5 8 3.5zm0 7.2A2.7 2.7 0 1 1 8 5.3a2.7 2.7 0 0 1 0 5.4z" fill="currentColor"/></svg>';
  const EYE_OFF = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.3 2.3l11.4 11.4-.9.9-2.3-2.3c-.8.3-1.6.4-2.5.4-3.6 0-6-3.1-6.7-4.5.4-.8 1.3-2.1 2.6-3.1L1.4 3.2zM8 3.5c3.6 0 6 3.1 6.7 4.5-.3.6-.9 1.5-1.7 2.4L5.6 3.9c.7-.2 1.5-.4 2.4-.4z" fill="currentColor"/></svg>';

  function treeModel() {
    const r = state.result;
    if (!r) return null;
    const st = r.stages[state.stage];
    const zoneNode = (z, i) => ({
      id: "z:" + i, label: z.name || kname(z.kind), meta: PR.PATH_KINDS.includes(z.kind) ? "" : `${z.cells.length} cells`,
      icon: PR.PATH_KINDS.includes(z.kind) ? { cls: "line", color: PR.PATH_COLORS[z.kind] } : z.kind === "special" ? { cls: "ring" } :
        { color: z.kind === "lake" ? PR.CLASS_COLORS.w : PR.CLASS_COLORS.g },
      sel: { type: "zone", i }, dim: i >= st.zones,
    });
    const zs = r.zones.map((z, i) => [z, i]);
    const lanes = zs.filter(([z]) => PR.PATH_KINDS.includes(z.kind)).map(([z, i]) => zoneNode(z, i));
    const special = zs.filter(([z]) => z.kind === "special").map(([z, i]) => zoneNode(z, i));
    const places = zs.filter(([z]) => !PR.PATH_KINDS.includes(z.kind) && z.kind !== "special").map(([z, i]) => zoneNode(z, i));
    const evGroups = {};
    r.events.forEach((e, i) => { (evGroups[e.kind] = evGroups[e.kind] || []).push([e, i]); });
    const items = Object.entries(evGroups).map(([k, list]) => ({
      id: "g:ev:" + k, label: kname(k) + "s", meta: String(list.length), group: true,
      children: list.map(([e, i]) => ({
        id: "e:" + i, label: `${kname(e.kind)} (${e.x}, ${e.y})`, meta: e.item || e.flag || "",
        icon: { cls: "dot", color: e.kind === "hidden_item" ? "#e0453a" : e.kind === "cut_tree" ? "#6fcf6a" : "#b9b2a3" },
        sel: { type: "event", i }, dim: i >= st.events,
      })),
    }));
    const connections = scene_connections();
    const exits = (r.exits || []).map((x, i) => exit_tree_node(x, i, connections[i]));
    const eye = (k) => ({ on: state.ov[k], toggle: () => toggleOv(k) });
    const layers = [
      { id: "l:tiles", label: "Metatiles", meta: state.layer === "tiles" ? "shown" : "", icon: { color: "#7aa060" }, run: () => setLayer("tiles"), on: state.layer === "tiles" },
      { id: "l:classes", label: "Classes", meta: state.layer === "classes" ? "shown" : "", icon: { color: "#c49a5a" }, run: () => setLayer("classes"), on: state.layer === "classes" },
      { id: "l:areas", label: "Areas overlay", icon: { cls: "ring" }, eye: eye("areas") },
      { id: "l:events", label: "Items overlay", icon: { cls: "dot", color: "#e0453a" }, eye: eye("events") },
      { id: "l:grid", label: "Grid", icon: { color: "#666" }, eye: eye("grid") },
    ];
    return {
      id: "route", label: r.name, meta: `${r.width}×${r.height}`, sel: { type: "route" }, icon: { color: "#4d90d6" },
      children: [
        ...back_tree_nodes(),
        { id: "g:exits", label: "Exits", meta: String(exits.length), group: true, children: exits },
        { id: "g:lanes", label: "Lanes", meta: String(lanes.length), group: true, children: lanes },
        { id: "g:special", label: "Special areas", meta: String(special.length), group: true, children: special },
        { id: "g:places", label: "Clearings & lakes", meta: String(places.length), group: true, children: places },
        { id: "g:items", label: "Items & events", meta: String(r.events.length), group: true, children: items },
        { id: "g:layers", label: "Layers", group: true, children: layers },
      ],
    };
  }

  const selId = () => {
    const s = state.sel;
    if (!s) return "";
    return s.type === "route" ? "route" : s.type === "zone" ? "z:" + s.i : s.type === "event" ? "e:" + s.i : s.type === "exit" ? "x:" + s.i : "";
  };

  function renderTree() {
    const ul = $("tree");
    const scroll = ul.parentElement.scrollTop;
    ul.innerHTML = "";
    const m = treeModel();
    if (!m) { ul.append(el("li", "empty", "Generate a route to see its parts.")); return; }
    const q = $("hierFilter").value.trim().toLowerCase();
    const sid = selId();
    const matches = (n) => !q || n.label.toLowerCase().includes(q) || (n.children || []).some(matches);
    const add = (n, parent, depth) => {
      if (!matches(n)) return;
      const li = el("li", "node");
      li.setAttribute("role", "treeitem");
      const kids = (n.children || []).filter(matches);
      const open = q ? true : state.expanded.has(n.id);
      if (n.children) li.setAttribute("aria-expanded", open);
      if (n.id === sid) li.classList.add("sel");
      if (n.dim) li.classList.add("dim");
      if (!open && n.children) li.classList.add("collapsed");
      const row = el("div", "row");
      row.style.paddingLeft = 4 + depth * 12 + "px";
      const tw = el("span", "tw" + (open ? " open" : ""), n.children && n.children.length ? "▶" : "");
      tw.onclick = (ev) => { ev.stopPropagation(); if (state.expanded.has(n.id)) state.expanded.delete(n.id); else state.expanded.add(n.id); renderTree(); };
      row.append(tw);
      if (n.icon) {
        const ic = el("span", "ic" + (n.icon.cls ? " " + n.icon.cls : ""));
        if (n.icon.color) ic.style.background = n.icon.color;
        row.append(ic);
      }
      const lb = el("span", "lb", n.label);
      if (n.group) lb.style.fontWeight = "500";
      row.append(lb);
      if (n.meta) row.append(el("span", "meta", n.meta));
      if (n.eye) {
        const b = el("button", "eye" + (n.eye.on ? " on" : ""));
        b.type = "button";
        b.innerHTML = n.eye.on ? EYE : EYE_OFF;
        b.setAttribute("aria-label", (n.eye.on ? "Hide " : "Show ") + n.label);
        b.onclick = (ev) => { ev.stopPropagation(); n.eye.toggle(); };
        row.append(b);
      }
      if (n.follow) row.append(follow_button(n.follow));
      row.onclick = () => {
        if (n.sel) select(n.sel);
        else if (n.run) n.run();
        else if (n.eye) n.eye.toggle();
        else if (n.children) tw.onclick(new Event("click"));
      };
      row.ondblclick = () => {
        if (n.follow) { n.follow.run(); return; }
        if (n.sel) { select(n.sel); focusSel(); }
      };
      li.append(row);
      if (n.children && kids.length) {
        const sub = el("ul");
        sub.setAttribute("role", "group");
        kids.forEach((c) => add(c, sub, depth + 1));
        li.append(sub);
      }
      parent.append(li);
    };
    add(m, ul, 0);
    ul.parentElement.scrollTop = scroll;
  }
  $("hierFilter").addEventListener("input", renderTree);

  // ------------------------------------------------------------ layers / overlays
  function setLayer(l) {
    state.layer = l;
    saveView();
    syncViewButtons();
    renderTree();
    draw();
  }
  function toggleOv(k) {
    state.ov[k] = !state.ov[k];
    if (!state.ov.areas) { state.hoverArea = null; hideTip(); }
    saveView();
    syncViewButtons();
    renderTree();
    draw();
  }
  function sync_markings_button() {
    const markings_button = $("tbMarkings");
    if (!markings_button) return;
    markings_button.classList.toggle("on", !!state.ov.markings);
    markings_button.setAttribute("aria-pressed", String(!!state.ov.markings));
    markings_button.title = state.ov.markings ? "Hide lanes, areas and item markers (they stop being clickable)" : "Show lanes, areas and item markers";
  }
  function syncViewButtons() {
    sync_markings_button();
    document.querySelectorAll("[data-layer]").forEach((b) => { const on = b.dataset.layer === state.layer; b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); });
    document.querySelectorAll("[data-ov]").forEach((b) => { const on = !!state.ov[b.dataset.ov]; b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); });
  }

  const isEmpty = (i) => { const f = state.stageInfo[i] || {}; return i > 0 && !f.changed && !(f.zones > 0) && !(f.events > 0); };
  const flashLeft = () => Math.max(0, 1 - (performance.now() - state.flashStart) / Math.min(FLASH_MS, prefs.stageMs * 1.2));

  function setStage(i, fade) {
    const r = state.result;
    if (!r) return;
    i = Math.max(0, Math.min(r.stages.length - 1, i));
    if (fade && i !== state.stage) { state.prevStage = state.stage; state.fadeStart = state.flashStart = performance.now(); }
    else { state.prevStage = null; state.flashStart = 0; }
    state.stage = i;
    const label = `${i + 1} / ${r.stages.length} · ${r.stages[i].name}`;
    renderTree();
    draw();
  }

  function startPlay() {
    const r = state.result;
    if (!r) return;
    if (state.stage >= r.stages.length - 1) setStage(0, false);
    state.playing = true;
    syncPlay();
    clearInterval(state.timer);
    state.timer = setInterval(() => {
      const last = state.result.stages.length - 1;
      if (state.stage >= last) {
        if (prefs.loop) return setStage(0, false);
        return stopPlay();
      }
      let i = state.stage + 1;
      while (i < last && isEmpty(i)) i++; // don't sit on stages that changed nothing
      setStage(i, true);
    }, prefs.stageMs);
  }
  function stopPlay() {
    state.playing = false;
    clearInterval(state.timer);
    syncPlay();
  }
  function togglePlay() { state.playing ? stopPlay() : startPlay(); }
  function syncPlay() {
    for (const b of []) {
      b.classList.toggle("playing", state.playing);
      b.setAttribute("aria-label", state.playing ? "Pause" : "Play stages");
    }
  }
  const step = (d) => { if (!state.result) return; stopPlay(); setStage(state.stage + d, true); };
  const stageEnd = (last) => { if (!state.result) return; stopPlay(); setStage(last ? state.result.stages.length - 1 : 0, false); };

  // ------------------------------------------------------------ report / encounters
  function reportRows(r) {
    const rep = r.report || {};
    const zc = rep.zones || {}, ec = rep.events || {};
    if (!rep.size) return [["size", `${r.width} x ${r.height}`]];
    const rows = [
      ["size", `${rep.size[0]} x ${rep.size[1]}`],
      ["walkable cells", rep.walkable_cells],
      ["walkable", `${Math.round(rep.walkable_cells / (rep.size[0] * rep.size[1]) * 100)}%`],
      ["reachable", `${Math.round(rep.reachable_fraction * 100)}%`],
      ["branches", zc.branch || 0], ["loops", zc.loop || 0], ["dead ends", zc.dead_end || 0],
      ["clearings", zc.clearing || 0], ["hidden trails", zc.hidden || 0],
      ["hidden items", ec.hidden_item || 0], ["cut trees", ec.cut_tree || 0],
      ["tall grass cells", (rep.classes || {}).t], ["water cells", (rep.classes || {}).w], ["ledge cells", (rep.classes || {}).s],
    ];
    if (ec.rock_smash) rows.push(["smashable rocks", ec.rock_smash]);
    return rows;
  }

  function renderInfo() {
    const r = state.result;
    const dl = $("stats");
    dl.innerHTML = "";
    for (const [k, v] of reportRows(r)) dl.append(el("dt", "", k), el("dd", "", v == null ? "-" : String(v)));
    if (r.warnings && r.warnings.length) dl.append(el("dt", "", "warnings"), Object.assign(el("dd", "long", r.warnings.join("; ")), { style: "color: var(--warn)" }));
    renderEnc();
  }

  function encTable(rows, cols) {
    const t = el("table", "enc");
    const thead = el("thead");
    const htr = el("tr");
    cols.forEach((c, i) => htr.append(el("th", i ? "n" : "", c)));
    thead.append(htr);
    t.append(thead);
    const tb = el("tbody");
    rows.forEach((cells) => {
      const tr = el("tr");
      cells.forEach((c, i) => {
        const td = el("td", i ? "n" : "");
        if (c instanceof Node) td.append(c); else td.textContent = c;
        tr.append(td);
      });
      tb.append(tr);
    });
    t.append(tb);
    return t;
  }
  function nameCell(name, sub) {
    const f = document.createDocumentFragment();
    f.append(document.createTextNode(name));
    if (sub) f.append(el("span", "types", sub));
    return f;
  }
  function pctCell(p) {
    const f = document.createDocumentFragment();
    const bar = el("span", "pct");
    bar.style.width = Math.max(2, Math.round(p * 0.4)) + "px";
    f.append(bar, document.createTextNode(p >= 10 ? `${Math.round(p)}%` : `${p.toFixed(1)}%`));
    return f;
  }
  function heading(text) { return el("div", "enc-h", text); }

  function renderEnc() {
    const box = $("enc");
    box.innerHTML = "";
    const r = state.result;
    if (!r) { box.append(el("p", "muted", "none yet")); return; }
    if (state.tab === "dex") {
      const enc = r.encounters || {};
      const rows = PR.encounterRows(enc.route);
      if (!rows.length) { box.append(el("p", "muted", "no species for this biome")); return; }
      box.append(heading("Tall grass"));
      box.append(encTable(rows.map((x) => [nameCell(x.name, `${x.types.join(" / ")} · ${x.rarity}`), `${x.levels[0]}-${x.levels[1]}`, pctCell(x.pct)]), ["species", "lv", "share"]));
      const areaNames = {};
      r.zones.forEach((z) => { if (z.kind === "special" && z.area) areaNames[z.area.id] = z.name; });
      for (const [id, table] of Object.entries(enc.areas || {})) {
        box.append(heading(areaNames[id] || id));
        box.append(encTable(PR.encounterRows(table).map((x) => [nameCell(x.name, x.shiny_boost > 1 ? `shiny x${x.shiny_boost}` : x.rarity), `${x.levels[0]}-${x.levels[1]}`, pctCell(x.pct)]), ["species", "lv", "share"]));
      }
    } else {
      const em = r.emerald_encounters;
      if (!em) { box.append(el("p", "muted", "no wild table")); return; }
      box.append(el("p", "hint", "Slot table for wild_encounters.json, levels clipped to the route's band."));
      for (const [key, label, rates] of [["land_mons", "Land", LAND_RATES], ["water_mons", "Surfing", WATER_RATES]]) {
        if (!em[key]) continue;
        box.append(heading(`${label} · rate ${em[key].encounter_rate}`));
        box.append(encTable(PR.slotRows(em[key], rates).map((x) => [PR.prettySpecies(x.species), `${x.min}-${x.max}`, pctCell(x.pct)]), ["species", "lv", "slots"]));
      }
    }
  }
  document.querySelectorAll("[data-enc]").forEach((b) => {
    b.onclick = () => {
      state.tab = b.dataset.enc;
      document.querySelectorAll("[data-enc]").forEach((o) => o.classList.toggle("on", o === b));
      renderEnc();
    };
  });

  // ------------------------------------------------------------ geography / region editor (editor/app.js)
  // Docked straight into the shell: its map fills the centre view, its tools go in the toolbar,
  // its places and layers in the Hierarchy, its panels in the Inspector, its messages in the Console.
  let region_editor_actions = null;
  function region_editor() { return region_editor_actions; }
  function show_region_editor_in_mode(mode_name) {
    if (region_editor_actions) { region_editor_actions.set_editor_mode(mode_name); return; }
    const seed_of_last_region = sget("pokeroutes-shell-region-seed") || Math.floor(Math.random() * 100000);
    region_editor_actions = PREditor.init($("viewMap"), {
      mode: mode_name, seed: seed_of_last_region, onOpenRoute: openInBench,
      host: {
        tools: $("tbMapTools"), side: $("mapInspector"), tree: $("mapTree"),
        filter: () => $("hierFilter").value, icons: { eye: EYE, eye_off: EYE_OFF }, dice_icon: $("dice").innerHTML,
        log: (text, is_error) => log(is_error ? "error" : "info", text),
        onView: syncNonSceneToolbar,
      },
    });
  }
  function current_region_seed() {
    const editor_document = region_editor() && region_editor().getDoc();
    const region_seed = editor_document ? editor_document.seed : sget("pokeroutes-shell-region-seed") || 1;
    return region_seed;
  }

  function openInBench(spec) {
    const { params: p, region } = SL.specToParams(spec, params());
    replaceParams(p, "region route");
    state.route_trail = state.pending_walk && state.pending_walk.to === p.name ? state.pending_walk.trail : [];
    state.pending_walk = null;
    state.region = region;
    if (region) sset(REGION_KEY, region); else sdel(REGION_KEY);
    renderRegionLink();
    sset("pokeroutes-shell-region-seed", current_region_seed());
    Shell.setCenter("scene");
    log("info", `Opened ${p.name} from region ${current_region_seed()}`);
    generate();
  }

  function renderRegionLink() {
    const fold = $("foldRegion");
    const box = $("regionLink");
    box.innerHTML = "";
    const reg = state.region;
    fold.hidden = !reg;
    if (!reg) return;
    const r = reg.region || {};
    const dl = el("dl", "kv");
    const add = (k, v) => { if (v != null && v !== "") dl.append(el("dt", "", k), el("dd", "", String(v))); };
    add("route", reg.name);
    add("role", r.role);
    add("shape", r.shape);
    add("tags", (r.tags || []).join(", "));
    box.append(dl);
    box.append(el("p", "hint", "Its region block (elevation, rivers, role) goes into Generate while the route keeps this name."));
    const b = el("button", "btn", "Detach from region");
    b.type = "button";
    b.onclick = () => { state.region = null; state.route_trail = []; sdel(REGION_KEY); renderRegionLink(); renderTree(); log("info", "Detached the route from its region block"); };
    box.append(b);
    if (state.route_trail.length) box.append(follow_button({ label: back_label(), run: go_back_a_route }, "btn go"));
  }

  // ------------------------------------------------------------ walking the region through its connections
  // An exit of a route opened from a region leads to the route or town on the other side;
  // clicking it (its label on the map, its Hierarchy row, or the Inspector) opens that route.
  const ARROW_OUT_OF_SIDE = { north: "↑", south: "↓", east: "→", west: "←" };
  function open_region_document() {
    const editor_actions = region_editor();
    return editor_actions ? editor_actions.getDoc() : null;
  }
  function scene_connections() {
    const route = state.result;
    if (!route || !state.region) return [];
    return SL.exitConnections(route.exits, open_region_document(), state.region.name);
  }
  function follow_action(exit_index, connection) {
    const verb = connection.kind === "town" ? "Show" : "Open";
    return { label: `${verb} ${connection.name}`, run: () => follow_connection(exit_index) };
  }
  function follow_button(follow, class_name) {
    const button = el("button", class_name || "go", class_name ? follow.label : "go");
    button.type = "button";
    button.title = follow.label;
    button.onclick = (ev) => { ev.stopPropagation(); follow.run(); };
    return button;
  }
  function exit_tree_node(scene_exit, exit_index, connection) {
    const destination = connection ? connection.name : scene_exit.to;
    const node = {
      id: "x:" + exit_index, label: `${scene_exit.side}${destination ? " → " + destination : ""}`,
      meta: connection && connection.kind === "town" ? "town" : `${scene_exit.cells.length} wide`,
      icon: { color: "#9cc6f0" }, sel: { type: "exit", i: exit_index },
    };
    if (connection) node.follow = follow_action(exit_index, connection);
    return node;
  }
  function back_label() {
    return `← Back to ${state.route_trail[state.route_trail.length - 1]}`;
  }
  function back_tree_nodes() {
    if (!state.route_trail.length) return [];
    return [{ id: "back", label: back_label(), icon: { color: "#9cc6f0" }, run: go_back_a_route }];
  }
  function follow_connection(exit_index) {
    const connection = scene_connections()[exit_index];
    if (!connection) return;
    if (connection.kind === "unknown") { log("warn", `Open the region in the Map view first to follow the way to ${connection.name}.`); return; }
    if (connection.kind === "town") { show_town_in_routes_view(connection.town); return; }
    walk_to(connection.route, [...state.route_trail, state.region.name]);
  }
  function go_back_a_route() {
    const previous_name = state.route_trail[state.route_trail.length - 1];
    const region_document = open_region_document();
    const previous_route = region_document && (region_document.routes || []).find((route) => route.name === previous_name);
    if (!previous_route) { log("warn", `${previous_name} is not in the open region any more.`); return; }
    walk_to(previous_route, state.route_trail.slice(0, -1));
  }
  function walk_to(region_route, trail_after) {
    state.pending_walk = { to: region_route.name, trail: trail_after };
    region_editor().openInBench(region_route);
  }
  function show_town_in_routes_view(town) {
    Shell.setCenter("region");
    region_editor().select({ kind: "city", id: town.id });
    log("info", `${town.name} is a town; towns can't open in the Routes view yet, so it is selected on the Map.`);
  }
  function connection_label_at(sx, sy) {
    return state.connection_labels.find((box) => sx >= box.x && sx < box.x + box.w && sy >= box.y && sy < box.y + box.h) || null;
  }
  function connection_label_box(scene_exit, text_width, label_height, ox, oy, s) {
    const [x0, y0, x1, y1] = bboxOf(scene_exit.cells);
    const width = text_width + 12;
    const center_x = ox + (x0 + x1) / 2 * s - width / 2;
    const center_y = oy + (y0 + y1) / 2 * s - label_height / 2;
    switch (scene_exit.side) {
      case "north": return { x: center_x, y: oy + y1 * s + 4, w: width, h: label_height };
      case "south": return { x: center_x, y: oy + y0 * s - label_height - 4, w: width, h: label_height };
      case "west": return { x: ox + x1 * s + 4, y: center_y, w: width, h: label_height };
      default: return { x: ox + x0 * s - width - 4, y: center_y, w: width, h: label_height };
    }
  }
  function draw_connection_labels(g, ox, oy, s) {
    state.connection_labels = [];
    const route = state.result;
    const connections = scene_connections();
    const font_size = Math.round(Math.max(11, Math.min(14, s * 0.7)));
    g.font = `600 ${font_size}px Inter, system-ui, sans-serif`;
    g.textBaseline = "middle";
    connections.forEach((connection, exit_index) => {
      const scene_exit = route.exits[exit_index];
      if (!connection || !scene_exit || !scene_exit.cells.length) return;
      const text = `${ARROW_OUT_OF_SIDE[scene_exit.side] || "→"} ${connection.name}`;
      const box = connection_label_box(scene_exit, g.measureText(text).width, font_size + 8, ox, oy, s);
      g.fillStyle = connection.kind === "unknown" ? "rgba(28,28,28,0.7)" : "rgba(20,40,64,0.92)";
      g.fillRect(box.x, box.y, box.w, box.h);
      g.strokeStyle = "#9cc6f0";
      g.lineWidth = 1;
      g.strokeRect(box.x + 0.5, box.y + 0.5, box.w - 1, box.h - 1);
      g.fillStyle = "#eef5fc";
      g.fillText(text, box.x + 6, box.y + box.h / 2 + 1);
      state.connection_labels.push(Object.assign({ i: exit_index }, box));
    });
  }

  function show_center_tab(tab) {
    $("viewScene").hidden = tab !== "scene";
    $("viewMap").hidden = tab === "scene";
    if (tab !== "scene") show_region_editor_in_mode(tab);
    // the toolbar, hierarchy and inspector are shared: each shows the Scene's or the map editor's
    // contents, while Generate and zoom stay and drive whichever view is showing
    $("tbSceneTools").hidden = tab !== "scene";
    $("tbMapTools").hidden = tab === "scene";
    $("tree").hidden = tab !== "scene";
    $("mapTree").hidden = tab === "scene";
    $("form").hidden = tab !== "scene";
    $("mapInspector").hidden = tab === "scene";
    if (tab === "scene") updateTitle();
    $("tbStageGroup").hidden = tab !== "scene";
    $("tbTilerGroup").hidden = tab !== "scene";
    show_tiles_tab_only_in_scene(tab);
    $("tbGenerate").textContent = generate_button_label_for_view(tab);
    $("tbGenerate").title = generate_button_label_for_view(tab);
    syncNonSceneToolbar();
  }
  function show_tiles_tab_only_in_scene(center_tab) {
    const tiles_tab_button = document.querySelector('[data-left-tab="tiles"]');
    if (!tiles_tab_button) return;
    const palette_tab_label = { scene: "Tiles", geography: "Biomes" }[center_tab];
    tiles_tab_button.hidden = !palette_tab_label;
    tiles_tab_button.textContent = palette_tab_label || "Tiles";
    const palette_tab_is_open = !$("leftTiles").hidden || !$("leftBiomes").hidden;
    if (!palette_tab_label && palette_tab_is_open) { show_left_tab("hierarchy"); return; }
    if (palette_tab_is_open) show_left_tab("tiles");
  }
  function generate_button_label_for_view(center_tab) {
    switch (center_tab) {
      case "geography":
        return "Generate geography";
      case "region":
      case "story":
      case "gates":
        return "Generate routes";
      default:
        return "Generate";
    }
  }
  function is_routes_view() {
    const routes_view_is_open = ["region", "story", "gates"].includes(Shell.layout.center);
    return routes_view_is_open;
  }
  function syncNonSceneToolbar() {
    if (isScene()) return;
    const ea = region_editor();
    const doc = ea && ea.getDoc();
    $("tbZoom").textContent = ea ? `${ea.getZoomPct()}%` : "100%";
    if (!doc) return;
    $("docTitle").textContent = `${doc.name || "region"}${ea.isUnsaved() ? " •" : ""} · seed ${doc.seed}`;
    if (sget("pokeroutes-shell-region-seed") !== doc.seed) sset("pokeroutes-shell-region-seed", doc.seed);
  }
  // Generate/Regenerate are shared between the toolbar and the Generate menu, and drive
  // whichever view (Scene or the geography/region/story/gates editor) is on screen.
  function doGenerate() {
    if (isScene()) { generate(); return; }
    const ea = region_editor();
    if (!ea) return;
    if (is_routes_view()) { ea.recompute("routes"); return; }
    ea.generate();
  }
  function doRegenerate() {
    if (isScene()) { regenerate(); return; }
    const ea = region_editor();
    if (!ea) return;
    ea.generate(Math.floor(Math.random() * 100000));
  }
  Shell.onCenter((tab) => {
    show_center_tab(tab);
    if (tab === "scene") requestAnimationFrame(resize);
    renderSelection();
  });

  // ------------------------------------------------------------ files
  function download(name, blob) {
    const a = el("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  }
  async function downloadURL(url, fallbackName) {
    const res = await fetch(url);
    if (!res.ok) {
      let msg = res.statusText;
      try { msg = (await res.json()).error || msg; } catch (e) { /* not json */ }
      throw new Error(msg);
    }
    const cd = res.headers.get("Content-Disposition") || "";
    const m = /filename="([^"]+)"/.exec(cd);
    const name = m ? m[1] : fallbackName;
    download(name, await res.blob());
    return name;
  }
  const specText = () => JSON.stringify(SL.paramsToSpecFile(params(), state.region), null, 2) + "\n";

  async function saveSpec(as) {
    const text = specText();
    const name = state.file.name || SL.safeFileName(params().name, ".json");
    if (!as && state.file.handle) {
      try {
        const w = await state.file.handle.createWritable();
        await w.write(text);
        await w.close();
        markSaved(state.file.name);
        return;
      } catch (e) { log("warn", `Could not write ${state.file.name} (${e.message}); choose where to save`); }
    }
    if (window.showSaveFilePicker) {
      try {
        const h = await window.showSaveFilePicker({ suggestedName: name, types: [{ description: "Route spec", accept: { "application/json": [".json"] } }] });
        const w = await h.createWritable();
        await w.write(text);
        await w.close();
        state.file = { handle: h, name: h.name };
        markSaved(h.name);
      } catch (e) { if (e.name !== "AbortError") log("error", "Save failed: " + e.message); }
      return;
    }
    download(name, new Blob([text], { type: "application/json" }));
    state.file = { handle: null, name };
    markSaved(name);
  }
  function markSaved(name) {
    state.saved = JSON.stringify(params());
    updateTitle();
    log("info", `Saved spec ${name}`);
  }

  async function openSpec() {
    if (window.showOpenFilePicker) {
      try {
        const [h] = await window.showOpenFilePicker({ types: [{ description: "Route spec", accept: { "application/json": [".json"], "text/yaml": [".yaml", ".yml"] } }] });
        const f = await h.getFile();
        await loadSpecText(await f.text(), f.name, /\.json$/i.test(f.name) ? h : null);
      } catch (e) { if (e.name !== "AbortError") log("error", "Open failed: " + e.message); }
      return;
    }
    $("fileOpen").value = "";
    $("fileOpen").click();
  }
  $("fileOpen").addEventListener("change", async () => {
    const f = $("fileOpen").files[0];
    if (f) await loadSpecText(await f.text(), f.name, null);
  });

  async function parseSpec(text) {
    try { return JSON.parse(text); } catch (e) { /* YAML: let the server parse it */ }
    return postJSON("/api/spec-parse", { text });
  }
  async function loadSpecText(text, name, handle) {
    let spec;
    try { spec = await parseSpec(text); } catch (e) { log("error", `Could not read ${name}: ${e.message}`); return; }
    const { params: p, region } = SL.specToParams(spec.format === "poke-routes-spec" ? Object.assign({}, spec.params, { region: spec.region, endings: spec.endings }) : spec, params());
    replaceParams(p, "open");
    state.region = region;
    if (region) sset(REGION_KEY, region); else sdel(REGION_KEY);
    renderRegionLink();
    state.file = { handle, name: handle ? name : "" };
    state.saved = JSON.stringify(params());
    updateTitle();
    log("info", `Opened ${name}`);
    Shell.setCenter("scene");
    generate();
  }

  async function exportZip() {
    const r = state.result;
    if (!r) return log("warn", "Generate a route first.");
    if (!r.exportable) return log("warn", "Too big for the GBA map buffer; no Porymap export.");
    try { log("info", `Exported ${await downloadURL(`/api/export?id=${encodeURIComponent(r.id)}`, `${r.route_id}_porymap.zip`)}`); }
    catch (e) { log("error", "Porymap export failed: " + e.message); }
  }
  async function exportScene() {
    const r = state.result;
    if (!r) return log("warn", "Generate a route first.");
    try { log("info", `Exported ${await downloadURL(`/api/scene?id=${encodeURIComponent(r.id)}`, `${r.route_id}_scene.json`)}`); }
    catch (e) { log("error", "scene.json export failed: " + e.message); }
  }
  function exportPNG() {
    const r = state.result;
    if (!r) return log("warn", "Generate a route first.");
    const c = document.createElement("canvas");
    c.width = r.width * CELL;
    c.height = r.height * CELL;
    paintScene(c.getContext("2d"), { zoom: 1, ox: 0, oy: 0 }, { interactive: false });
    const name = `${r.route_id}_stage${state.stage + 1}_${state.layer}.png`;
    c.toBlob((b) => { download(name, b); log("info", `Exported ${name} (${c.width}×${c.height}, stage ${state.stage + 1}, visible overlays)`); }, "image/png");
  }
  async function exportRegionJSON() {
    const s = current_region_seed();
    try {
      const res = await fetch(`/api/region?seed=${s}`);
      if (!res.ok) throw new Error(res.statusText);
      download(`region-${s}.json`, await res.blob());
      log("info", `Exported region-${s}.json`);
    } catch (e) { log("error", "Region export failed: " + e.message); }
  }
  async function exportAllSpecs() {
    if (region_editor()) { region_editor().exportSpecs(); return; }
    const s = current_region_seed();
    try { log("info", `Exported ${await downloadURL(`/api/region-specs.zip?seed=${s}`, `region-${s}_specs.zip`)}`); }
    catch (e) { log("error", "Spec export failed: " + e.message); }
  }

  function newRoute() {
    const p = { name: "Testweg", seed: Math.floor(Math.random() * 100000), special_areas: DEFAULT_SPECIALS.slice(), exits: DEFAULT_EXITS.map((e) => ({ ...e })) };
    for (const f of schema.fields) if (f.name !== "seed") p[f.name] = FIRST_RUN[f.name] != null ? FIRST_RUN[f.name] : f.default;
    replaceParams(p, "new route");
    state.region = null;
    sdel(REGION_KEY);
    renderRegionLink();
    state.file = { handle: null, name: "" };
    state.saved = "";
    updateTitle();
    log("info", "New route (default spec, random seed)");
    Shell.setCenter("scene");
    generate();
  }

  async function copySpec() {
    try { await navigator.clipboard.writeText(specText()); log("info", "Copied the spec to the clipboard"); }
    catch (e) { log("error", "Copy failed: " + e.message); }
  }
  async function pasteSpec() {
    let text;
    try { text = await navigator.clipboard.readText(); } catch (e) { log("error", "Paste needs clipboard permission: " + e.message); return; }
    if (!text.trim()) return log("warn", "The clipboard is empty.");
    await loadSpecText(text, "the clipboard", null);
  }

  function updateTitle() {
    const p = params();
    const name = state.file.name || p.name;
    const dirty = state.saved && state.saved !== JSON.stringify(p);
    const stale = state.generated && state.generated !== JSON.stringify(p);
    $("docTitle").textContent = `${name}${dirty ? " •" : ""} · seed ${p.seed}${stale ? " · edited since Generate" : ""}`;
    document.title = `${name} – poke-routes editor`;
  }

  // ------------------------------------------------------------ preferences dialog
  function renderPrefs() {
    const box = $("prefsBody");
    box.innerHTML = "";
    const row = (label, ctl) => { const r = el("div", "prop"); const l = el("label", "", label); if (ctl.id) l.htmlFor = ctl.id; r.append(l, ctl); box.append(r); };
    const chk = (k, id) => { const i = Object.assign(el("input"), { type: "checkbox", id, checked: !!prefs[k] }); i.onchange = () => { prefs[k] = i.checked; savePrefs(); applyPrefs(); }; return i; };
    const sel = (k, id, opts) => {
      const s = Object.assign(el("select"), { id });
      for (const [v, t] of opts) s.append(Object.assign(el("option", "", t), { value: v, selected: String(prefs[k]) === String(v) }));
      s.onchange = () => { prefs[k] = typeof PREF_DEFAULTS[k] === "number" ? +s.value : s.value; savePrefs(); applyPrefs(); if (k === "stageMs" && state.playing) startPlay(); };
      return s;
    };
    row("Generate on edit", chk("autogen", "pAutogen"));
    row("Play stages after Generate", chk("playOnGenerate", "pAuto"));
    row("Stage duration", sel("stageMs", "pMs", [[1600, "1.6 s (0.5x)"], [900, "0.9 s (1x)"], [450, "0.45 s (2x)"], [220, "0.22 s (4x)"]]));
    row("Crossfade between stages", chk("fade", "pFade"));
    row("Loop playback", chk("loop", "pLoop"));
    row("Fit view after Generate", sel("fit", "pFit", [["resize", "when the size changes"], ["always", "always"]]));
    row("Interface text", sel("ui", "pUi", [["12", "12 px (compact)"], ["13", "13 px"]]));
    box.append(el("p", "hint", "Stored in this browser only. The panel layout is remembered separately (Window > Reset layout)."));
  }
  $("prefsReset").onclick = () => { Object.assign(prefs, PREF_DEFAULTS); savePrefs(); applyPrefs(); renderPrefs(); };
  function openPrefs() { renderPrefs(); Shell.openDialog("dlgPrefs"); }

  // ------------------------------------------------------------ menus
  const isScene = () => Shell.layout.center === "scene";
  const hasRoute = () => !!state.result;
  const sceneRoute = () => !!state.result && isScene();
  // outside Scene these go to the docked geography / route design / story editor
  const mapView = () => !isScene() && !!region_editor();
  const viewZoomable = () => sceneRoute() || mapView();
  const zoomInView = () => { if (isScene()) zoomBy(1); else if (mapView()) region_editor().zoomIn(); };
  const zoomOutView = () => { if (isScene()) zoomBy(-1); else if (mapView()) region_editor().zoomOut(); };
  const fitView = () => { if (isScene()) fit(); else if (mapView()) region_editor().fit(); };
  function setTiler(t) {
    if (t === prefs.tiler) return;
    const info = (state.tilers || []).find((x) => x.id === t);
    if (info && !info.ok) { log("warn", `${info.label} isn't available here: ${info.error}`); return; }
    prefs.tiler = t;
    prefs.tiler_picked_by_hand = true;
    savePrefs();
    applyPrefs();
    log("info", `Tiler: ${t === "ark" ? "Ark rule tiles" : "learned Emerald tiles"}`);
    if (state.result) generate();
  }
  const menus = [
    { label: "File", items: [
      { id: "new", label: "New route", run: newRoute },
      { id: "open", label: "Open spec…", run: openSpec },
      { id: "recent", label: "Open recent", items: () => {
        const list = sget(SL.RECENT_KEY) || [];
        if (!list.length) return [{ label: "No recent routes", enabled: false }];
        return list.map((e) => ({ label: e.label, run: () => { replaceParams(e.params, "recent"); Shell.setCenter("scene"); generate(); } }))
          .concat([{ sep: true }, { label: "Clear recent", run: () => { sdel(SL.RECENT_KEY); log("info", "Cleared recent routes"); } }]);
      } },
      { sep: true },
      { id: "save", label: "Save spec", run: () => { if (mapView()) region_editor().save(); else saveSpec(false); } },
      { id: "saveas", label: "Save spec as…", run: () => saveSpec(true) },
      { sep: true },
      { id: "save-region", label: "Save region", enabled: mapView, run: () => region_editor().save() },
      { id: "open-region", label: "Open saved region", items: () => {
        if (!mapView()) return [{ label: "Open Geography first", enabled: false }];
        const names = region_editor().saved_names();
        if (!names.length) return [{ label: "No saved regions", enabled: false }];
        return names.map((n) => ({ label: n, run: () => region_editor().load(n) }));
      } },
      { sep: true },
      { id: "import", label: "Import", items: [
        { id: "imp-spec", label: "Spec file (JSON or YAML)…", run: openSpec },
        { id: "imp-paste", label: "Spec from clipboard", run: pasteSpec },
        { id: "imp-region", label: "Route from a region…", run: () => Shell.setCenter("region") },
      ] },
      { id: "export", label: "Export", items: [
        { id: "exp-zip", label: "Porymap files (.zip)", enabled: () => hasRoute() && state.result.exportable, run: exportZip },
        { id: "exp-scene", label: "scene.json", enabled: hasRoute, run: exportScene },
        { id: "exp-png", label: "PNG render of this stage", enabled: hasRoute, run: exportPNG },
        { sep: true },
        { id: "exp-region", label: "Region JSON", run: exportRegionJSON },
        { id: "exp-specs", label: "All region specs (.zip)", run: exportAllSpecs },
      ] },
    ] },
    { label: "Edit", items: [
      { id: "undo", label: "Undo", run: () => { if (mapView()) region_editor().undo(); else undo(); }, enabled: () => mapView() ? region_editor().can_undo() : undoStack.canUndo() },
      { id: "redo", label: "Redo", run: () => { if (mapView()) region_editor().redo(); else redo(); }, enabled: () => mapView() ? region_editor().can_redo() : undoStack.canRedo() },
      { sep: true },
      { id: "copy-spec", label: "Copy spec", run: copySpec },
      { id: "paste-spec", label: "Paste spec", run: pasteSpec },
      { sep: true },
      { id: "deselect", label: "Deselect", run: () => select(null), enabled: () => isScene() && !!state.sel },
      { sep: true },
      { id: "prefs", label: "Preferences…", run: openPrefs },
    ] },
    { label: "View", items: [
      { id: "v-left", label: "Hierarchy", checked: () => Shell.layout.left.open, run: () => Shell.toggleDock("left") },
      { id: "v-right", label: "Inspector", checked: () => Shell.layout.right.open, run: () => Shell.toggleDock("right") },
      { id: "v-bottom", label: "Bottom panel", checked: () => Shell.layout.bottom.open, run: () => Shell.toggleDock("bottom") },
      { sep: true },
      { id: "zin", label: "Zoom in", enabled: viewZoomable, run: zoomInView },
      { id: "zout", label: "Zoom out", enabled: viewZoomable, run: zoomOutView },
      { id: "zfit", label: "Fit to view", enabled: viewZoomable, run: fitView },
      { id: "z100", label: "Actual size (100%)", enabled: sceneRoute, run: () => zoomTo(1) },
      { id: "frame", label: "Frame selection", enabled: sceneRoute, run: focusSel },
      { sep: true },
      { head: "Overlays" },
      { id: "ov-areas", label: "Areas", checked: () => state.ov.areas, run: () => toggleOv("areas") },
      { id: "ov-items", label: "Items", checked: () => state.ov.events, run: () => toggleOv("events") },
      { id: "ov-classes", label: "Classes instead of tiles", checked: () => state.layer === "classes", run: () => setLayer(state.layer === "classes" ? "tiles" : "classes") },
      { id: "ov-grid", label: "Grid", checked: () => state.ov.grid, run: () => toggleOv("grid") },
      { sep: true },
      { id: "v-autogen", label: "Generate on edit", checked: () => prefs.autogen, run: () => { prefs.autogen = !prefs.autogen; savePrefs(); } },
      { id: "v-autoplay", label: "Play stages after Generate", checked: () => prefs.playOnGenerate, run: () => { prefs.playOnGenerate = !prefs.playOnGenerate; savePrefs(); } },
      { id: "v-stages", label: "Show stage", items: () => {
        const r = state.result;
        if (!r) return [{ label: "No route yet", enabled: false }];
        return r.stages.map((s, i) => ({ label: `${i + 1}. ${s.name}`, radio: true, checked: () => state.stage === i, run: () => { stopPlay(); setStage(i, true); } }));
      } },
    ] },
    { label: "Generate", items: [
      { id: "gen", label: "Generate", run: () => doGenerate() },
      { id: "regen", label: "Regenerate with new seed", run: () => doRegenerate() },
      { sep: true },
      { id: "gen-region", label: "Generate region…", run: () => Shell.setCenter("geography") },
      { sep: true },
      { id: "tiler", label: "Tiler", items: [
        { id: "t-learned", label: "Learned (Emerald General + Fortree)", radio: true, checked: () => prefs.tiler === "learned", run: () => setTiler("learned") },
        { id: "t-ark", label: "Ark rule tiles", radio: true, checked: () => prefs.tiler === "ark", enabled: () => !state.tilers || state.tilers.some((t) => t.id === "ark" && t.ok), run: () => setTiler("ark") },
      ] },
      { sep: true },
      { id: "play", label: "Play / pause tile animation", enabled: sceneRoute, run: (ev) => { if (ev && document.activeElement && document.activeElement.tagName === "BUTTON") document.activeElement.blur(); toggle_tile_animation(); } },
      { id: "next", label: "Faster tile animation", enabled: sceneRoute, run: () => change_tile_animation_speed(1) },
      { id: "prev", label: "Slower tile animation", enabled: sceneRoute, run: () => change_tile_animation_speed(-1) },
      { id: "first", label: "Previous animation frame", enabled: sceneRoute, run: () => step_tile_animation(-1) },
      { id: "last", label: "Next animation frame", enabled: sceneRoute, run: () => step_tile_animation(1) },
    ] },
    { label: "Window", items: [
      { id: "w-scene", label: "Routes", radio: true, checked: () => Shell.layout.center === "scene", run: () => Shell.setCenter("scene") },
      { id: "w-geography", label: "Geography editor", radio: true, checked: () => Shell.layout.center === "geography", run: () => Shell.setCenter("geography") },
      { id: "w-region", label: "Map", radio: true, checked: () => Shell.layout.center === "region", run: () => Shell.setCenter("region") },
      { id: "w-story", label: "Story", radio: true, checked: () => Shell.layout.center === "story", run: () => Shell.setCenter("story") },
      { id: "w-gates", label: "Route design B", radio: true, checked: () => Shell.layout.center === "gates", run: () => Shell.setCenter("gates") },
      { sep: true },
      { id: "w-console", label: "Console", run: () => Shell.setBottom("console") },
      { id: "w-improve", label: "Improve queue", enabled: is_improve_tab_shown, run: () => { Shell.setBottom("improve"); const t = document.getElementById("impText"); if (t) t.focus(); } },
      { id: "w-agent", label: "Agent", run: () => { Shell.setBottom("agent"); const t = document.getElementById("agText"); if (t) t.focus(); } },
      { sep: true },
      { id: "w-reset", label: "Reset layout", run: () => Shell.resetLayout() },
    ] },
    { label: "Help", items: [
      { id: "keys", label: "Keyboard shortcuts", run: () => Shell.showShortcuts() },
      { sep: true },
      { id: "about", label: "About poke-routes", run: () => { $("aboutServer").textContent = location.origin; Shell.openDialog("dlgAbout"); } },
    ] },
  ];

  // plain-letter keys belong to whichever view is open: outside Scene the map editor has its own (V R L F S B W A, ?)
  const toolKey = (t, label) => ({ id: "tool-" + t, label, group: "Scene tools", enabled: isScene, run: () => setTool(t) });
  const extras = [
    toolKey("select", "Select tool"), toolKey("pan", "Pan tool"), toolKey("paint", "Paint tool"), toolKey("stamp", "Stamp tool"),
    { id: "x-zin", label: "Zoom in", group: "Scene view", enabled: sceneRoute, run: () => zoomBy(1) },
    { id: "x-zout", label: "Zoom out", group: "Scene view", enabled: sceneRoute, run: () => zoomBy(-1) },
    { id: "x-zfit", label: "Fit to view", group: "Scene view", enabled: sceneRoute, run: fit },
    { id: "x-help", label: "Keyboard shortcuts", group: "Help", enabled: isScene, run: () => Shell.showShortcuts() },
    { id: "x-ledge-rot", label: "Rotate ledge or fence", group: "Scene tools",
      enabled: () => isScene() && state.tool === "paint" && (state.brush === LEDGE_BRUSH || is_rotatable_object_selected()),
      run: () => (state.brush === LEDGE_BRUSH ? rotateLedgeDir() : rotate_selected_object()) },
  ];

  // ------------------------------------------------------------ wiring
  function initUI() {
    document.querySelectorAll("[data-tool]").forEach((b) => { b.onclick = () => setTool(b.dataset.tool); });
    document.querySelectorAll("[data-layer]").forEach((b) => { b.onclick = () => setLayer(b.dataset.layer); });
    document.querySelectorAll("[data-ov]").forEach((b) => { b.onclick = () => toggleOv(b.dataset.ov); });
    $("tbFirst").onclick = () => step_tile_animation(-1);
    $("tbPrev").onclick = () => change_tile_animation_speed(-1);
    $("tbNext").onclick = () => change_tile_animation_speed(1);
    $("tbLast").onclick = () => step_tile_animation(1);
    $("tbPlay").onclick = toggle_tile_animation;
    sync_tile_animation_buttons();
    $("tbGenerate").onclick = doGenerate;
    $("tbGenerate").title = "Generate";
    $("tbZoomIn").onclick = zoomInView;
    $("tbZoomOut").onclick = zoomOutView;
    $("tbFit").onclick = fitView;
    $("tbTiler").onchange = () => setTiler($("tbTiler").value);
    $("paintClass").addEventListener("change", () => { choose_brush($("paintClass").value); });
    document.querySelectorAll("[data-left-tab]").forEach((tab_button) => { tab_button.onclick = () => show_left_tab(tab_button.dataset.leftTab); });
    $("tbMarkings").onclick = () => toggleOv("markings");
    sync_markings_button();
    $("dice").onclick = doRegenerate;
    $("name").addEventListener("change", () => edited("name"));
    $("seed").addEventListener("change", () => edited("seed"));
    $("hierFilter").addEventListener("input", () => { if (mapView()) region_editor().refresh_tree(); });
    $("areaAdd").onclick = addSpecial;
    $("areaInput").addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); addSpecial(); } });
    $("exitAdd").onclick = () => { if (state.exits.length < 4) { state.exits.push({ side: "west", at: 0.5, width: 3 }); renderExits(); edited("exits"); } };
    fill_brush_select();
    $("exactTiles").onclick = pick_exact_tile_from_palette;
    $("ledgeRot").querySelectorAll(".ledge-dir-btn").forEach((b) => { b.onclick = () => { choose_ledge(b.dataset.dir); }; });
    const sk = $("stampKind");
    for (const [k, s] of Object.entries(SL.STAMPS)) sk.append(Object.assign(el("option", "", s.label), { value: k }));
    setTool("select");
    syncViewButtons();
    SL.attachShortcuts(menus, extras);
    Shell.setMenus(menus, extras);
  }

  async function boot() {
    applyPrefs();
    initUI();
    await loadSchema();
    const saved = sget(STORE_KEY);
    buildControls(saved);
    if (saved) {
      if (saved.name) $("name").value = saved.name;
      if (saved.seed != null) $("seed").value = saved.seed;
    }
    state.specials = saved && Array.isArray(saved.special_areas) ? saved.special_areas.slice(0, 6) : DEFAULT_SPECIALS.slice();
    state.exits = saved && Array.isArray(saved.exits) && saved.exits.length >= 1 ? saved.exits.map((e) => ({ ...e })) : DEFAULT_EXITS.map((e) => ({ ...e }));
    renderChips();
    renderExits();
    lastParams = params();
    renderRegionLink();
    renderTree();
    updateTitle();
    new ResizeObserver(resize).observe(wrap);
    const q = new URLSearchParams(location.search);
    if (q.get("view") && SL.CENTER_TABS.includes(q.get("view"))) Shell.setCenter(q.get("view"));
    else if (q.has("region")) Shell.setCenter("scene");
    // fire the centre-tab hook once for whatever the layout restored
    show_center_tab(Shell.layout.center);
    if (q.has("view") || q.has("region")) window.history.replaceState(null, "", "/");
    resize();
    loadOptions();
    loadTilers();
    log("info", "poke-routes editor ready");
    generate({ keepPaint: true });
  }

  const EMPTY_PALETTE = { tiler: "", tileset: "", terrain: [], unavailable: [], objects: [], tiles: {} };
  const PALETTE_GROUPS = [["ground", "Ground"], ["water", "Water"], ["trees", "Trees"], ["rock", "Rock"], ["ledges", "Ledges"]];
  const terrain_preview_size_in_pixels = 40;
  const exact_tile_size_in_pixels = 24;
  const exact_tile_columns = 8;

  async function load_paint_palette(route_result) {
    const palette_key = `${route_result.tiler}|${route_result.tileset}`;
    if (!state.palettes[palette_key]) state.palettes[palette_key] = await fetch_paint_palette(route_result);
    state.palette = state.palettes[palette_key];
    if (!is_brush_available(state.brush)) state.brush = "g";
    fill_brush_select();
    updateLedgeUI();
    if (!$("leftTiles").hidden) render_paint_palette();
  }

  async function fetch_paint_palette(route_result) {
    try {
      return await fetchJSON(`/api/paint-palette?id=${encodeURIComponent(route_result.id)}`);
    } catch (e) {
      log("warn", "No paint palette from the server (" + e.message + "); only plain grass can be painted.");
      return EMPTY_PALETTE;
    }
  }

  function is_brush_available(brush) {
    const palette = state.palette, { kind, key } = SL.brush_kind(brush);
    if (kind === "ledge") return SL.palette_ledge_codes(palette).length > 0;
    if (kind === "object") return !!SL.palette_object_entry(palette, key);
    if (kind === "stamp") return !!SL.STAMPS[key] && !!palette && palette.tiler === "learned";
    if (kind === "tile") return !!palette && palette.tiles[key] != null;
    const entry = SL.palette_terrain_entry(palette, key);
    return key === "g" || (!!entry && entry.group !== "ledges");
  }

  function brush_cells_at(cell_x, cell_y) {
    const r = state.result, { kind, key } = SL.brush_kind(state.brush);
    if (kind === "object") {
      const object_entry = SL.palette_object_entry(state.palette, key);
      return object_entry ? SL.object_stamp_cells(object_entry, cell_x, cell_y, r.width, r.height) : [];
    }
    if (kind === "stamp") return SL.stampCells(key, cell_x, cell_y, r.width, r.height);
    if (kind === "tile") return [[cell_x, cell_y, SL.exact_tile_value(state.palette, key)]];
    return terrain_brush_cells_at(cell_x, cell_y, kind === "ledge" ? state.ledgeDir : key);
  }

  function terrain_brush_cells_at(cell_x, cell_y, class_code) {
    const r = state.result, entry = SL.palette_terrain_entry(state.palette, class_code);
    const is_two_wide = entry ? entry.two_wide : SL.TWO_WIDE.includes(class_code);
    const chosen_size = +$("brushSize").value;
    const brush_cells = SL.brushCells(cell_x, cell_y, is_two_wide ? Math.max(2, chosen_size) : chosen_size, class_code, r.width, r.height);
    if (!entry || !entry.snap_blocks) return brush_cells;
    return SL.snap_cells_to_blocks(brush_cells, r.width, r.height);
  }

  function add_brush_cells_to_stroke(brush_cells) {
    const places_whole_objects = SL.brush_kind(state.brush).kind === "object";
    if (places_whole_objects && brush_cells.some(([x, y]) => state.paint.cells.has(`${x},${y}`))) return;
    for (const [x, y, value] of brush_cells) state.paint.cells.set(`${x},${y}`, value);
  }

  function draw_paint_value(graphics_context, cell_x, cell_y, value, screen_x, screen_y, cell_size_on_screen) {
    if (value.includes("#")) { draw_metatile(graphics_context, +value.split("#")[1], screen_x, screen_y, cell_size_on_screen); return; }
    const entry = SL.palette_terrain_entry(state.palette, value);
    if (entry && state.atlas) { draw_metatile(graphics_context, entry.sample[(cell_x % 2) + (cell_y % 2) * 2], screen_x, screen_y, cell_size_on_screen); return; }
    graphics_context.fillStyle = PR.CLASS_COLORS[value] || "#888";
    graphics_context.fillRect(screen_x, screen_y, cell_size_on_screen, cell_size_on_screen);
  }

  function draw_brush_preview_under_cursor(graphics_context, origin_x, origin_y, cell_size_on_screen) {
    if (state.tool !== "paint" || !state.result) return;
    graphics_context.globalAlpha = 0.75;
    for (const [x, y, value] of brush_cells_at(state.hover.x, state.hover.y)) {
      draw_paint_value(graphics_context, x, y, value, origin_x + x * cell_size_on_screen, origin_y + y * cell_size_on_screen, cell_size_on_screen);
    }
    graphics_context.globalAlpha = 1;
  }

  function draw_metatile(graphics_context, metatile_id, screen_x, screen_y, cell_size_on_screen) {
    if (!state.atlas) return;
    const { meta, img } = state.atlas;
    const atlas_position = meta.index[metatile_id];
    if (atlas_position == null) return;
    graphics_context.imageSmoothingEnabled = false;
    graphics_context.drawImage(img, (atlas_position % meta.columns) * CELL, Math.floor(atlas_position / meta.columns) * CELL, CELL, CELL,
      screen_x, screen_y, cell_size_on_screen, cell_size_on_screen);
  }

  function brush_label(brush) {
    const { kind, key } = SL.brush_kind(brush);
    if (kind === "ledge") return "ledge";
    if (kind === "object") return (SL.palette_object_entry(state.palette, key) || { name: key }).name;
    if (kind === "stamp") return (SL.STAMPS[key] || { label: key }).label;
    if (kind === "tile") return `tile #${key}`;
    const entry = SL.palette_terrain_entry(state.palette, key);
    return entry ? entry.name : (PR.CLASS_NAMES[key] || key);
  }

  function palette_brushes() {
    const palette = state.palette || EMPTY_PALETTE;
    const terrain_brushes = palette.terrain.filter((entry) => entry.group !== "ledges").map((entry) => entry.code);
    const ledge_brushes = SL.palette_ledge_codes(palette).length ? [LEDGE_BRUSH] : [];
    const object_brushes = palette.objects.map((entry) => "object:" + entry.id);
    const stamp_brushes = palette.tiler === "learned" ? Object.keys(SL.STAMPS).map((name) => "stamp:" + name) : [];
    return terrain_brushes.concat(ledge_brushes, object_brushes, stamp_brushes);
  }

  function fill_brush_select() {
    const brush_select = $("paintClass");
    brush_select.innerHTML = "";
    const brushes = palette_brushes();
    if (!brushes.includes(state.brush)) brushes.push(state.brush);
    for (const brush of brushes) brush_select.append(Object.assign(el("option", "", brush_label(brush)), { value: brush }));
    brush_select.value = state.brush;
  }

  function choose_brush(brush) {
    state.brush = brush;
    fill_brush_select();
    if (state.tool !== "paint") setTool("paint");
    updateLedgeUI();
    render_paint_palette();
  }

  function choose_ledge(ledge_code) {
    state.ledgeDir = ledge_code;
    choose_brush(LEDGE_BRUSH);
  }

  function show_left_tab(left_tab_name) {
    document.querySelectorAll("[data-left-tab]").forEach((tab_button) => tab_button.classList.toggle("on", tab_button.dataset.leftTab === left_tab_name));
    const is_geography_view = Shell.layout.center === "geography";
    $("leftHierarchy").hidden = left_tab_name !== "hierarchy";
    $("leftTiles").hidden = left_tab_name !== "tiles" || is_geography_view;
    $("leftBiomes").hidden = left_tab_name !== "tiles" || !is_geography_view;
    if (left_tab_name !== "tiles") return;
    if (is_geography_view) { render_biome_brushes(); return; }
    render_paint_palette();
  }

  function render_biome_brushes() {
    const biome_grid = $("biomeGrid");
    const editor_actions = region_editor();
    biome_grid.innerHTML = "";
    if (!editor_actions || !window.ED) return;
    const current_paint_biome = editor_actions.paint_biome ? editor_actions.paint_biome() : null;
    for (const biome_code of window.ED.PAINT_BIOMES) {
      const biome_rgb = window.ED.BIOME_RGB[biome_code] || [255, 0, 255];
      const biome_button = el("button", "terrain-brush" + (biome_code === current_paint_biome ? " on" : ""));
      biome_button.type = "button";
      biome_button.title = `Paint ${window.ED.BIOME_NAMES[biome_code] || biome_code}`;
      const biome_swatch = el("span", "biome-swatch");
      biome_swatch.style.background = `rgb(${biome_rgb.join(",")})`;
      biome_button.append(biome_swatch, el("span", "", window.ED.BIOME_NAMES[biome_code] || biome_code));
      biome_button.onclick = () => { editor_actions.set_paint_biome(biome_code); render_biome_brushes(); };
      biome_grid.append(biome_button);
    }
  }

  function preview_canvas_of(cell_rows) {
    const preview_canvas = Object.assign(el("canvas"), { width: terrain_preview_size_in_pixels, height: terrain_preview_size_in_pixels });
    const preview_context = preview_canvas.getContext("2d");
    const row_count = cell_rows.length, column_count = Math.max(...cell_rows.map((row) => row.length));
    const cell_size = terrain_preview_size_in_pixels / Math.max(2, row_count, column_count);
    const left = (terrain_preview_size_in_pixels - column_count * cell_size) / 2, top = (terrain_preview_size_in_pixels - row_count * cell_size) / 2;
    cell_rows.forEach((row, row_index) => row.forEach((metatile_id, column_index) => {
      if (metatile_id != null) draw_metatile(preview_context, metatile_id, left + column_index * cell_size, top + row_index * cell_size, cell_size);
    }));
    return preview_canvas;
  }

  function sample_rows(entry) {
    return [entry.sample.slice(0, 2), entry.sample.slice(2, 4)];
  }

  function stamp_rows(stamp_name) {
    const stamp_cells = SL.STAMPS[stamp_name].cells;
    const min_x = Math.min(...stamp_cells.map((cell) => cell[0])), min_y = Math.min(...stamp_cells.map((cell) => cell[1]));
    const rows = [];
    for (const [offset_x, offset_y, class_code] of stamp_cells) {
      const entry = SL.palette_terrain_entry(state.palette, class_code);
      const row = rows[offset_y - min_y] || (rows[offset_y - min_y] = []);
      row[offset_x - min_x] = entry ? entry.sample[0] : null;
    }
    return rows;
  }

  function palette_button(brush, label, title, cell_rows, is_on, on_choose) {
    const brush_button = el("button", "terrain-brush" + (is_on ? " on" : ""));
    brush_button.type = "button";
    brush_button.title = title;
    brush_button.dataset.brush = brush;
    brush_button.append(preview_canvas_of(cell_rows), el("span", "", label));
    brush_button.onclick = on_choose;
    return brush_button;
  }

  function palette_section(heading, buttons) {
    const section = el("section", "palette-section");
    const button_grid = el("div", "terrain-grid");
    button_grid.append(...buttons);
    section.append(el("h4", "palette-heading", heading), button_grid);
    return section;
  }

  function terrain_buttons(group_name) {
    const painting = state.tool === "paint";
    const entries_in_group = state.palette.terrain.filter((entry) => entry.group === group_name);
    if (group_name === "ledges") return single_ledge_button(entries_in_group, painting);
    return entries_in_group.map((entry) => palette_button(entry.code, entry.name, `Paint ${entry.name}`, sample_rows(entry), painting && state.brush === entry.code, () => choose_brush(entry.code)));
  }

  function single_ledge_button(ledge_entries, painting) {
    if (!ledge_entries.length) return [];
    const current_ledge = ledge_entries.find((entry) => entry.code === state.ledgeDir) || ledge_entries[0];
    const ledge_is_selected = painting && state.brush === LEDGE_BRUSH;
    const ledge_button = palette_button(LEDGE_BRUSH, current_ledge.name, `Paint ${current_ledge.name}; click again or press R to turn it`, sample_rows(current_ledge), ledge_is_selected,
      () => (ledge_is_selected ? rotateLedgeDir() : choose_ledge(current_ledge.code)));
    return [ledge_button];
  }

  const rotatable_object_suffixes = ["_h", "_v"];

  function rotatable_object_base(object_id) {
    const suffix = rotatable_object_suffixes.find((candidate_suffix) => object_id.endsWith(candidate_suffix));
    const object_base = suffix ? object_id.slice(0, -suffix.length) : null;
    return object_base;
  }

  function is_rotatable_object_group(object_base) {
    const ids = state.palette.objects.map((entry) => entry.id);
    const group_has_both_orientations = rotatable_object_suffixes.every((suffix) => ids.includes(object_base + suffix));
    return group_has_both_orientations;
  }

  function rotate_selected_object() {
    const object_id = state.brush.startsWith("object:") ? state.brush.slice("object:".length) : "";
    const object_base = rotatable_object_base(object_id);
    if (!object_base || !is_rotatable_object_group(object_base)) return;
    const next_suffix = object_id.endsWith("_h") ? "_v" : "_h";
    choose_brush("object:" + object_base + next_suffix);
  }

  function is_rotatable_object_selected() {
    const object_id = state.brush.startsWith("object:") ? state.brush.slice("object:".length) : "";
    const object_base = rotatable_object_base(object_id);
    return !!object_base && is_rotatable_object_group(object_base);
  }

  function object_button(entry, painting) {
    const brush = "object:" + entry.id, size = `${entry.cells[0].length}×${entry.cells.length}`;
    const object_base = rotatable_object_base(entry.id);
    const is_rotatable = !!object_base && is_rotatable_object_group(object_base);
    if (!is_rotatable) return palette_button(brush, entry.name, `Place ${entry.name} (${size}, exact tiles with their own collision)`, entry.cells, painting && state.brush === brush, () => choose_brush(brush));
    const selected_variant = state.brush.startsWith("object:" + object_base + "_") ? state.brush.slice("object:".length) : object_base + "_h";
    const shown_entry = state.palette.objects.find((candidate) => candidate.id === selected_variant) || entry;
    const group_is_selected = painting && state.brush === "object:" + shown_entry.id;
    const group_name = object_base.replace(/_/g, " ");
    const direction_name = shown_entry.id.endsWith("_v") ? "down" : "across";
    return palette_button("object:" + shown_entry.id, `${group_name} (${direction_name})`, `Place ${group_name}; click again or press R to switch across/down`, shown_entry.cells, group_is_selected,
      () => (group_is_selected ? rotate_selected_object() : choose_brush("object:" + shown_entry.id)));
  }

  function object_buttons() {
    const painting = state.tool === "paint";
    const shown_object_entries = state.palette.objects.filter((entry) => !(entry.id.endsWith("_v") && is_rotatable_object_group(rotatable_object_base(entry.id))));
    const objects = shown_object_entries.map((entry) => object_button(entry, painting));
    if (state.palette.tiler !== "learned") return objects;
    const stamps = Object.entries(SL.STAMPS).map(([stamp_name, stamp]) => {
      const brush = "stamp:" + stamp_name;
      return palette_button(brush, stamp.label, `Stamp ${stamp.label}`, stamp_rows(stamp_name), painting && state.brush === brush, () => choose_brush(brush));
    });
    return objects.concat(stamps);
  }

  function render_paint_palette() {
    const sections = $("paletteSections");
    sections.innerHTML = "";
    if (!state.palette) return;
    for (const [group_name, heading] of PALETTE_GROUPS) {
      const buttons = terrain_buttons(group_name);
      if (buttons.length) sections.append(palette_section(heading, buttons));
    }
    if (state.palette.objects.length) sections.append(palette_section("Objects", object_buttons()));
    if (state.palette.unavailable.length) sections.append(unavailable_note(state.palette.unavailable));
    render_exact_tiles();
  }

  function unavailable_note(unavailable_entries) {
    const note = el("p", "tiles-note tiles-missing", "Not in this tileset: " + unavailable_entries.map((entry) => entry.name).join(", ") + ".");
    note.title = unavailable_entries.map((entry) => `${entry.name}: ${entry.reason}`).join("\n");
    return note;
  }

  function exact_tile_ids() {
    if (!state.palette || !state.atlas) return [];
    const atlas_index = state.atlas.meta.index;
    return Object.keys(state.palette.tiles).map(Number).filter((metatile_id) => atlas_index[metatile_id] != null).sort((a, b) => a - b);
  }

  function render_exact_tiles() {
    const tiles_canvas = $("exactTiles");
    const metatile_ids = exact_tile_ids();
    tiles_canvas.width = exact_tile_columns * exact_tile_size_in_pixels;
    tiles_canvas.height = Math.max(1, Math.ceil(metatile_ids.length / exact_tile_columns)) * exact_tile_size_in_pixels;
    tiles_canvas.dataset.ids = metatile_ids.join(",");
    const tiles_context = tiles_canvas.getContext("2d");
    const { kind, key } = SL.brush_kind(state.brush);
    metatile_ids.forEach((metatile_id, position) => {
      const cell_left = (position % exact_tile_columns) * exact_tile_size_in_pixels, cell_top = Math.floor(position / exact_tile_columns) * exact_tile_size_in_pixels;
      draw_metatile(tiles_context, metatile_id, cell_left, cell_top, exact_tile_size_in_pixels);
      if (kind !== "tile" || +key !== metatile_id) return;
      tiles_context.strokeStyle = "#ffde59";
      tiles_context.lineWidth = 2;
      tiles_context.strokeRect(cell_left + 1, cell_top + 1, exact_tile_size_in_pixels - 2, exact_tile_size_in_pixels - 2);
    });
  }

  function pick_exact_tile_from_palette(click_event) {
    const tiles_canvas = $("exactTiles");
    const bounds = tiles_canvas.getBoundingClientRect();
    const scale = tiles_canvas.width / bounds.width;
    const column = Math.floor(((click_event.clientX - bounds.left) * scale) / exact_tile_size_in_pixels);
    const row = Math.floor(((click_event.clientY - bounds.top) * scale) / exact_tile_size_in_pixels);
    const metatile_ids = (tiles_canvas.dataset.ids || "").split(",").filter(Boolean);
    const metatile_id = metatile_ids[row * exact_tile_columns + column];
    if (metatile_id == null) return;
    choose_brush("tile:" + metatile_id);
  }

  function is_improve_tab_shown() {
    const improve_tab_button = document.querySelector('[data-bottom="improve"]');
    const improve_tab_is_shown = !!improve_tab_button && !improve_tab_button.hidden;
    return improve_tab_is_shown;
  }

  window.Bench = { generate, state, params, setParams, undoStack, menus, extras, loadSpecText };
  boot();
})();
