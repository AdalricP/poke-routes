/* Route bench inside the editor shell: spec inspector -> /api/generate -> stage canvases
   drawn from the metatile atlas, plus the hierarchy, timeline, the geography / region editor,
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
  const PAINT_CLASSES = "fgptwrsexa";
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
    $("tlSpeed").value = String(prefs.stageMs);
    if (!$("tlSpeed").value) $("tlSpeed").value = "900";
    $("tlLoop").checked = !!prefs.loop;
    $("tbTiler").value = prefs.tiler;
  }

  // ------------------------------------------------------------ state
  const viewSaved = sget(VIEW_KEY) || {};
  const state = {
    atlases: {}, result: null,
    stage: 0, prevStage: null, fadeStart: 0,
    tileCanvases: [], classCanvases: [], changedMasks: [], stageInfo: [], flashStart: 0,
    view: { zoom: 1, ox: 0, oy: 0 },
    layer: viewSaved.layer === "classes" ? "classes" : "tiles",
    ov: Object.assign({ areas: true, events: true, grid: false }, viewSaved.ov || {}),
    hover: null, hoverArea: null, sel: null,
    playing: false, timer: null, busy: false,
    specials: [], exits: [], tab: "dex",
    tool: "select", paint: { cells: new Map() },
    region: sget(REGION_KEY),      // {name, region, endings} or null
    file: { handle: null, name: "" },
    saved: "", generated: "",
    expanded: new Set(["route", "g:lanes", "g:special", "g:layers"]),
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
      x.disabled = state.exits.length <= 2;
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
    if (Array.isArray(p.exits) && p.exits.length >= 2) state.exits = p.exits.map((e) => ({ ...e }));
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
    if (state.busy) return;
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
      state.sel = null;
      undoStack.filter((e) => e.kind !== "paint");
      buildCanvases();
      state.stage = res.stages.length - 1;
      state.prevStage = null;
      buildTimeline();
      renderInfo();
      renderTree();
      renderSelection();
      if (resized || prefs.fit === "always") fit();
      $("sceneMsg").hidden = true;
      state.generated = JSON.stringify(p);
      sset(SL.RECENT_KEY, SL.pushRecent(sget(SL.RECENT_KEY), p, 10, Date.now()));
      reportToConsole(res, (performance.now() - t0) / 1000);
      updatePaintCount();
      await reapplyPaint();
      if (prefs.playOnGenerate && !(opts && opts.auto) && res.stages.length > 1) { setStage(0, false); startPlay(); }
      else setStage(res.stages.length - 1, false);
      updateTitle();
    } catch (e) {
      $("sceneMsg").hidden = !!state.result;
      log("error", "Generate failed: " + e.message);
    } finally {
      setBusy(false);
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
    return { tiles: c, classes: cc, mask: prevTiles ? mask : null, changed: prevTiles ? changed : st.tiles.length };
  }

  function buildCanvases() {
    const r = state.result;
    state.tileCanvases = [];
    state.classCanvases = [];
    state.changedMasks = [];
    state.stageInfo = [];
    let prev = null, prevTiles = null;
    r.stages.forEach((st, i) => {
      const d = drawStage(i, prev, prevTiles);
      state.tileCanvases.push(d.tiles);
      state.classCanvases.push(d.classes);
      state.changedMasks.push(d.mask);
      const pz = i ? r.stages[i - 1].zones : 0, pe = i ? r.stages[i - 1].events : 0;
      state.stageInfo.push({ changed: d.changed, zones: st.zones - pz, events: st.events - pe });
      prev = d.tiles;
      prevTiles = st.tiles;
    });
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
      if (!canPaint(true)) { drag = null; return; }
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
    const r = state.result;
    const c = r && cellOf(sx, sy);
    if (!c) { select(null); return; }
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
    if (state.ov.events) drawEvents(g, PR.eventsAt(r, state.stage), ox, oy, s);
    if (state.ov.areas) drawAreas(g, zones, ox, oy, s);
    if (!opt.interactive) return;
    drawSelection(g, ox, oy, s);
    if (state.paint.cells.size) {
      g.globalAlpha = 0.7;
      for (const [k, cls] of state.paint.cells) {
        const [x, y] = k.split(",").map(Number);
        g.fillStyle = PR.CLASS_COLORS[cls] || "#f0f";
        g.fillRect(ox + x * s, oy + y * s, s, s);
      }
      g.globalAlpha = 1;
    }
    if (state.hover && !state.hoverArea) {
      const cells = hoverCells();
      g.strokeStyle = "rgba(255,255,255,0.9)";
      g.lineWidth = 1;
      for (const [x, y] of cells) g.strokeRect(ox + x * s + 0.5, oy + y * s + 0.5, s - 1, s - 1);
    }
  }

  function hoverCells() {
    const h = state.hover, r = state.result;
    if (state.tool === "paint") return SL.brushCells(h.x, h.y, +$("brushSize").value, "g", r.width, r.height);
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
    if (r.tiler === "ark") { if (loud) log("warn", "Painting re-tiles with the learned tiler; switch Generate > Tiler to Learned to paint."); return false; }
    return true;
  }
  function paintAt(sx, sy) {
    const r = state.result;
    const c = cellOf(sx, sy);
    if (!c) { state.paint.last = null; return; }
    if (state.tool === "stamp") {
      if (state.paint.last && state.paint.last.x === c.x && state.paint.last.y === c.y) return;
      for (const [x, y, k] of SL.stampCells($("stampKind").value, c.x, c.y, r.width, r.height)) state.paint.cells.set(`${x},${y}`, k);
    } else {
      const pts = state.paint.last ? SL.lineCells(state.paint.last, c).slice(1) : [c];
      const k = $("paintClass").value, size = SL.TWO_WIDE.includes(k) ? Math.max(2, +$("brushSize").value) : +$("brushSize").value;
      for (const p of pts) for (const [x, y] of SL.brushCells(p.x, p.y, size, k, r.width, r.height)) state.paint.cells.set(`${x},${y}`, k);
    }
    state.paint.last = c;
    draw();
  }
  async function commitPaint() {
    const r = state.result;
    const fin = r.stages[r.stages.length - 1];
    const stroke = [...state.paint.cells].map(([k, cls]) => [...k.split(",").map(Number), cls]);
    const cells = [], old = [];
    for (const [x, y, cls] of stroke.concat(SL.tidyPaint(fin.cls, stroke))) {
      const was = fin.cls[y][x];
      if (was === cls) continue;
      cells.push([x, y, cls]);
      old.push([x, y, PAINT_CLASSES.includes(was) ? was : "g"]);
    }
    if (!cells.length) { state.paint.cells = new Map(); draw(); return; }
    const before = savedPaint(r);
    const ok = await retile(cells);
    if (!ok) return;
    const after = SL.mergePaint(before, cells);
    storePaint(r, after);
    undoStack.push({ kind: "paint", id: r.id, cells, old, before, after });
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
    $("paintClear").disabled = !n;
  }
  function clearPaint() {
    const r = state.result;
    if (!r || !Object.keys(savedPaint(r)).length) return;
    storePaint(r, {});
    undoStack.filter((e) => e.kind !== "paint");
    log("info", `Cleared the painted cells of ${r.name}; regenerating`);
    generate();
  }
  // a new route with remembered paint: put it back
  async function reapplyPaint() {
    const r = state.result;
    const cells = SL.paintList(savedPaint(r));
    if (!cells.length) return;
    if (r.tiler === "ark") { log("warn", `${cells.length} painted cells are remembered for ${r.name}, but Ark-tiled routes can't be painted; switch the tiler to Learned to see them`); return; }
    if (await retile(cells, true)) log("info", `Put back ${cells.length} painted cells on ${r.name} (Edit > Clear painted cells drops them)`);
  }
  async function retile(cells, quiet) {
    const r = state.result;
    if (!r) return false;
    try {
      const res = await postJSON("/api/retile", { id: r.id, cells });
      if (state.result !== r) return false; // a new route arrived meanwhile
      const last = r.stages.length - 1;
      r.stages[last].cls = res.cls;
      r.stages[last].tiles = res.tiles;
      const d = drawStage(last, last ? state.tileCanvases[last - 1] : null, last ? r.stages[last - 1].tiles : null);
      state.tileCanvases[last] = d.tiles;
      state.classCanvases[last] = d.classes;
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
    special: "Special area", clearing: "Clearing", lake: "Lake", hidden_item: "Hidden item", cut_tree: "Cut tree", rock_smash: "Smashable rock" };
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
      kv([["side", x.side], ["to", x.to || "nothing (open edge)"], ["width", x.cells.length], ["position", sp ? sp.at : null],
        ["role", sel.i < 2 ? "joined by the main road" : "extra exit"]]);
      actions();
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
    const exits = (r.exits || []).map((x, i) => ({
      id: "x:" + i, label: `${x.side}${x.to ? " → " + x.to : ""}`, meta: `${x.cells.length} wide`,
      icon: { color: "#9cc6f0" }, sel: { type: "exit", i },
    }));
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
      row.onclick = () => {
        if (n.sel) select(n.sel);
        else if (n.run) n.run();
        else if (n.eye) n.eye.toggle();
        else if (n.children) tw.onclick(new Event("click"));
      };
      row.ondblclick = () => { if (n.sel) { select(n.sel); focusSel(); } };
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
  function syncViewButtons() {
    document.querySelectorAll("[data-layer]").forEach((b) => { const on = b.dataset.layer === state.layer; b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); });
    document.querySelectorAll("[data-ov]").forEach((b) => { const on = !!state.ov[b.dataset.ov]; b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); });
  }

  // ------------------------------------------------------------ timeline
  // One block per generator stage: what it added (cells / zones / items), a yellow bar
  // scaled to the cells it changed, and a fill that runs across the block while playing.
  const tracks = $("tlTracks");
  const isEmpty = (i) => { const f = state.stageInfo[i] || {}; return i > 0 && !f.changed && !(f.zones > 0) && !(f.events > 0); };
  const flashLeft = () => Math.max(0, 1 - (performance.now() - state.flashStart) / Math.min(FLASH_MS, prefs.stageMs * 1.2));
  function buildTimeline() {
    const r = state.result;
    tracks.innerHTML = "";
    if (!r) return;
    const maxC = Math.max(1, ...state.stageInfo.slice(1).map((s) => s.changed));
    r.stages.forEach((st, i) => {
      const info = state.stageInfo[i] || {};
      const bits = [];
      if (i === 0) bits.push("start");
      else if (info.changed) bits.push(`${info.changed} cells`);
      if (info.zones > 0) bits.push(`+${info.zones} zone${info.zones > 1 ? "s" : ""}`);
      if (info.events > 0) bits.push(`+${info.events} item${info.events > 1 ? "s" : ""}`);
      const dt = bits.join(" · ") || "no change";
      const seg = el("div", "tl-seg" + (isEmpty(i) ? " empty" : ""));
      seg.append(el("span", "fill"));
      const nm = el("span", "nm");
      nm.append(el("b", "", String(i + 1)), document.createTextNode(st.name));
      seg.append(nm, el("span", "dt", dt));
      if (i > 0 && info.changed) {
        const amt = el("span", "amt");
        amt.style.width = Math.max(4, Math.round((info.changed / maxC) * 100)) + "%";
        seg.append(amt);
      }
      seg.title = `${i + 1}. ${st.name}: ${dt}`;
      tracks.append(seg);
    });
    syncTimeline();
  }
  function syncTimeline() {
    tracks.querySelectorAll(".tl-seg").forEach((t, k) => {
      t.classList.toggle("on", k === state.stage);
      t.classList.toggle("past", k < state.stage);
      t.classList.remove("run");
    });
    const cur = tracks.children[state.stage];
    if (cur && state.playing) {
      void cur.offsetWidth; // restart the fill animation
      cur.style.setProperty("--dur", prefs.stageMs + "ms");
      cur.classList.add("run");
    }
  }
  function stageFromX(clientX) {
    const segs = [...tracks.children];
    if (!segs.length) return 0;
    const k = segs.findIndex((t) => clientX < t.getBoundingClientRect().right);
    return k < 0 ? segs.length - 1 : k;
  }
  tracks.addEventListener("pointerdown", (ev) => {
    if (!state.result || ev.button !== 0) return;
    stopPlay();
    tracks.setPointerCapture(ev.pointerId);
    setStage(stageFromX(ev.clientX), true);
    const move = (e) => { const i = stageFromX(e.clientX); if (i !== state.stage) setStage(i, true); };
    const up = () => { tracks.removeEventListener("pointermove", move); tracks.removeEventListener("pointerup", up); };
    tracks.addEventListener("pointermove", move);
    tracks.addEventListener("pointerup", up);
  });

  function setStage(i, fade) {
    const r = state.result;
    if (!r) return;
    i = Math.max(0, Math.min(r.stages.length - 1, i));
    if (fade && i !== state.stage) { state.prevStage = state.stage; state.fadeStart = state.flashStart = performance.now(); }
    else { state.prevStage = null; state.flashStart = 0; }
    state.stage = i;
    const label = `${i + 1} / ${r.stages.length} · ${r.stages[i].name}`;
    $("tbStage").textContent = label;
    $("tbStage").title = label;
    $("tlCur").textContent = `Stage ${label}`;
    syncTimeline();
    renderTree();
    draw();
  }

  function startPlay() {
    const r = state.result;
    if (!r) return;
    if (state.stage >= r.stages.length - 1) setStage(0, false);
    state.playing = true;
    syncPlay();
    syncTimeline();
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
    syncTimeline();
  }
  function togglePlay() { state.playing ? stopPlay() : startPlay(); }
  function syncPlay() {
    for (const b of [$("tbPlay"), $("tlPlay")]) {
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

  // ------------------------------------------------------------ geography / region editor (editor.html)
  let region_editor_frame = null;
  function region_editor() {
    const editor_actions = region_editor_frame && region_editor_frame.contentWindow && region_editor_frame.contentWindow.editor;
    return editor_actions || null;
  }
  function show_region_editor_in_mode(mode_name) {
    if (region_editor()) { region_editor().set_editor_mode(mode_name); return; }
    if (region_editor_frame) return;
    const seed_of_last_region = sget("pokeroutes-shell-region-seed") || 1;
    region_editor_frame = el("iframe");
    region_editor_frame.title = "Geography and region editor";
    region_editor_frame.src = `editor.html?mode=${mode_name}&seed=${seed_of_last_region}`;
    region_editor_frame.addEventListener("load", connect_region_editor_to_scene);
    $("viewMap").append(region_editor_frame);
  }
  function connect_region_editor_to_scene() {
    const editor_actions = region_editor();
    if (!editor_actions) return;
    editor_actions.set_route_opener(openInBench);
    if (Shell.layout.center !== "scene") editor_actions.set_editor_mode(Shell.layout.center);
  }
  function current_region_seed() {
    const editor_document = region_editor() && region_editor().getDoc();
    const region_seed = editor_document ? editor_document.seed : sget("pokeroutes-shell-region-seed") || 1;
    return region_seed;
  }

  function openInBench(spec) {
    const { params: p, region } = SL.specToParams(spec, params());
    replaceParams(p, "region route");
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
    b.onclick = () => { state.region = null; sdel(REGION_KEY); renderRegionLink(); log("info", "Detached the route from its region block"); };
    box.append(b);
  }

  function show_center_tab(tab) {
    $("viewScene").hidden = tab !== "scene";
    $("viewMap").hidden = tab === "scene";
    if (tab !== "scene") show_region_editor_in_mode(tab);
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
      { id: "save", label: "Save spec", run: () => saveSpec(false) },
      { id: "saveas", label: "Save spec as…", run: () => saveSpec(true) },
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
      { id: "undo", label: "Undo", run: undo, enabled: () => undoStack.canUndo() },
      { id: "redo", label: "Redo", run: redo, enabled: () => undoStack.canRedo() },
      { sep: true },
      { id: "clear-paint", label: "Clear painted cells", enabled: () => hasRoute() && Object.keys(savedPaint(state.result)).length > 0, run: clearPaint },
      { sep: true },
      { id: "copy-spec", label: "Copy spec", run: copySpec },
      { id: "paste-spec", label: "Paste spec", run: pasteSpec },
      { sep: true },
      { id: "deselect", label: "Deselect", run: () => select(null), enabled: () => !!state.sel },
      { sep: true },
      { id: "prefs", label: "Preferences…", run: openPrefs },
    ] },
    { label: "View", items: [
      { id: "v-left", label: "Hierarchy", checked: () => Shell.layout.left.open, run: () => Shell.toggleDock("left") },
      { id: "v-right", label: "Inspector", checked: () => Shell.layout.right.open, run: () => Shell.toggleDock("right") },
      { id: "v-bottom", label: "Bottom panel", checked: () => Shell.layout.bottom.open, run: () => Shell.toggleDock("bottom") },
      { sep: true },
      { id: "zin", label: "Zoom in", enabled: sceneRoute, run: () => zoomBy(1) },
      { id: "zout", label: "Zoom out", enabled: sceneRoute, run: () => zoomBy(-1) },
      { id: "zfit", label: "Fit to view", enabled: sceneRoute, run: fit },
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
      { id: "gen", label: "Generate", run: () => { Shell.setCenter("scene"); generate(); } },
      { id: "regen", label: "Regenerate with new seed", run: () => { Shell.setCenter("scene"); regenerate(); } },
      { sep: true },
      { id: "gen-region", label: "Generate region…", run: () => Shell.setCenter("geography") },
      { sep: true },
      { id: "tiler", label: "Tiler", items: [
        { id: "t-learned", label: "Learned (Emerald General + Fortree)", radio: true, checked: () => prefs.tiler === "learned", run: () => setTiler("learned") },
        { id: "t-ark", label: "Ark rule tiles", radio: true, checked: () => prefs.tiler === "ark", enabled: () => !state.tilers || state.tilers.some((t) => t.id === "ark" && t.ok), run: () => setTiler("ark") },
      ] },
      { sep: true },
      { id: "play", label: "Play / pause stages", enabled: hasRoute, run: (ev) => { if (ev && document.activeElement && document.activeElement.tagName === "BUTTON") document.activeElement.blur(); togglePlay(); } },
      { id: "next", label: "Next stage", enabled: hasRoute, run: () => step(1) },
      { id: "prev", label: "Previous stage", enabled: hasRoute, run: () => step(-1) },
      { id: "first", label: "First stage", enabled: hasRoute, run: () => stageEnd(false) },
      { id: "last", label: "Final stage", enabled: hasRoute, run: () => stageEnd(true) },
    ] },
    { label: "Window", items: [
      { id: "w-scene", label: "Route bench (Scene)", radio: true, checked: () => Shell.layout.center === "scene", run: () => Shell.setCenter("scene") },
      { id: "w-geography", label: "Geography editor", radio: true, checked: () => Shell.layout.center === "geography", run: () => Shell.setCenter("geography") },
      { id: "w-region", label: "Route design A", radio: true, checked: () => Shell.layout.center === "region", run: () => Shell.setCenter("region") },
      { id: "w-story", label: "Story", radio: true, checked: () => Shell.layout.center === "story", run: () => Shell.setCenter("story") },
      { id: "w-gates", label: "Route design B", radio: true, checked: () => Shell.layout.center === "gates", run: () => Shell.setCenter("gates") },
      { sep: true },
      { id: "w-console", label: "Console", run: () => Shell.setBottom("console") },
      { id: "w-timeline", label: "Timeline", run: () => Shell.setBottom("timeline") },
      { id: "w-improve", label: "Improve queue", run: () => { Shell.setBottom("improve"); const t = document.getElementById("impText"); if (t) t.focus(); } },
      { id: "w-agent", label: "Agent (tilesets etc.)", run: () => { Shell.setBottom("agent"); const t = document.getElementById("agText"); if (t) t.focus(); } },
      { sep: true },
      { id: "w-reset", label: "Reset layout", run: () => Shell.resetLayout() },
    ] },
    { label: "Help", items: [
      { id: "keys", label: "Keyboard shortcuts", run: () => Shell.showShortcuts() },
      { sep: true },
      { id: "about", label: "About poke-routes", run: () => { $("aboutServer").textContent = location.origin; Shell.openDialog("dlgAbout"); } },
    ] },
  ];

  const toolKey = (t, label) => ({ id: "tool-" + t, label, group: "Scene tools", run: () => setTool(t) });
  const extras = [
    toolKey("select", "Select tool"), toolKey("pan", "Pan tool"), toolKey("paint", "Paint tool"), toolKey("stamp", "Stamp tool"),
    { id: "x-zin", label: "Zoom in", group: "Scene view", enabled: sceneRoute, run: () => zoomBy(1) },
    { id: "x-zout", label: "Zoom out", group: "Scene view", enabled: sceneRoute, run: () => zoomBy(-1) },
    { id: "x-zfit", label: "Fit to view", group: "Scene view", enabled: sceneRoute, run: fit },
    { id: "x-help", label: "Keyboard shortcuts", group: "Help", run: () => Shell.showShortcuts() },
  ];

  // ------------------------------------------------------------ wiring
  function initUI() {
    document.querySelectorAll("[data-tool]").forEach((b) => { b.onclick = () => setTool(b.dataset.tool); });
    document.querySelectorAll("[data-layer]").forEach((b) => { b.onclick = () => setLayer(b.dataset.layer); });
    document.querySelectorAll("[data-ov]").forEach((b) => { b.onclick = () => toggleOv(b.dataset.ov); });
    $("tbFirst").onclick = () => stageEnd(false);
    $("tbPrev").onclick = () => step(-1);
    $("tbNext").onclick = () => step(1);
    $("tbLast").onclick = () => stageEnd(true);
    $("tbPlay").onclick = togglePlay;
    $("tlPlay").onclick = togglePlay;
    $("tbGenerate").onclick = () => { Shell.setCenter("scene"); generate(); };
    $("tbGenerate").title = `Generate (${Shell.fmt("Mod+g")})`;
    $("tbZoomIn").onclick = () => zoomBy(1);
    $("tbZoomOut").onclick = () => zoomBy(-1);
    $("tbFit").onclick = fit;
    $("tbTiler").onchange = () => setTiler($("tbTiler").value);
    $("tlSpeed").onchange = () => { prefs.stageMs = +$("tlSpeed").value; savePrefs(); if (state.playing) startPlay(); };
    $("tlLoop").onchange = () => { prefs.loop = $("tlLoop").checked; savePrefs(); };
    $("dice").onclick = () => regenerate();
    $("name").addEventListener("change", () => edited("name"));
    $("seed").addEventListener("change", () => edited("seed"));
    $("areaAdd").onclick = addSpecial;
    $("areaInput").addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); addSpecial(); } });
    $("exitAdd").onclick = () => { if (state.exits.length < 4) { state.exits.push({ side: "west", at: 0.5, width: 3 }); renderExits(); edited("exits"); } };
    $("paintClear").onclick = clearPaint;
    const pc = $("paintClass");
    for (const k of PAINT_CLASSES) pc.append(Object.assign(el("option", "", PR.CLASS_NAMES[k] || k), { value: k }));
    pc.value = "g";
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
    state.exits = saved && Array.isArray(saved.exits) && saved.exits.length >= 2 ? saved.exits.map((e) => ({ ...e })) : DEFAULT_EXITS.map((e) => ({ ...e }));
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
    generate();
  }

  window.Bench = { generate, state, params, setParams, undoStack, menus, extras };
  boot();
})();
