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
    "ov-areas": "Alt+2", "ov-items": "Alt+3", "ov-classes": "Alt+4", "ov-grid": "Alt+5",
    // Generate (no key for Generate itself: the toolbar button is it)
    play: "Space", next: "ArrowRight", prev: "ArrowLeft", first: "Home", last: "End",
    // Help
    keys: "Mod+/",
    // not in a menu: scene tools and plain-key zoom
    "tool-select": "v", "tool-pan": "h", "tool-paint": "b", "x-zin": "=", "x-zout": "-", "x-zfit": "0", "x-help": "?",
    "x-rotate": "r", "tool-item": "i", "tool-people": "p", "x-item-delete": "Delete", "x-brush-smaller": "[", "x-brush-bigger": "]",
  };
  const ALT_SHORTCUTS = { redo: "Mod+y", zin: "Mod+Shift+=" };
  // Modifier shortcuts that still must not fire while typing.
  const NOT_IN_FIELDS = ["ov-areas", "ov-items", "ov-classes", "ov-grid"];

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

  // ------------------------------------------------------------ views
  const CENTER_TABS = ["scene", "geography", "region"];
  const ROUTES_VIEW_TAB = "scene";

  function is_region_editor_tab(center_tab) {
    const is_region_tab = CENTER_TABS.includes(center_tab) && center_tab !== ROUTES_VIEW_TAB;
    return is_region_tab;
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
        if (e.offset != null) o.offset = +e.offset;
        return o;
      });
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

  // ------------------------------------------------------------ region connections
  // Which place each Scene exit leads to, read from the region document the route came
  // from: [{name, kind: "route" | "town", route, town} | null] in exit order.
  function name_from_map_constant(map_constant) {
    const words = String(map_constant || "").replace(/^MAP_/, "").toLowerCase().split("_").filter(Boolean);
    return words.map((word) => word.replace(/^([a-z]+)(\d+)$/, "$1 $2")).map((word) => word[0].toUpperCase() + word.slice(1)).join(" ");
  }
  function region_exit_for(scene_exit, region_exits, used_region_exits) {
    const matches_side_and_map = (region_exit) => region_exit.to_map === scene_exit.to && region_exit.side === scene_exit.side;
    const matches_map = (region_exit) => region_exit.to_map === scene_exit.to;
    const unused = region_exits.filter((region_exit) => !used_region_exits.has(region_exit));
    return unused.find(matches_side_and_map) || unused.find(matches_map) || null;
  }
  function chain_maps_of_document(region_document) {
    const chain_maps = (region_document.routes || []).flatMap((route) => route.chain || []);
    return chain_maps;
  }
  function region_route_named(region_document, route_name) {
    if (!region_document) return null;
    const chain_map = chain_maps_of_document(region_document).find((candidate) => candidate.name === route_name);
    const region_route = chain_map || (region_document.routes || []).find((candidate) => candidate.name === route_name) || null;
    return region_route;
  }
  function chain_map_with_constant(region_document, map_constant) {
    if (!region_document) return null;
    const chain_map = chain_maps_of_document(region_document).find((candidate) => candidate.map === map_constant) || null;
    return chain_map;
  }
  function chain_of_map(region_document, map_name) {
    if (!region_document) return [];
    const route_holding_map = (region_document.routes || []).find((route) => (route.chain || []).some((chain_map) => chain_map.name === map_name));
    const chain = route_holding_map && route_holding_map.chain.length > 1 ? route_holding_map.chain : [];
    return chain;
  }
  function chain_map_placement(chain_map, chain_left_in_region_cells, chain_top_in_region_cells, metatiles_per_region_cell) {
    const [x0, y0, x1, y1] = chain_map.rect;
    const placement = {
      name: chain_map.name,
      offset_x_in_metatiles: (x0 - chain_left_in_region_cells) * metatiles_per_region_cell,
      offset_y_in_metatiles: (y0 - chain_top_in_region_cells) * metatiles_per_region_cell,
      width_in_metatiles: (x1 - x0) * metatiles_per_region_cell,
      height_in_metatiles: (y1 - y0) * metatiles_per_region_cell,
    };
    return placement;
  }
  function chain_layout(chain_maps, metatiles_per_region_cell) {
    const chain_left_in_region_cells = Math.min(...chain_maps.map((chain_map) => chain_map.rect[0]));
    const chain_top_in_region_cells = Math.min(...chain_maps.map((chain_map) => chain_map.rect[1]));
    const placements = chain_maps.map((chain_map) => chain_map_placement(chain_map, chain_left_in_region_cells, chain_top_in_region_cells, metatiles_per_region_cell));
    const layout = {
      maps: placements,
      width_in_metatiles: Math.max(...placements.map((placement) => placement.offset_x_in_metatiles + placement.width_in_metatiles)),
      height_in_metatiles: Math.max(...placements.map((placement) => placement.offset_y_in_metatiles + placement.height_in_metatiles)),
    };
    return layout;
  }
  function is_inside_chain_map(placement, world_x_in_metatiles, world_y_in_metatiles) {
    const is_inside = world_x_in_metatiles >= placement.offset_x_in_metatiles && world_x_in_metatiles < placement.offset_x_in_metatiles + placement.width_in_metatiles &&
      world_y_in_metatiles >= placement.offset_y_in_metatiles && world_y_in_metatiles < placement.offset_y_in_metatiles + placement.height_in_metatiles;
    return is_inside;
  }
  function chain_map_index_at(layout, world_x_in_metatiles, world_y_in_metatiles) {
    const map_index = layout.maps.findIndex((placement) => is_inside_chain_map(placement, world_x_in_metatiles, world_y_in_metatiles));
    return map_index;
  }
  function shared_edge_between(placement, other_placement) {
    const right = placement.offset_x_in_metatiles + placement.width_in_metatiles, bottom = placement.offset_y_in_metatiles + placement.height_in_metatiles;
    const other_right = other_placement.offset_x_in_metatiles + other_placement.width_in_metatiles, other_bottom = other_placement.offset_y_in_metatiles + other_placement.height_in_metatiles;
    const overlap_top = Math.max(placement.offset_y_in_metatiles, other_placement.offset_y_in_metatiles), overlap_bottom = Math.min(bottom, other_bottom);
    const overlap_left = Math.max(placement.offset_x_in_metatiles, other_placement.offset_x_in_metatiles), overlap_right = Math.min(right, other_right);
    if (right === other_placement.offset_x_in_metatiles && overlap_bottom > overlap_top) return { x0: right, y0: overlap_top, x1: right, y1: overlap_bottom };
    if (bottom === other_placement.offset_y_in_metatiles && overlap_right > overlap_left) return { x0: overlap_left, y0: bottom, x1: overlap_right, y1: bottom };
    return null;
  }
  function chain_seam_segments(layout) {
    const seam_segments = layout.maps.flatMap((placement) => layout.maps.map((other_placement) => shared_edge_between(placement, other_placement))).filter(Boolean);
    return seam_segments;
  }
  function chain_map_offset_between(layout, from_map_index, to_map_index) {
    const from_placement = layout.maps[from_map_index], to_placement = layout.maps[to_map_index];
    const offset_in_metatiles = {
      x: to_placement.offset_x_in_metatiles - from_placement.offset_x_in_metatiles,
      y: to_placement.offset_y_in_metatiles - from_placement.offset_y_in_metatiles,
    };
    return offset_in_metatiles;
  }
  function chain_maps_of_route(route) {
    const chain_maps = route.chain && route.chain.length ? route.chain : [{ name: route.name, id: route.id, map: route.map, rect: route.rect, exits: route.exits || [] }];
    return chain_maps;
  }
  function chain_maps_named_after_their_route(route) {
    const chain_maps = chain_maps_of_route(route);
    const named_chain_maps = chain_maps.map((chain_map, map_index) => Object.assign({}, chain_map, chain_map_names(route, map_index, chain_maps.length)));
    return named_chain_maps;
  }
  function chain_map_names(route, map_index, map_count) {
    if (map_count === 1) return { name: route.name, id: route.id };
    const names = { name: `${route.name} (${map_index + 1}/${map_count})`, id: `${route.id}_${map_index + 1}` };
    return names;
  }
  function centre_of_rect(rect) {
    const centre = [(rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2];
    return centre;
  }
  function is_point_inside_rect(point, rect) {
    const inside = point[0] >= rect[0] && point[0] < rect[2] && point[1] >= rect[1] && point[1] < rect[3];
    return inside;
  }
  function distance_between_rect_centres(rect, other_rect) {
    const [centre_x, centre_y] = centre_of_rect(rect), [other_centre_x, other_centre_y] = centre_of_rect(other_rect);
    const distance = Math.hypot(centre_x - other_centre_x, centre_y - other_centre_y);
    return distance;
  }
  function chain_map_nearest_to(route, rect) {
    const by_distance = (first_map, second_map) => distance_between_rect_centres(first_map.rect, rect) - distance_between_rect_centres(second_map.rect, rect);
    const nearest_chain_map = chain_maps_of_route(route).slice().sort(by_distance)[0];
    return nearest_chain_map;
  }
  function route_with_a_map_over(routes, point) {
    const route_over_point = routes.find((route) => chain_maps_of_route(route).some((chain_map) => is_point_inside_rect(point, chain_map.rect))) || null;
    return route_over_point;
  }
  // Which route and chain map the Routes view should show after the Map changed: the same route (found by name,
  // else by the route now painted over the open map's square) and in it the open map's square, else its nearest map.
  function sync_target_for_open_map(region_document, open_map) {
    const routes = (region_document && region_document.routes) || [];
    const centre = centre_of_rect(open_map.rect);
    const route = routes.find((candidate) => candidate.name === open_map.route_name) || route_with_a_map_over(routes, centre);
    if (!route) return null;
    const named_route = Object.assign({}, route, { chain: chain_maps_named_after_their_route(route) });
    const chain_map = named_route.chain.find((candidate) => is_point_inside_rect(centre, candidate.rect)) || chain_map_nearest_to(named_route, open_map.rect);
    const sync_target = { route: named_route, chain_map };
    return sync_target;
  }
  function spec_without_seed(spec) {
    const { seed, ...spec_fields } = spec || {};
    return spec_fields;
  }
  function open_map_signature(region_document, sync_target) {
    const { route, chain_map } = sync_target;
    const signature = JSON.stringify({
      route: route.name, map: chain_map.name, rect: chain_map.rect, exits: chain_map.exits || [], route_cells: route.grid_cells || [],
      maps: chain_maps_of_route(route).map((each_map) => [each_map.name, each_map.rect]), spec: spec_without_seed(route.spec),
      square: ((region_document && region_document.map_grid) || {}).cell || 0,
    });
    return signature;
  }
  const FIELDS_ALWAYS_TAKEN_FROM_THE_MAP = ["name", "width", "height", "exits"];
  // The Routes view's params after its route changed on the Map: what the Map decides (name, size, exits) comes
  // from the fresh spec, the seed stays, and any setting changed in the Routes view since opening keeps its value.
  function params_after_map_sync(opened_params, current_params, fresh_params) {
    const synced_params = Object.assign({}, fresh_params);
    for (const [key, current_value] of Object.entries(current_params || {})) {
      if (FIELDS_ALWAYS_TAKEN_FROM_THE_MAP.includes(key)) continue;
      const is_changed_since_opening = JSON.stringify(current_value) !== JSON.stringify((opened_params || {})[key]);
      if (key === "seed" || is_changed_since_opening) synced_params[key] = current_value;
    }
    return synced_params;
  }
  function paint_key_of_chain_map(chain_map, metatiles_per_region_cell) {
    const [x0, y0, x1, y1] = chain_map.rect;
    const key = paintKey({ name: chain_map.name, width: (x1 - x0) * metatiles_per_region_cell, height: (y1 - y0) * metatiles_per_region_cell });
    return key;
  }
  function is_same_rect(rect, other_rect) {
    const same = rect.length === other_rect.length && rect.every((coordinate, index) => coordinate === other_rect[index]);
    return same;
  }
  function chain_map_holding_world_cell(chain_maps, world_x, world_y, metatiles_per_region_cell) {
    const holding_map = chain_maps.find((chain_map) => world_x >= chain_map.rect[0] * metatiles_per_region_cell && world_x < chain_map.rect[2] * metatiles_per_region_cell
      && world_y >= chain_map.rect[1] * metatiles_per_region_cell && world_y < chain_map.rect[3] * metatiles_per_region_cell);
    return holding_map || null;
  }
  function painted_entries_in_world(remembered_paint, old_chain_map, metatiles_per_region_cell) {
    const left = old_chain_map.rect[0] * metatiles_per_region_cell;
    const top = old_chain_map.rect[1] * metatiles_per_region_cell;
    if (Array.isArray(remembered_paint)) {
      const objects_in_world = remembered_paint.map((painted_object) => ({ world_x: painted_object.x + left, world_y: painted_object.y + top, painted_object }));
      return objects_in_world;
    }
    const cells_in_world = Object.entries(remembered_paint || {}).map(([cell_key, painted_class]) => {
      const [x, y] = cell_key.split(",").map(Number);
      return { world_x: x + left, world_y: y + top, painted_class };
    });
    return cells_in_world;
  }
  function add_painted_entry_to_map(followed_memory, entry, new_chain_map, metatiles_per_region_cell) {
    const new_key = paint_key_of_chain_map(new_chain_map, metatiles_per_region_cell);
    const local_x = entry.world_x - new_chain_map.rect[0] * metatiles_per_region_cell;
    const local_y = entry.world_y - new_chain_map.rect[1] * metatiles_per_region_cell;
    if (entry.painted_object) {
      followed_memory[new_key] = (Array.isArray(followed_memory[new_key]) ? followed_memory[new_key] : []).concat([Object.assign({}, entry.painted_object, { x: local_x, y: local_y })]);
      return;
    }
    followed_memory[new_key] = Object.assign({}, followed_memory[new_key] || {}, { [`${local_x},${local_y}`]: entry.painted_class });
  }
  function paint_memory_after_chain_change(paint_memory, old_chain_maps, new_chain_maps, metatiles_per_region_cell) {
    const followed_memory = Object.assign({}, paint_memory || {});
    const entries_in_world = [];
    for (const old_chain_map of old_chain_maps) {
      const old_key = paint_key_of_chain_map(old_chain_map, metatiles_per_region_cell);
      if (!(old_key in followed_memory)) continue;
      entries_in_world.push(...painted_entries_in_world(followed_memory[old_key], old_chain_map, metatiles_per_region_cell));
      delete followed_memory[old_key];
    }
    for (const entry of entries_in_world) {
      const new_chain_map = chain_map_holding_world_cell(new_chain_maps, entry.world_x, entry.world_y, metatiles_per_region_cell);
      if (new_chain_map) add_painted_entry_to_map(followed_memory, entry, new_chain_map, metatiles_per_region_cell);
    }
    return followed_memory;
  }
  function connection_for(region_exit, region_document) {
    const destination_name = region_exit.to_chain_map || region_exit.to;
    const route = region_route_named(region_document, destination_name);
    const town = route ? null : (region_document.cities || []).find((candidate) => candidate.name === region_exit.to) || null;
    if (!route && !town) return null;
    return { name: destination_name, kind: route ? "route" : "town", route, town };
  }
  function exitConnections(scene_exits, region_document, route_name) {
    const exits = scene_exits || [];
    const region_route = region_route_named(region_document, route_name);
    if (!region_route) return exits.map((scene_exit) => (scene_exit.to ? { name: name_from_map_constant(scene_exit.to), kind: "unknown", route: null, town: null } : null));
    const used_region_exits = new Set();
    return exits.map((scene_exit) => {
      if (!scene_exit.to) return null;
      const region_exit = region_exit_for(scene_exit, region_route.exits || [], used_region_exits);
      if (!region_exit) return null;
      used_region_exits.add(region_exit);
      return connection_for(region_exit, region_document);
    });
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
  const PAINT_OBJECTS_KEY = "pokeroutes-paint-objects-v1";
  const paintKey = (r) => `${r.name}|${r.width}x${r.height}`;
  function mergePaint(map, cells) {
    const out = Object.assign({}, map || {});
    for (const [x, y, k] of cells) out[`${x},${y}`] = k;
    return out;
  }
  function paintList(map) {
    return Object.entries(map || {}).map(([k, c]) => { const [x, y] = k.split(",").map(Number); return [x, y, c]; });
  }
  // Items placed by hand in the Routes view ([{kind, x, y, item}]), remembered per route
  // name + size like paint and sent to /api/route-items after every generate.
  const PLACED_ITEMS_KEY = "pokeroutes-placed-items-v1";
  const PLACEABLE_ITEM_KINDS = ["item_ball", "hidden_item"];
  function placed_item_index(items, x, y) {
    return (items || []).findIndex((item) => item.x === x && item.y === y);
  }
  function place_item(items, new_item) {
    const others = (items || []).filter((item) => item.x !== new_item.x || item.y !== new_item.y);
    return others.concat([Object.assign({}, new_item)]);
  }
  function update_placed_item(items, index, changes) {
    return (items || []).map((item, item_index) => (item_index === index ? Object.assign({}, item, changes) : item));
  }
  function remove_placed_item(items, index) {
    return (items || []).filter((item, item_index) => item_index !== index);
  }
  // memory: {paintKey: items}; an empty list forgets the route
  function remember_placed_items(memory, route_key, items) {
    const out = Object.assign({}, memory || {});
    if (items && items.length) out[route_key] = items.map((item) => Object.assign({}, item));
    else delete out[route_key];
    return out;
  }
  // After a regenerate the server answers {placed, skipped}: the placed ones are what the
  // map now shows; skipped ones stay remembered so they come back when the cell is walkable again.
  function merge_placement_result(remembered, result) {
    const placed = (result && result.placed) || [];
    const skipped = ((result && result.skipped) || []).map((entry) => entry.item).filter((item) => item && typeof item === "object");
    const shown = placed.map((item) => Object.assign({}, item));
    const kept = shown.concat(skipped.filter((item) => placed_item_index(shown, item.x, item.y) < 0));
    const order = (item) => placed_item_index(remembered, item.x, item.y);
    return { shown, remembered: kept.sort((a, b) => order(a) - order(b)) };
  }
  // Classes GBA metatiles draw at least 2 cells wide (trees are 2x2, a 1-cell pond has no tiles).
  const TWO_WIDE = "fw";
  // The generator's stage_cleanup (pokeroutes/generate.py) for a paint stroke: around the
  // painted cells, 1-wide tree walls become grass, grass boxed in by trees becomes tree and
  // lone water cells become grass. The painted cells themselves are left as painted.
  // `rows` is the finished stage's cls (strings); returns the extra [x, y, class] cells.
  function tidyPaint(rows, cells) {
    const H = rows.length, W = H ? rows[0].length : 0;
    const g = rows.map((r) => r.split(""));
    const painted = new Set();
    for (const [x, y, k] of cells) { g[y][x] = k; painted.add(`${x},${y}`); }
    const near = new Set();
    for (const [x, y] of cells) for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) {
      const cx = x + dx, cy = y + dy;
      if (cx > 0 && cy > 0 && cx < W - 1 && cy < H - 1 && !painted.has(`${cx},${cy}`)) near.add(`${cx},${cy}`);
    }
    const fixes = new Map();
    for (let pass = 0; pass < 3; pass++) {
      let changed = 0;
      for (const key of near) {
        const [x, y] = key.split(",").map(Number);
        const c = g[y][x], up = g[y - 1][x], dn = g[y + 1][x], lf = g[y][x - 1], rt = g[y][x + 1];
        let to = null;
        if (c === "f" && ((up !== "f" && dn !== "f") || (lf !== "f" && rt !== "f"))) to = "g";
        else if ((c === "g" || c === "t") && up === "f" && dn === "f" && lf === "f" && rt === "f") to = "f";
        else if (c === "w" && up !== "w" && dn !== "w" && lf !== "w" && rt !== "w") to = "g";
        if (to) { g[y][x] = to; fixes.set(key, to); changed++; }
      }
      if (!changed) break;
    }
    return [...fixes].map(([k, c]) => { const [x, y] = k.split(",").map(Number); return [x, y, c]; });
  }
  function stampCells(name, x, y, W, H) {
    const s = STAMPS[name];
    if (!s) return [];
    return s.cells.map(([dx, dy, k]) => [x + dx, y + dy, k]).filter(([cx, cy]) => cx >= 0 && cy >= 0 && cx < W && cy < H);
  }

  // ------------------------------------------------------------ paint palette (Tiles tab)
  const LEDGE_BRUSH = "ledge";

  function brush_kind(brush) {
    const separator_index = String(brush).indexOf(":");
    if (separator_index < 0) return brush === LEDGE_BRUSH ? { kind: "ledge", key: brush } : { kind: "terrain", key: brush };
    return { kind: brush.slice(0, separator_index), key: brush.slice(separator_index + 1) };
  }

  function palette_ledge_codes(palette) {
    if (!palette) return [];
    return palette.terrain.filter((entry) => entry.group === "ledges").map((entry) => entry.code);
  }

  function palette_terrain_entry(palette, code) {
    if (!palette) return null;
    return palette.terrain.find((entry) => entry.code === code) || null;
  }

  function palette_object_entry(palette, object_id) {
    if (!palette) return null;
    return palette.objects.find((entry) => entry.id === object_id) || null;
  }

  function palette_class_codes(palette) {
    if (!palette) return "";
    return palette.terrain.concat(palette.unavailable).map((entry) => entry.code).join("");
  }

  const LEDGE_CODES_CLOCKWISE = ["s", "x", "j", "e"];

  function clockwise_rank_of_ledge(ledge_code) {
    const clockwise_rank = LEDGE_CODES_CLOCKWISE.indexOf(ledge_code);
    return clockwise_rank < 0 ? LEDGE_CODES_CLOCKWISE.length : clockwise_rank;
  }

  function next_ledge_code(palette, current_code) {
    const ledge_codes = palette_ledge_codes(palette).slice().sort((first_code, second_code) => clockwise_rank_of_ledge(first_code) - clockwise_rank_of_ledge(second_code));
    if (!ledge_codes.length) return current_code;
    const current_index = ledge_codes.indexOf(current_code);
    const next_code = ledge_codes[(current_index + 1) % ledge_codes.length];
    return next_code;
  }

  function snap_cells_to_blocks(cells, map_width, map_height) {
    const snapped = new Map();
    for (const [cell_x, cell_y, code] of cells) {
      const block_left = cell_x - (cell_x % 2), block_top = cell_y - (cell_y % 2);
      for (const [offset_x, offset_y] of [[0, 0], [1, 0], [0, 1], [1, 1]]) {
        const snapped_x = block_left + offset_x, snapped_y = block_top + offset_y;
        if (snapped_x >= map_width || snapped_y >= map_height) continue;
        snapped.set(`${snapped_x},${snapped_y}`, [snapped_x, snapped_y, code]);
      }
    }
    return [...snapped.values()];
  }

  function object_stamp_cells(object_entry, center_x, center_y, map_width, map_height) {
    const object_height = object_entry.cells.length, object_width = object_entry.cells[0].length;
    const left = center_x - Math.floor((object_width - 1) / 2), top = center_y - Math.floor((object_height - 1) / 2);
    if (left < 0 || top < 0 || left + object_width > map_width || top + object_height > map_height) return [];
    const stamp_cells = [];
    object_entry.cells.forEach((metatile_row, row_index) => metatile_row.forEach((metatile_id, column_index) => {
      stamp_cells.push([left + column_index, top + row_index, `${object_entry.classes[row_index][column_index]}#${metatile_id}`]);
    }));
    return stamp_cells;
  }

  function object_top_left(object_entry, center_x, center_y) {
    const object_height = object_entry.cells.length, object_width = object_entry.cells[0].length;
    const top_left = { x: center_x - Math.floor((object_width - 1) / 2), y: center_y - Math.floor((object_height - 1) / 2) };
    return top_left;
  }

  function object_footprint_keys(object_entry, left, top) {
    const footprint_keys = [];
    object_entry.cells.forEach((metatile_row, row_index) => metatile_row.forEach((_metatile_id, column_index) => {
      footprint_keys.push(`${left + column_index},${top + row_index}`);
    }));
    return footprint_keys;
  }

  function instance_footprint_keys(palette, object_instance) {
    const object_entry = palette_object_entry(palette, object_instance.object);
    if (!object_entry) return [`${object_instance.x},${object_instance.y}`];
    const footprint_keys = object_footprint_keys(object_entry, object_instance.x, object_instance.y);
    return footprint_keys;
  }

  function object_instance_index_at(palette, object_instances, cell_x, cell_y) {
    const cell_key = `${cell_x},${cell_y}`;
    const covers_cell = (object_instance) => instance_footprint_keys(palette, object_instance).includes(cell_key);
    const instance_index = (object_instances || []).findIndex(covers_cell);
    return instance_index;
  }

  function cell_from_key(cell_key) {
    const [cell_x, cell_y] = cell_key.split(",").map(Number);
    return { x: cell_x, y: cell_y };
  }

  function value_under_removed_cell(object_instance, cell_key, generated_class_rows) {
    const remembered_under = (object_instance.under || {})[cell_key];
    if (remembered_under != null) return remembered_under;
    const cell = cell_from_key(cell_key);
    const generated_class = generated_class_rows && generated_class_rows[cell.y] ? generated_class_rows[cell.y][cell.x] : "g";
    return generated_class || "g";
  }

  function restore_cells_under_instance(object_instance, painted_keys, palette, generated_class_rows, remembered_cells) {
    const restore_cells = [];
    for (const cell_key of instance_footprint_keys(palette, object_instance)) {
      const remembered_under = (object_instance.under || {})[cell_key];
      if (remembered_under != null) remembered_cells[cell_key] = remembered_under; else delete remembered_cells[cell_key];
      if (painted_keys.has(cell_key)) continue;
      const cell = cell_from_key(cell_key);
      restore_cells.push([cell.x, cell.y, value_under_removed_cell(object_instance, cell_key, generated_class_rows)]);
    }
    return restore_cells;
  }

  function placed_instance_with_ground_under(palette, new_instance, remembered_cells) {
    const under = {};
    for (const cell_key of instance_footprint_keys(palette, new_instance)) if (remembered_cells[cell_key] != null) under[cell_key] = remembered_cells[cell_key];
    const placed_instance = { object: new_instance.object, x: new_instance.x, y: new_instance.y, under };
    return placed_instance;
  }

  // A paint stroke over remembered paint and whole objects: every object the stroke touches is
  // lifted off in one piece (its other cells go back to what was under it; stroke.lift names objects
  // to lift even when nothing is painted over them), then the stroke's
  // own objects go down remembering what they cover. Returns the new memory and the extra cells to re-tile.
  function apply_paint_stroke(stroke) {
    const painted_keys = new Set(Object.keys(stroke.cells));
    for (const new_instance of stroke.objects || []) for (const cell_key of instance_footprint_keys(stroke.palette, new_instance)) painted_keys.add(cell_key);
    const touches_stroke = (object_instance) => (stroke.lift || []).includes(object_instance) ||
      instance_footprint_keys(stroke.palette, object_instance).some((cell_key) => painted_keys.has(cell_key));
    const remembered_cells = Object.assign({}, stroke.remembered_cells || {});
    const lifted_instances = (stroke.remembered_objects || []).filter(touches_stroke);
    const kept_instances = (stroke.remembered_objects || []).filter((object_instance) => !touches_stroke(object_instance));
    const restore_cells = lifted_instances.flatMap((object_instance) => restore_cells_under_instance(object_instance, painted_keys, stroke.palette, stroke.generated_class_rows, remembered_cells));
    const placed_instances = (stroke.objects || []).map((new_instance) => placed_instance_with_ground_under(stroke.palette, new_instance, remembered_cells));
    const painted_cells = Object.assign(remembered_cells, stroke.cells);
    const stroke_outcome = { cells: painted_cells, objects: kept_instances.concat(placed_instances), restore_cells, lifted: lifted_instances.length };
    return stroke_outcome;
  }

  function tree_block_keys_around(cell_x, cell_y, map_width, map_height) {
    const block_left = cell_x - (cell_x % 2), block_top = cell_y - (cell_y % 2);
    const block_keys = [[0, 0], [1, 0], [0, 1], [1, 1]]
      .map(([offset_x, offset_y]) => [block_left + offset_x, block_top + offset_y])
      .filter(([block_x, block_y]) => block_x < map_width && block_y < map_height)
      .map(([block_x, block_y]) => `${block_x},${block_y}`);
    return block_keys;
  }

  // Painting anything but a tree over one cell of a tree takes the whole tree (its 2x2 block on
  // the tree grid) away: the rest of the block becomes plain ground instead of a stray bush.
  function whole_trees_under_stroke(stroke_cells, class_rows, tree_codes, ground_code) {
    const map_height = class_rows.length, map_width = map_height ? class_rows[0].length : 0;
    const painted_keys = new Set(stroke_cells.map(([cell_x, cell_y]) => `${cell_x},${cell_y}`));
    const freed_cells = new Map();
    for (const [cell_x, cell_y, painted_value] of stroke_cells) {
      if (tree_codes.includes(String(painted_value)[0]) || !tree_codes.includes(class_rows[cell_y][cell_x])) continue;
      for (const block_key of tree_block_keys_around(cell_x, cell_y, map_width, map_height)) {
        const block_cell = cell_from_key(block_key);
        if (painted_keys.has(block_key) || !tree_codes.includes(class_rows[block_cell.y][block_cell.x])) continue;
        freed_cells.set(block_key, [block_cell.x, block_cell.y, ground_code]);
      }
    }
    const freed_tree_cells = [...freed_cells.values()];
    return freed_tree_cells;
  }

  function exact_tile_value(palette, metatile_id) {
    const tile_class = (palette && palette.tiles && palette.tiles[String(metatile_id)]) || "g";
    return `${tile_class}#${metatile_id}`;
  }

  function expand_paint_cells(cells) {
    return cells.flatMap(([cell_x, cell_y, value]) => {
      const exact_match = /^([A-Za-z])(#\d+)$/.exec(value);
      if (!exact_match) return [[cell_x, cell_y, value]];
      return [[cell_x, cell_y, exact_match[1]], [cell_x, cell_y, exact_match[2]]];
    });
  }

  // which /api/sprites key draws an event: people by npc role, trainers alternate the two
  // trainer sheets by position (same rule as pokeroutes/route_people.py trainer_sheet)
  function event_sprite_key(event) {
    if (event.kind === "npc" && event.role) return "npc_" + event.role;
    if (event.kind === "trainer") return (Number(event.x) + Number(event.y)) % 2 ? "trainer_f" : "trainer";
    return event.kind;
  }

  const api = {
    isMacPlatform, parseShortcut, formatShortcut, eventKey, matchShortcut, focusKind, allowedIn,
    flattenMenus, findShortcut, shortcutClashes, SHORTCUTS, ALT_SHORTCUTS, NOT_IN_FIELDS, attachShortcuts,
    CENTER_TABS,
    History, RECENT_KEY, pushRecent, recentLabel, specToParams, paramsToSpecFile, safeFileName, exitConnections, region_route_named, chain_of_map, chain_map_with_constant,
    chain_layout, chain_map_index_at, chain_seam_segments, chain_map_offset_between,
    sync_target_for_open_map, open_map_signature, params_after_map_sync, paint_memory_after_chain_change, chain_maps_of_route,
    STAMPS, brushCells, stampCells, lineCells, PAINT_KEY, PAINT_OBJECTS_KEY, paintKey, mergePaint, paintList,
    PLACED_ITEMS_KEY, PLACEABLE_ITEM_KINDS, placed_item_index, place_item, update_placed_item, remove_placed_item,
    remember_placed_items, merge_placement_result,
    TWO_WIDE, tidyPaint,
    LEDGE_BRUSH, brush_kind, palette_ledge_codes, palette_terrain_entry, palette_object_entry, palette_class_codes,
    next_ledge_code, LEDGE_CODES_CLOCKWISE, snap_cells_to_blocks, object_stamp_cells, exact_tile_value, expand_paint_cells,
    object_top_left, object_footprint_keys, instance_footprint_keys, object_instance_index_at, apply_paint_stroke,
    tree_block_keys_around, whole_trees_under_stroke,
    ROUTES_VIEW_TAB, is_region_editor_tab,
    event_sprite_key,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SL = api;
})(typeof window !== "undefined" ? window : globalThis);
