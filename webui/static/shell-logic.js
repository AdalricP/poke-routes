// Pure helpers for the editor shell (shell.js, bench.js) and the node tests: keyboard
// shortcuts, menu lookup, dock layout + persistence, undo history, recent list and
// spec-file conversion. No DOM in here.
(function (root) {
  "use strict";

  // ------------------------------------------------------------ platform / shortcuts
  function isMacPlatform(nav) {
    nav = nav || {};
    const p = (nav.userAgentData && nav.userAgentData.platform) || nav.platform || nav.userAgent || "";
    return /Mac|iPhone|iPad|iPod/i.test(p);
  }

  // "Mod+Shift+G" -> {mod, ctrl, shift, alt, key}. Mod is Cmd on macOS, Ctrl elsewhere;
  // Ctrl is the literal Control key (only used where it is the platform convention).
  function parseShortcut(s) {
    const out = { mod: false, ctrl: false, shift: false, alt: false, key: "" };
    if (!s) return null;
    const parts = String(s).split("+");
    // "Mod+=" / "Mod++" style: a trailing empty part means the key is "+"
    let key = parts.pop();
    if (key === "" && parts.length) { parts.pop(); key = "+"; }
    for (const p of parts) {
      const k = p.toLowerCase();
      if (k === "mod") out.mod = true;
      else if (k === "ctrl") out.ctrl = true;
      else if (k === "shift") out.shift = true;
      else if (k === "alt") out.alt = true;
    }
    out.key = key;
    return out;
  }

  const MAC_SYM = { Shift: "⇧", Alt: "⌥", Mod: "⌘", Ctrl: "⌃" };
  const KEY_NAMES = {
    ArrowLeft: "←", ArrowRight: "→", ArrowUp: "↑", ArrowDown: "↓",
    Space: "Space", Escape: "Esc", Enter: "Enter", Delete: "Del", Home: "Home", End: "End",
  };

  function keyLabel(key) {
    if (KEY_NAMES[key]) return KEY_NAMES[key];
    return key.length === 1 ? key.toUpperCase() : key;
  }

  // Menu label for a shortcut: "⇧⌘G" on macOS, "Ctrl+Shift+G" elsewhere.
  function formatShortcut(s, mac) {
    const p = typeof s === "string" ? parseShortcut(s) : s;
    if (!p) return "";
    const k = keyLabel(p.key);
    if (mac) {
      return (p.ctrl ? MAC_SYM.Ctrl : "") + (p.alt ? MAC_SYM.Alt : "") + (p.shift ? MAC_SYM.Shift : "") +
        (p.mod ? MAC_SYM.Mod : "") + k;
    }
    const mods = [];
    if (p.mod || p.ctrl) mods.push("Ctrl");
    if (p.shift) mods.push("Shift");
    if (p.alt) mods.push("Alt");
    mods.push(k);
    return mods.join("+");
  }

  // Key of a keyboard event, layout independent for letters/digits (Alt on macOS turns
  // "g" into "©", so letters and digits come from ev.code).
  function eventKey(ev) {
    const code = ev.code || "";
    if (/^Key[A-Z]$/.test(code)) return code.slice(3).toLowerCase();
    if (/^Digit[0-9]$/.test(code)) return code.slice(5);
    if (code === "Equal") return "=";
    if (code === "Minus") return "-";
    if (code === "Comma") return ",";
    if (code === "Slash") return "/";
    if (code === "Quote") return "'";
    if (code === "Space" || ev.key === " ") return "Space";
    const k = ev.key || "";
    return k.length === 1 ? k.toLowerCase() : k;
  }

  function matchShortcut(ev, s, mac) {
    const p = typeof s === "string" ? parseShortcut(s) : s;
    if (!p) return false;
    const wantMeta = mac ? p.mod : false;
    const wantCtrl = mac ? p.ctrl : p.mod || p.ctrl;
    if (!!ev.metaKey !== wantMeta || !!ev.ctrlKey !== wantCtrl || !!ev.altKey !== p.alt) return false;
    const k = eventKey(ev);
    const want = p.key.length === 1 ? p.key.toLowerCase() : p.key;
    // "?" and "+" are typed with Shift on most layouts: don't demand an exact Shift match
    if (want === "?" || want === "+") return ev.key === want;
    if (!!ev.shiftKey !== p.shift) return false;
    return k === want;
  }

  // Where keyboard focus is, as far as shortcuts care.
  //   "text"    typing goes into it (text inputs, textareas, contenteditable)
  //   "control" other form controls (sliders, selects, checkboxes, buttons in forms)
  //   "none"    the page, a canvas, a panel
  function focusKind(el) {
    if (!el || !el.tagName) return "none";
    const tag = el.tagName.toLowerCase();
    if (el.isContentEditable) return "text";
    if (tag === "textarea") return "text";
    if (tag === "input") {
      const t = (el.type || "text").toLowerCase();
      if (["checkbox", "radio", "range", "button", "submit", "reset", "color", "file"].includes(t)) return "control";
      return "text";
    }
    if (tag === "select") return "control";
    return "none";
  }

  // The text-editing shortcuts a text field owns (native undo, copy, select all...).
  const TEXT_OWNED = ["Mod+z", "Mod+Shift+z", "Mod+y", "Ctrl+y", "Mod+a", "Mod+c", "Mod+v", "Mod+x"];

  // May a shortcut fire with focus of this kind?
  //   - plain keys (no Mod/Ctrl/Alt) only fire when focus is on the page, never in fields;
  //   - text-editing shortcuts belong to the text field;
  //   - item.field === false: blocked in text fields (e.g. Generate, so typing never regenerates).
  function allowedIn(item, kind) {
    const p = parseShortcut(item.shortcut);
    if (!p) return false;
    const plain = !p.mod && !p.ctrl && !p.alt;
    if (plain) return kind === "none";
    if (kind === "text") {
      if (item.field === false) return false;
      const norm = (x) => x.toLowerCase();
      if (TEXT_OWNED.map(norm).includes(norm(item.shortcut))) return false;
    }
    return true;
  }

  // Flatten a menu tree to its leaf items (with their menu path).
  function flattenMenus(menus) {
    const out = [];
    const walk = (items, path) => {
      for (const it of items || []) {
        if (!it || it.sep) continue;
        if (typeof it.items === "function") continue; // dynamic submenus (Recent, Stages) carry no shortcuts
        if (it.items) walk(it.items, path.concat(it.label));
        else out.push(Object.assign({ path: path.concat(it.label) }, it));
        if (it.alt) for (const a of [].concat(it.alt)) out.push(Object.assign({ path: path.concat(it.label) }, it, { shortcut: a }));
      }
    };
    for (const m of menus) walk(m.items, [m.label]);
    return out;
  }

  // The item a key event triggers, or null. `extra` are shortcuts not shown in menus.
  function findShortcut(menus, ev, kind, mac, extra) {
    const items = flattenMenus(menus).concat(extra || []);
    for (const it of items) {
      if (!it.shortcut) continue;
      if (!matchShortcut(ev, it.shortcut, mac)) continue;
      if (!allowedIn(it, kind)) continue;
      if (typeof it.enabled === "function" && !it.enabled()) continue;
      return it;
    }
    return null;
  }

  // Two items may not share a shortcut (tests use this to keep the menus honest).
  function shortcutClashes(menus, extra) {
    const seen = new Map();
    const clashes = [];
    for (const it of flattenMenus(menus).concat(extra || [])) {
      if (!it.shortcut) continue;
      const p = parseShortcut(it.shortcut);
      const k = [p.mod, p.ctrl, p.shift, p.alt, p.key.toLowerCase()].join("|");
      if (seen.has(k) && seen.get(k) !== it.id) clashes.push([seen.get(k), it.id, it.shortcut]);
      else seen.set(k, it.id);
    }
    return clashes;
  }

  // ------------------------------------------------------------ the shortcut table
  // One place for every key binding; bench.js attaches them to its menu items by id.
  // Mod = Cmd on macOS, Ctrl elsewhere. Browser-reserved combos (Cmd/Ctrl+N, T, W, 1-9)
  // are avoided because a page can't override them.
  const SHORTCUTS = {
    // File
    new: "Mod+Alt+n", open: "Mod+o", save: "Mod+s", saveas: "Mod+Shift+s", "exp-zip": "Mod+e", "exp-png": "Mod+Shift+e",
    // Edit
    undo: "Mod+z", redo: "Mod+Shift+z", deselect: "Escape", prefs: "Mod+,",
    // View
    zin: "Mod+=", zout: "Mod+-", zfit: "Mod+0", frame: "f",
    "ov-paths": "Alt+1", "ov-areas": "Alt+2", "ov-items": "Alt+3", "ov-classes": "Alt+4", "ov-grid": "Alt+5",
    // Generate
    gen: "Mod+g", regen: "Mod+Shift+g", play: "Space", next: "ArrowRight", prev: "ArrowLeft", first: "Home", last: "End",
    // Help
    keys: "Mod+/",
    // not in a menu: scene tools and plain-key zoom
    "tool-select": "v", "tool-pan": "h", "tool-paint": "b", "tool-stamp": "s", "x-zin": "=", "x-zout": "-", "x-zfit": "0", "x-help": "?",
  };
  const ALT_SHORTCUTS = { redo: "Mod+y", zin: "Mod+Shift+=" };
  // Modifier shortcuts that still must not fire while typing (Generate never runs from a text field).
  const NOT_IN_FIELDS = ["gen", "regen", "ov-paths", "ov-areas", "ov-items", "ov-classes", "ov-grid"];

  // Fill shortcut / alt / field into menu items (and extra key items) by id.
  function attachShortcuts(menus, extra) {
    const fix = (it) => {
      if (!it || it.sep || it.head) return;
      if (it.id && SHORTCUTS[it.id]) it.shortcut = SHORTCUTS[it.id];
      if (it.id && ALT_SHORTCUTS[it.id]) it.alt = ALT_SHORTCUTS[it.id];
      if (it.id && NOT_IN_FIELDS.includes(it.id)) it.field = false;
      if (Array.isArray(it.items)) it.items.forEach(fix);
    };
    menus.forEach((m) => (m.items || []).forEach(fix));
    (extra || []).forEach(fix);
    return menus;
  }

  // ------------------------------------------------------------ dock layout
  const LAYOUT_KEY = "pokeroutes-shell-layout-v1";
  const CHROME = { menu: 26, toolbar: 34, status: 22, collapsed: 24, head: 26, splitter: 4 };
  const DEFAULT_LAYOUT = {
    v: 1,
    left: { size: 240, open: true, float: null },
    right: { size: 320, open: true, float: null },
    bottom: { size: 180, open: true, tab: "console", float: null },   // float: {x, y, w, h} when torn off
    center: "scene",
    inspector: {},   // section id -> collapsed (true)
  };
  const BOTTOM_TABS = ["console", "timeline", "improve"];
  const CENTER_TABS = ["scene", "region", "world"];

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  function defaultLayout() { return JSON.parse(JSON.stringify(DEFAULT_LAYOUT)); }

  // Repair anything a stale or hand-edited localStorage entry might hold.
  function sanitizeLayout(x) {
    const d = defaultLayout();
    if (!x || typeof x !== "object" || x.v !== 1) return d;
    for (const k of ["left", "right", "bottom"]) {
      const s = x[k];
      if (!s || typeof s !== "object") continue;
      if (Number.isFinite(s.size)) d[k].size = clamp(Math.round(s.size), 120, 2000);
      if (typeof s.open === "boolean") d[k].open = s.open;
      d[k].float = sanitizeFloat(s.float);
    }
    if (x.bottom && BOTTOM_TABS.includes(x.bottom.tab)) d.bottom.tab = x.bottom.tab;
    if (CENTER_TABS.includes(x.center)) d.center = x.center;
    if (x.inspector && typeof x.inspector === "object") {
      for (const [k, v] of Object.entries(x.inspector)) if (typeof v === "boolean" && /^[\w-]{1,40}$/.test(k)) d.inspector[k] = v;
    }
    return d;
  }

  function sanitizeFloat(f) {
    if (!f || typeof f !== "object") return null;
    if (![f.x, f.y, f.w, f.h].every(Number.isFinite)) return null;
    return { x: Math.round(f.x), y: Math.round(f.y), w: clamp(Math.round(f.w), 120, 4000), h: clamp(Math.round(f.h), 60, 4000) };
  }

  function loadLayout(storage) {
    try {
      const raw = storage && storage.getItem(LAYOUT_KEY);
      return sanitizeLayout(raw ? JSON.parse(raw) : null);
    } catch (e) {
      return defaultLayout();
    }
  }

  function saveLayout(storage, layout) {
    try {
      storage.setItem(LAYOUT_KEY, JSON.stringify(sanitizeLayout(layout)));
      return true;
    } catch (e) {
      return false; // private mode / blocked storage: the layout just isn't remembered
    }
  }

  // Pixel rects of every region for a viewport. Inspector runs the full body height on
  // the right (Unity's default layout); the bottom dock spans hierarchy + scene.
  // Sizes are clamped so the scene view keeps at least MIN_CENTER x MIN_CENTER_H.
  // A floating (torn-off) dock gives its space back to the scene and is kept on screen.
  const MIN_CENTER = 320, MIN_CENTER_H = 160, MIN_SIDE = 160, MIN_BOTTOM = 90;
  function computeRects(layout, vw, vh, chrome) {
    const c = Object.assign({}, CHROME, chrome || {});
    const L = sanitizeLayout(layout);
    const top = c.menu + c.toolbar;
    const bodyH = Math.max(0, vh - top - c.status);
    const fl = { left: !!L.left.float, right: !!L.right.float, bottom: !!L.bottom.float };
    const spL = fl.left ? 0 : c.splitter, spR = fl.right ? 0 : c.splitter, spB = fl.bottom ? 0 : c.splitter;

    let rw = fl.right ? 0 : L.right.open ? clamp(L.right.size, MIN_SIDE, Math.max(MIN_SIDE, vw * 0.5)) : c.collapsed;
    let lw = fl.left ? 0 : L.left.open ? clamp(L.left.size, MIN_SIDE, Math.max(MIN_SIDE, vw * 0.4)) : c.collapsed;
    // keep the centre usable: shrink the side docks (right first) if needed
    const spare = vw - lw - rw - spL - spR - MIN_CENTER;
    if (spare < 0) {
      if (L.right.open && !fl.right) { const cut = Math.min(-spare, rw - MIN_SIDE); rw -= cut; }
      const spare2 = vw - lw - rw - spL - spR - MIN_CENTER;
      if (spare2 < 0 && L.left.open && !fl.left) lw -= Math.min(-spare2, lw - MIN_SIDE);
    }
    const midX = lw + spL, midW = Math.max(0, vw - lw - rw - spL - spR);
    const rightX = vw - rw;

    let bh = fl.bottom ? 0 : L.bottom.open ? clamp(L.bottom.size, MIN_BOTTOM, Math.max(MIN_BOTTOM, bodyH - MIN_CENTER_H - spB)) : c.head;
    bh = Math.min(bh, bodyH);
    const upperH = Math.max(0, bodyH - bh - spB);
    const bottomY = top + upperH + spB;

    const r = (x, y, w, h) => ({ x: Math.round(x), y: Math.round(y), w: Math.max(0, Math.round(w)), h: Math.max(0, Math.round(h)) });
    const floatRect = (f) => {
      const w = Math.min(f.w, vw), h = Math.min(f.h, bodyH);
      return r(clamp(f.x, 0, vw - w), clamp(f.y, top, top + bodyH - h), w, h);
    };
    return {
      menu: r(0, 0, vw, c.menu),
      toolbar: r(0, c.menu, vw, c.toolbar),
      left: fl.left ? floatRect(L.left.float) : r(0, top, lw, upperH),
      center: r(midX, top, midW, upperH),
      right: fl.right ? floatRect(L.right.float) : r(rightX, top, rw, bodyH),
      bottom: fl.bottom ? floatRect(L.bottom.float) : r(0, bottomY, rightX - spR, bh),
      status: r(0, vh - c.status, vw, c.status),
      splitLeft: r(lw, top, spL, upperH),
      splitRight: r(rightX - spR, top, spR, bodyH),
      splitBottom: r(0, top + upperH, rightX - spR, spB),
      collapsed: { left: !L.left.open && !fl.left, right: !L.right.open && !fl.right, bottom: !L.bottom.open && !fl.bottom },
      floating: fl,
    };
  }

  // Dropping a floating dock within DOCK_SNAP px of its home edge docks it again.
  const DOCK_SNAP = 48;
  function nearHomeEdge(which, px, py, vw, vh, chrome) {
    const c = Object.assign({}, CHROME, chrome || {});
    if (which === "left") return px < DOCK_SNAP;
    if (which === "right") return px > vw - DOCK_SNAP;
    return py > vh - c.status - DOCK_SNAP; // bottom
  }

  // New size of a dock after dragging its splitter by (dx, dy) from `start`.
  function dragSize(which, start, dx, dy) {
    if (which === "left") return Math.round(start + dx);
    if (which === "right") return Math.round(start - dx);
    return Math.round(start - dy); // bottom
  }

  // ------------------------------------------------------------ undo history
  // Generic command stack. Entries are {kind, before, after, key?, t?, ...}; the caller
  // applies entry.before on undo and entry.after on redo. Consecutive entries with the
  // same `key` within `mergeMs` coalesce (dragging a slider is one undo step).
  function History(limit, mergeMs) {
    this.limit = limit || 100;
    this.mergeMs = mergeMs == null ? 800 : mergeMs;
    this.past = [];
    this.future = [];
  }
  History.prototype.push = function (e) {
    if (e.kind === "spec" && JSON.stringify(e.before) === JSON.stringify(e.after)) return false;
    const last = this.past[this.past.length - 1];
    if (last && e.key && last.key === e.key && e.t != null && last.t != null && e.t - last.t <= this.mergeMs) {
      last.after = e.after;
      last.t = e.t;
      if (JSON.stringify(last.before) === JSON.stringify(last.after)) this.past.pop();
    } else {
      this.past.push(e);
      if (this.past.length > this.limit) this.past.shift();
    }
    this.future = [];
    return true;
  };
  History.prototype.undo = function () {
    const e = this.past.pop();
    if (e) this.future.push(e);
    return e || null;
  };
  History.prototype.redo = function () {
    const e = this.future.pop();
    if (e) this.past.push(e);
    return e || null;
  };
  History.prototype.canUndo = function () { return this.past.length > 0; };
  History.prototype.canRedo = function () { return this.future.length > 0; };
  History.prototype.peekUndo = function () { return this.past[this.past.length - 1] || null; };
  History.prototype.peekRedo = function () { return this.future[this.future.length - 1] || null; };
  // Drop entries that no longer apply (paint strokes on a route that was regenerated).
  History.prototype.filter = function (keep) {
    this.past = this.past.filter(keep);
    this.future = this.future.filter(keep);
  };

  // ------------------------------------------------------------ recent routes
  const RECENT_KEY = "pokeroutes-shell-recent-v1";
  function recentLabel(p) {
    return `${p.name || "Route"} · seed ${p.seed} · ${p.width}×${p.height}${p.biome ? " " + p.biome : ""}`;
  }
  function pushRecent(list, params, max, now) {
    max = max || 10;
    const key = (p) => `${p.name}|${p.seed}|${p.width}|${p.height}|${p.biome}`;
    const entry = { label: recentLabel(params), params: JSON.parse(JSON.stringify(params)), t: now || 0 };
    const out = [entry].concat((list || []).filter((e) => e && e.params && key(e.params) !== key(params)));
    return out.slice(0, max);
  }

  // ------------------------------------------------------------ spec files
  // A spec file (the CLI's YAML spec, a region route spec, or a spec saved from here)
  // -> the bench's params. Unknown keys pass through so new generator fields keep working.
  const PASS = ["biome", "meander", "branches", "loops", "dead_ends", "clearings", "lakes", "tall_grass",
    "hidden_paths", "hidden_items", "max_lanes", "dead_end_chance", "ledge_chance", "sections",
    "merge_chance", "extra_exits", "levels", "road"];
  function specToParams(spec, defaults) {
    spec = spec || {};
    if (spec.params && typeof spec.params === "object" && spec.format === "poke-routes-spec") spec = spec.params;
    const p = Object.assign({}, defaults || {});
    for (const [k, v] of Object.entries(spec)) {
      if (["size", "exits", "special_areas", "region", "endings", "id", "species_file", "tileset", "format"].includes(k)) continue;
      if (v === null || typeof v === "object" && !Array.isArray(v)) continue;
      p[k] = v;
    }
    for (const k of PASS) if (spec[k] !== undefined) p[k] = spec[k];
    if (Array.isArray(spec.size) && spec.size.length === 2) { p.width = +spec.size[0]; p.height = +spec.size[1]; }
    if (spec.width != null) p.width = +spec.width;
    if (spec.height != null) p.height = +spec.height;
    if (spec.name) p.name = String(spec.name);
    if (spec.seed != null) p.seed = Math.max(0, parseInt(spec.seed, 10) || 0);
    if (Array.isArray(spec.special_areas)) p.special_areas = spec.special_areas.map(String).slice(0, 6);
    if (Array.isArray(spec.exits)) {
      p.exits = spec.exits.slice(0, 4).map((e) => {
        const o = { side: e.side || "south", at: e.at != null ? +e.at : 0.5, width: e.width != null ? +e.width : 2 };
        if (e.to) o.to = String(e.to);
        return o;
      });
    }
    // the bench needs two exits; a dead end gets a stand-in exit on its goal side
    if (p.exits && p.exits.length < 2) {
      const g = spec.region && spec.region.goal;
      p.exits.push({ side: g ? g.side : "north", at: 0.5, width: 2 });
    }
    const region = spec.region && typeof spec.region === "object" ? { name: p.name, region: spec.region, endings: spec.endings || [] } : null;
    return { params: p, region };
  }

  // What File > Save writes: the params plus the region block they were opened with.
  function paramsToSpecFile(params, region) {
    const out = { format: "poke-routes-spec", version: 1, params: JSON.parse(JSON.stringify(params)) };
    if (region && region.region) { out.region = region.region; if (region.endings && region.endings.length) out.endings = region.endings; }
    return out;
  }

  function safeFileName(s, ext) {
    const base = String(s || "route").replace(/[^A-Za-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "") || "route";
    return base + ext;
  }

  // ------------------------------------------------------------ stamps (paint tool)
  // Offsets are [dx, dy, class] relative to the clicked cell.
  const STAMPS = {
    tree: { label: "Tree block 2×2", cells: [[0, 0, "f"], [1, 0, "f"], [0, 1, "f"], [1, 1, "f"]] },
    pond: { label: "Pond 3×3", cells: square(3, "w") },
    grass: { label: "Tall grass 3×3", cells: square(3, "t") },
    clearing: { label: "Clearing 3×3", cells: square(3, "g") },
    rock: { label: "Rock", cells: [[0, 0, "r"]] },
  };
  function square(n, k) {
    const out = [];
    const o = Math.floor(n / 2);
    for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) out.push([x - o, y - o, k]);
    return out;
  }
  // Cells a brush of `size` covers around (x, y), clipped to the map.
  function brushCells(x, y, size, k, W, H) {
    const out = [];
    const o = Math.floor((size - 1) / 2);
    for (let dy = 0; dy < size; dy++) for (let dx = 0; dx < size; dx++) {
      const cx = x + dx - o, cy = y + dy - o;
      if (cx >= 0 && cy >= 0 && cx < W && cy < H) out.push([cx, cy, k]);
    }
    return out;
  }
  // Cells on the straight line from a to b (inclusive), so a quick stroke leaves no gaps.
  function lineCells(a, b) {
    const out = [];
    const n = Math.max(Math.abs(b.x - a.x), Math.abs(b.y - a.y));
    for (let k = 0; k <= n; k++) out.push({ x: Math.round(a.x + ((b.x - a.x) * k) / (n || 1)), y: Math.round(a.y + ((b.y - a.y) * k) / (n || 1)) });
    return out;
  }
  // Painted cells are remembered per route name + size ({"x,y": class}) and put back
  // after every regenerate; same storage format as the first bench paint mode.
  const PAINT_KEY = "pokeroutes-paint-v1";
  const paintKey = (r) => `${r.name}|${r.width}x${r.height}`;
  function mergePaint(map, cells) {
    const out = Object.assign({}, map || {});
    for (const [x, y, k] of cells) out[`${x},${y}`] = k;
    return out;
  }
  function paintList(map) {
    return Object.entries(map || {}).map(([k, c]) => { const [x, y] = k.split(",").map(Number); return [x, y, c]; });
  }
  function stampCells(name, x, y, W, H) {
    const s = STAMPS[name];
    if (!s) return [];
    return s.cells.map(([dx, dy, k]) => [x + dx, y + dy, k]).filter(([cx, cy]) => cx >= 0 && cy >= 0 && cx < W && cy < H);
  }

  const api = {
    isMacPlatform, parseShortcut, formatShortcut, eventKey, matchShortcut, focusKind, allowedIn,
    flattenMenus, findShortcut, shortcutClashes, SHORTCUTS, ALT_SHORTCUTS, NOT_IN_FIELDS, attachShortcuts,
    LAYOUT_KEY, CHROME, BOTTOM_TABS, CENTER_TABS, defaultLayout, sanitizeLayout, loadLayout, saveLayout,
    computeRects, dragSize, nearHomeEdge, MIN_CENTER, MIN_SIDE,
    History, RECENT_KEY, pushRecent, recentLabel, specToParams, paramsToSpecFile, safeFileName,
    STAMPS, brushCells, stampCells, lineCells, PAINT_KEY, paintKey, mergePaint, paintList,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SL = api;
})(typeof window !== "undefined" ? window : globalThis);
