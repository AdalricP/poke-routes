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
  // ledge direction; state.ledgeDir picks the class and R turns it clockwise.
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
    tileCanvases: [], top_layer_canvas: null, classCanvases: [], changedMasks: [], stageInfo: [], animCells: [], flashStart: 0,
    view: { zoom: 1, ox: 0, oy: 0 },
    layer: viewSaved.layer === "classes" ? "classes" : "tiles",
    ov: Object.assign({ areas: true, events: true, grid: false, markings: true }, viewSaved.ov || {}),
    hover: null, hoverArea: null, sel: null,
    playing: false, timer: null, busy: false,
    specials: [], exits: [], tab: "dex",
    tool: "select", paint: { cells: new Map(), objects: [], busy: false }, ledgeDir: "s", brush: "g", brush_size: viewSaved.brush_size || 1, palette: null, palettes: {},
    region: sget(REGION_KEY),      // {name, region, endings} or null
    file: { handle: null, name: "" },
    saved: "", generated: "",
    expanded: new Set(["chain", "route", "g:exits", "g:lanes", "g:special", "g:layers"]),
    route_trail: [],           // names of the region routes walked through, for Back
    connection_labels: [],     // screen boxes of the exit labels drawn on the Scene: {x, y, w, h, i}
    chain: null,
  };
  const undoStack = new SL.History(150, 800);
  const push_undo_entry_of_any_map = undoStack.push.bind(undoStack);
  undoStack.push = (undo_entry) => push_undo_entry_of_any_map(undo_entry_tagged_with_open_map(undo_entry));
  function undo_entry_tagged_with_open_map(undo_entry) {
    const open_map_slot = state.chain ? state.chain.slots[state.chain.active_index] : null;
    if (!open_map_slot) return undo_entry;
    const tagged_entry = Object.assign({}, undo_entry, { chain_map_name: open_map_slot.name, key: undo_entry.key ? open_map_slot.name + "|" + undo_entry.key : undo_entry.key });
    return tagged_entry;
  }
  function saveView() { sset(VIEW_KEY, { layer: state.layer, ov: state.ov, brush_size: state.brush_size }); }

  // ------------------------------------------------------------ inspector controls
  // Built from /api/spec-schema (RouteSpec's dataclass fields), so new generator
  // parameters show up without touching this file.
  const FALLBACK_SCHEMA = { fields: [
    { name: "biome", kind: "str", default: "forest", group: "route", choices: ["forest", "meadow", "riverland"] },
    { name: "width", kind: "int", default: 48, group: "route", min: 24, max: 96, step: 2 },
    { name: "height", kind: "int", default: 80, group: "route", min: 24, max: 112, step: 2 },
    { name: "meander", kind: "float", default: 0.6, group: "shape", label: "winding", min: 0, max: 1, step: 0.05 },
    { name: "max_lanes", kind: "int", default: 3, group: "shape", label: "paths to the exit", min: 1, max: 3 },
    { name: "dead_ends", kind: "int", default: 1, group: "shape", label: "side areas", min: 0, max: 6 },
    { name: "lakes", kind: "int", default: 1, group: "shape", label: "water", min: 0, max: 4 },
    { name: "tall_grass", kind: "float", default: 0.35, group: "shape", min: 0, max: 0.9, step: 0.05 },
    { name: "ledge_chance", kind: "float", default: 0.35, group: "shape", label: "ledges", min: 0, max: 1, step: 0.05 },
    { name: "road", kind: "bool", default: true, group: "shape", label: "dirt road" },
    { name: "clearings", kind: "int", default: 3, group: "advanced", min: 0, max: 8 },
    { name: "hidden_paths", kind: "int", default: 2, group: "secrets", min: 0, max: 6 },
    { name: "hidden_items", kind: "int", default: 4, group: "secrets", min: 0, max: 12 },
  ], exit_fields: [] };
  const FIRST_RUN = { width: 64, height: 72, meander: 0.7, dead_ends: 2, clearings: 4, tall_grass: 0.3 };
  const SHAPE_CONTROL_ORDER = ["meander", "max_lanes", "dead_ends", "lakes", "tall_grass", "ledge_chance", "road"];
  const FIELD_TOOLTIPS = {
    meander: "How far the main path swings and turns", max_lanes: "Parallel paths that braid toward the exit",
    dead_ends: "Side paths that end at an item, lookout or pocket", lakes: "Ponds along the route",
    ledge_chance: "One-way ledges between paths", road: "Dirt road along the main path",
  };
  const DEFAULT_EXITS = [{ side: "south", at: 0.45, width: 2 }, { side: "north", at: 0.6, width: 2 }, { side: "east", at: 0.35, width: 3 }];
  const DEFAULT_SPECIALS = ["auto", "auto", "pond"];
  const controls = {};
  let schema = FALLBACK_SCHEMA;
  let fid = 0;
  let pendingGenerate = false;

  function labelOf(f) {
    if (f.label) return f.label.charAt(0).toUpperCase() + f.label.slice(1);
    const t = f.name.replace(/_/g, " ");
    return t.charAt(0).toUpperCase() + t.slice(1);
  }

  function makeControl(f, value) {
    const row = el("div", "prop");
    const id = "f-" + f.name + "-" + (++fid);
    const lab = el("label", "", labelOf(f));
    lab.htmlFor = id;
    lab.title = FIELD_TOOLTIPS[f.name] || f.name.replace(/_/g, " ");
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

  const GROUPS = ["route", "shape", "secrets", "encounters", "advanced"];
  function shape_control_rank(field) {
    const rank = SHAPE_CONTROL_ORDER.indexOf(field.name);
    return rank < 0 ? SHAPE_CONTROL_ORDER.length : rank;
  }
  function fields_in_inspector_order(fields) {
    const ordered_fields = fields.slice().sort((first_field, second_field) => shape_control_rank(first_field) - shape_control_rank(second_field));
    return ordered_fields;
  }
  function buildControls(saved) {
    for (const g of GROUPS) $("g-" + g).innerHTML = "";
    for (const k of Object.keys(controls)) delete controls[k];
    for (const f of fields_in_inspector_order(schema.fields)) {
      if (f.name === "seed") continue; // has its own box
      const v = saved && saved[f.name] != null ? saved[f.name] : FIRST_RUN[f.name] != null && !saved ? FIRST_RUN[f.name] : f.default;
      const c = makeControl(f, v);
      ($("g-" + f.group) || $("g-advanced")).append(c.el);
      controls[f.name] = c;
    }
    set_fold_empty($("sec-advanced"), !$("g-advanced").children.length);
    sizeWarn();
  }

  function set_fold_empty(fold, is_empty) {
    fold.dataset.hiddenByContent = String(is_empty);
    fold.hidden = is_empty || (!!state.result && is_object_focused(state.sel));
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

  function exit_summary_text(scene_exit) {
    const destination_name = scene_exit.to ? readable_map_name(scene_exit.to) : "";
    const summary_text = `${scene_exit.side} · ${scene_exit.width} wide${destination_name ? " → " + destination_name : ""}`;
    return summary_text;
  }

  function exit_list_row(scene_exit, exit_index) {
    const list_row = el("li", state.sel && state.sel.type === "exit" && state.sel.i === exit_index ? "sel" : "");
    const select_button = Object.assign(el("button", "exit-pick", exit_summary_text(scene_exit)), { type: "button" });
    select_button.onclick = () => select({ type: "exit", i: exit_index });
    const remove_button = Object.assign(el("button", "x", "×"), { type: "button", disabled: state.exits.length <= 1 });
    remove_button.setAttribute("aria-label", `Remove exit ${exit_index + 1}`);
    remove_button.onclick = () => remove_exit(exit_index);
    list_row.append(select_button, remove_button);
    return list_row;
  }

  function renderExits() {
    const ol = $("exits");
    ol.innerHTML = "";
    state.exits.forEach((scene_exit, exit_index) => ol.append(exit_list_row(scene_exit, exit_index)));
    $("exitAdd").disabled = state.exits.length >= 4;
  }

  function remove_exit(exit_index) {
    state.exits.splice(exit_index, 1);
    if (state.sel && state.sel.type === "exit") state.sel = null;
    renderExits();
    renderSelection();
    edited("exits");
  }

  function exit_side_select(scene_exit) {
    const side_select = el("select");
    ["north", "south", "east", "west"].forEach((side) => side_select.append(Object.assign(el("option", "", side), { value: side, selected: side === scene_exit.side })));
    side_select.onchange = () => { scene_exit.side = side_select.value; renderExits(); edited("exits"); };
    return side_select;
  }

  function exit_position_slider(scene_exit, exit_index) {
    const position_slider = Object.assign(el("input"), { type: "range", min: 0, max: 1, step: 0.05, value: scene_exit.at });
    position_slider.oninput = () => { scene_exit.at = +position_slider.value; edited("exits:" + exit_index, true); };
    return position_slider;
  }

  function exit_width_input(scene_exit) {
    const width_input = Object.assign(el("input"), { type: "number", min: 1, max: 6, value: scene_exit.width });
    width_input.onchange = () => {
      scene_exit.width = Math.max(1, Math.min(6, parseInt(width_input.value, 10) || 3));
      width_input.value = scene_exit.width;
      renderExits();
      edited("exits");
    };
    return width_input;
  }

  function inspector_property_row(label_text, control) {
    const property_row = el("div", "prop");
    property_row.append(el("label", "", label_text), control);
    return property_row;
  }

  function exit_destination_input(scene_exit, exit_index) {
    const destination_input = Object.assign(el("input", "exit-to"), { type: "text" });
    destination_input.setAttribute("list", "exitDestinations");
    destination_input.setAttribute("aria-label", `Exit ${exit_index + 1} goes to`);
    destination_input.value = scene_exit.to ? readable_map_name(scene_exit.to) : "";
    destination_input.title = "A town or route of the open region, or a map name";
    destination_input.onfocus = fill_exit_destination_choices;
    destination_input.onchange = () => {
      const destination_name = destination_input.value.trim();
      scene_exit.to = destination_name ? map_constant_for_place(destination_name) : "";
      scene_exit.to_set_by_hand = !!destination_name;
      renderExits();
      edited("exits");
    };
    return destination_input;
  }

  function region_places() {
    const region_document = open_region_document();
    if (!region_document) return [];
    const places = [...(region_document.cities || []).map((city) => ({ kind: "town", name: city.name, place: city })),
      ...(region_document.routes || []).map((route) => ({ kind: "route", name: route.name, place: route }))];
    return places;
  }

  function fill_exit_destination_choices() {
    let destination_list = document.getElementById("exitDestinations");
    if (!destination_list) {
      destination_list = el("datalist");
      destination_list.id = "exitDestinations";
      document.body.append(destination_list);
    }
    destination_list.innerHTML = "";
    for (const region_place of region_places()) destination_list.append(Object.assign(el("option"), { value: region_place.name }));
  }

  function map_constant_for_place(place_name) {
    if (/^MAP_[A-Z0-9_]+$/.test(place_name)) return place_name;
    const region_place = region_places().find((candidate) => candidate.name.toLowerCase() === place_name.toLowerCase());
    if (region_place && region_place.place.map) return region_place.place.map;
    const map_constant = "MAP_" + place_name.toUpperCase().replace(/[^A-Z0-9]+/g, "");
    return map_constant;
  }

  function hand_set_connection(exit_index) {
    const scene_exit = state.exits[exit_index];
    if (!scene_exit || !scene_exit.to_set_by_hand) return undefined;
    const destination_name = readable_map_name(scene_exit.to);
    const region_place = region_places().find((candidate) => map_constant_for_place(candidate.name) === scene_exit.to || candidate.name === destination_name);
    if (!region_place) return { name: destination_name, kind: "unknown", route: null, town: null };
    const connection = { name: region_place.name, kind: region_place.kind, route: region_place.kind === "route" ? region_place.place : null, town: region_place.kind === "town" ? region_place.place : null };
    return connection;
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
    remember_open_chain();
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
    if (e.kind === "items" || e.kind === "people" || e.kind === "events") return e.label;
    const k = String(e.key || e.label || "").split(":")[0];
    return k ? `change to ${k.replace(/_/g, " ")}` : "change";
  }
  async function applyEntry(e, side) {
    if (!open_map_of_undo_entry(e)) return;
    if (e.kind === "spec") {
      setParams(e[side]);
      sset(STORE_KEY, params());
      autoGenerate();
    } else if (e.kind === "paint") {
      if (!state.result || state.result.id !== e.id || !(await retile(side === "before" ? e.old : e.cells, true))) return;
      storePaint(state.result, e[side]);
      store_painted_objects(state.result, e["objects_" + side] || []);
      if (state.sel && state.sel.type === "object") select(null);
    } else if (e.kind === "items") {
      route_items.restore(e[side]);
    } else if (e.kind === "people") {
      route_people.restore(e[side]);
    } else if (e.kind === "events") {
      route_events.restore(e[side]);
    }
  }

  function open_map_of_undo_entry(undo_entry) {
    if (!state.chain || !undo_entry.chain_map_name) return true;
    const map_index = state.chain.slots.findIndex((slot) => slot.name === undo_entry.chain_map_name);
    if (map_index < 0) return false;
    activate_chain_map(map_index);
    const is_open = state.chain.active_index === map_index;
    return is_open;
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
  const route_items = window.RouteItems.create({
    state, $, el, log, sget, sset, fetchJSON, postJSON, undoStack, select: (sel) => select(sel), draw: () => draw(),
    refresh: () => { renderTree(); renderSelection(); route_items.update_count(); draw(); },
  });
  const route_people = window.RoutePeople.create({
    state, $, el, log, sget, sset, fetchJSON, postJSON, undoStack, select: (sel) => select(sel), draw: () => draw(),
    refresh: () => { renderTree(); renderSelection(); draw(); },
    has_placed_item_at: (x, y) => route_items.index_at(x, y) >= 0, event_rules: () => route_events.rules(),
  });
  const route_events = window.RouteEvents.create({
    state, $, el, log, sget, sset, postJSON, undoStack, draw: () => draw(), status: (text) => Shell.status(text),
    region_document: () => open_region_document(), current_people: () => route_people.people(),
    person_at: (x, y) => route_people.person_at(x, y),
  });
  Shell.on_panel_shown((panel_id) => {
    if (panel_id === "events") route_events.render();
    if (panel_id === "tiles") render_tiles_panel();
  });

  window.addEventListener("pokeroutes:tileset-saved", () => { state.atlases = {}; state.palettes = {}; });

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
    if (state.chain) { await generate_open_chain(opts); return; }
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
      res.generated_class_rows = res.stages[res.stages.length - 1].cls.slice();
      state.result = res;
      state.atlas = atlas;
      await load_paint_palette(res);
      state.sel = null;
      undoStack.filter((e) => e.kind !== "paint" && e.kind !== "items" && e.kind !== "people");
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
      if (opts && opts.keepPaint) await reapplyPaint();
      else { storePaint(res, {}); store_painted_objects(res, []); }
      await route_items.on_new_route(res);
      await route_people.on_new_route(res);
      route_events.on_new_route(res);
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
    if (state.chain) { regenerate_open_chain_with_new_seeds(); return; }
    $("seed").value = Math.floor(Math.random() * 100000);
    edited("seed");
    generate();
  }

  function reportToConsole(r, rt) {
    const t = r.timing || {};
    log("info", `Generated ${r.name} (seed ${r.seed}, ${r.width}×${r.height}, ${r.biome || "?"}, ${r.tiler === "ark" ? "PokeRoutes" : "learned"} tiles) in ${rt.toFixed(1)} s`,
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
  function drawStage(r, atlas, i, prev, prevTiles) {
    const W = r.width, H = r.height;
    const st = r.stages[i];
    const { meta, img } = atlas;
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
        const motion = (meta.motion || {})[m];
        const phase = breeze_metatiles.has(m) ? breeze_phase_for_tree_block(cell_x, cell_y) : idle_motion_phase(motion, cell_x, cell_y);
        anim.push({ x: cell_x * CELL, y: cell_y * CELL, mid: m, phase, speed: motion ? motion.speed : 1, shown_atlas_cell: -1 });
      }
    }
    return { tiles: c, classes: cc, mask: prevTiles ? mask : null, changed: prevTiles ? changed : st.tiles.length, anim };
  }

  function buildCanvases() {
    Object.assign(state, canvases_of_route(state.result, state.atlas));
  }
  function canvases_of_route(r, atlas) {
    const canvases = { tileCanvases: [], classCanvases: [], changedMasks: [], stageInfo: [], animCells: [] };
    let prev = null, prevTiles = null;
    r.stages.forEach((st, i) => {
      const d = drawStage(r, atlas, i, prev, prevTiles);
      canvases.tileCanvases.push(d.tiles);
      canvases.classCanvases.push(d.classes);
      canvases.changedMasks.push(d.mask);
      canvases.animCells.push(d.anim);
      const pz = i ? r.stages[i - 1].zones : 0, pe = i ? r.stages[i - 1].events : 0;
      canvases.stageInfo.push({ changed: d.changed, zones: st.zones - pz, events: st.events - pe });
      prev = d.tiles;
      prevTiles = st.tiles;
    });
    canvases.top_layer_canvas = top_layer_canvas_of_stage(r, atlas, r.stages.length - 1);
    return canvases;
  }

  function top_layer_canvas_of_stage(route_result, atlas, stage_index) {
    const top_layer_canvas = document.createElement("canvas");
    top_layer_canvas.width = route_result.width * CELL;
    top_layer_canvas.height = route_result.height * CELL;
    if (!atlas) return top_layer_canvas;
    const top_layer_context = top_layer_canvas.getContext("2d");
    const stage_tiles = route_result.stages[stage_index].tiles;
    for (let cell_index = 0; cell_index < stage_tiles.length; cell_index++) {
      const left = (cell_index % route_result.width) * CELL, top = Math.floor(cell_index / route_result.width) * CELL;
      draw_top_layer_cell(top_layer_context, atlas, atlas.meta.index[stage_tiles[cell_index]], left, top);
    }
    return top_layer_canvas;
  }

  function draw_top_layer_cell(context, atlas, atlas_cell, left, top) {
    const { meta, img } = atlas;
    const top_layer_cell = (meta.top_layer || {})[atlas_cell];
    if (top_layer_cell == null) return;
    context.drawImage(img, (top_layer_cell % meta.columns) * CELL, Math.floor(top_layer_cell / meta.columns) * CELL, CELL, CELL, left, top, CELL, CELL);
  }

  function is_final_stage_shown() {
    const is_final = !!state.result && state.stage === state.result.stages.length - 1;
    return is_final;
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
    if (route_items.has_glitter() || route_items.has_moving_sprites()) draw();
  }
  function draw_tile_animation_frame() {
    animate_tiles_of_map(state);
    for (const slot of inactive_chain_slots_with_maps()) animate_tiles_of_map(slot);
    draw();
  }
  function animate_tiles_of_map(map_holder) {
    const cells = map_holder.animCells[map_holder.stage];
    if (!map_holder.atlas || !cells || !cells.length) return;
    const { meta, img } = map_holder.atlas;
    const g = map_holder.tileCanvases[map_holder.stage].getContext("2d");
    const is_final_stage_of_map = !!map_holder.result && map_holder.stage === map_holder.result.stages.length - 1;
    const top_layer_context = is_final_stage_of_map && map_holder.top_layer_canvas ? map_holder.top_layer_canvas.getContext("2d") : null;
    for (const c of cells) {
      const seq = [meta.index[c.mid], ...meta.anim[c.mid]];
      const a = seq[(Math.floor(animFrame * c.speed) + c.phase) % seq.length];
      if (a === c.shown_atlas_cell) continue;
      c.shown_atlas_cell = a;
      g.clearRect(c.x, c.y, CELL, CELL);
      g.drawImage(img, (a % meta.columns) * CELL, Math.floor(a / meta.columns) * CELL, CELL, CELL, c.x, c.y, CELL, CELL);
      if (!top_layer_context) continue;
      top_layer_context.clearRect(c.x, c.y, CELL, CELL);
      draw_top_layer_cell(top_layer_context, map_holder.atlas, a, c.x, c.y);
    }
  }
  const idle_motion_cycle_in_ticks = 16;
  function idle_motion_phase(motion, cell_x, cell_y) {
    if (!motion || !motion.origin) return 0;
    const prop_x = cell_x - motion.origin[0], prop_y = cell_y - motion.origin[1];
    const prop_hash = ((prop_x * 73856093) ^ (prop_y * 19349663)) >>> 0;
    const idle_phase_in_ticks = prop_hash % idle_motion_cycle_in_ticks;
    return idle_phase_in_ticks;
  }
  async function reload_atlas_at_version(atlas_version) {
    const route_result = state.result;
    if (!route_result) return;
    state.atlas = await atlas_of_route_at_version(route_result, atlas_version);
    buildCanvases();
    for (const slot of inactive_chain_slots_with_maps()) await reload_atlas_of_slot(slot, atlas_version);
    draw();
  }
  async function reload_atlas_of_slot(slot, atlas_version) {
    slot.atlas = await atlas_of_route_at_version(slot.result, atlas_version);
    Object.assign(slot, canvases_of_route(slot.result, slot.atlas));
  }
  function atlas_of_route_at_version(route_result, atlas_version) {
    const atlas_address = new URL(route_result.atlas || "/api/atlas", location.origin);
    atlas_address.searchParams.set("v", atlas_version);
    route_result.atlas = atlas_address.pathname + atlas_address.search;
    return atlasFor(route_result.atlas);
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
    draw();
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
    if (state.chain) { fit_chain_rectangle(0, 0, state.chain.layout.width_in_metatiles, state.chain.layout.height_in_metatiles); return; }
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

  // pointer: select / pan / paint
  let drag = null;
  let wheelAcc = 0;
  let spaceDown = false;
  const pt = (ev) => { const b = wrap.getBoundingClientRect(); return { sx: ev.clientX - b.left, sy: ev.clientY - b.top }; };

  wrap.addEventListener("pointerdown", (ev) => {
    if (ev.target.closest(".scene-back")) return;
    wrap.focus({ preventScroll: true });
    const { sx, sy } = pt(ev);
    const panBtn = ev.button === 1 || ev.button === 2 || spaceDown || state.tool === "pan";
    wrap.setPointerCapture(ev.pointerId);
    if (!panBtn && state.tool === "paint" && ev.button === 0) {
      activate_chain_map_under(sx, sy);
      if (!canPaint(true) || state.paint.busy) { drag = null; return; }
      drag = { mode: "paint" };
      if (state.stage !== state.result.stages.length - 1) { stopPlay(); setStage(state.result.stages.length - 1, false); }
      state.paint.cells = new Map();
      state.paint.objects = [];
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
    if (d.select && !d.moved) { const { sx, sy } = pt(ev); activate_chain_map_under(sx, sy); click_scene_cell(sx, sy); }
  });
  function click_scene_cell(sx, sy) {
    if (route_events.pick_cell(cellOf(sx, sy))) return;
    if (state.tool === "item") { route_items.click(cellOf(sx, sy)); return; }
    if (state.tool === "people") { route_people.click(cellOf(sx, sy)); return; }
    pickAt(sx, sy);
  }
  wrap.addEventListener("pointercancel", () => { drag = null; wrap.classList.remove("dragging"); state.paint.cells = new Map(); state.paint.objects = []; draw(); });
  wrap.addEventListener("contextmenu", (ev) => ev.preventDefault());
  wrap.addEventListener("dblclick", () => { if (state.sel) focusSel(); });
  wrap.addEventListener("pointerleave", () => { state.hover = null; state.hoverArea = null; if (state.chain) state.chain.hover = null; hideTip(); $("stCell").textContent = ""; draw(); });
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
    if (hover_on_another_chain_map(sx, sy)) return;
    const r = state.result;
    if (!r) return;
    const c = cellOf(sx, sy);
    state.hover = c;
    const zones = PR.zonesAt(r, state.stage);
    const area = c && state.ov.areas && state.tool === "select" ? PR.specialAt(zones, c.x, c.y) : null;
    const changed = area !== state.hoverArea;
    state.hoverArea = area;
    $("stCell").textContent = c ? cell_status_text(r, state.stage, c) : "";
    wrap.classList.toggle("over-connection", state.tool === "select" && !!connection_label_at(sx, sy));
    if (area) showTip(area, sx, sy); else hideTip();
    if (changed || state.tool !== "select") draw();
  }

  function cell_status_text(route_result, stage_index, cell) {
    const st = route_result.stages[stage_index];
    const k = st.cls[cell.y][cell.x];
    const m = st.tiles[cell.y * route_result.width + cell.x];
    const map_label = state.chain ? `map ${chain_map_index_named(route_result.name) + 1}  ` : "";
    const status_text = `${map_label}x ${cell.x}  y ${cell.y}  ${PR.CLASS_NAMES[k] || k}  0x${m.toString(16).toUpperCase().padStart(3, "0")}`;
    return status_text;
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
    const placed_index = state.ov.events ? route_items.index_at(c.x, c.y) : -1;
    if (placed_index >= 0) hit = { type: "placed", i: placed_index };
    if (!hit && state.ov.events) hit = route_people.pick_at(c);
    const object_index = SL.object_instance_index_at(state.palette, painted_objects(r), c.x, c.y);
    if (!hit && object_index >= 0) hit = { type: "object", i: object_index };
    if (!hit && state.ov.events) {
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
  const leaf_colours_by_tree_class = {
    f: ["#5f9e45", "#7fb85a", "#467f34", "#9ccc6e"],
    F: ["#e9a63a", "#d8692c", "#c4822b", "#f0c05a"],
    R: ["#b9381f", "#9c2f1c", "#d0503a", "#e06a4a"],
  };
  function leaf_colours_for_tree_class(tree_class) {
    const atlas_leaf_colours = state.atlas && state.atlas.meta.leaf_colours_by_tree_class;
    const leaf_colours = (atlas_leaf_colours && atlas_leaf_colours[tree_class]) || leaf_colours_by_tree_class[tree_class];
    return leaf_colours;
  }
  function leaf_colours_from_route_trees() {
    const route_result = state.result;
    if (!route_result) return null;
    const final_classes = route_result.stages[route_result.stages.length - 1].cls;
    const tree_cell_counts = { f: 0, F: 0, R: 0 };
    for (const class_row of final_classes) for (const class_code of class_row) if (class_code in tree_cell_counts) tree_cell_counts[class_code]++;
    const weighted_colours = Object.entries(tree_cell_counts).flatMap(([tree_class, cell_count]) =>
      Array.from({ length: Math.ceil(cell_count / 40) }, () => leaf_colours_for_tree_class(tree_class)).flat());
    return weighted_colours.length ? weighted_colours : null;
  }
  function random_colour_of(preset) {
    const colour_choices = (preset.shape === "leaf" && leaf_colours_from_route_trees()) || preset.colours;
    const colour = colour_choices[Math.floor(Math.random() * colour_choices.length)];
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
    const { zoom } = state.view;
    const shown_area = shown_area_in_metatiles();
    const ox = state.view.ox + shown_area.left * CELL * zoom, oy = state.view.oy + shown_area.top * CELL * zoom;
    const left = Math.max(0, ox), top = Math.max(0, oy);
    const right = Math.min(view_width, ox + shown_area.width * CELL * zoom), bottom = Math.min(view_height, oy + shown_area.height * CELL * zoom);
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
  function draw_particle(target_context, particle, preset, rectangle, pixel_scale) {
    const screen_x = Math.round(rectangle.left + particle.x_fraction * rectangle.width);
    const screen_y = Math.round(rectangle.top + particle.y_fraction * rectangle.height);
    const square_size = particle.size * pixel_scale;
    target_context.fillStyle = particle.colour;
    switch (preset.shape) {
      case "streak":
        draw_rain_streak(target_context, screen_x, screen_y, particle, pixel_scale);
        break;
      case "leaf": {
        const tumble_width = Math.abs(Math.sin(particle.sway_phase)) > 0.35 ? square_size : pixel_scale;
        target_context.fillRect(screen_x, screen_y, tumble_width, square_size);
        break;
      }
      default:
        target_context.fillRect(screen_x, screen_y, square_size, square_size);
    }
  }
  function draw_rain_streak(target_context, screen_x, screen_y, particle, pixel_scale) {
    const slant_per_pixel_of_fall = particle.drift_speed / particle.fall_speed;
    for (let streak_step = 0; streak_step < rain_streak_length_in_map_pixels; streak_step++) {
      const step_x = Math.round(screen_x - streak_step * slant_per_pixel_of_fall * pixel_scale);
      target_context.fillRect(step_x, screen_y - streak_step * pixel_scale, pixel_scale, pixel_scale);
    }
  }
  // the play-test window keeps its own particles and draws them over its own canvas
  function draw_route_effect_particles_into(target_context, particle_set, rectangle, pixel_scale, elapsed_in_seconds) {
    const effect = current_route_effect();
    const preset = route_effect_presets[effect];
    if (!preset) return;
    if (particle_set.effect !== effect) {
      particle_set.effect = effect;
      particle_set.particles = particles_for_effect(effect);
    }
    for (const particle of particle_set.particles) {
      advance_particle(particle, preset, elapsed_in_seconds, rectangle, pixel_scale);
      draw_particle(target_context, particle, preset, rectangle, pixel_scale);
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
    const is_route_on_screen = !!state.result && state.tileCanvases.length > 0 && Shell.is_panel_visible("scene");
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
      draw_particle(particle_context, particle, preset, rectangle, pixel_scale);
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
    state.connection_labels = [];
    const r = state.result;
    if (!state.chain && (!r || !state.tileCanvases.length)) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    let t = 1;
    if (state.prevStage != null && prefs.fade) {
      t = Math.min(1, (performance.now() - state.fadeStart) / FADE_MS);
      if (t >= 1) state.prevStage = null;
    } else state.prevStage = null;
    if (state.chain) paint_open_chain(ctx, { fade: t, prev: state.prevStage, interactive: true });
    else paintScene(ctx, state.view, { fade: t, prev: state.prevStage, interactive: true });
    if (t < 1 || flashLeft() > 0) draw();
  }

  // Draws the map + overlays into any 2D context (the scene view, and File > Export PNG).
  function paintScene(g, view, opt) {
    if (opt.interactive) paint_map_shadow(g, view, state.result);
    paint_map_layer(g, view, opt);
    paint_map_overlays(g, view, opt);
  }

  function paint_map_shadow(g, view, r) {
    g.fillStyle = "rgba(0,0,0,0.35)";
    g.fillRect(view.ox + 2, view.oy + 2, r.width * CELL * view.zoom, r.height * CELL * view.zoom);
  }

  function paint_map_layer(g, view, opt) {
    const r = state.result;
    const { zoom, ox, oy } = view;
    const W = r.width * CELL * zoom, H = r.height * CELL * zoom;
    const layer = state.layer === "classes" ? state.classCanvases : state.tileCanvases;
    g.imageSmoothingEnabled = false;
    if (opt.prev != null && opt.fade < 1) g.drawImage(layer[opt.prev], ox, oy, W, H);
    g.globalAlpha = opt.fade == null ? 1 : opt.fade;
    g.drawImage(layer[state.stage], ox, oy, W, H);
    g.globalAlpha = 1;
    const mask = state.changedMasks[state.stage], fl = flashLeft();
    if (opt.interactive && mask && fl > 0) { g.globalAlpha = 0.65 * fl; g.drawImage(mask, ox, oy, W, H); g.globalAlpha = 1; }
    const s = CELL * zoom;
    if (state.ov.grid && s >= 8) drawGrid(g, r, ox, oy, s);
  }

  function paint_map_overlays(g, view, opt) {
    const r = state.result;
    const { zoom, ox, oy } = view;
    const s = CELL * zoom;
    const zones = PR.zonesAt(r, state.stage);
    paint_sprites_and_top_layer_by_row(g, r, ox, oy, s, opt);
    if (state.ov.markings && state.ov.events) route_people.draw_selection_outline(g, ox, oy, s);
    if (state.ov.markings) route_events.draw(g, ox, oy, s);
    if (state.ov.markings && state.ov.areas) drawAreas(g, zones, ox, oy, s);
    if (!opt.interactive) return;
    if (state.ov.markings) drawSelection(g, ox, oy, s);
    if (state.ov.markings) draw_connection_labels(g, ox, oy, s);
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

  function draw_top_layer_row(g, row, ox, oy, s, W, opt) {
    const canvas = state.top_layer_canvas;
    g.globalAlpha = opt.fade == null ? 1 : opt.fade;
    g.drawImage(canvas, 0, row * CELL, canvas.width, CELL, ox, oy + row * s, W, s);
    g.globalAlpha = 1;
  }

  // Sorts every sprite/object (people, item balls, boulders, rocks, signs...) by the row
  // its feet stand on, lower rows closer to screen, and interleaves the tiles' top layer
  // (tree crowns etc.) one row at a time: an object standing where a crown reaches draws
  // behind that row's crown, an object below the tree draws in front of it.
  function paint_sprites_and_top_layer_by_row(g, r, ox, oy, s, opt) {
    const show_top_layer = state.layer !== "classes" && is_final_stage_shown() && state.top_layer_canvas;
    const rows_of_sprites = new Map();
    const add_sprite = (row, draw_fn) => {
      const clamped_row = Math.max(0, Math.min(r.height - 1, Math.round(row)));
      if (!rows_of_sprites.has(clamped_row)) rows_of_sprites.set(clamped_row, []);
      rows_of_sprites.get(clamped_row).push(draw_fn);
    };
    if (state.ov.markings && state.ov.events) {
      const events = PR.eventsAt(r, state.stage).filter((e) => !route_people.hides_generated_event(e)).concat(route_items.visible_items());
      for (const e of events) add_sprite(e.y, () => draw_one_event(g, e, ox, oy, s));
      for (const shown_entry of route_people.shown()) add_sprite(shown_entry.entry.y, () => route_people.draw_entry(g, shown_entry.entry, ox, oy, s));
    }
    const W = r.width * s;
    for (let row = 0; row < r.height; row++) {
      for (const draw_fn of rows_of_sprites.get(row) || []) draw_fn();
      if (show_top_layer) draw_top_layer_row(g, row, ox, oy, s, W, opt);
    }
  }

  function hoverCells() {
    const h = state.hover, r = state.result;
    if (state.tool === "paint") return brush_cells_at(h.x, h.y).map(([x, y]) => [x, y]);
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

  function draw_one_event(g, e, ox, oy, s) {
    if (route_items.draw_event_sprite(g, e, ox, oy, s, animFrame)) return;
    const rad = Math.max(3.5, Math.min(7, s * 0.3));
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
      g.closePath(); g.fillStyle = e.kind === "story_blocker" ? "#ff50c8" : "#b9b2a3"; g.fill(); g.stroke();
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
    } else if (sel.type === "placed") {
      const item = route_items.selected_item();
      if (item) g.strokeRect(ox + item.x * s - 1.5, oy + item.y * s - 1.5, s + 3, s + 3);
    } else if (sel.type === "object") {
      const object_instance = painted_objects(r)[sel.i];
      if (object_instance) {
        const [x0, y0, x1, y1] = object_instance_box(object_instance);
        g.strokeRect(ox + x0 * s - 1.5, oy + y0 * s - 1.5, (x1 - x0) * s + 3, (y1 - y0) * s + 3);
      }
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

  // ------------------------------------------------------------ paint (POST /api/retile)
  function canPaint(loud) {
    const r = state.result;
    if (!r) { if (loud) log("warn", "Generate a route before painting."); return false; }
    return true;
  }
  function paintAt(sx, sy) {
    const c = cellOf(sx, sy);
    if (!c) { state.paint.last = null; return; }
    const stroke_points = state.paint.last ? SL.lineCells(state.paint.last, c).slice(1) : [c];
    for (const stroke_point of stroke_points) add_brush_to_stroke(stroke_point.x, stroke_point.y);
    state.paint.last = c;
    draw();
  }

  const TREE_CLASS_CODES = "fFR";
  const PLAIN_GROUND_CODE = "g";

  function stroke_cells_with_freed_trees(stroke_cells, final_stage) {
    const freed_tree_cells = SL.whole_trees_under_stroke(stroke_cells, final_stage.cls, TREE_CLASS_CODES, PLAIN_GROUND_CODE);
    const all_cells = stroke_cells.concat(freed_tree_cells);
    return all_cells;
  }

  function value_shown_at(route_result, remembered_cells, cell_x, cell_y) {
    const remembered_value = remembered_cells[`${cell_x},${cell_y}`];
    if (remembered_value && remembered_value.includes("#")) return remembered_value;
    const final_stage = route_result.stages[route_result.stages.length - 1];
    const class_codes = SL.palette_class_codes(state.palette);
    const shown_class = final_stage.cls[cell_y][cell_x];
    const plain_class = class_codes.includes(shown_class) ? shown_class : PLAIN_GROUND_CODE;
    return plain_class;
  }

  function paintable_value(value) {
    if (value.includes("#")) return value;
    const class_codes = SL.palette_class_codes(state.palette);
    const plain_value = class_codes.includes(value) ? value : PLAIN_GROUND_CODE;
    return plain_value;
  }

  function changed_cells_of_stroke(route_result, cells_to_send, remembered_cells) {
    const final_stage = route_result.stages[route_result.stages.length - 1];
    const shown_exact = (cell_x, cell_y) => `${final_stage.cls[cell_y][cell_x]}#${final_stage.tiles[cell_y * route_result.width + cell_x]}`;
    const covers_an_exact_tile = (cell_x, cell_y) => String(remembered_cells[`${cell_x},${cell_y}`] || "").includes("#");
    const is_already_there = ([cell_x, cell_y, value]) => (value.includes("#") ? shown_exact(cell_x, cell_y) === value :
      final_stage.cls[cell_y][cell_x] === value && !covers_an_exact_tile(cell_x, cell_y));
    const changed_cells = cells_to_send.filter((cell) => !is_already_there(cell));
    return changed_cells;
  }

  async function commitPaint() {
    const r = state.result;
    const fin = r.stages[r.stages.length - 1];
    const stroke = [...state.paint.cells].map(([k, value]) => [...k.split(",").map(Number), value]);
    const class_stroke = stroke.filter((cell) => !cell[2].includes("#"));
    const remembered_cells = savedPaint(r);
    const is_remembered_exact = (x, y) => String(remembered_cells[`${x},${y}`] || "").includes("#");
    const tidy = SL.tidyPaint(fin.cls, class_stroke).filter(([x, y]) => !is_remembered_exact(x, y));
    const painted_cells = stroke_cells_with_freed_trees(stroke.concat(tidy), fin);
    const outcome = SL.apply_paint_stroke({
      remembered_cells, remembered_objects: painted_objects(r), cells: Object.fromEntries(painted_cells.map(([x, y, value]) => [`${x},${y}`, value])),
      objects: state.paint.objects, palette: state.palette, generated_class_rows: r.generated_class_rows,
    });
    const cells_to_send = changed_cells_of_stroke(r, painted_cells.concat(outcome.restore_cells).map(([x, y, value]) => [x, y, paintable_value(value)]), remembered_cells);
    state.paint.objects = [];
    if (!cells_to_send.length) { state.paint.cells = new Map(); draw(); return; }
    const old = cells_to_send.map(([x, y]) => [x, y, value_shown_at(r, remembered_cells, x, y)]);
    const objects_before = painted_objects(r);
    state.paint.busy = true;
    try {
      if (!(await retile(cells_to_send))) return;
      storePaint(r, outcome.cells);
      store_painted_objects(r, outcome.objects);
      undoStack.push({ kind: "paint", id: r.id, cells: cells_to_send, old, before: remembered_cells, after: outcome.cells, objects_before, objects_after: outcome.objects });
      if (state.sel && state.sel.type === "object") select(null);
    } finally {
      state.paint.busy = false;
    }
  }

  async function remove_painted_object(object_index) {
    const r = state.result;
    const object_instance = painted_objects(r)[object_index];
    if (!object_instance) return;
    const remembered_cells = savedPaint(r);
    const outcome = SL.apply_paint_stroke({
      remembered_cells, remembered_objects: [object_instance], lift: [object_instance], cells: {}, objects: [], palette: state.palette, generated_class_rows: r.generated_class_rows,
    });
    const remaining_objects = painted_objects(r).filter((_instance, index) => index !== object_index);
    const cells_to_send = outcome.restore_cells.map(([x, y, value]) => [x, y, paintable_value(value)]);
    const old = cells_to_send.map(([x, y]) => [x, y, value_shown_at(r, remembered_cells, x, y)]);
    if (!(await retile(cells_to_send))) return;
    storePaint(r, outcome.cells);
    store_painted_objects(r, remaining_objects);
    undoStack.push({ kind: "paint", id: r.id, cells: cells_to_send, old, before: remembered_cells, after: outcome.cells, objects_before: painted_objects_before_removal(object_instance, remaining_objects, object_index), objects_after: remaining_objects });
    select(null);
  }

  function painted_objects_before_removal(object_instance, remaining_objects, object_index) {
    const objects_before = remaining_objects.slice();
    objects_before.splice(object_index, 0, object_instance);
    return objects_before;
  }

  // paint memory per route name + size (see SL.paintKey); whole objects are remembered beside it
  function savedPaint(r) { const all = sget(SL.PAINT_KEY) || {}; return (r && all[SL.paintKey(r)]) || {}; }
  function storePaint(r, map) {
    const all = sget(SL.PAINT_KEY) || {};
    if (map && Object.keys(map).length) all[SL.paintKey(r)] = map; else delete all[SL.paintKey(r)];
    sset(SL.PAINT_KEY, all);
  }
  function painted_objects(route_result) {
    const all_painted_objects = sget(SL.PAINT_OBJECTS_KEY) || {};
    const route_objects = (route_result && all_painted_objects[SL.paintKey(route_result)]) || [];
    return route_objects;
  }
  function store_painted_objects(route_result, object_instances) {
    const all_painted_objects = sget(SL.PAINT_OBJECTS_KEY) || {};
    if (object_instances && object_instances.length) all_painted_objects[SL.paintKey(route_result)] = object_instances;
    else delete all_painted_objects[SL.paintKey(route_result)];
    sset(SL.PAINT_OBJECTS_KEY, all_painted_objects);
    renderTree();
  }
  // a new route with remembered paint: put it back
  function reapplyPaint() {
    return reapply_paint_on_map(state);
  }
  async function reapply_paint_on_map(map_holder) {
    const r = map_holder.result;
    const cells = SL.paintList(savedPaint(r));
    if (!cells.length) return;
    if (await retile_map(map_holder, cells, true)) log("info", `Put back ${cells.length} painted cells on ${r.name}`);
  }
  function retile(cells, quiet) {
    return retile_map(state, cells, quiet);
  }
  async function retile_map(map_holder, cells, quiet) {
    const r = map_holder.result;
    if (!r) return false;
    try {
      const res = await postJSON("/api/retile", { id: r.id, cells: SL.expand_paint_cells(cells) });
      if (map_holder.result !== r) return false; // a new route arrived meanwhile
      const last = r.stages.length - 1;
      r.stages[last].cls = res.cls;
      r.stages[last].tiles = res.tiles;
      const d = drawStage(r, map_holder.atlas, last, last ? map_holder.tileCanvases[last - 1] : null, last ? r.stages[last - 1].tiles : null);
      map_holder.tileCanvases[last] = d.tiles;
      map_holder.classCanvases[last] = d.classes;
      map_holder.animCells[last] = d.anim;
      map_holder.top_layer_canvas = top_layer_canvas_of_stage(r, map_holder.atlas, last);
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
    $("itemOpts").hidden = t !== "item";
    $("peopleOpts").hidden = t !== "people";
    for (const tool_class of [...wrap.classList].filter((class_name) => class_name.startsWith("tool-"))) wrap.classList.remove(tool_class);
    wrap.classList.add("tool-" + t);
    if (t === "paint") { canPaint(true); Shell.show_panel("tiles"); }
    hideTip();
    draw();
  }

  // ------------------------------------------------------------ selection
  function select(sel) {
    state.sel = sel;
    renderTree();
    renderExits();
    renderSelection();
    draw();
  }

  function object_instance_box(object_instance) {
    const footprint_cells = SL.instance_footprint_keys(state.palette, object_instance).map((cell_key) => cell_key.split(",").map(Number));
    const box = [Math.min(...footprint_cells.map((cell) => cell[0])), Math.min(...footprint_cells.map((cell) => cell[1])),
      Math.max(...footprint_cells.map((cell) => cell[0])) + 1, Math.max(...footprint_cells.map((cell) => cell[1])) + 1];
    return box;
  }

  function selBBox(sel) {
    const r = state.result;
    if (!sel || !r) return null;
    if (sel.type === "zone") { const z = r.zones[sel.i]; return z && (z.bbox || bboxOf(z.cells)); }
    if (sel.type === "event") { const e = r.events[sel.i]; return e && [e.x - 2, e.y - 2, e.x + 3, e.y + 3]; }
    if (sel.type === "exit") { const x = r.exits[sel.i]; return x && x.cells.length ? bboxOf(x.cells) : null; }
    if (sel.type === "placed") return route_items.selected_box();
    if (route_people.is_selection(sel)) return route_people.selected_box();
    if (sel.type === "object") { const object_instance = painted_objects(r)[sel.i]; return object_instance ? object_instance_box(object_instance) : null; }
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
    if (state.chain && state.sel && state.sel.type === "route") { frame_chain_map(state.chain.active_index); return; }
    if (!state.sel || state.sel.type === "route") { fit(); return; }
    if (!presentAt(state.sel)) { stopPlay(); setStage(r.stages.length - 1, false); }
    const bb = selBBox(state.sel);
    if (bb) centerOn(bb);
  }

  const KIND_NAMES = { main: "Main lane", branch: "Branch", loop: "Loop", dead_end: "Dead end", hidden: "Hidden trail", trail: "Area trail",
    special: "Special area", clearing: "Clearing", lake: "Lake", hidden_item: "Hidden item", cut_tree: "Cut tree", rock_smash: "Smashable rock",
    sealed_gate: "Sealed stone", gate_switch: "Hidden lever", item_ball: "Poké Ball", berry_tree: "Berry tree", npc: "Person",
    strength_boulder: "Strength boulder", story_blocker: "Someone in the way" };
  const kname = (k) => KIND_NAMES[k] || String(k).replace(/_/g, " ").replace(/^./, (first_letter) => first_letter.toUpperCase());
  const EVENT_FIELD_LABELS = { item: "item", flag: "flag", text: "text", script: "script", role: "role", type: "kind of sign", why: "placed for", trainer: "trainer" };

  function is_object_focused(sel) {
    const focuses_one_object = !!sel && sel.type !== "route";
    return focuses_one_object;
  }

  function show_inspector_folds_for(sel) {
    const focuses_one_object = is_object_focused(sel) && !!state.result;
    for (const fold of document.querySelectorAll("#form > details.fold")) {
      if (fold.id === "foldSel") { fold.hidden = !focuses_one_object; continue; }
      fold.hidden = focuses_one_object || fold.dataset.hiddenByContent === "true";
    }
  }

  function selection_title_helpers(box) {
    const title = (txt, color, cls) => {
      const summary = $("selSummary");
      const ic = el("span", "ic" + (cls ? " " + cls : ""));
      ic.style.background = color || "#888";
      summary.innerHTML = "";
      summary.append(ic, el("span", "", txt));
    };
    const kv = (rows) => {
      const dl = el("dl", "kv");
      for (const [k, v] of rows) { if (v == null || v === "") continue; dl.append(el("dt", "", k), el("dd", "", String(v))); }
      box.append(dl);
    };
    const actions = () => {
      const a = el("div", "sel-actions");
      const f = Object.assign(el("button", "btn", "Frame"), { type: "button", title: "F" });
      f.onclick = focusSel;
      a.append(f);
      box.append(a);
      return a;
    };
    return { title, kv, actions };
  }

  function renderSelection() {
    const box = $("selBody");
    box.innerHTML = "";
    const sel = state.sel, r = state.result;
    show_inspector_folds_for(sel);
    if (!is_object_focused(sel) || !r) return;
    const helpers = selection_title_helpers(box);
    switch (sel.type) {
      case "zone": render_zone_inspector(box, helpers, r.zones[sel.i]); return;
      case "event": render_event_inspector(helpers, r.events[sel.i]); return;
      case "placed": route_items.render_selection(box, helpers); return;
      case "object": render_object_inspector(box, helpers, painted_objects(r)[sel.i]); return;
      case "exit": render_exit_inspector(box, helpers, sel.i); return;
      default:
        if (route_people.is_selection(sel)) route_people.render_selection(box, helpers);
    }
  }

  function lane_number_of(zone) {
    const lane_number_match = /lane (\d+)/.exec(zone.name || "");
    return lane_number_match ? lane_number_match[1] : null;
  }

  function purpose_zone_of_lane(route_result, lane_zone) {
    const lane_number = lane_number_of(lane_zone);
    if (!lane_number) return null;
    const ends_with_lane_number = (zone) => zone.kind === "clearing" && new RegExp(` ${lane_number}$`).test(zone.name || "");
    const purpose_zone = route_result.zones.find(ends_with_lane_number) || null;
    return purpose_zone;
  }

  function is_event_on_lane(event, lane_cell_keys, purpose_zone) {
    const near_lane = [[0, 0], [1, 0], [-1, 0], [0, 1], [0, -1]].some(([offset_x, offset_y]) => lane_cell_keys.has(`${event.x + offset_x},${event.y + offset_y}`));
    if (near_lane || !purpose_zone || !purpose_zone.bbox) return near_lane;
    const [x0, y0, x1, y1] = purpose_zone.bbox;
    const inside_purpose_area = event.x >= x0 && event.x < x1 && event.y >= y0 && event.y < y1;
    return inside_purpose_area;
  }

  function lane_contents_list(route_result, lane_zone, purpose_zone) {
    const lane_cell_keys = new Set(lane_zone.cells.map(([y, x]) => `${x},${y}`));
    const contents_list = el("ul", "sel-contents");
    route_result.events.forEach((event, event_index) => {
      if (!is_event_on_lane(event, lane_cell_keys, purpose_zone)) return;
      const content_button = Object.assign(el("button", "", `${kname(event.kind)}${event.item ? " · " + event.item : ""}`), { type: "button" });
      content_button.onclick = () => select({ type: "event", i: event_index });
      const content_row = el("li");
      content_row.append(content_button);
      contents_list.append(content_row);
    });
    return contents_list;
  }

  function render_zone_inspector(box, helpers, zone) {
    if (!zone) return;
    const r = state.result;
    const is_lane = PR.PATH_KINDS.includes(zone.kind);
    helpers.title(zone.name || kname(zone.kind), is_lane ? PR.PATH_COLORS[zone.kind] : zone.kind === "special" ? "#f0a94a" : zone.kind === "lake" ? PR.CLASS_COLORS.w : PR.CLASS_COLORS.g);
    if (is_lane) { render_lane_details(box, helpers, r, zone); return; }
    const bb = zone.bbox || bboxOf(zone.cells);
    helpers.kv([["kind", kname(zone.kind)], ["size", `${bb[2] - bb[0]} × ${bb[3] - bb[1]}`], ["at", `x ${bb[0]}, y ${bb[1]}`]]);
    if (zone.area) render_special_area_details(box, helpers, r, zone.area);
    helpers.actions();
  }

  function render_lane_details(box, helpers, route_result, lane_zone) {
    const purpose_zone = purpose_zone_of_lane(route_result, lane_zone);
    helpers.kv([["kind", kname(lane_zone.kind)], ["purpose", purpose_zone ? purpose_zone.name.replace(/ \d+$/, "") : null], ["length", `${lane_zone.cells.length} cells`],
      ["ledge", lane_zone.ledge ? `x ${lane_zone.ledge[1]}–${lane_zone.ledge[2]}, y ${lane_zone.ledge[0]}` : null]]);
    const contents_list = lane_contents_list(route_result, lane_zone, purpose_zone);
    if (contents_list.children.length) { box.append(heading("Along it")); box.append(contents_list); }
    helpers.actions();
  }

  function render_special_area_details(box, helpers, route_result, area) {
    helpers.kv([["species", area.species || "none"], ["archetype", area.archetype], ["shiny", area.shiny_boost > 1 ? `x${area.shiny_boost}` : null]]);
    const table = (route_result.encounters && route_result.encounters.areas || {})[area.id];
    if (!table || !table.length) return;
    box.append(heading("Encounters here"));
    box.append(encTable(PR.encounterRows(table).map((x) => [nameCell(x.name, x.shiny_boost > 1 ? `shiny x${x.shiny_boost}` : x.rarity), `${x.levels[0]}-${x.levels[1]}`, pctCell(x.pct)]),
      ["species", "lv", "share"]));
  }

  function render_event_inspector(helpers, event) {
    if (!event) return;
    helpers.title(kname(event.kind), event.kind === "hidden_item" ? "#e0453a" : event.kind === "cut_tree" ? "#6fcf6a" : "#b9b2a3");
    const rows = [["at", `x ${event.x}, y ${event.y}`]];
    for (const [field_name, field_value] of Object.entries(event)) {
      if (["kind", "x", "y"].includes(field_name) || field_value === "" || field_value == null) continue;
      rows.push([EVENT_FIELD_LABELS[field_name] || field_name.replace(/_/g, " "), field_value]);
    }
    helpers.kv(rows);
    helpers.actions();
  }

  function render_object_inspector(box, helpers, object_instance) {
    if (!object_instance) return;
    const object_entry = SL.palette_object_entry(state.palette, object_instance.object);
    helpers.title(object_entry ? object_entry.name : object_instance.object.replace(/_/g, " "), "#c49a5a");
    const [x0, y0, x1, y1] = object_instance_box(object_instance);
    helpers.kv([["size", `${x1 - x0} × ${y1 - y0}`], ["at", `x ${x0}, y ${y0}`]]);
    const delete_button = Object.assign(el("button", "btn", "Delete"), { type: "button", title: "Del" });
    delete_button.onclick = () => remove_painted_object(state.sel.i);
    helpers.actions().append(delete_button);
  }

  function render_exit_inspector(box, helpers, exit_index) {
    const scene_exit = state.exits[exit_index];
    if (!scene_exit) return;
    helpers.title(`Exit ${exit_index + 1}`, "#9cc6f0");
    box.append(inspector_property_row("Side", exit_side_select(scene_exit)), inspector_property_row("Position", exit_position_slider(scene_exit, exit_index)),
      inspector_property_row("Width", exit_width_input(scene_exit)), inspector_property_row("Goes to", exit_destination_input(scene_exit, exit_index)));
    fill_exit_destination_choices();
    const remove_button = Object.assign(el("button", "btn", "Remove"), { type: "button", disabled: state.exits.length <= 1 });
    remove_button.onclick = () => remove_exit(exit_index);
    helpers.actions().append(remove_button);
  }

  // ------------------------------------------------------------ hierarchy
  const EYE = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3.5C4.4 3.5 2 6.6 1.3 8c.7 1.4 3.1 4.5 6.7 4.5s6-3.1 6.7-4.5C14 6.6 11.6 3.5 8 3.5zm0 7.2A2.7 2.7 0 1 1 8 5.3a2.7 2.7 0 0 1 0 5.4z" fill="currentColor"/></svg>';
  const EYE_OFF = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.3 2.3l11.4 11.4-.9.9-2.3-2.3c-.8.3-1.6.4-2.5.4-3.6 0-6-3.1-6.7-4.5.4-.8 1.3-2.1 2.6-3.1L1.4 3.2zM8 3.5c3.6 0 6 3.1 6.7 4.5-.3.6-.9 1.5-1.7 2.4L5.6 3.9c.7-.2 1.5-.4 2.4-.4z" fill="currentColor"/></svg>';

  function object_tree_node(object_instance, object_index) {
    const object_entry = SL.palette_object_entry(state.palette, object_instance.object);
    const tree_node = {
      id: "o:" + object_index, label: `${object_entry ? object_entry.name : object_instance.object} (${object_instance.x}, ${object_instance.y})`,
      icon: { color: "#c49a5a" }, sel: { type: "object", i: object_index },
    };
    return tree_node;
  }
  function object_tree_nodes() {
    const object_nodes = painted_objects(state.result).map(object_tree_node);
    return object_nodes;
  }

  function treeModel() {
    if (state.chain) return chain_tree_model();
    const map_node = map_tree_node();
    if (!map_node) return null;
    const route_node = Object.assign(map_node, { children: map_node.children.concat([layers_tree_group()]) });
    return route_node;
  }

  function layers_tree_group() {
    const eye = (k) => ({ on: state.ov[k], toggle: () => toggleOv(k) });
    const layers = [
      { id: "l:tiles", label: "Metatiles", meta: state.layer === "tiles" ? "shown" : "", icon: { color: "#7aa060" }, run: () => setLayer("tiles"), on: state.layer === "tiles" },
      { id: "l:classes", label: "Classes", meta: state.layer === "classes" ? "shown" : "", icon: { color: "#c49a5a" }, run: () => setLayer("classes"), on: state.layer === "classes" },
      { id: "l:areas", label: "Areas overlay", icon: { cls: "ring" }, eye: eye("areas") },
      { id: "l:events", label: "Items overlay", icon: { cls: "dot", color: "#e0453a" }, eye: eye("events") },
      { id: "l:grid", label: "Grid", icon: { color: "#666" }, eye: eye("grid") },
    ];
    const layers_group = { id: "g:layers", label: "Layers", group: true, children: layers };
    return layers_group;
  }

  function map_tree_node() {
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
    return {
      id: "route", label: r.name, meta: `${r.width}×${r.height}`, sel: { type: "route" }, icon: { color: "#4d90d6" },
      children: [
        { id: "g:exits", label: "Exits", meta: String(exits.length), group: true, children: exits },
        { id: "g:lanes", label: "Lanes", meta: String(lanes.length), group: true, children: lanes },
        { id: "g:special", label: "Special areas", meta: String(special.length), group: true, children: special },
        { id: "g:places", label: "Clearings & lakes", meta: String(places.length), group: true, children: places },
        { id: "g:items", label: "Items & events", meta: String(r.events.length), group: true, children: items },
        { id: "g:placed", label: "Placed items", meta: String(route_items.tree_nodes().length), group: true, children: route_items.tree_nodes() },
        { id: "g:people", label: "People & signs", meta: String(route_people.tree_nodes().length), group: true, children: route_people.tree_nodes() },
        { id: "g:objects", label: "Placed objects", meta: String(object_tree_nodes().length), group: true, children: object_tree_nodes() },
      ],
    };
  }

  const selId = () => {
    const s = state.sel;
    if (!s) return "";
    return s.type === "route" ? "route" : s.type === "zone" ? "z:" + s.i : s.type === "event" ? "e:" + s.i : s.type === "exit" ? "x:" + s.i : s.type === "placed" ? "p:" + s.i : s.type === "object" ? "o:" + s.i : route_people.is_selection(s) ? route_people.tree_id(s) : "";
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
      const expand_key = n.expand_key || n.id;
      const open = q ? true : state.expanded.has(expand_key);
      if (n.children) li.setAttribute("aria-expanded", open);
      if (n.id === sid) li.classList.add("sel");
      if (n.dim) li.classList.add("dim");
      if (!open && n.children) li.classList.add("collapsed");
      const row = el("div", "row");
      row.style.paddingLeft = 4 + depth * 12 + "px";
      const tw = el("span", "tw" + (open ? " open" : ""), n.children && n.children.length ? "▶" : "");
      tw.onclick = (ev) => { ev.stopPropagation(); if (state.expanded.has(expand_key)) state.expanded.delete(expand_key); else state.expanded.add(expand_key); renderTree(); };
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
      row.ondblclick = () => {
        if (n.sel) { select(n.sel); focusSel(); return; }
        if (n.on_double_click) n.on_double_click();
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
  function find_menu_item(menu_item_id) {
    const found_item = SL.flattenMenus(menus).find((menu_item) => menu_item.id === menu_item_id);
    return found_item;
  }
  function run_menu_item(menu_item_id) {
    const menu_item = find_menu_item(menu_item_id);
    if (!menu_item || (typeof menu_item.enabled === "function" && !menu_item.enabled())) return;
    menu_item.run();
  }
  function sync_history_buttons() {
    for (const [button_id, menu_item_id] of [["tbUndo", "undo"], ["tbRedo", "redo"]]) {
      const menu_item = find_menu_item(menu_item_id);
      const is_available = !!menu_item && (typeof menu_item.enabled !== "function" || !!menu_item.enabled());
      $(button_id).disabled = !is_available;
    }
  }
  function sync_markings_button() {
    const markings_button = $("tbMarkings");
    if (!markings_button) return;
    markings_button.classList.toggle("on", !!state.ov.markings);
    markings_button.setAttribute("aria-pressed", String(!!state.ov.markings));
    markings_button.title = state.ov.markings ? "Hide markings" : "Show markings";
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
    for (const gate of rep.gates || []) rows.push(["gate", describe_route_gate_record(gate)]);
    return rows;
  }
  function describe_route_gate_record(gate) {
    const gate_description = gate.built ? gate.note : `not built: ${gate.note}`;
    return gate_description;
  }

  function renderInfo() {
    const r = state.result;
    const dl = $("stats");
    dl.innerHTML = "";
    if (!r) { renderEnc(); return; }
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
  function region_view_modes_shown() {
    const shown_views = Shell.shown_views();
    const mode_of_each_region_view = ["geography", "region"].map((view_mode) => (shown_views.includes(view_mode) ? view_mode : null));
    return mode_of_each_region_view;
  }
  function show_region_editor_views() {
    const mode_of_each_region_view = region_view_modes_shown();
    if (region_editor_actions) { region_editor_actions.set_view_modes(mode_of_each_region_view, Shell.layout.center); return; }
    const seed_of_last_region = sget("pokeroutes-shell-region-seed") || Math.floor(Math.random() * 100000);
    region_editor_actions = PREditor.init($("viewMap"), {
      mode: "geography", seed: seed_of_last_region, onOpenRoute: openInBench,
      host: {
        tools: $("tbMapTools"), side: $("mapInspector"), tree: $("mapTree"), extra_views: [$("viewMapBeside")],
        filter: () => $("hierFilter").value, icons: { eye: EYE, eye_off: EYE_OFF },
        log: (text, is_error) => log(is_error ? "error" : "info", text),
        onView: syncNonSceneToolbar,
        is_active: () => SL.is_region_editor_tab(Shell.layout.center),
        on_document_change: follow_map_changes_of_open_route,
      },
    });
    region_editor_actions.set_view_modes(mode_of_each_region_view, Shell.layout.center);
    show_open_route_on_map();
  }
  function show_open_route_on_map() {
    const editor_actions = region_editor();
    if (!editor_actions || !editor_actions.set_open_route) return;
    editor_actions.set_open_route(state.chain ? state.chain.route_name : state.region ? state.region.name : null);
  }
  function current_region_seed() {
    const editor_document = region_editor() && region_editor().getDoc();
    const region_seed = editor_document ? editor_document.seed : sget("pokeroutes-shell-region-seed") || 1;
    return region_seed;
  }

  function openInBench(opened) {
    if (opened && Array.isArray(opened.maps)) { open_route_chain(opened); return; }
    leave_chain_view();
    const spec = opened;
    const { params: p, region } = SL.specToParams(spec, params());
    replaceParams(p, "region route");
    state.route_trail = state.pending_walk && state.pending_walk.to === p.name ? state.pending_walk.trail : [];
    state.pending_walk = null;
    if (region) region.map_sync = map_sync_record_of_spec(spec, params());
    state.region = region;
    if (region) sset(REGION_KEY, region); else sdel(REGION_KEY);
    renderRegionLink();
    show_open_route_on_map();
    sync_back_button();
    sset("pokeroutes-shell-region-seed", current_region_seed());
    Shell.setCenter("scene");
    log("info", `Opened ${p.name} from region ${current_region_seed()}`);
    generate();
  }

  function open_map_of_spec(spec) {
    const region_block = spec.region || {};
    const open_map = { route_name: (region_block.chain || {}).route || spec.name, map_name: spec.name, rect: region_block.rect || [0, 0, 0, 0] };
    return open_map;
  }
  function map_sync_record(open_map, sync_target, region_document, opened_params) {
    const record = {
      route_name: sync_target ? sync_target.route.name : open_map.route_name, map_name: open_map.map_name, rect: open_map.rect,
      signature: sync_target ? SL.open_map_signature(region_document, sync_target) : "",
      chain: sync_target ? sync_target.route.chain.map((chain_map) => ({ name: chain_map.name, rect: chain_map.rect })) : [],
      opened_params,
    };
    return record;
  }
  function map_sync_record_of_spec(spec, opened_params) {
    const region_document = region_editor() && region_editor().getDoc();
    if (!region_document || !spec.region || !spec.region.rect) return null;
    const open_map = open_map_of_spec(spec);
    const record = map_sync_record(open_map, SL.sync_target_for_open_map(region_document, open_map), region_document, opened_params);
    return record;
  }

  // Map -> Routes: when the route open here changes on the Map, the Routes view follows it (same seed, the
  // settings changed here kept, painted cells kept on maps that are still there).
  let map_sync_in_flight = false, map_sync_waiting_document = null;
  async function follow_map_changes_of_open_route(region_document) {
    if (map_sync_in_flight) { map_sync_waiting_document = region_document; return; }
    map_sync_in_flight = true;
    try {
      await follow_one_map_change(region_document);
    } catch (problem) {
      log("error", "Could not follow the Map: " + problem.message);
    } finally {
      map_sync_in_flight = false;
      const waiting_document = map_sync_waiting_document;
      map_sync_waiting_document = null;
      if (waiting_document) follow_map_changes_of_open_route(waiting_document);
    }
  }
  async function follow_one_map_change(region_document) {
    if (state.chain) { await follow_map_change_of_open_chain(region_document); return; }
    const sync = state.region && state.region.map_sync;
    if (!sync || !region_document) return;
    const sync_target = SL.sync_target_for_open_map(region_document, sync);
    if (!sync_target) { report_open_route_erased(sync); return; }
    const signature = SL.open_map_signature(region_document, sync_target);
    if (signature === sync.signature) return;
    const spec = await region_editor().spec_of_map(sync_target.chain_map.id);
    if (!state.region || state.region.map_sync !== sync) return;
    move_painted_cells_along_the_chain(sync.chain, sync_target.route.chain, region_document.cell || 4);
    apply_followed_spec(spec, sync, sync_target, region_document);
  }
  function report_open_route_erased(sync) {
    if (sync.erased_reported) return;
    sync.erased_reported = true;
    const message = `${sync.map_name} was erased on the Map; the Routes view keeps its last version`;
    log("warn", message);
    Shell.status(message);
  }
  function move_painted_cells_along_the_chain(old_chain_maps, new_chain_maps, metatiles_per_region_cell) {
    for (const memory_key of [SL.PAINT_KEY, SL.PAINT_OBJECTS_KEY, SL.PLACED_ITEMS_KEY]) {
      const followed_memory = SL.paint_memory_after_chain_change(sget(memory_key) || {}, old_chain_maps, new_chain_maps, metatiles_per_region_cell);
      sset(memory_key, followed_memory);
    }
  }
  function apply_followed_spec(spec, sync, sync_target, region_document) {
    const { params: fresh_params, region } = SL.specToParams(spec, params());
    const synced_params = SL.params_after_map_sync(sync.opened_params, params(), fresh_params);
    const opened_params = Object.assign({}, fresh_params, { seed: synced_params.seed });
    replaceParams(synced_params, "follow the Map");
    region.map_sync = map_sync_record(open_map_of_spec(spec), sync_target, region_document, opened_params);
    state.region = region;
    sset(REGION_KEY, region);
    renderRegionLink();
    show_open_route_on_map();
    log("info", `Routes view follows the Map: ${spec.name}`);
    generate({ keepPaint: true });
  }

  function describe_region_gate(gate) {
    if (!gate) return "";
    const needs_text = gate.needs ? `needs ${gate.needs.replace(/_/g, " ")}` : "";
    const opens_text = gate.after_beat ? `opens after ${gate.after_beat}` : "";
    const entrance_text = gate.entrance_toward ? `at the exit to ${gate.entrance_toward}` : "";
    const gate_description = [needs_text, opens_text, entrance_text].filter(Boolean).join(", ");
    return gate_description;
  }

  function renderRegionLink() {
    const fold = $("foldRegion");
    const box = $("regionLink");
    box.innerHTML = "";
    const reg = state.region;
    set_fold_empty(fold, !reg);
    if (!reg) return;
    const r = reg.region || {};
    const dl = el("dl", "kv");
    const add = (k, v) => { if (v != null && v !== "") dl.append(el("dt", "", k), el("dd", "", String(v))); };
    add("route", state.chain ? state.chain.route_name : reg.name);
    if (state.chain) add("map", `${state.chain.active_index + 1} of ${state.chain.slots.length}`);
    add("role", r.role);
    add("shape", r.shape);
    add("tags", (r.tags || []).join(", "));
    add("gate", describe_region_gate(r.gate));
    box.append(dl);
    const b = el("button", "btn", "Detach from region");
    b.type = "button";
    b.onclick = detach_from_region;
    box.append(b);
  }
  function detach_from_region() {
    leave_chain_view();
    state.region = null;
    state.route_trail = [];
    sdel(REGION_KEY);
    renderRegionLink();
    renderTree();
    show_open_route_on_map();
    sync_back_button();
    log("info", "Detached the route from its region block");
  }

  // ------------------------------------------------------------ walking the region through its connections
  // An exit of a route opened from a region leads to the route or town on the other side;
  // clicking its label on the map opens that route, and the Back button over the map returns.
  const ARROW_OUT_OF_SIDE = { north: "↑", south: "↓", east: "→", west: "←" };
  function open_region_document() {
    const editor_actions = region_editor();
    return editor_actions ? editor_actions.getDoc() : null;
  }
  function scene_connections() {
    const route = state.result;
    if (!route) return [];
    const automatic_connections = state.region ? SL.exitConnections(route.exits, open_region_document(), state.region.name) : [];
    const connections = (route.exits || []).map((_scene_exit, exit_index) => {
      const hand_set = hand_set_connection(exit_index);
      return hand_set !== undefined ? hand_set : automatic_connections[exit_index] || null;
    });
    return connections;
  }
  function exit_tree_node(scene_exit, exit_index, connection) {
    const destination = connection ? connection.name : scene_exit.to;
    const node = {
      id: "x:" + exit_index, label: `${scene_exit.side}${destination ? " → " + destination : ""}`,
      meta: connection && connection.kind === "town" ? "town" : `${scene_exit.cells.length} wide`,
      icon: { color: "#9cc6f0" }, sel: { type: "exit", i: exit_index },
    };
    return node;
  }
  function sync_back_button() {
    const back_button = $("sceneBack");
    const previous_name = state.route_trail[state.route_trail.length - 1];
    back_button.hidden = !previous_name;
    back_button.textContent = previous_name ? `← ${previous_name}` : "";
    back_button.title = previous_name ? `Back to ${previous_name}` : "";
  }
  function follow_connection(exit_index) {
    const connection = scene_connections()[exit_index];
    if (!connection) return;
    if (connection.kind === "unknown") { log("warn", `Open the region in the Map view first to follow the way to ${connection.name}.`); return; }
    if (connection.kind === "town") { show_town_in_routes_view(connection.town); return; }
    if (is_connection_inside_open_chain(connection)) { activate_chain_map(chain_map_index_named(connection.name)); return; }
    walk_to(connection.route, [...state.route_trail, open_route_name()]);
  }
  function open_route_name() {
    if (state.chain) return state.chain.route_name;
    const route_name = state.region ? state.region.name : state.result.name;
    return route_name;
  }
  function chain_map_index_named(map_name) {
    const map_index = state.chain ? state.chain.slots.findIndex((slot) => slot.name === map_name) : -1;
    return map_index;
  }
  function is_connection_inside_open_chain(connection) {
    const leads_inside_chain = !!connection && connection.kind === "route" && chain_map_index_named(connection.name) >= 0;
    return leads_inside_chain;
  }
  function go_back_a_route() {
    if (!state.route_trail.length) return;
    const previous_name = state.route_trail[state.route_trail.length - 1];
    const region_document = open_region_document();
    const previous_route = SL.region_route_named(region_document, previous_name);
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
    const route = state.result;
    const connections = scene_connections();
    const font_size = Math.round(Math.max(11, Math.min(14, s * 0.7)));
    g.font = `600 ${font_size}px "Pixelify Sans", ui-monospace, monospace`;
    g.textBaseline = "middle";
    (route.exits || []).forEach((scene_exit, exit_index) => {
      const connection = connections[exit_index];
      const destination_name = connection ? connection.name : readable_map_name(scene_exit.to);
      if (!destination_name || !scene_exit.cells.length || is_connection_inside_open_chain(connection)) return;
      const text = `${ARROW_OUT_OF_SIDE[scene_exit.side] || "→"} ${destination_name}`;
      const box = connection_label_box(scene_exit, g.measureText(text).width, font_size + 8, ox, oy, s);
      g.lineJoin = "round";
      g.lineWidth = 3;
      g.strokeStyle = "#fbfaf2";
      g.strokeText(text, box.x + 6, box.y + box.h / 2 + 1);
      g.fillStyle = "#2a2a2a";
      g.fillText(text, box.x + 6, box.y + box.h / 2 + 1);
      if (connection) state.connection_labels.push(Object.assign({ i: exit_index, chain_map_index: chain_map_index_being_shown() }, box));
    });
  }
  function readable_map_name(map_constant) {
    if (!map_constant) return "";
    const chain_map = SL.chain_map_with_constant(open_region_document(), map_constant);
    if (chain_map) return chain_map.name;
    const without_prefix = String(map_constant).replace(/^MAP_/, "");
    const route_match = without_prefix.match(/^ROUTE_?(\d+)$/);
    if (route_match) return `Route ${Number(route_match[1])}`;
    const spaced_name = without_prefix.replace(/([A-Z])(\d)/g, "$1_$2");
    const readable_name = spaced_name.toLowerCase().split("_").filter(Boolean).map((word) => word[0].toUpperCase() + word.slice(1)).join(" ");
    return readable_name;
  }

  function show_center_tab(tab) {
    const region_tab_shown = Shell.shown_views().find(SL.is_region_editor_tab);
    if (region_tab_shown || region_editor()) show_region_editor_views();
    // the toolbar, hierarchy and inspector are shared: each shows the active view's contents
    // (Routes or the map editor), while Generate and zoom stay and drive the active view
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
    const palette_tab_label = center_tab === "geography" ? "Biomes" : "Tiles";
    Shell.set_panel_title("tiles", palette_tab_label);
    if (Shell.is_panel_visible("tiles")) render_tiles_panel();
  }
  function generate_button_label_for_view(center_tab) {
    switch (center_tab) {
      case "geography":
        return "Generate geography";
      case "region":
        return "Generate map";
      default:
        return "Generate";
    }
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
  // the active view (Routes or the geography / map editor).
  function doGenerate() {
    if (isScene()) { generate(); return; }
    const ea = region_editor();
    if (!ea) return;
    if (Shell.layout.center === "region" && ea.generate_map) { ea.generate_map(); return; }
    ea.generate();
  }
  function doRegenerate() {
    if (isScene()) { regenerate(); return; }
    const ea = region_editor();
    if (!ea) return;
    ea.generate(Math.floor(Math.random() * 100000));
  }
  let shown_views_signature = Shell.shown_views().join(" ");
  function fit_views_after_split_change() {
    const views_signature = Shell.shown_views().join(" ");
    if (views_signature === shown_views_signature) return;
    shown_views_signature = views_signature;
    requestAnimationFrame(() => {
      resize();
      fit();
      if (region_editor()) region_editor().fit_every_view();
    });
  }
  Shell.onCenter((tab) => {
    show_center_tab(tab);
    requestAnimationFrame(resize);
    fit_views_after_split_change();
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
    leave_chain_view();
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
    leave_chain_view();
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
    const name = state.chain ? state.chain.route_name : state.file.name || p.name;
    const dirty = state.saved && state.saved !== JSON.stringify(p);
    const stale = state.generated && state.generated !== JSON.stringify(p);
    const map_text = state.chain && state.chain.slots.length > 1 ? ` · map ${state.chain.active_index + 1} of ${state.chain.slots.length}` : "";
    $("docTitle").textContent = `${name}${dirty ? " •" : ""}${map_text} · seed ${p.seed}${stale ? " · edited since Generate" : ""}`;
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
    log("info", `Tiler: ${t === "ark" ? "PokeRoutes tiles" : "learned Emerald tiles"}`);
    if (state.result) generate();
  }
  const menus = [
    { label: "File", items: [
      { id: "new", label: "New route", run: newRoute },
      { id: "open", label: "Open spec…", run: openSpec },
      { id: "recent", label: "Open recent", items: () => {
        const list = sget(SL.RECENT_KEY) || [];
        if (!list.length) return [{ label: "No recent routes", enabled: false }];
        return list.map((e) => ({ label: e.label, run: () => { leave_chain_view(); replaceParams(e.params, "recent"); Shell.setCenter("scene"); generate(); } }))
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
      { id: "zin", label: "Zoom in", enabled: viewZoomable, run: zoomInView },
      { id: "zout", label: "Zoom out", enabled: viewZoomable, run: zoomOutView },
      { id: "zfit", label: "Fit to view", enabled: viewZoomable, run: fitView },
      { id: "z100", label: "Actual size (100%)", enabled: sceneRoute, run: () => zoomTo(1) },
      { id: "frame", label: "Frame selection", enabled: sceneRoute, run: focusSel },
      { sep: true },
      { id: "v-back", label: "Back to the previous route", enabled: () => state.route_trail.length > 0, run: go_back_a_route },
      { sep: true },
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
      { id: "tiler", label: "Tiler", items: [
        { id: "t-learned", label: "Learned (Emerald General + Fortree)", radio: true, checked: () => prefs.tiler === "learned", run: () => setTiler("learned") },
        { id: "t-ark", label: "PokeRoutes tiles", radio: true, checked: () => prefs.tiler === "ark", enabled: () => !state.tilers || state.tilers.some((t) => t.id === "ark" && t.ok), run: () => setTiler("ark") },
      ] },
    ] },
    { label: "Window", items: [
      ...Shell.window_menu_items(),
      { sep: true },
      { id: "w-tools", label: "Tools", items: [
        { id: "w-tile-editor", label: "Tile editor", checked: () => !!window.TileEditor && window.TileEditor.is_open(), run: () => window.TileEditor && window.TileEditor.open() },
        { id: "w-playtest", label: "Play-test", enabled: hasRoute, run: open_playtest },
      ] },
      { sep: true },
      { id: "w-reset", label: "Reset layout", run: () => Shell.resetLayout() },
    ] },
    { label: "Help", items: [
      { id: "keys", label: "Keyboard shortcuts", run: () => Shell.showShortcuts() },
      { sep: true },
      { id: "about", label: "About poke-routes", run: () => { $("aboutServer").textContent = location.origin; Shell.openDialog("dlgAbout"); } },
    ] },
  ];

  // plain-letter keys belong to whichever view is active: the map editor has its own (V R L F S B W D T, ?)
  const toolKey = (t, label) => ({ id: "tool-" + t, label, group: "Scene tools", enabled: isScene, run: () => setTool(t) });
  const sceneKey = (id, label, run) => ({ id, label, group: "Scene view", enabled: sceneRoute, run });
  const extras = [
    toolKey("select", "Select tool"), toolKey("pan", "Pan tool"), toolKey("paint", "Paint tool"), toolKey("item", "Item tool"), toolKey("people", "People & signs tool"),
    { id: "x-item-delete", label: "Delete the selected item, person, sign or object", group: "Scene tools", enabled: () => isScene() && has_deletable_selection(), run: delete_selection },
    { id: "x-rotate", label: "Rotate clockwise", group: "Scene tools", enabled: () => isScene() && can_rotate(), run: rotate_brush_or_selection },
    { id: "x-brush-smaller", label: "Smaller brush", group: "Scene tools", enabled: isScene, run: () => set_brush_size(state.brush_size - 1) },
    { id: "x-brush-bigger", label: "Bigger brush", group: "Scene tools", enabled: isScene, run: () => set_brush_size(state.brush_size + 1) },
    sceneKey("x-zin", "Zoom in", () => zoomBy(1)), sceneKey("x-zout", "Zoom out", () => zoomBy(-1)), sceneKey("x-zfit", "Fit to view", fit),
    { id: "x-help", label: "Keyboard shortcuts", group: "Help", enabled: isScene, run: () => Shell.showShortcuts() },
    { id: "ov-areas", label: "Areas overlay", group: "Layers", run: () => toggleOv("areas") },
    { id: "ov-items", label: "Items overlay", group: "Layers", run: () => toggleOv("events") },
    { id: "ov-classes", label: "Classes instead of tiles", group: "Layers", run: () => setLayer(state.layer === "classes" ? "tiles" : "classes") },
    { id: "ov-grid", label: "Grid", group: "Layers", run: () => toggleOv("grid") },
    sceneKey("play", "Play / pause tile animation", (ev) => { if (ev && document.activeElement && document.activeElement.tagName === "BUTTON") document.activeElement.blur(); toggle_tile_animation(); }),
    sceneKey("next", "Faster tile animation", () => change_tile_animation_speed(1)),
    sceneKey("prev", "Slower tile animation", () => change_tile_animation_speed(-1)),
    sceneKey("first", "Previous animation frame", () => step_tile_animation(-1)),
    sceneKey("last", "Next animation frame", () => step_tile_animation(1)),
  ];

  function has_deletable_selection() {
    const selection = state.sel;
    const is_deletable = route_items.has_selection() || route_people.has_selection() || (!!selection && selection.type === "object" && !!painted_objects(state.result)[selection.i]);
    return is_deletable;
  }
  function delete_selection() {
    if (state.sel && state.sel.type === "object") { remove_painted_object(state.sel.i); return; }
    if (route_people.has_selection()) { route_people.delete_selected(); return; }
    route_items.delete_selected();
  }

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
    $("tbMarkings").onclick = () => toggleOv("markings");
    $("tbUndo").onclick = () => run_menu_item("undo");
    $("tbRedo").onclick = () => run_menu_item("redo");
    $("sceneBack").onclick = go_back_a_route;
    setInterval(sync_history_buttons, 500);
    sync_markings_button();
    $("name").addEventListener("change", () => edited("name"));
    $("seed").addEventListener("change", () => edited("seed"));
    $("hierFilter").addEventListener("input", () => { if (mapView()) region_editor().refresh_tree(); });
    $("areaAdd").onclick = addSpecial;
    $("areaInput").addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); addSpecial(); } });
    $("exitAdd").onclick = add_exit;
    render_brush_size_row();
    setTool("select");
    syncViewButtons();
    SL.attachShortcuts(menus, extras);
    Shell.setMenus(menus, extras);
  }

  function add_exit() {
    if (state.exits.length >= 4) return;
    state.exits.push({ side: "west", at: 0.5, width: 3 });
    renderExits();
    edited("exits");
    select({ type: "exit", i: state.exits.length - 1 });
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
    renderSelection();
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
    if (reopen_remembered_chain()) { generate_chain_maps(all_map_indices_of(state.chain), { keepPaint: true, fit: true }); return; }
    generate({ keepPaint: true });
  }

  const EMPTY_PALETTE = { tiler: "", tileset: "", terrain: [], unavailable: [], objects: [], tiles: {} };
  const PALETTE_GROUPS = [["ground", "Ground"], ["water", "Water"], ["trees", "Trees"], ["rock", "Rock"], ["ledges", "Ledges"]];
  const terrain_preview_size_in_pixels = 40;
  const BRUSH_SIZES = [1, 2, 3, 5];

  async function load_paint_palette(route_result) {
    state.palette = await paint_palette_of_route(route_result);
    settle_brush_on_palette();
  }

  async function paint_palette_of_route(route_result) {
    const palette_key = `${route_result.tiler}|${route_result.tileset}`;
    if (!state.palettes[palette_key]) state.palettes[palette_key] = await fetch_paint_palette(route_result);
    const route_palette = state.palettes[palette_key];
    return route_palette;
  }

  function settle_brush_on_palette() {
    if (!is_brush_available(state.brush)) state.brush = "g";
    keep_ledge_direction_available();
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
    if (kind === "tile") return !!palette && palette.tiles[key] != null;
    const entry = SL.palette_terrain_entry(palette, key);
    return key === "g" || (!!entry && entry.group !== "ledges");
  }

  function keep_ledge_direction_available() {
    const ledge_codes = SL.palette_ledge_codes(state.palette);
    if (ledge_codes.length && !ledge_codes.includes(state.ledgeDir)) state.ledgeDir = ledge_codes[0];
  }

  function brush_cells_at(cell_x, cell_y) {
    const r = state.result, { kind, key } = SL.brush_kind(state.brush);
    if (kind === "object") {
      const object_entry = SL.palette_object_entry(state.palette, key);
      return object_entry ? SL.object_stamp_cells(object_entry, cell_x, cell_y, r.width, r.height) : [];
    }
    if (kind === "tile") return [[cell_x, cell_y, SL.exact_tile_value(state.palette, key)]];
    return terrain_brush_cells_at(cell_x, cell_y, kind === "ledge" ? state.ledgeDir : key);
  }

  function is_tree_terrain(class_code) {
    const entry = SL.palette_terrain_entry(state.palette, class_code);
    const draws_whole_trees = entry ? entry.group === "trees" : TREE_CLASS_CODES.includes(class_code);
    return draws_whole_trees;
  }

  function terrain_brush_cells_at(cell_x, cell_y, class_code) {
    const r = state.result, entry = SL.palette_terrain_entry(state.palette, class_code);
    if (is_tree_terrain(class_code)) {
      const one_tree = SL.tree_block_keys_around(cell_x, cell_y, r.width, r.height).map((cell_key) => [...cell_key.split(",").map(Number), class_code]);
      return one_tree;
    }
    const needs_two_cells = entry ? entry.two_wide : SL.TWO_WIDE.includes(class_code);
    const chosen_size = needs_two_cells ? Math.max(2, state.brush_size) : state.brush_size;
    const brush_cells = SL.brushCells(cell_x, cell_y, chosen_size, class_code, r.width, r.height);
    return brush_cells;
  }

  function add_brush_to_stroke(cell_x, cell_y) {
    const { kind, key } = SL.brush_kind(state.brush);
    const brush_cells = brush_cells_at(cell_x, cell_y);
    if (kind !== "object") { for (const [x, y, value] of brush_cells) state.paint.cells.set(`${x},${y}`, value); return; }
    if (!brush_cells.length || brush_cells.some(([x, y]) => state.paint.cells.has(`${x},${y}`))) return;
    for (const [x, y, value] of brush_cells) state.paint.cells.set(`${x},${y}`, value);
    const object_entry = SL.palette_object_entry(state.palette, key);
    const top_left = SL.object_top_left(object_entry, cell_x, cell_y);
    state.paint.objects.push({ object: key, x: top_left.x, y: top_left.y });
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

  function choose_brush(brush) {
    state.brush = brush;
    if (state.tool !== "paint") setTool("paint");
    render_paint_palette();
  }

  function choose_ledge(ledge_code) {
    state.ledgeDir = ledge_code;
    choose_brush(LEDGE_BRUSH);
  }

  function set_brush_size(requested_size) {
    const smallest = BRUSH_SIZES[0], largest = BRUSH_SIZES[BRUSH_SIZES.length - 1];
    const clamped_size = Math.max(smallest, Math.min(largest, requested_size));
    const stepping_up = requested_size > state.brush_size;
    state.brush_size = BRUSH_SIZES.includes(clamped_size) ? clamped_size : BRUSH_SIZES.find((size) => (stepping_up ? size > clamped_size : size >= clamped_size)) || largest;
    saveView();
    render_brush_size_row();
    draw();
  }

  function render_brush_size_row() {
    const size_row = $("brushSizeRow");
    size_row.innerHTML = "";
    size_row.append(el("span", "brush-size-label", "Size"));
    for (const brush_size of BRUSH_SIZES) {
      const size_button = Object.assign(el("button", "seg-btn" + (brush_size === state.brush_size ? " on" : ""), String(brush_size)), { type: "button", title: `${brush_size} × ${brush_size}  ([ ])` });
      size_button.onclick = () => set_brush_size(brush_size);
      size_row.append(size_button);
    }
  }

  function render_tiles_panel() {
    const is_geography_view = Shell.layout.center === "geography";
    const is_map_view = Shell.layout.center === "region";
    $("leftTiles").hidden = is_geography_view || is_map_view;
    $("leftBiomes").hidden = !is_geography_view;
    if (is_geography_view) { render_biome_brushes(); return; }
    if (!is_map_view) render_paint_palette();
  }

  function render_biome_brushes() {
    const biome_grid = $("biomeGrid");
    const editor_actions = region_editor();
    biome_grid.innerHTML = "";
    if (!editor_actions || !window.ED) return;
    const current_paint_biome = editor_actions.paint_biome ? editor_actions.paint_biome() : null;
    for (const biome_code of window.ED.PAINT_BIOMES.split("").concat([""])) {
      const biome_rgb = window.ED.BIOME_RGB[biome_code] || [58, 58, 58];
      const biome_button = el("button", "terrain-brush" + (biome_code === current_paint_biome ? " on" : ""));
      biome_button.type = "button";
      biome_button.title = biome_code ? window.ED.BIOME_NAMES[biome_code] : "Remove painted biome";
      const biome_swatch = el("span", "biome-swatch");
      biome_swatch.style.background = `rgb(${biome_rgb.join(",")})`;
      biome_button.append(biome_swatch, el("span", "", biome_code ? window.ED.BIOME_NAMES[biome_code] : "clear paint"));
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
    return entries_in_group.map((entry) => palette_button(entry.code, entry.name, entry.name, sample_rows(entry), painting && state.brush === entry.code, () => choose_brush(entry.code)));
  }

  function single_ledge_button(ledge_entries, painting) {
    if (!ledge_entries.length) return [];
    const current_ledge = ledge_entries.find((entry) => entry.code === state.ledgeDir) || ledge_entries[0];
    const ledge_is_selected = painting && state.brush === LEDGE_BRUSH;
    const ledge_button = palette_button(LEDGE_BRUSH, current_ledge.name.replace("jump ", ""), `${current_ledge.name} (R turns it)`, sample_rows(current_ledge), ledge_is_selected,
      () => choose_ledge(current_ledge.code));
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

  function is_rotatable_object(object_id) {
    const object_base = rotatable_object_base(object_id || "");
    const can_turn = !!object_base && !!state.palette && is_rotatable_object_group(object_base);
    return can_turn;
  }

  function turned_object_id(object_id) {
    const object_base = rotatable_object_base(object_id);
    const next_suffix = object_id.endsWith("_h") ? "_v" : "_h";
    return object_base + next_suffix;
  }

  function brush_object_id() {
    const object_id = state.brush.startsWith("object:") ? state.brush.slice("object:".length) : "";
    return object_id;
  }

  function selected_object_instance() {
    const selection = state.sel;
    if (!selection || selection.type !== "object") return null;
    return painted_objects(state.result)[selection.i] || null;
  }

  function can_rotate() {
    const selected_instance = selected_object_instance();
    if (state.tool !== "paint" && selected_instance) return is_rotatable_object(selected_instance.object);
    if (state.tool !== "paint") return false;
    const brush_turns = state.brush === LEDGE_BRUSH || is_rotatable_object(brush_object_id());
    return brush_turns;
  }

  function rotate_brush_or_selection() {
    const selected_instance = selected_object_instance();
    if (state.tool !== "paint" && selected_instance) { turn_selected_object(selected_instance); return; }
    if (state.brush === LEDGE_BRUSH) {
      state.ledgeDir = SL.next_ledge_code(state.palette, state.ledgeDir);
      render_paint_palette();
      draw();
      return;
    }
    if (is_rotatable_object(brush_object_id())) choose_brush("object:" + turned_object_id(brush_object_id()));
    draw();
  }

  async function turn_selected_object(object_instance) {
    const r = state.result;
    const turned_entry = SL.palette_object_entry(state.palette, turned_object_id(object_instance.object));
    if (!turned_entry) return;
    const turned_cells = SL.object_stamp_cells(turned_entry, object_instance.x + Math.floor((turned_entry.cells[0].length - 1) / 2),
      object_instance.y + Math.floor((turned_entry.cells.length - 1) / 2), r.width, r.height);
    if (!turned_cells.length) { log("warn", "No room to turn it here."); return; }
    state.paint.cells = new Map(turned_cells.map(([x, y, value]) => [`${x},${y}`, value]));
    state.paint.objects = [{ object: turned_entry.id, x: object_instance.x, y: object_instance.y }];
    await commitPaint();
    const turned_index = SL.object_instance_index_at(state.palette, painted_objects(r), object_instance.x, object_instance.y);
    if (turned_index >= 0) select({ type: "object", i: turned_index });
  }

  function object_button(entry, painting) {
    const brush = "object:" + entry.id, size = `${entry.cells[0].length}×${entry.cells.length}`;
    const object_base = rotatable_object_base(entry.id);
    const is_rotatable = !!object_base && is_rotatable_object_group(object_base);
    if (!is_rotatable) return palette_button(brush, entry.name, `${entry.name} (${size})`, entry.cells, painting && state.brush === brush, () => choose_brush(brush));
    const selected_variant = state.brush.startsWith("object:" + object_base + "_") ? state.brush.slice("object:".length) : object_base + "_h";
    const shown_entry = state.palette.objects.find((candidate) => candidate.id === selected_variant) || entry;
    const group_is_selected = painting && state.brush === "object:" + shown_entry.id;
    const group_name = object_base.replace(/_/g, " ");
    return palette_button("object:" + shown_entry.id, group_name, `${group_name} (R turns it)`, shown_entry.cells, group_is_selected,
      () => choose_brush("object:" + shown_entry.id));
  }

  function object_buttons() {
    const painting = state.tool === "paint";
    const shown_object_entries = state.palette.objects.filter((entry) => !(entry.id.endsWith("_v") && is_rotatable_object_group(rotatable_object_base(entry.id))));
    const objects = shown_object_entries.map((entry) => object_button(entry, painting));
    return objects;
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
  }

  const CHAIN_KEY = "pokeroutes-open-route-chain-v1";
  const live_map_field_names = ["result", "atlas", "tileCanvases", "classCanvases", "changedMasks", "stageInfo", "animCells", "stage", "prevStage", "region", "generated", "palette"];
  const metatiles_per_region_cell_without_editor = 4;
  const seam_line_dash_in_pixels = [6, 5];
  const seam_line_colour = "rgba(20,20,20,0.5)";
  const seam_line_halo_colour = "rgba(255,255,255,0.32)";
  const placeholder_line_colour = "rgba(255,255,255,0.22)";
  const placeholder_text_colour = "rgba(255,255,255,0.6)";
  let chain_map_index_drawn = null;

  function active_chain_slot() {
    const chain = state.chain;
    const open_slot = chain ? chain.slots[chain.active_index] : null;
    return open_slot;
  }

  function chain_map_index_being_shown() {
    if (chain_map_index_drawn !== null) return chain_map_index_drawn;
    const shown_index = state.chain ? state.chain.active_index : null;
    return shown_index;
  }

  function inactive_chain_slots_with_maps() {
    if (!state.chain) return [];
    const open_slot = active_chain_slot();
    const slots_with_maps = state.chain.slots.filter((slot) => slot !== open_slot && !!slot.result);
    return slots_with_maps;
  }

  function inactive_chain_map_indices_with_maps() {
    const slots_with_maps = inactive_chain_slots_with_maps();
    const map_indices = slots_with_maps.map((slot) => state.chain.slots.indexOf(slot));
    return map_indices;
  }

  function metatiles_per_region_cell() {
    const metatiles_per_cell = (window.ED && window.ED.CELL) || metatiles_per_region_cell_without_editor;
    return metatiles_per_cell;
  }

  function live_map_fields() {
    const fields = Object.fromEntries(live_map_field_names.map((field_name) => [field_name, state[field_name]]));
    fields.items_snapshot = route_items.snapshot();
    fields.people_snapshot = route_people.snapshot();
    return fields;
  }

  function install_map_fields(fields) {
    for (const field_name of live_map_field_names) state[field_name] = fields[field_name];
    route_items.resume(fields.items_snapshot);
    route_people.resume(fields.people_snapshot);
  }

  function store_live_map_into_slot() {
    const open_slot = active_chain_slot();
    if (!open_slot) return;
    Object.assign(open_slot, live_map_fields(), { params: params() });
  }

  function empty_hand_edits_of(map_name) {
    const hand_edits = {
      items_snapshot: { route_key: "", shown_items: [] },
      people_snapshot: { route_key: "", route_name: map_name, entries: [], generated: [] },
    };
    return hand_edits;
  }

  function chain_slot(chain_map, placement, map_params, map_region, opened_params) {
    const slot = Object.assign({
      name: chain_map.name, rect: chain_map.rect, placement, params: map_params, region: map_region, opened_params: opened_params || map_params,
      result: null, atlas: null, tileCanvases: [], classCanvases: [], changedMasks: [], stageInfo: [], animCells: [],
      stage: 0, prevStage: null, generated: "", palette: state.palette, is_generating: false,
    }, empty_hand_edits_of(chain_map.name));
    return slot;
  }

  function slot_of_chain_map(layout, params_and_region_of_map) {
    return (chain_map, map_index) => chain_slot(chain_map, layout.maps[map_index], ...params_and_region_of_map(chain_map));
  }

  function chain_of_route(route_name, chain_maps, params_and_region_of_map, active_index) {
    const layout = SL.chain_layout(chain_maps, metatiles_per_region_cell());
    const chain = {
      route_name, layout, active_index, hover: null,
      slots: chain_maps.map(slot_of_chain_map(layout, params_and_region_of_map)),
      seam_segments: SL.chain_seam_segments(layout),
    };
    return chain;
  }

  function params_and_region_of_spec(chain_map) {
    const { params: map_params, region } = SL.specToParams(chain_map.spec, params());
    return [map_params, region];
  }

  function params_and_region_remembered(remembered_map) {
    const params_and_region = [remembered_map.params, remembered_map.region, remembered_map.opened_params];
    return params_and_region;
  }

  function params_and_region_following(old_chain) {
    return (chain_map) => followed_params_and_region(old_chain, chain_map);
  }

  function followed_params_and_region(old_chain, chain_map) {
    const [fresh_params, region] = params_and_region_of_spec(chain_map);
    const old_slot = old_chain.slots.find((slot) => slot.rect.join() === chain_map.rect.join()) || old_chain.slots.find((slot) => slot.name === chain_map.name);
    if (!old_slot) return [fresh_params, region];
    const synced_params = SL.params_after_map_sync(old_slot.opened_params, old_slot.params, fresh_params);
    const followed = [synced_params, region, Object.assign({}, fresh_params, { seed: synced_params.seed })];
    return followed;
  }

  function open_chain_place() {
    const open_slot = active_chain_slot();
    const open_place = { route_name: state.chain.route_name, map_name: open_slot.name, rect: open_slot.rect };
    return open_place;
  }

  function route_signature(region_document, synced_route) {
    const signature = JSON.stringify(synced_route.chain.map((chain_map) => SL.open_map_signature(region_document, { route: synced_route, chain_map })));
    return signature;
  }

  function chain_map_sync_record(region_document) {
    const sync_target = region_document ? SL.sync_target_for_open_map(region_document, open_chain_place()) : null;
    if (!sync_target) return null;
    const record = {
      map_name: state.chain.route_name, signature: route_signature(region_document, sync_target.route),
      chain: sync_target.route.chain.map((chain_map) => ({ name: chain_map.name, rect: chain_map.rect })),
    };
    return record;
  }

  async function follow_map_change_of_open_chain(region_document) {
    const chain = state.chain, sync = chain.map_sync;
    if (!sync || !region_document) return;
    const sync_target = SL.sync_target_for_open_map(region_document, open_chain_place());
    if (!sync_target) { report_open_route_erased(sync); return; }
    if (route_signature(region_document, sync_target.route) === sync.signature) return;
    const opened = await region_editor().route_chain_specs_of_map(sync_target.chain_map.id);
    if (state.chain !== chain) return;
    move_painted_cells_along_the_chain(sync.chain, sync_target.route.chain, region_document.cell || metatiles_per_region_cell());
    reopen_followed_chain(chain, Object.assign(opened, { focus_map_name: sync_target.chain_map.name }), region_document);
  }

  function reopen_followed_chain(old_chain, opened, region_document) {
    store_live_map_into_slot();
    const focus_index = Math.max(0, opened.maps.findIndex((chain_map) => chain_map.name === opened.focus_map_name));
    const followed_chain = chain_of_route(opened.route, opened.maps, params_and_region_following(old_chain), focus_index);
    show_chain_in_routes_view(followed_chain, "follow the Map");
    followed_chain.map_sync = chain_map_sync_record(region_document);
    remember_open_chain();
    log("info", `Routes view follows the Map: ${opened.route}`);
    generate_chain_maps(all_map_indices_of(followed_chain), { keepPaint: true });
  }

  function all_map_indices_of(chain) {
    const map_indices = chain.slots.map((_slot, map_index) => map_index);
    return map_indices;
  }

  function open_route_chain(opened) {
    const focus_index = Math.max(0, opened.maps.findIndex((chain_map) => chain_map.name === opened.focus_map_name));
    const chain = chain_of_route(opened.route, opened.maps, params_and_region_of_spec, focus_index);
    state.route_trail = trail_after_walking_into(opened);
    state.pending_walk = null;
    show_chain_in_routes_view(chain, "region route");
    chain.map_sync = chain_map_sync_record(open_region_document());
    remember_open_chain();
    sset("pokeroutes-shell-region-seed", current_region_seed());
    Shell.setCenter("scene");
    log("info", `Opened ${opened.route} (${chain.slots.length} map${chain.slots.length === 1 ? "" : "s"}) from region ${current_region_seed()}`);
    requestAnimationFrame(fit);
    generate_chain_maps(all_map_indices_of(chain), {});
  }

  function trail_after_walking_into(opened) {
    const pending_walk = state.pending_walk;
    const walked_into_route = !!pending_walk && (pending_walk.to === opened.route || opened.maps.some((chain_map) => chain_map.name === pending_walk.to));
    const trail = walked_into_route ? pending_walk.trail : [];
    return trail;
  }

  function show_chain_in_routes_view(chain, undo_label) {
    const focus_slot = chain.slots[chain.active_index];
    state.chain = chain;
    if (undo_label) replaceParams(focus_slot.params, undo_label); else setParams(focus_slot.params);
    install_map_fields(focus_slot);
    state.sel = null;
    state.expanded.add(map_tree_expand_key(focus_slot));
    if (focus_slot.region) sset(REGION_KEY, focus_slot.region); else sdel(REGION_KEY);
    remember_open_chain();
    route_items.update_count();
    show_open_route_on_map();
    sync_back_button();
    render_open_map_panels();
  }

  function reopen_remembered_chain() {
    const remembered_chain = sget(CHAIN_KEY);
    const saved_params = sget(STORE_KEY);
    if (!remembered_chain || !Array.isArray(remembered_chain.maps) || !remembered_chain.maps.length || !saved_params) return false;
    const focus_index = remembered_chain.maps.findIndex((remembered_map) => remembered_map.name === saved_params.name);
    if (focus_index < 0) return false;
    const chain = chain_of_route(remembered_chain.route, remembered_chain.maps, params_and_region_remembered, focus_index);
    chain.slots[focus_index].params = saved_params;
    chain.map_sync = remembered_chain.map_sync || null;
    show_chain_in_routes_view(chain, null);
    return true;
  }

  function remember_open_chain() {
    const chain = state.chain;
    if (!chain) return;
    active_chain_slot().params = params();
    const remembered_chain = {
      route: chain.route_name, map_sync: chain.map_sync || null,
      maps: chain.slots.map((slot) => ({ name: slot.name, rect: slot.rect, params: slot.params, region: slot.region, opened_params: slot.opened_params })),
    };
    sset(CHAIN_KEY, remembered_chain);
  }

  function leave_chain_view() {
    if (!state.chain) return;
    state.chain = null;
    sdel(CHAIN_KEY);
    renderTree();
    updateTitle();
    draw();
  }

  function render_open_map_panels() {
    renderInfo();
    renderTree();
    renderExits();
    renderSelection();
    renderRegionLink();
    updateTitle();
    draw();
  }

  function view_shifted_between(from_slot, to_slot) {
    const cell_size_on_screen = CELL * state.view.zoom;
    const shifted_view = Object.assign({}, state.view, {
      ox: state.view.ox + (to_slot.placement.offset_x_in_metatiles - from_slot.placement.offset_x_in_metatiles) * cell_size_on_screen,
      oy: state.view.oy + (to_slot.placement.offset_y_in_metatiles - from_slot.placement.offset_y_in_metatiles) * cell_size_on_screen,
    });
    return shifted_view;
  }

  function view_of_chain_map(map_index) {
    const map_view = view_shifted_between(active_chain_slot(), state.chain.slots[map_index]);
    return map_view;
  }

  function activate_chain_map(map_index) {
    const chain = state.chain;
    if (!chain || map_index === chain.active_index || !chain.slots[map_index] || state.paint.busy) return;
    store_live_map_into_slot();
    const entered_slot = chain.slots[map_index];
    state.view = view_shifted_between(active_chain_slot(), entered_slot);
    chain.active_index = map_index;
    chain.hover = null;
    install_map_fields(entered_slot);
    setParams(entered_slot.params);
    if (entered_slot.generated === JSON.stringify(entered_slot.params)) state.generated = JSON.stringify(params());
    Object.assign(state, { sel: null, hover: null, hoverArea: null });
    state.expanded.add(map_tree_expand_key(entered_slot));
    sset(STORE_KEY, params());
    if (state.region) sset(REGION_KEY, state.region);
    remember_open_chain();
    route_items.update_count();
    if (state.result) settle_brush_on_palette();
    if (state.result) route_events.on_new_route(state.result);
    render_open_map_panels();
  }

  function chain_map_index_under(screen_x, screen_y) {
    const open_placement = active_chain_slot().placement;
    const cell_size_on_screen = CELL * state.view.zoom;
    const x_in_chain = Math.floor((screen_x - state.view.ox) / cell_size_on_screen) + open_placement.offset_x_in_metatiles;
    const y_in_chain = Math.floor((screen_y - state.view.oy) / cell_size_on_screen) + open_placement.offset_y_in_metatiles;
    const map_index = SL.chain_map_index_at(state.chain.layout, x_in_chain, y_in_chain);
    return map_index;
  }

  function activate_chain_map_under(screen_x, screen_y) {
    if (!state.chain) return;
    const label = connection_label_at(screen_x, screen_y);
    const map_index = label && label.chain_map_index != null ? label.chain_map_index : chain_map_index_under(screen_x, screen_y);
    if (map_index < 0 || !state.chain.slots[map_index].result) return;
    activate_chain_map(map_index);
  }

  function hover_cell_on_chain_map(map_index) {
    const chain_hover = state.chain && state.chain.hover;
    const hover_cell = chain_hover && chain_hover.map_index === map_index ? chain_hover.cell : null;
    return hover_cell;
  }

  function hover_on_another_chain_map(screen_x, screen_y) {
    const chain = state.chain;
    if (!chain) return false;
    const map_index = chain_map_index_under(screen_x, screen_y);
    if (map_index < 0 || map_index === chain.active_index) { chain.hover = null; return false; }
    const slot = chain.slots[map_index];
    const cell = PR.cellAt(view_of_chain_map(map_index), screen_x, screen_y, CELL);
    chain.hover = slot.result ? { map_index, cell } : null;
    Object.assign(state, { hover: null, hoverArea: null });
    hideTip();
    wrap.classList.toggle("over-connection", state.tool === "select" && !!connection_label_at(screen_x, screen_y));
    $("stCell").textContent = slot.result ? cell_status_text(slot.result, slot.stage, cell) : `${slot.name} · generating`;
    draw();
    return true;
  }

  function with_chain_map_shown(map_index, show) {
    const slot = state.chain.slots[map_index];
    const live_fields = live_map_fields();
    const live_view_fields = { exits: state.exits, sel: state.sel, hover: state.hover, hoverArea: state.hoverArea };
    const live_paint_cells = state.paint.cells;
    install_map_fields(slot);
    Object.assign(state, { exits: slot.params.exits || [], sel: null, hover: hover_cell_on_chain_map(map_index), hoverArea: null });
    state.paint.cells = new Map();
    chain_map_index_drawn = map_index;
    try {
      const shown = show();
      return shown;
    } finally {
      chain_map_index_drawn = null;
      install_map_fields(live_fields);
      Object.assign(state, live_view_fields);
      state.paint.cells = live_paint_cells;
    }
  }

  function paint_open_chain(g, opt) {
    const chain = state.chain;
    const shown_indices = inactive_chain_map_indices_with_maps();
    const open_map_has_tiles = !!state.result && state.tileCanvases.length > 0;
    for (const slot of chain.slots) if (!slot.result || (slot === active_chain_slot() && !open_map_has_tiles)) paint_chain_map_placeholder(g, slot);
    for (const map_index of shown_indices) paint_map_shadow(g, view_of_chain_map(map_index), chain.slots[map_index].result);
    if (open_map_has_tiles) paint_map_shadow(g, state.view, state.result);
    for (const map_index of shown_indices) with_chain_map_shown(map_index, () => paint_map_layer(g, view_of_chain_map(map_index), { interactive: false }));
    if (open_map_has_tiles) paint_map_layer(g, state.view, opt);
    for (const map_index of shown_indices) with_chain_map_shown(map_index, () => paint_map_overlays(g, view_of_chain_map(map_index), { interactive: true }));
    if (open_map_has_tiles) paint_map_overlays(g, state.view, opt);
    paint_chain_seams(g);
  }

  function paint_chain_map_placeholder(g, slot) {
    const map_view = view_shifted_between(active_chain_slot(), slot);
    const cell_size_on_screen = CELL * map_view.zoom;
    const width_on_screen = slot.placement.width_in_metatiles * cell_size_on_screen, height_on_screen = slot.placement.height_in_metatiles * cell_size_on_screen;
    g.save();
    g.strokeStyle = placeholder_line_colour;
    g.setLineDash(seam_line_dash_in_pixels);
    g.lineWidth = 1;
    g.strokeRect(Math.round(map_view.ox) + 0.5, Math.round(map_view.oy) + 0.5, width_on_screen - 1, height_on_screen - 1);
    g.font = "600 12px Inter, system-ui, sans-serif";
    g.fillStyle = placeholder_text_colour;
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillText(slot.is_generating ? `${slot.name} · generating…` : slot.name, map_view.ox + width_on_screen / 2, map_view.oy + height_on_screen / 2);
    g.restore();
  }

  function paint_chain_seams(g) {
    if (!state.ov.markings || !state.chain.seam_segments.length) return;
    const open_placement = active_chain_slot().placement;
    const cell_size_on_screen = CELL * state.view.zoom;
    const screen_x_of = (x_in_chain) => Math.round(state.view.ox + (x_in_chain - open_placement.offset_x_in_metatiles) * cell_size_on_screen) + 0.5;
    const screen_y_of = (y_in_chain) => Math.round(state.view.oy + (y_in_chain - open_placement.offset_y_in_metatiles) * cell_size_on_screen) + 0.5;
    g.save();
    g.setLineDash(seam_line_dash_in_pixels);
    g.beginPath();
    for (const seam of state.chain.seam_segments) {
      g.moveTo(screen_x_of(seam.x0), screen_y_of(seam.y0));
      g.lineTo(screen_x_of(seam.x1), screen_y_of(seam.y1));
    }
    g.lineWidth = 3;
    g.strokeStyle = seam_line_halo_colour;
    g.stroke();
    g.lineWidth = 1;
    g.strokeStyle = seam_line_colour;
    g.stroke();
    g.restore();
  }

  function fit_chain_rectangle(left_in_metatiles, top_in_metatiles, width_in_metatiles, height_in_metatiles) {
    const box = wrap.getBoundingClientRect();
    const zoom = PR.fitZoom(width_in_metatiles * CELL, height_in_metatiles * CELL, box.width, box.height, 16);
    const open_placement = active_chain_slot().placement;
    const left_on_screen = Math.round((box.width - width_in_metatiles * CELL * zoom) / 2) - left_in_metatiles * CELL * zoom;
    const top_on_screen = Math.round(Math.max(16, (box.height - height_in_metatiles * CELL * zoom) / 2)) - top_in_metatiles * CELL * zoom;
    state.view = {
      zoom, ox: left_on_screen + open_placement.offset_x_in_metatiles * CELL * zoom, oy: top_on_screen + open_placement.offset_y_in_metatiles * CELL * zoom,
    };
    draw();
  }

  function frame_chain_map(map_index) {
    const placement = state.chain.slots[map_index].placement;
    fit_chain_rectangle(placement.offset_x_in_metatiles, placement.offset_y_in_metatiles, placement.width_in_metatiles, placement.height_in_metatiles);
  }

  function shown_area_in_metatiles() {
    if (!state.chain) return { left: 0, top: 0, width: state.result.width, height: state.result.height };
    const open_placement = active_chain_slot().placement;
    const chain_area = {
      left: -open_placement.offset_x_in_metatiles, top: -open_placement.offset_y_in_metatiles,
      width: state.chain.layout.width_in_metatiles, height: state.chain.layout.height_in_metatiles,
    };
    return chain_area;
  }

  function map_tree_expand_key(slot) {
    const expand_key = "map:" + slot.name;
    return expand_key;
  }

  function chain_tree_model() {
    const chain = state.chain;
    const map_nodes = all_map_indices_of(chain).map(chain_map_tree_node);
    const chain_node = {
      id: "chain", label: chain.route_name, meta: `${chain.slots.length} map${chain.slots.length === 1 ? "" : "s"}`, icon: { color: "#4d90d6" }, group: true,
      children: map_nodes.concat([layers_tree_group()]),
    };
    return chain_node;
  }

  function chain_map_tree_node(map_index) {
    const chain = state.chain, slot = chain.slots[map_index];
    const naming = { label: `Map ${map_index + 1}`, expand_key: map_tree_expand_key(slot) };
    const waiting_node = { id: map_index === chain.active_index ? "route" : `m${map_index}:map`, meta: slot.is_generating ? "generating…" : "not generated", icon: { color: "#4d90d6" }, dim: true };
    if (map_index === chain.active_index) return Object.assign(map_tree_node() || waiting_node, naming);
    if (!slot.result) return Object.assign(waiting_node, naming);
    const reached_node = tree_node_reached_through_map(with_chain_map_shown(map_index, map_tree_node), map_index);
    return Object.assign(reached_node, naming);
  }

  function tree_node_reached_through_map(tree_node, map_index) {
    const reached_node = Object.assign({}, tree_node, { id: `m${map_index}:${tree_node.id}`, expand_key: tree_node.expand_key || tree_node.id, sel: null });
    if (tree_node.sel) reached_node.run = () => select_on_chain_map(map_index, tree_node.sel);
    if (tree_node.sel) reached_node.on_double_click = () => frame_selection_on_chain_map(map_index, tree_node.sel);
    if (tree_node.children) reached_node.children = tree_node.children.map((child_node) => tree_node_reached_through_map(child_node, map_index));
    return reached_node;
  }

  function select_on_chain_map(map_index, selection) {
    activate_chain_map(map_index);
    select(selection);
  }

  function frame_selection_on_chain_map(map_index, selection) {
    select_on_chain_map(map_index, selection);
    focusSel();
  }

  function generate_open_chain(opts) {
    const chain = state.chain;
    const map_indices = opts && opts.auto ? [chain.active_index] : all_map_indices_of(chain);
    return generate_chain_maps(map_indices, opts || {});
  }

  function regenerate_open_chain_with_new_seeds() {
    store_live_map_into_slot();
    for (const slot of state.chain.slots) slot.params = Object.assign({}, slot.params, { seed: Math.floor(Math.random() * 100000) });
    setParams(active_chain_slot().params);
    sset(STORE_KEY, params());
    remember_open_chain();
    generate_open_chain({});
  }

  async function generate_chain_maps(map_indices, opts) {
    if (state.busy) { pendingGenerate = opts.keepPaint ? { keepPaint: true } : true; return; }
    const chain = state.chain;
    store_live_map_into_slot();
    remember_open_chain();
    setBusy(true, map_indices.length > 1 ? `generating ${map_indices.length} maps` : "generating");
    stopPlay();
    const started_at_in_milliseconds = performance.now();
    const generations = map_indices.map((map_index) => generate_chain_slot(chain, chain.slots[map_index], opts));
    renderTree();
    const outcomes = await Promise.all(generations);
    setBusy(false);
    if (state.chain === chain) finish_chain_generation(chain, outcomes, started_at_in_milliseconds, opts);
    if (!pendingGenerate) return;
    const pending_generate_options = pendingGenerate === true ? undefined : pendingGenerate;
    pendingGenerate = false;
    generate(pending_generate_options);
  }

  function finish_chain_generation(chain, outcomes, started_at_in_milliseconds, opts) {
    const generated_count = outcomes.filter(Boolean).length;
    const seconds_taken = (performance.now() - started_at_in_milliseconds) / 1000;
    log(generated_count === outcomes.length ? "info" : "warn", `Generated ${generated_count} of ${outcomes.length} map${outcomes.length === 1 ? "" : "s"} of ${chain.route_name} in ${seconds_taken.toFixed(1)} s`);
    $("sceneMsg").hidden = !!state.result;
    if (opts.fit) fit();
    renderTree();
    updateTitle();
  }

  function generate_body_of_slot(requested_params, slot) {
    const body = Object.assign({}, requested_params, { tiler: prefs.tiler });
    if (!slot.region || !slot.region.region) return body;
    body.region = slot.region.region;
    if (slot.region.endings && slot.region.endings.length) body.endings = slot.region.endings;
    return body;
  }

  async function generate_chain_slot(chain, slot, opts) {
    const requested_params = slot.params;
    slot.is_generating = true;
    draw();
    try {
      const route_result = await postJSON("/api/generate", generate_body_of_slot(requested_params, slot));
      const route_atlas = await atlasFor(route_result.atlas || "/api/atlas");
      const route_palette = await paint_palette_of_route(route_result);
      if (state.chain !== chain) return false;
      install_generated_map_into_slot(slot, route_result, route_atlas, route_palette, requested_params);
      await put_back_hand_edits_on_slot(chain, slot, route_result, opts);
      return true;
    } catch (error) {
      log("error", `Generate failed for ${slot.name}: ${error.message}`);
      return false;
    } finally {
      slot.is_generating = false;
      if (state.chain === chain) renderTree();
      draw();
    }
  }

  function is_hand_edit_of_map(undo_entry, map_name) {
    const is_hand_edit = ["paint", "items", "people"].includes(undo_entry.kind) && (!undo_entry.chain_map_name || undo_entry.chain_map_name === map_name);
    return is_hand_edit;
  }

  function install_generated_map_into_slot(slot, route_result, route_atlas, route_palette, requested_params) {
    route_result.generated_class_rows = route_result.stages[route_result.stages.length - 1].cls.slice();
    const generated_map = Object.assign({
      result: route_result, atlas: route_atlas, palette: route_palette, stage: route_result.stages.length - 1, prevStage: null,
      generated: JSON.stringify(requested_params),
    }, canvases_of_route(route_result, route_atlas), empty_hand_edits_of(route_result.name));
    Object.assign(slot, generated_map);
    undoStack.filter((undo_entry) => !is_hand_edit_of_map(undo_entry, slot.name));
    reportToConsole(route_result, (route_result.timing || {}).total || 0);
    if (slot === active_chain_slot()) show_open_slot(slot);
    draw();
  }

  function show_open_slot(slot) {
    install_map_fields(slot);
    state.sel = null;
    settle_brush_on_palette();
    route_items.update_count();
    render_open_map_panels();
  }

  async function put_back_hand_edits_on_slot(chain, slot, route_result, opts) {
    if (opts.keepPaint) await reapply_paint_on_map(slot);
    if (!opts.keepPaint) { storePaint(route_result, {}); store_painted_objects(route_result, []); }
    const [items_snapshot, people_snapshot] = await Promise.all([route_items.placed_items_put_back_on(route_result), route_people.people_put_back_on(route_result)]);
    route_events.on_new_route(route_result);
    if (state.chain !== chain || slot.result !== route_result) return;
    Object.assign(slot, { items_snapshot, people_snapshot });
    if (slot !== active_chain_slot()) { renderTree(); draw(); return; }
    route_items.resume(items_snapshot);
    route_people.resume(people_snapshot);
    route_items.update_count();
    renderTree();
    renderSelection();
    draw();
  }

  function chain_map_offset_in_metatiles() {
    const open_slot = active_chain_slot();
    const offset = open_slot ? { x: open_slot.placement.offset_x_in_metatiles, y: open_slot.placement.offset_y_in_metatiles } : { x: 0, y: 0 };
    return offset;
  }

  function neighbouring_map_of_slot(slot, open_offset) {
    const neighbouring_map = {
      canvas: slot.tileCanvases[slot.tileCanvases.length - 1],
      offset_x_in_metatiles: slot.placement.offset_x_in_metatiles - open_offset.x, offset_y_in_metatiles: slot.placement.offset_y_in_metatiles - open_offset.y,
      width_in_metatiles: slot.result.width, height_in_metatiles: slot.result.height,
    };
    return neighbouring_map;
  }

  function neighbouring_tile_canvases() {
    const open_offset = chain_map_offset_in_metatiles();
    const neighbouring_maps = inactive_chain_slots_with_maps().map((slot) => neighbouring_map_of_slot(slot, open_offset));
    return neighbouring_maps;
  }

  function leads_to_another_map_of_this_route(exit_index) {
    const leads_inside = is_connection_inside_open_chain(scene_connections()[exit_index]);
    return leads_inside;
  }

  // ------------------------------------------------------------ play-test window (playtest.js)
  function open_playtest() {
    if (!window.Playtest) return;
    Shell.setCenter("scene");
    window.Playtest.open();
  }
  function show_final_stage() {
    stopPlay();
    if (state.result) setStage(state.result.stages.length - 1, false);
  }
  function final_route_events() {
    const final_events = state.result ? state.result.events.concat(route_items.visible_items()) : [];
    return final_events;
  }
  function final_tile_canvas() {
    const final_canvas = state.tileCanvases[state.tileCanvases.length - 1] || null;
    return final_canvas;
  }
  function final_top_layer_canvas() {
    const top_layer_canvas = state.top_layer_canvas || null;
    return top_layer_canvas;
  }
  function playtest_route_name() {
    const route_name = state.region ? state.region.name : (state.result ? state.result.name : "");
    return route_name;
  }
  const playtest_hooks = {
    show_final_stage, final_route_events, final_tile_canvas, final_top_layer_canvas, playtest_route_name, scene_connections, follow_connection,
    chain_map_offset_in_metatiles, neighbouring_tile_canvases, leads_to_another_map_of_this_route,
    draw_route_effect_particles_into, fetchJSON,
    draw_event_sprite: (graphics_context, route_event, origin_x, origin_y, cell_size) => route_items.draw_event_sprite(graphics_context, route_event, origin_x, origin_y, cell_size, animFrame),
  };
  $("tbPlaytest").onclick = open_playtest;

  window.Bench = { generate, state, params, setParams, undoStack, menus, extras, loadSpecText, playtest_hooks, reload_atlas_at_version, fetchJSON, postJSON, log, draw, region_editor, open_route_name };
  boot();
})();
