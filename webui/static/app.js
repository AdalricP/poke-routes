/* poke-routes web UI: controls -> /api/generate -> stage canvases drawn from the metatile atlas. */
(function () {
  "use strict";
  const PR = window.PR;
  const $ = (id) => document.getElementById(id);
  const CELL = 16;
  const STAGE_MS = 900;
  const FADE_MS = 260;
  const LAND_RATES = [20, 20, 10, 10, 10, 10, 5, 5, 4, 4, 1, 1];
  const WATER_RATES = [60, 30, 5, 4, 1];
  const STORE_KEY = "pokeroutes-webui-v1";

  const state = {
    atlas: null, atlasImg: null, result: null,
    stage: 0, prevStage: null, fadeStart: 0,
    tileCanvases: [], classCanvases: [],
    view: { zoom: 1, ox: 0, oy: 0 },
    layer: "tiles", ov: { paths: true, areas: true, events: true, grid: false },
    hover: null, hoverArea: null, highlight: null,
    playing: false, timer: null, busy: false,
    specials: [], exits: [], tab: "dex",
  };

  // ------------------------------------------------------------ storage
  function load() {
    try { return JSON.parse(localStorage.getItem(STORE_KEY) || "null"); } catch (e) { return null; }
  }
  function save(params) {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(params)); } catch (e) { /* private mode */ }
  }

  // ------------------------------------------------------------ controls
  // Controls are built from /api/spec-schema (RouteSpec's dataclass fields), so new
  // generator parameters show up without touching this file.
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
    { name: "road", kind: "bool", default: true, group: "shape", label: "dirt road on the main path" },
    { name: "hidden_paths", kind: "int", default: 2, group: "secrets", min: 0, max: 6 },
    { name: "hidden_items", kind: "int", default: 4, group: "secrets", min: 0, max: 12 },
  ], exit_fields: [] };
  // what the page opens with the first time (a 64x72 forest route)
  const FIRST_RUN = { width: 64, height: 72, meander: 0.7, branches: 3, dead_ends: 2, clearings: 4, tall_grass: 0.3 };
  const controls = {}; // name -> {get(), set(v), field}
  let schema = FALLBACK_SCHEMA;

  function labelOf(f) {
    if (f.label) return f.label;
    const t = f.name.replace(/_/g, " ");
    return t.charAt(0).toUpperCase() + t.slice(1);
  }

  function makeControl(f, value) {
    const wrapEl = document.createElement("div");
    const id = "f-" + f.name;
    let get, set;
    if (f.kind === "bool") {
      wrapEl.className = "check";
      wrapEl.innerHTML = `<input id="${id}" type="checkbox"><label for="${id}"></label>`;
      wrapEl.querySelector("label").textContent = labelOf(f);
      const inp = wrapEl.querySelector("input");
      get = () => inp.checked;
      set = (v) => { inp.checked = !!v; };
    } else if (f.choices) {
      wrapEl.className = "row";
      wrapEl.innerHTML = `<span></span><select id="${id}"></select>`;
      wrapEl.querySelector("span").textContent = labelOf(f);
      const sel = wrapEl.querySelector("select");
      f.choices.forEach((c) => { const o = document.createElement("option"); o.value = o.textContent = c; sel.appendChild(o); });
      get = () => sel.value;
      set = (v) => { if (f.choices.includes(v)) sel.value = v; };
    } else if (f.kind === "int" || f.kind === "float") {
      const step = f.step != null ? f.step : f.kind === "int" ? 1 : 0.05;
      wrapEl.className = "slider";
      wrapEl.innerHTML = `<label for="${id}"><span></span><output></output></label>` +
        `<input type="range" id="${id}" min="${f.min}" max="${f.max}" step="${step}">`;
      wrapEl.querySelector("span").textContent = labelOf(f);
      const inp = wrapEl.querySelector("input");
      const out = wrapEl.querySelector("output");
      const fmt = () => { out.textContent = step < 1 ? Number(inp.value).toFixed(2) : inp.value; };
      inp.addEventListener("input", () => { fmt(); sizeWarn(); });
      get = () => (f.kind === "int" ? parseInt(inp.value, 10) : parseFloat(inp.value));
      set = (v) => { inp.value = v; fmt(); };
    } else if (f.kind === "range") {
      wrapEl.className = "row";
      wrapEl.innerHTML = `<span></span><input type="number" min="${f.min != null ? f.min : 0}" max="${f.max != null ? f.max : 100}">` +
        `<input type="number" min="${f.min != null ? f.min : 0}" max="${f.max != null ? f.max : 100}">`;
      wrapEl.querySelector("span").textContent = labelOf(f);
      const [a, b] = wrapEl.querySelectorAll("input");
      a.setAttribute("aria-label", labelOf(f) + " low");
      b.setAttribute("aria-label", labelOf(f) + " high");
      get = () => [Number(a.value), Number(b.value)].sort((x, y) => x - y);
      set = (v) => { if (Array.isArray(v)) { a.value = v[0]; b.value = v[1]; } };
    } else {
      wrapEl.className = "row";
      wrapEl.innerHTML = `<span></span><input type="text" id="${id}">`;
      wrapEl.querySelector("span").textContent = labelOf(f);
      const inp = wrapEl.querySelector("input");
      get = () => inp.value;
      set = (v) => { inp.value = v == null ? "" : v; };
    }
    set(value);
    return { el: wrapEl, get, set, field: f };
  }

  function buildControls(saved) {
    for (const g of ["route", "shape", "secrets", "other"]) $("g-" + g).innerHTML = "";
    for (const k of Object.keys(controls)) delete controls[k];
    for (const f of schema.fields) {
      if (f.name === "seed") continue; // has its own box with the dice
      const v = saved && saved[f.name] != null ? saved[f.name] : FIRST_RUN[f.name] != null && !saved ? FIRST_RUN[f.name] : f.default;
      const c = makeControl(f, v);
      const box = $("g-" + f.group) || $("g-other");
      box.appendChild(c.el);
      controls[f.name] = c;
    }
    $("sec-other").hidden = !$("g-other").children.length;
    sizeWarn();
  }

  function sizeWarn() {
    if (!controls.width || !controls.height) return;
    $("sizewarn").hidden = PR.gbaSizeOk(controls.width.get(), controls.height.get());
  }

  function renderChips() {
    const ul = $("chips");
    ul.innerHTML = "";
    if (!state.specials.length) {
      ul.innerHTML = '<li class="none">none</li>';
      return;
    }
    state.specials.forEach((s, i) => {
      const li = document.createElement("li");
      li.textContent = s;
      const b = document.createElement("button");
      b.type = "button";
      b.setAttribute("aria-label", "Remove " + s);
      b.textContent = "×";
      b.onclick = () => { state.specials.splice(i, 1); renderChips(); };
      li.appendChild(b);
      ul.appendChild(li);
    });
  }
  function addSpecial() {
    const v = $("areaInput").value.trim();
    if (!v || state.specials.length >= 6) return;
    state.specials.push(v);
    $("areaInput").value = "";
    renderChips();
  }

  function renderExits() {
    const ol = $("exits");
    ol.innerHTML = "";
    state.exits.forEach((e, i) => {
      const li = document.createElement("li");
      li.innerHTML =
        `<select aria-label="Exit ${i + 1} side">${["north", "south", "east", "west"].map((s) => `<option${s === e.side ? " selected" : ""}>${s}</option>`).join("")}</select>` +
        `<input type="range" min="0" max="1" step="0.05" value="${e.at}" aria-label="Exit ${i + 1} position" title="position along the edge">` +
        `<input type="number" min="1" max="6" value="${e.width}" aria-label="Exit ${i + 1} width" title="width">` +
        `<button type="button" class="x" aria-label="Remove exit ${i + 1}"${state.exits.length <= 2 ? " disabled" : ""}>×</button>`;
      const [sel, rng, num, x] = li.children;
      sel.onchange = () => { e.side = sel.value; };
      rng.oninput = () => { e.at = +rng.value; };
      num.onchange = () => { e.width = Math.max(1, Math.min(6, parseInt(num.value, 10) || 3)); num.value = e.width; };
      x.onclick = () => { state.exits.splice(i, 1); renderExits(); };
      ol.appendChild(li);
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

  async function loadSchema() {
    try {
      const sc = await fetchJSON("/api/spec-schema");
      if (sc && Array.isArray(sc.fields) && sc.fields.length) schema = sc;
    } catch (e) { /* keep the built-in fallback */ }
  }

  function initControls() {
    const saved = load();
    buildControls(saved);
    if (saved) {
      if (saved.name) $("name").value = saved.name;
      if (saved.seed != null) $("seed").value = saved.seed;
    }
    state.specials = saved && Array.isArray(saved.special_areas) ? saved.special_areas.slice(0, 6) : ["auto", "auto", "pond"];
    state.exits = saved && Array.isArray(saved.exits) && saved.exits.length >= 2 ? saved.exits.map((e) => ({ ...e })) :
      [{ side: "south", at: 0.45, width: 2 }, { side: "north", at: 0.6, width: 2 }, { side: "east", at: 0.35, width: 3 }];
    renderChips();
    renderExits();
    $("dice").onclick = () => { $("seed").value = Math.floor(Math.random() * 100000); generate(); };
    $("areaAdd").onclick = addSpecial;
    $("areaInput").addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); addSpecial(); } });
    $("exitAdd").onclick = () => {
      if (state.exits.length < 4) { state.exits.push({ side: "west", at: 0.5, width: 3 }); renderExits(); }
    };
    $("form").addEventListener("submit", (ev) => { ev.preventDefault(); generate(); });
    $("generate").onclick = generate;
    document.addEventListener("keydown", (ev) => {
      if ((ev.ctrlKey || ev.metaKey) && ev.key === "Enter") { ev.preventDefault(); generate(); }
    });
  }

  // ------------------------------------------------------------ server
  async function fetchJSON(url, opts) {
    const r = await fetch(url, opts);
    let body;
    try { body = await r.json(); } catch (e) { throw new Error(`${r.status} ${r.statusText}`); }
    if (!r.ok || body.error) throw new Error(body.error || `${r.status}`);
    return body;
  }

  async function loadAtlas() {
    const meta = await fetchJSON("/api/atlas");
    const img = new Image();
    img.src = meta.png;
    await img.decode();
    state.atlas = meta;
    state.atlasImg = img;
  }

  async function loadOptions() {
    try {
      const o = await fetchJSON("/api/options");
      const dl = $("areaList");
      dl.innerHTML = "";
      ["auto", ...o.archetypes, ...o.species].forEach((v) => {
        const opt = document.createElement("option");
        opt.value = v;
        dl.appendChild(opt);
      });
    } catch (e) { /* the datalist is a nicety */ }
  }

  async function generate() {
    if (state.busy) return;
    const p = params();
    save(p);
    setBusy(true, "generating");
    stopPlay();
    try {
      const t0 = performance.now();
      const res = await fetchJSON("/api/generate", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(p),
      });
      setBusy(true, "drawing tiles");
      await new Promise((r) => requestAnimationFrame(r));
      const resized = !state.result || state.result.width !== res.width || state.result.height !== res.height;
      state.result = res;
      buildCanvases();
      state.highlight = null;
      buildTimeline();
      renderInfo();
      if (resized) fit();
      $("empty").hidden = true;
      $("timing").textContent = `server ${res.timing.total.toFixed(1)} s (generate ${res.timing.generate.toFixed(1)}, ` +
        `tiles ${(res.timing.tile_final + res.timing.tile_stages).toFixed(1)}), round trip ${((performance.now() - t0) / 1000).toFixed(1)} s`;
      setStage(0, false);
      startPlay();
    } catch (e) {
      $("empty").hidden = !!state.result;
      showError(e.message);
    } finally {
      setBusy(false);
    }
  }

  function setBusy(on, text) {
    state.busy = on;
    $("busy").hidden = !on;
    if (text) $("busyText").textContent = text;
    $("generate").disabled = on;
  }
  function showError(msg) {
    $("busy").hidden = false;
    $("busyText").textContent = "error: " + msg;
    setTimeout(() => { if (!state.busy) $("busy").hidden = true; }, 6000);
  }

  // ------------------------------------------------------------ canvases
  function buildCanvases() {
    const r = state.result;
    const W = r.width, H = r.height;
    const idx = state.atlas.index, cols = state.atlas.columns, img = state.atlasImg;
    state.tileCanvases = [];
    state.classCanvases = [];
    let prev = null, prevTiles = null;
    r.stages.forEach((st) => {
      const c = document.createElement("canvas");
      c.width = W * CELL;
      c.height = H * CELL;
      const g = c.getContext("2d");
      if (prev) g.drawImage(prev, 0, 0); // start from the previous stage; only redraw what changed
      for (let i = 0; i < st.tiles.length; i++) {
        const m = st.tiles[i];
        if (prevTiles && prevTiles[i] === m) continue;
        const x = (i % W) * CELL, y = Math.floor(i / W) * CELL;
        const k = idx[m];
        if (k == null) {
          g.fillStyle = "#ff00ff";
          g.fillRect(x, y, CELL, CELL);
        } else {
          g.drawImage(img, (k % cols) * CELL, Math.floor(k / cols) * CELL, CELL, CELL, x, y, CELL, CELL);
        }
      }
      state.tileCanvases.push(c);
      prev = c;
      prevTiles = st.tiles;

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
      state.classCanvases.push(cc);
    });
  }

  // ------------------------------------------------------------ view
  const canvas = $("map");
  const ctx = canvas.getContext("2d");
  const wrap = $("mapwrap");

  function resize() {
    const dpr = window.devicePixelRatio || 1;
    const r = wrap.getBoundingClientRect();
    canvas.width = Math.max(1, Math.round(r.width * dpr));
    canvas.height = Math.max(1, Math.round(r.height * dpr));
    draw();
  }

  function fit() {
    const r = state.result;
    if (!r) return;
    const box = wrap.getBoundingClientRect();
    const topPad = 48, botPad = 40;
    const availH = box.height - topPad - botPad;
    const z = PR.fitZoom(r.width * CELL, r.height * CELL, box.width, availH, 12);
    state.view = {
      zoom: z,
      ox: Math.round((box.width - r.width * CELL * z) / 2),
      oy: Math.round(topPad + Math.max(0, availH - r.height * CELL * z) / 2),
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

  function centerOn(bbox) {
    const box = wrap.getBoundingClientRect();
    const [x0, y0, x1, y1] = bbox;
    const w = (x1 - x0 + 6) * CELL, h = (y1 - y0 + 6) * CELL;
    const z = Math.max(state.view.zoom, Math.min(PR.fitZoom(w, h, box.width, box.height, 40), 4));
    const cx = ((x0 + x1) / 2) * CELL, cy = ((y0 + y1) / 2) * CELL;
    state.view = { zoom: z, ox: Math.round(box.width / 2 - cx * z), oy: Math.round(box.height / 2 - cy * z) };
    draw();
  }

  // pan / zoom input
  let drag = null;
  let wheelAcc = 0;
  wrap.addEventListener("pointerdown", (ev) => {
    if (ev.target.closest(".toolbar")) return;
    drag = { x: ev.clientX, y: ev.clientY, ox: state.view.ox, oy: state.view.oy, moved: false };
    wrap.setPointerCapture(ev.pointerId);
  });
  wrap.addEventListener("pointermove", (ev) => {
    const box = wrap.getBoundingClientRect();
    const sx = ev.clientX - box.left, sy = ev.clientY - box.top;
    if (drag) {
      const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) { drag.moved = true; wrap.classList.add("dragging"); }
      state.view.ox = drag.ox + dx;
      state.view.oy = drag.oy + dy;
      hideTip();
      draw();
      return;
    }
    hoverAt(sx, sy);
  });
  const endDrag = () => { drag = null; wrap.classList.remove("dragging"); };
  wrap.addEventListener("pointerup", endDrag);
  wrap.addEventListener("pointercancel", endDrag);
  wrap.addEventListener("pointerleave", () => { state.hover = null; state.hoverArea = null; hideTip(); $("status").textContent = ""; draw(); });
  wrap.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    const box = wrap.getBoundingClientRect();
    wheelAcc += ev.deltaY;
    if (Math.abs(wheelAcc) < 40) return;
    zoomBy(wheelAcc < 0 ? 1 : -1, ev.clientX - box.left, ev.clientY - box.top);
    wheelAcc = 0;
  }, { passive: false });

  function hoverAt(sx, sy) {
    const r = state.result;
    if (!r) return;
    const c = PR.cellAt(state.view, sx, sy, CELL);
    const inside = c.x >= 0 && c.y >= 0 && c.x < r.width && c.y < r.height;
    state.hover = inside ? c : null;
    const zones = PR.zonesAt(r, state.stage);
    const area = inside && state.ov.areas ? PR.specialAt(zones, c.x, c.y) : null;
    const changed = area !== state.hoverArea;
    state.hoverArea = area;
    if (inside) {
      const st = r.stages[state.stage];
      const k = st.cls[c.y][c.x];
      const m = st.tiles[c.y * r.width + c.x];
      $("status").textContent = `x ${c.x}  y ${c.y}  ${PR.CLASS_NAMES[k] || k}  metatile 0x${m.toString(16).toUpperCase().padStart(3, "0")}`;
    } else {
      $("status").textContent = "";
    }
    if (area) showTip(area, sx, sy); else hideTip();
    if (changed) draw();
  }

  function showTip(z, sx, sy) {
    const tip = $("tip");
    const a = z.area || {};
    tip.innerHTML = "";
    const h = document.createElement("h3");
    h.textContent = z.name;
    tip.appendChild(h);
    const sp = document.createElement("div");
    sp.className = "sp";
    sp.textContent = (a.species ? a.species : "no resident species") + (a.archetype ? ` · ${a.archetype}` : "") +
      (a.shiny_boost > 1 ? ` · shiny x${a.shiny_boost}` : "");
    tip.appendChild(sp);
    if (a.description) {
      const p = document.createElement("p");
      p.textContent = a.description;
      tip.appendChild(p);
    }
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
    $("zoomLabel").textContent = `${state.view.zoom}x`;
    const r = state.result;
    if (!r || !state.tileCanvases.length) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.imageSmoothingEnabled = false;
    const { zoom, ox, oy } = state.view;
    const W = r.width * CELL * zoom, H = r.height * CELL * zoom;
    const layer = state.layer === "classes" ? state.classCanvases : state.tileCanvases;

    ctx.fillStyle = "rgba(0,0,0,0.18)";
    ctx.fillRect(ox + 3, oy + 3, W, H);
    let t = 1;
    if (state.prevStage != null) {
      t = Math.min(1, (performance.now() - state.fadeStart) / FADE_MS);
      ctx.drawImage(layer[state.prevStage], ox, oy, W, H);
      if (t >= 1) state.prevStage = null;
    }
    ctx.globalAlpha = t;
    ctx.drawImage(layer[state.stage], ox, oy, W, H);
    ctx.globalAlpha = 1;
    if (t < 1) draw();

    const s = CELL * zoom;
    if (state.ov.grid && s >= 8) drawGrid(r, ox, oy, s);
    const zones = PR.zonesAt(r, state.stage);
    if (state.ov.paths) drawPaths(zones, ox, oy, s);
    if (state.ov.events) drawEvents(PR.eventsAt(r, state.stage), ox, oy, s);
    if (state.ov.areas) drawAreas(zones, ox, oy, s);
    if (state.hover && !state.hoverArea) {
      ctx.strokeStyle = "rgba(255,255,255,0.85)";
      ctx.lineWidth = 1;
      ctx.strokeRect(ox + state.hover.x * s + 0.5, oy + state.hover.y * s + 0.5, s - 1, s - 1);
    }
  }

  function drawGrid(r, ox, oy, s) {
    ctx.strokeStyle = "rgba(0,0,0,0.16)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = 0; x <= r.width; x++) { ctx.moveTo(ox + x * s + 0.5, oy); ctx.lineTo(ox + x * s + 0.5, oy + r.height * s); }
    for (let y = 0; y <= r.height; y++) { ctx.moveTo(ox, oy + y * s + 0.5); ctx.lineTo(ox + r.width * s, oy + y * s + 0.5); }
    ctx.stroke();
  }

  function drawPaths(zones, ox, oy, s) {
    const lw = Math.max(2, Math.min(6, s * 0.2));
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    for (const pass of [0, 1]) {
      for (const z of zones) {
        if (!PR.PATH_KINDS.includes(z.kind)) continue;
        ctx.setLineDash(z.kind === "hidden" ? [lw * 1.6, lw * 1.6] : z.kind === "trail" ? [lw * 0.5, lw * 1.8] : []);
        ctx.strokeStyle = pass === 0 ? "rgba(0,0,0,0.38)" : PR.PATH_COLORS[z.kind];
        ctx.lineWidth = pass === 0 ? lw + 2.5 : lw;
        for (const line of PR.polylines(z.cells)) {
          ctx.beginPath();
          line.forEach(([y, x], i) => {
            const px = ox + (x + 0.5) * s, py = oy + (y + 0.5) * s;
            if (i) ctx.lineTo(px, py); else ctx.moveTo(px, py);
          });
          ctx.stroke();
        }
      }
    }
    ctx.setLineDash([]);
  }

  function drawEvents(events, ox, oy, s) {
    const rad = Math.max(3.5, Math.min(7, s * 0.3));
    for (const e of events) {
      const cx = ox + (e.x + 0.5) * s, cy = oy + (e.y + 0.5) * s;
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = "#1b1a17";
      if (e.kind === "hidden_item") {
        // a tiny item ball: red top, white bottom
        ctx.beginPath(); ctx.arc(cx, cy, rad, Math.PI, 0); ctx.closePath(); ctx.fillStyle = "#e0453a"; ctx.fill();
        ctx.beginPath(); ctx.arc(cx, cy, rad, 0, Math.PI); ctx.closePath(); ctx.fillStyle = "#f5f2ea"; ctx.fill();
        ctx.beginPath(); ctx.arc(cx, cy, rad, 0, Math.PI * 2); ctx.moveTo(cx - rad, cy); ctx.lineTo(cx + rad, cy); ctx.stroke();
      } else if (e.kind === "cut_tree") {
        ctx.beginPath();
        ctx.moveTo(cx, cy - rad * 1.1); ctx.lineTo(cx + rad, cy + rad * 0.8); ctx.lineTo(cx - rad, cy + rad * 0.8); ctx.closePath();
        ctx.fillStyle = "#6fcf6a"; ctx.fill(); ctx.stroke();
      } else {
        ctx.beginPath();
        for (let i = 0; i < 6; i++) {
          const a = Math.PI / 6 + (i * Math.PI) / 3;
          const px = cx + Math.cos(a) * rad, py = cy + Math.sin(a) * rad;
          if (i) ctx.lineTo(px, py); else ctx.moveTo(px, py);
        }
        ctx.closePath(); ctx.fillStyle = "#b9b2a3"; ctx.fill(); ctx.stroke();
      }
    }
  }

  function drawAreas(zones, ox, oy, s) {
    ctx.font = `600 ${Math.round(Math.max(12, Math.min(16, s * 0.75)))}px "Pixelify Sans", monospace`;
    ctx.textBaseline = "middle";
    for (const z of zones) {
      if (z.kind !== "special" || !z.bbox) continue;
      const [x0, y0, x1, y1] = z.bbox;
      const hot = z === state.hoverArea || (state.highlight && state.highlight === z.area?.id);
      const X = ox + x0 * s, Y = oy + y0 * s, Wd = (x1 - x0) * s, Ht = (y1 - y0) * s;
      ctx.fillStyle = hot ? "rgba(240,169,74,0.16)" : "rgba(240,169,74,0.06)";
      ctx.fillRect(X, Y, Wd, Ht);
      ctx.strokeStyle = "rgba(0,0,0,0.45)";
      ctx.lineWidth = 3.5;
      ctx.strokeRect(X + 0.5, Y + 0.5, Wd, Ht);
      ctx.strokeStyle = hot ? "#ffd27a" : "#f0a94a";
      ctx.lineWidth = hot ? 2.5 : 1.75;
      ctx.strokeRect(X + 0.5, Y + 0.5, Wd, Ht);
      const label = z.name;
      const tw = ctx.measureText(label).width;
      const lh = Math.round(Math.max(12, Math.min(16, s * 0.75))) + 6;
      const ly = Y - lh - 2 < 0 ? Y + Ht + 2 : Y - lh - 2;
      ctx.fillStyle = hot ? "#ffd27a" : "rgba(28,26,22,0.86)";
      ctx.fillRect(X, ly, tw + 10, lh);
      ctx.fillStyle = hot ? "#1c1a16" : "#f6eedc";
      ctx.fillText(label, X + 5, ly + lh / 2 + 1);
    }
  }

  // ------------------------------------------------------------ timeline
  function buildTimeline() {
    const r = state.result;
    const scrub = $("scrub");
    scrub.max = r.stages.length - 1;
    const ticks = $("ticks");
    ticks.innerHTML = "";
    r.stages.forEach((st, i) => {
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = st.name;
      b.title = st.name;
      b.onclick = () => { stopPlay(); setStage(i, true); };
      li.appendChild(b);
      ticks.appendChild(li);
    });
  }

  function setStage(i, fade) {
    const r = state.result;
    if (!r) return;
    i = Math.max(0, Math.min(r.stages.length - 1, i));
    if (fade && i !== state.stage) {
      state.prevStage = state.stage;
      state.fadeStart = performance.now();
    } else {
      state.prevStage = null;
    }
    state.stage = i;
    $("scrub").value = i;
    $("stageNo").textContent = `stage ${i + 1} of ${r.stages.length}`;
    $("stageName").textContent = r.stages[i].name;
    [...$("ticks").querySelectorAll("button")].forEach((b, k) => {
      b.classList.toggle("on", k === i);
      b.classList.toggle("past", k < i);
    });
    renderAreas();
    draw();
  }

  function startPlay() {
    const r = state.result;
    if (!r) return;
    if (state.stage >= r.stages.length - 1) setStage(0, false);
    state.playing = true;
    $("play").classList.add("playing");
    $("play").setAttribute("aria-label", "Pause");
    clearInterval(state.timer);
    state.timer = setInterval(() => {
      if (state.stage >= state.result.stages.length - 1) return stopPlay();
      setStage(state.stage + 1, true);
    }, STAGE_MS);
  }
  function stopPlay() {
    state.playing = false;
    clearInterval(state.timer);
    $("play").classList.remove("playing");
    $("play").setAttribute("aria-label", "Play");
  }

  // ------------------------------------------------------------ info panel
  function renderInfo() {
    const r = state.result;
    const rep = r.report;
    const zc = rep.zones || {}, ec = rep.events || {};
    const rows = !rep.size ? [["size", `${r.width} x ${r.height}`]] : [
      ["size", `${rep.size[0]} x ${rep.size[1]}`],
      ["walkable cells", rep.walkable_cells],
      ["reachable", `${Math.round(rep.reachable_fraction * 100)}%`],
      ["branches", zc.branch || 0],
      ["loops", zc.loop || 0],
      ["dead ends", zc.dead_end || 0],
      ["clearings", zc.clearing || 0],
      ["hidden trails", zc.hidden || 0],
      ["hidden items", ec.hidden_item || 0],
      ["cut trees", ec.cut_tree || 0],
      ["tall grass cells", rep.classes.t],
      ["water cells", rep.classes.w],
      ["ledge cells", rep.classes.s],
    ];
    if (rep.size && ec.rock_smash) rows.push(["smashable rocks", ec.rock_smash]);
    const dl = $("stats");
    dl.innerHTML = "";
    for (const [k, v] of rows) {
      const dt = document.createElement("dt"); dt.textContent = k;
      const dd = document.createElement("dd"); dd.textContent = v;
      dl.append(dt, dd);
    }
    if (r.warnings && r.warnings.length) {
      const dt = document.createElement("dt"); dt.textContent = "warnings";
      const dd = document.createElement("dd"); dd.textContent = r.warnings.join("; ");
      dd.style.fontFamily = "inherit"; dd.style.fontSize = "12.5px"; dd.style.color = "var(--warn)"; dd.style.textAlign = "left";
      dl.append(dt, dd);
    }
    if (r.log && r.log.length) {
      const dt = document.createElement("dt"); dt.textContent = "notes";
      const dd = document.createElement("dd"); dd.textContent = r.log.join("; "); dd.style.fontFamily = "inherit"; dd.style.fontSize = "12.5px";
      dl.append(dt, dd);
    }
    $("download").disabled = !r.exportable;
    $("download").title = r.exportable ? "" : "Too big for the GBA map buffer";
    renderAreas();
    renderEnc();
  }

  function renderAreas() {
    const r = state.result;
    const ul = $("areas");
    ul.innerHTML = "";
    if (!r) return;
    const all = r.zones.filter((z) => z.kind === "special");
    const present = new Set(PR.zonesAt(r, state.stage).filter((z) => z.kind === "special"));
    if (!all.length) { ul.innerHTML = '<li class="muted">none placed</li>'; return; }
    for (const z of all) {
      const a = z.area || {};
      const li = document.createElement("li");
      if (state.highlight === a.id) li.className = "hl";
      const b = document.createElement("button");
      b.type = "button";
      const strong = document.createElement("strong"); strong.textContent = z.name;
      const sp = document.createElement("span"); sp.className = "sp";
      sp.textContent = (a.species || "no resident species") + (a.archetype ? ` · ${a.archetype}` : "") +
        (present.has(z) ? "" : " · not placed yet");
      const p = document.createElement("p"); p.textContent = a.description || "";
      b.append(strong, sp, p);
      b.onclick = () => {
        state.highlight = state.highlight === a.id ? null : a.id;
        if (state.highlight) {
          if (!present.has(z)) { stopPlay(); setStage(r.stages.length - 1, false); }
          centerOn(z.bbox);
        }
        renderAreas();
        draw();
      };
      li.appendChild(b);
      ul.appendChild(li);
    }
  }

  function encTable(rows, cols) {
    const t = document.createElement("table");
    t.className = "enc";
    t.innerHTML = `<thead><tr>${cols.map((c, i) => `<th${i ? ' class="n"' : ""}>${c}</th>`).join("")}</tr></thead>`;
    const tb = document.createElement("tbody");
    rows.forEach((cells) => {
      const tr = document.createElement("tr");
      cells.forEach((c, i) => {
        const td = document.createElement("td");
        if (i) td.className = "n";
        if (c instanceof Node) td.appendChild(c); else td.textContent = c;
        tr.appendChild(td);
      });
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    return t;
  }
  function nameCell(name, sub) {
    const f = document.createDocumentFragment();
    f.append(document.createTextNode(name));
    if (sub) { const s = document.createElement("span"); s.className = "types"; s.textContent = sub; f.append(s); }
    return f;
  }
  function pctCell(p) {
    const f = document.createDocumentFragment();
    const bar = document.createElement("span"); bar.className = "pct"; bar.style.width = Math.max(2, Math.round(p * 0.5)) + "px";
    f.append(bar, document.createTextNode(p >= 10 ? `${Math.round(p)}%` : `${p.toFixed(1)}%`));
    return f;
  }
  function heading(text) { const h = document.createElement("div"); h.className = "enc-h"; h.textContent = text; return h; }

  function renderEnc() {
    const box = $("enc");
    box.innerHTML = "";
    const r = state.result;
    if (!r) return;
    if (state.tab === "dex") {
      const enc = r.encounters || {};
      const rows = PR.encounterRows(enc.route);
      if (!rows.length) { box.innerHTML = '<p class="muted">no species for this biome</p>'; return; }
      box.appendChild(heading("tall grass"));
      box.appendChild(encTable(rows.map((x) => [nameCell(x.name, `${x.types.join(" / ")} · ${x.rarity}`), `${x.levels[0]}-${x.levels[1]}`, pctCell(x.pct)]),
        ["species", "lv", "share"]));
      const areaNames = {};
      r.zones.forEach((z) => { if (z.kind === "special" && z.area) areaNames[z.area.id] = z.name; });
      for (const [id, table] of Object.entries(enc.areas || {})) {
        box.appendChild(heading(areaNames[id] || id));
        box.appendChild(encTable(PR.encounterRows(table).map((x) => [nameCell(x.name, x.shiny_boost > 1 ? `shiny x${x.shiny_boost}` : x.rarity), `${x.levels[0]}-${x.levels[1]}`, pctCell(x.pct)]),
          ["species", "lv", "share"]));
      }
    } else {
      const em = r.emerald_encounters;
      if (!em) { box.innerHTML = '<p class="muted">no wild table</p>'; return; }
      const p = document.createElement("p");
      p.className = "hint";
      p.textContent = "Slot table for wild_encounters.json, levels clipped to the route's band.";
      box.appendChild(p);
      for (const [key, label, rates] of [["land_mons", "land", LAND_RATES], ["water_mons", "surfing", WATER_RATES]]) {
        if (!em[key]) continue;
        box.appendChild(heading(`${label} · rate ${em[key].encounter_rate}`));
        box.appendChild(encTable(PR.slotRows(em[key], rates).map((x) => [PR.prettySpecies(x.species), `${x.min}-${x.max}`, pctCell(x.pct)]),
          ["species", "lv", "slots"]));
      }
    }
  }

  async function download() {
    const r = state.result;
    if (!r) return;
    const btn = $("download");
    btn.disabled = true;
    try {
      const res = await fetch(`/api/export?id=${encodeURIComponent(r.id)}`);
      if (!res.ok) {
        let msg = res.statusText;
        try { msg = (await res.json()).error || msg; } catch (e) { /* not json */ }
        throw new Error(msg);
      }
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${r.route_id}_porymap.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    } catch (e) {
      showError(e.message);
    } finally {
      btn.disabled = !r.exportable;
    }
  }

  // ------------------------------------------------------------ legend / toggles
  function renderLegend() {
    const el = $("legend");
    const parts = [];
    if (state.layer === "classes") {
      for (const k of "fgptwrs") parts.push(`<span><i style="background:${PR.CLASS_COLORS[k]}"></i>${PR.CLASS_NAMES[k]}</span>`);
    }
    if (state.ov.paths) {
      const names = { main: "main road", branch: "branch", loop: "loop", dead_end: "dead end", hidden: "hidden trail", trail: "area trail" };
      for (const k of PR.PATH_KINDS) parts.push(`<span><i class="line" style="background:${PR.PATH_COLORS[k]}"></i>${names[k]}</span>`);
    }
    el.innerHTML = parts.join("");
  }

  function initToolbar() {
    document.querySelectorAll("[data-layer]").forEach((b) => {
      b.onclick = () => {
        state.layer = b.dataset.layer;
        document.querySelectorAll("[data-layer]").forEach((o) => o.classList.toggle("on", o === b));
        renderLegend();
        draw();
      };
    });
    document.querySelectorAll("[data-ov]").forEach((b) => {
      b.onclick = () => {
        const k = b.dataset.ov;
        state.ov[k] = !state.ov[k];
        b.classList.toggle("on", state.ov[k]);
        if (!state.ov.areas) { state.hoverArea = null; hideTip(); }
        renderLegend();
        draw();
      };
    });
    document.querySelectorAll(".tab").forEach((b) => {
      b.onclick = () => {
        state.tab = b.dataset.tab;
        document.querySelectorAll(".tab").forEach((o) => o.classList.toggle("on", o === b));
        renderEnc();
      };
    });
    $("zoomIn").onclick = () => zoomBy(1);
    $("zoomOut").onclick = () => zoomBy(-1);
    $("zoomFit").onclick = fit;
    $("play").onclick = () => (state.playing ? stopPlay() : startPlay());
    $("scrub").addEventListener("input", (ev) => { stopPlay(); setStage(parseInt(ev.target.value, 10), false); });
    $("download").onclick = download;
    document.addEventListener("keydown", (ev) => {
      if (ev.target.closest("input, select, textarea")) return;
      if (!state.result) return;
      if (ev.key === "ArrowRight") { stopPlay(); setStage(state.stage + 1, true); }
      else if (ev.key === "ArrowLeft") { stopPlay(); setStage(state.stage - 1, true); }
      else if (ev.key === " ") { ev.preventDefault(); state.playing ? stopPlay() : startPlay(); }
      else if (ev.key === "+" || ev.key === "=") zoomBy(1);
      else if (ev.key === "-") zoomBy(-1);
      else if (ev.key === "0") fit();
    });
    renderLegend();
  }

  // ------------------------------------------------------------ boot
  async function boot() {
    await loadSchema();
    initControls();
    initToolbar();
    new ResizeObserver(resize).observe(wrap);
    resize();
    loadOptions();
    try {
      await loadAtlas();
    } catch (e) {
      showError("could not load the tile atlas: " + e.message);
      return;
    }
    generate();
  }
  // for bench-paint.js (tile-level fixing on the finished route)
  window.PRBench = { state, draw, buildCanvases, setStage, stopPlay, fetchJSON, CELL };
  boot();
})();
