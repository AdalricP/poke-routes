// node webui/tests/shell-logic.test.js
// Pure parts of the editor shell: shortcuts + menu lookup, dock layout + persistence,
// undo history, recent list, spec-file conversion, paint brushes.
"use strict";
const assert = require("assert");
const path = require("path");
const SL = require(path.join(__dirname, "../static/shell-logic.js"));

let n = 0;
function test(name, fn) {
  try { fn(); n++; } catch (e) { console.error("FAIL " + name); throw e; }
}
const ev = (o) => Object.assign({ metaKey: false, ctrlKey: false, altKey: false, shiftKey: false, key: "", code: "" }, o);
const elt = (tag, type, extra) => Object.assign({ tagName: tag.toUpperCase(), type, isContentEditable: false }, extra || {});

// ------------------------------------------------------------ platform + formatting
test("platform detection", () => {
  assert.strictEqual(SL.isMacPlatform({ platform: "MacIntel" }), true);
  assert.strictEqual(SL.isMacPlatform({ userAgentData: { platform: "macOS" } }), true);
  assert.strictEqual(SL.isMacPlatform({ platform: "Win32" }), false);
  assert.strictEqual(SL.isMacPlatform({ platform: "Linux x86_64" }), false);
  assert.strictEqual(SL.isMacPlatform(undefined), false);
});

test("format shortcuts per platform", () => {
  assert.strictEqual(SL.formatShortcut("Mod+Shift+g", true), "⇧⌘G");
  assert.strictEqual(SL.formatShortcut("Mod+Shift+g", false), "Ctrl+Shift+G");
  assert.strictEqual(SL.formatShortcut("Mod+Alt+n", true), "⌥⌘N");
  assert.strictEqual(SL.formatShortcut("Mod+Alt+n", false), "Ctrl+Alt+N");
  assert.strictEqual(SL.formatShortcut("Mod+,", true), "⌘,");
  assert.strictEqual(SL.formatShortcut("Mod+=", false), "Ctrl+=");
  assert.strictEqual(SL.formatShortcut("ArrowRight", true), "→");
  assert.strictEqual(SL.formatShortcut("Space", false), "Space");
  assert.strictEqual(SL.formatShortcut("Alt+1", true), "⌥1");
  assert.strictEqual(SL.formatShortcut("", true), "");
});

test("parse handles a literal + key", () => {
  const p = SL.parseShortcut("Mod++");
  assert.strictEqual(p.mod, true);
  assert.strictEqual(p.key, "+");
});

// ------------------------------------------------------------ matching
test("Mod is Cmd on mac and Ctrl elsewhere", () => {
  const cmdG = ev({ metaKey: true, key: "g", code: "KeyG" });
  const ctrlG = ev({ ctrlKey: true, key: "g", code: "KeyG" });
  assert.ok(SL.matchShortcut(cmdG, "Mod+g", true));
  assert.ok(!SL.matchShortcut(ctrlG, "Mod+g", true));
  assert.ok(SL.matchShortcut(ctrlG, "Mod+g", false));
  assert.ok(!SL.matchShortcut(cmdG, "Mod+g", false));
  assert.ok(!SL.matchShortcut(ev({ metaKey: true, shiftKey: true, key: "G", code: "KeyG" }), "Mod+g", true));
  assert.ok(SL.matchShortcut(ev({ metaKey: true, shiftKey: true, key: "G", code: "KeyG" }), "Mod+Shift+g", true));
});

test("letters match by code, so Option/Alt doesn't break them", () => {
  // Option+Cmd+N on a mac reports key "˜"
  assert.ok(SL.matchShortcut(ev({ metaKey: true, altKey: true, key: "˜", code: "KeyN" }), "Mod+Alt+n", true));
  assert.ok(SL.matchShortcut(ev({ altKey: true, key: "¡", code: "Digit1" }), "Alt+1", true));
  assert.ok(!SL.matchShortcut(ev({ key: "1", code: "Digit1" }), "Alt+1", true));
});

test("plain keys need no modifiers", () => {
  assert.ok(SL.matchShortcut(ev({ key: " ", code: "Space" }), "Space", true));
  assert.ok(SL.matchShortcut(ev({ key: "ArrowLeft", code: "ArrowLeft" }), "ArrowLeft", false));
  assert.ok(!SL.matchShortcut(ev({ key: "v", code: "KeyV", metaKey: true }), "v", true));
  assert.ok(SL.matchShortcut(ev({ key: "?", code: "Slash", shiftKey: true }), "?", true));
  assert.ok(SL.matchShortcut(ev({ key: "=", code: "Equal" }), "=", true));
});

// ------------------------------------------------------------ focus rules
test("focus kinds", () => {
  assert.strictEqual(SL.focusKind(elt("input", "text")), "text");
  assert.strictEqual(SL.focusKind(elt("input", "number")), "text");
  assert.strictEqual(SL.focusKind(elt("input", "search")), "text");
  assert.strictEqual(SL.focusKind(elt("textarea")), "text");
  assert.strictEqual(SL.focusKind(elt("div", undefined, { isContentEditable: true })), "text");
  assert.strictEqual(SL.focusKind(elt("input", "range")), "control");
  assert.strictEqual(SL.focusKind(elt("input", "checkbox")), "control");
  assert.strictEqual(SL.focusKind(elt("select")), "control");
  assert.strictEqual(SL.focusKind(elt("button")), "none");
  assert.strictEqual(SL.focusKind(elt("body")), "none");
  assert.strictEqual(SL.focusKind(null), "none");
});

test("single keys never fire in fields; text fields keep their editing keys", () => {
  assert.ok(SL.allowedIn({ shortcut: "Space" }, "none"));
  assert.ok(!SL.allowedIn({ shortcut: "Space" }, "text"));
  assert.ok(!SL.allowedIn({ shortcut: "ArrowRight" }, "control")); // a focused slider keeps its arrows
  assert.ok(!SL.allowedIn({ shortcut: "Mod+z" }, "text"));
  assert.ok(!SL.allowedIn({ shortcut: "Mod+Shift+z" }, "text"));
  assert.ok(SL.allowedIn({ shortcut: "Mod+z" }, "none"));
  assert.ok(SL.allowedIn({ shortcut: "Mod+s" }, "text"));
  assert.ok(!SL.allowedIn({ shortcut: "Mod+g", field: false }, "text"));
  assert.ok(SL.allowedIn({ shortcut: "Mod+g", field: false }, "control"));
  assert.ok(!SL.allowedIn({}, "none"));
});

// ------------------------------------------------------------ the real shortcut table
// A skeleton of bench.js's menus (same ids), so the table is checked as the app uses it.
function skeleton() {
  const ids = (list) => list.map((id) => ({ id, label: id, run() {} }));
  const menus = [
    { label: "File", items: ids(["new", "open"]).concat([{ id: "recent", label: "Open recent", items: () => [] }, { sep: true }],
      ids(["save", "saveas"]), [{ id: "export", label: "Export", items: ids(["exp-zip", "exp-scene", "exp-png"]) }]) },
    { label: "Edit", items: ids(["undo", "redo", "copy-spec", "paste-spec", "deselect", "prefs"]) },
    { label: "View", items: ids(["zin", "zout", "zfit", "z100", "frame", "v-split", "v-back"]) },
    { label: "Generate", items: ids(["gen", "regen"]) },
    { label: "Window", items: ids(["v-left", "v-right", "v-bottom", "w-reset"]) },
    { label: "Help", items: ids(["keys", "about"]) },
  ];
  const extra = ids(["tool-select", "tool-pan", "tool-paint", "tool-item", "tool-people", "x-item-delete", "x-rotate", "x-brush-smaller", "x-brush-bigger",
    "x-zin", "x-zout", "x-zfit", "x-help", "ov-areas", "ov-items", "ov-classes", "ov-grid", "play", "next", "prev", "first", "last"]);
  SL.attachShortcuts(menus, extra);
  return { menus, extra };
}

test("every table entry is used and nothing clashes", () => {
  const { menus, extra } = skeleton();
  const used = new Set(SL.flattenMenus(menus).concat(extra).filter((i) => i.shortcut).map((i) => i.id));
  for (const id of Object.keys(SL.SHORTCUTS)) assert.ok(used.has(id), "unused shortcut id " + id);
  assert.deepStrictEqual(SL.shortcutClashes(menus, extra), []);
});

test("no browser-reserved combos", () => {
  for (const s of Object.values(SL.SHORTCUTS).concat(Object.values(SL.ALT_SHORTCUTS))) {
    const p = SL.parseShortcut(s);
    if (!p.mod) continue;
    const k = p.key.toLowerCase();
    const reserved = (!p.shift && !p.alt && ["n", "t", "w", "q", "l", "r", "p", "1", "2", "3", "4", "5", "6", "7", "8", "9"].includes(k)) ||
      (p.shift && ["n", "t", "w", "c", "i", "j"].includes(k));
    assert.ok(!reserved, "reserved by the browser: " + s);
  }
});

test("Generate has no key (the toolbar button is it)", () => {
  const { menus, extra } = skeleton();
  for (const k of [{ metaKey: true, key: "g", code: "KeyG" }, { metaKey: true, shiftKey: true, key: "G", code: "KeyG" },
    { ctrlKey: true, key: "Enter", code: "Enter" }, { metaKey: true, key: "Enter", code: "Enter" }])
    assert.strictEqual(SL.findShortcut(menus, ev(k), "none", true, extra), null);
});

test("findShortcut: platform, focus and enabled()", () => {
  const { menus, extra } = skeleton();
  const save = ev({ ctrlKey: true, key: "s", code: "KeyS" });
  assert.strictEqual(SL.findShortcut(menus, save, "text", false, extra).id, "save");
  assert.strictEqual(SL.findShortcut(menus, save, "text", true, extra), null); // Ctrl+S isn't Save on a mac
  const s = ev({ key: "r", code: "KeyR" });
  assert.strictEqual(SL.findShortcut(menus, s, "none", true, extra).id, "x-rotate");
  assert.strictEqual(SL.findShortcut(menus, s, "text", true, extra), null);
  const undo = ev({ metaKey: true, key: "z", code: "KeyZ" });
  assert.strictEqual(SL.findShortcut(menus, undo, "none", true, extra).id, "undo");
  assert.strictEqual(SL.findShortcut(menus, undo, "text", true, extra), null); // native text undo
  const redoY = ev({ ctrlKey: true, key: "y", code: "KeyY" });
  assert.strictEqual(SL.findShortcut(menus, redoY, "none", false, extra).id, "redo");
  // disabled items don't swallow the key
  const m2 = [{ label: "X", items: [{ id: "a", label: "a", shortcut: "Mod+e", enabled: () => false }] }];
  assert.strictEqual(SL.findShortcut(m2, ev({ metaKey: true, key: "e", code: "KeyE" }), "none", true), null);
  // overlays are Alt+digit and stay out of text fields (Option+1 types "¡" there)
  const alt2 = ev({ altKey: true, key: "™", code: "Digit2" });
  assert.strictEqual(SL.findShortcut(menus, alt2, "none", true, extra).id, "ov-areas");
  assert.strictEqual(SL.findShortcut(menus, alt2, "text", true, extra), null);
});

test("dynamic submenus are skipped when flattening", () => {
  const { menus } = skeleton();
  const ids = SL.flattenMenus(menus).map((i) => i.id);
  assert.ok(!ids.includes("recent"));
  assert.ok(ids.includes("exp-zip"));
  const zip = SL.flattenMenus(menus).find((i) => i.id === "exp-zip");
  assert.deepStrictEqual(zip.path, ["File", "Export", "exp-zip"]);
});

// ------------------------------------------------------------ layout
function memStore(init) {
  const m = new Map(Object.entries(init || {}));
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k), m };
}
const throwing = { getItem() { throw new Error("SecurityError"); }, setItem() { throw new Error("QuotaExceeded"); } };

test("layout persistence round trip", () => {
  const st = memStore();
  const L = SL.defaultLayout();
  L.left.size = 300; L.right.open = false; L.bottom.tab = "improve"; L.center = "region"; L.inspector.shape = true;
  assert.strictEqual(SL.saveLayout(st, L), true);
  const back = SL.loadLayout(st);
  assert.strictEqual(back.left.size, 300);
  assert.strictEqual(back.right.open, false);
  assert.strictEqual(back.bottom.tab, "improve");
  assert.strictEqual(back.center, "region");
  assert.strictEqual(back.inspector.shape, true);
});

test("layout survives blocked storage and junk", () => {
  assert.deepStrictEqual(SL.loadLayout(throwing), SL.defaultLayout());
  assert.strictEqual(SL.saveLayout(throwing, SL.defaultLayout()), false);
  assert.deepStrictEqual(SL.loadLayout(null), SL.defaultLayout());
  assert.deepStrictEqual(SL.loadLayout(memStore({ [SL.LAYOUT_KEY]: "{not json" })), SL.defaultLayout());
  assert.deepStrictEqual(SL.loadLayout(memStore({ [SL.LAYOUT_KEY]: JSON.stringify({ v: 99, left: { size: 5 } }) })), SL.defaultLayout());
  const bad = SL.sanitizeLayout({ v: 1, left: { size: -40, open: "yes" }, bottom: { tab: "nope", size: 1e9 }, center: "x", inspector: { "a b<": true, ok: 1, fine: false } });
  assert.strictEqual(bad.left.size, 120);
  assert.strictEqual(bad.left.open, true);
  assert.strictEqual(bad.bottom.tab, "console");
  assert.strictEqual(bad.bottom.size, 2000);
  assert.strictEqual(bad.center, "scene");
  assert.deepStrictEqual(bad.inspector, { fine: false });
});

test("a saved layout on the old Routes B or Story tab opens the Map tab, keeping its tab order", () => {
  const old = SL.sanitizeLayout({ v: 1, center: "gates", centerOrder: ["story", "gates", "region", "geography", "scene"] });
  assert.strictEqual(old.center, "region");
  assert.deepStrictEqual(old.centerOrder, ["region", "geography", "scene"]);
  assert.strictEqual(SL.sanitizeLayout({ v: 1, center: "story" }).center, "region");
  assert.ok(!SL.defaultLayout().centerOrder.includes("gates"));
  assert.ok(!SL.CENTER_TABS.includes("story"));
});

test("split centre: Routes beside a region view, dropped left or right, kept through activation", () => {
  const layout = SL.defaultLayout();
  assert.deepStrictEqual(SL.center_tabs_shown(layout), ["scene"]);
  const map_on_left = SL.split_after_center_tab_drop(layout, "region", "left");
  assert.deepStrictEqual(map_on_left, { left: "region", right: "scene", ratio: 0.5 });
  const routes_on_right = SL.split_after_center_tab_drop(Object.assign({}, layout, { center: "geography" }), "scene", "right");
  assert.deepStrictEqual(routes_on_right, { left: "geography", right: "scene", ratio: 0.5 });
  assert.strictEqual(SL.split_after_center_tab_drop(layout, "scene", "middle"), null);
  assert.deepStrictEqual(SL.split_after_activating_tab(map_on_left, "geography"), { left: "geography", right: "scene", ratio: 0.5 });
  assert.strictEqual(SL.split_after_activating_tab(map_on_left, "scene"), map_on_left);
  assert.deepStrictEqual(SL.sanitize_center_split({ left: "region", right: "geography" }), { left: "region", right: "geography", ratio: 0.5 });
  assert.strictEqual(SL.sanitize_center_split({ left: "region", right: "region" }), null, "one view cannot sit beside itself");
  assert.strictEqual(SL.sanitize_center_split({ left: "region", right: "nowhere" }), null);
  assert.deepStrictEqual(SL.sanitize_center_split({ left: "scene", right: "story", ratio: 2 }), { left: "scene", right: "region", ratio: 0.8 });
  const restored = SL.sanitizeLayout({ v: 1, center: "geography", split: { left: "scene", right: "region", ratio: 0.4 } });
  assert.strictEqual(restored.center, "scene", "the active tab is one of the shown ones");
  assert.deepStrictEqual(SL.center_tabs_shown(restored), ["scene", "region"]);
  assert.strictEqual(SL.center_drop_side(10, 0, 300), "left");
  assert.strictEqual(SL.center_drop_side(150, 0, 300), "middle");
  assert.strictEqual(SL.center_drop_side(290, 0, 300), "right");
});

test("split centre: Map beside Geography, dropped from either single view or an existing split", () => {
  const geography_alone = Object.assign(SL.defaultLayout(), { center: "geography" });
  const map_on_right_of_geography = SL.split_after_center_tab_drop(geography_alone, "region", "right");
  assert.deepStrictEqual(map_on_right_of_geography, { left: "geography", right: "region", ratio: 0.5 });
  const geography_on_left_of_map = SL.split_after_center_tab_drop(Object.assign(SL.defaultLayout(), { center: "region" }), "geography", "left");
  assert.deepStrictEqual(geography_on_left_of_map, { left: "geography", right: "region", ratio: 0.5 });
  const routes_beside_map = Object.assign(SL.defaultLayout(), { center: "scene", split: { left: "scene", right: "region", ratio: 0.4 } });
  assert.deepStrictEqual(SL.split_after_center_tab_drop(routes_beside_map, "geography", "left"), { left: "geography", right: "region", ratio: 0.4 }, "replaces the side it lands on");
  assert.deepStrictEqual(SL.split_after_center_tab_drop(routes_beside_map, "geography", "right"), { left: "scene", right: "geography", ratio: 0.4 });
  assert.deepStrictEqual(SL.split_after_center_tab_drop(routes_beside_map, "region", "left"), { left: "region", right: "scene", ratio: 0.4 }, "dropping a shown tab on the other side swaps them");
  const map_alone = Object.assign(SL.defaultLayout(), { center: "region" });
  assert.deepStrictEqual(SL.split_after_center_tab_drop(map_alone, "region", "left"), { left: "region", right: "scene", ratio: 0.5 }, "a lone view dragged aside gets Routes beside it");
});

test("split centre: activating a hidden tab keeps the focused view and Routes where they are", () => {
  const geography_beside_map = { left: "geography", right: "region", ratio: 0.5 };
  assert.deepStrictEqual(SL.split_after_activating_tab(geography_beside_map, "scene", "region"), { left: "scene", right: "region", ratio: 0.5 });
  assert.deepStrictEqual(SL.split_after_activating_tab(geography_beside_map, "scene", "geography"), { left: "geography", right: "scene", ratio: 0.5 });
  assert.strictEqual(SL.split_after_activating_tab(geography_beside_map, "geography", "region"), geography_beside_map);
  const routes_beside_map = { left: "scene", right: "region", ratio: 0.5 };
  assert.deepStrictEqual(SL.split_after_activating_tab(routes_beside_map, "geography", "scene"), { left: "scene", right: "geography", ratio: 0.5 });
  assert.deepStrictEqual(SL.split_after_activating_tab(routes_beside_map, "geography", "region"), { left: "scene", right: "geography", ratio: 0.5 });
});

test("split centre: each view element shows one tab, never the same tab twice", () => {
  const view_tab_lists = [["scene"], ["geography", "region"], ["region", "geography"]];
  assert.deepStrictEqual(SL.center_tab_of_each_view(view_tab_lists, ["geography", "region"]), [null, "geography", "region"]);
  assert.deepStrictEqual(SL.center_tab_of_each_view(view_tab_lists, ["region", "geography"]), [null, "geography", "region"]);
  assert.deepStrictEqual(SL.center_tab_of_each_view(view_tab_lists, ["scene", "region"]), ["scene", "region", null]);
  assert.deepStrictEqual(SL.center_tab_of_each_view(view_tab_lists, ["geography"]), [null, "geography", null]);
});

test("rects tile the window without overlap (default, 1440x900)", () => {
  const r = SL.computeRects(SL.defaultLayout(), 1440, 900);
  const C = SL.CHROME;
  assert.deepStrictEqual(r.menu, { x: 0, y: 0, w: 1440, h: C.menu });
  assert.deepStrictEqual(r.toolbar, { x: 0, y: C.menu, w: 1440, h: C.toolbar });
  assert.strictEqual(r.status.y + r.status.h, 900);
  const top = C.menu + C.toolbar;
  assert.strictEqual(r.left.y, top);
  assert.strictEqual(r.left.w, 240);
  assert.strictEqual(r.right.w, 320);
  assert.strictEqual(r.right.x + r.right.w, 1440);
  assert.strictEqual(r.right.h, 900 - top - C.status);           // inspector: full body height
  assert.strictEqual(r.center.x, r.left.w + C.splitter);
  assert.strictEqual(r.center.x + r.center.w + C.splitter, r.right.x);
  assert.strictEqual(r.bottom.y + r.bottom.h, r.status.y);         // bottom dock sits on the status bar
  assert.strictEqual(r.bottom.w, r.right.x - C.splitter);          // spans hierarchy + scene
  assert.strictEqual(r.bottom.h, 180);
  assert.strictEqual(r.center.y + r.center.h + C.splitter, r.bottom.y);
  assert.strictEqual(r.splitLeft.x, r.left.w);
  assert.strictEqual(r.splitBottom.y, r.center.y + r.center.h);
});

test("collapsed docks shrink to strips; the scene keeps its minimum size", () => {
  const L = SL.defaultLayout();
  L.left.open = false; L.right.open = false; L.bottom.open = false;
  const r = SL.computeRects(L, 1200, 800);
  assert.strictEqual(r.left.w, SL.CHROME.collapsed);
  assert.strictEqual(r.right.w, SL.CHROME.collapsed);
  assert.strictEqual(r.bottom.h, SL.CHROME.head);
  assert.deepStrictEqual(r.collapsed, { left: true, right: true, bottom: true });
  const big = SL.defaultLayout();
  big.left.size = 900; big.right.size = 900; big.bottom.size = 900;
  const s = SL.computeRects(big, 1000, 700);
  assert.ok(s.center.w >= SL.MIN_CENTER, "centre width " + s.center.w);
  assert.ok(s.left.w >= SL.MIN_SIDE && s.right.w >= SL.MIN_SIDE);
  assert.ok(s.center.h >= 160, "centre height " + s.center.h);
});

test("floating docks give their space to the scene and stay on screen", () => {
  const L = SL.defaultLayout();
  L.left.float = { x: -500, y: 0, w: 300, h: 250 };
  L.bottom.float = { x: 400, y: 300, w: 500, h: 200 };
  const r = SL.computeRects(L, 1200, 800);
  const top = SL.CHROME.menu + SL.CHROME.toolbar;
  assert.deepStrictEqual(r.left, { x: 0, y: top, w: 300, h: 250 });
  assert.deepStrictEqual(r.bottom, { x: 400, y: 300, w: 500, h: 200 });
  assert.strictEqual(r.center.x, 0);
  assert.strictEqual(r.center.h, 800 - top - SL.CHROME.status);
  assert.deepStrictEqual(r.floating, { left: true, right: false, bottom: true });
  assert.strictEqual(r.collapsed.left, false);
  assert.strictEqual(SL.sanitizeLayout({ v: 1, left: { float: { x: 1, y: "a", w: 2, h: 3 } } }).left.float, null);
  assert.ok(SL.nearHomeEdge("left", 10, 400, 1200, 800));
  assert.ok(SL.nearHomeEdge("right", 1190, 400, 1200, 800));
  assert.ok(SL.nearHomeEdge("bottom", 500, 770, 1200, 800));
  assert.ok(!SL.nearHomeEdge("bottom", 500, 400, 1200, 800));
});

test("splitter drag directions", () => {
  assert.strictEqual(SL.dragSize("left", 240, 30, 0), 270);
  assert.strictEqual(SL.dragSize("right", 320, 30, 0), 290);
  assert.strictEqual(SL.dragSize("bottom", 200, 0, -50), 250);
});

// ------------------------------------------------------------ undo history
test("undo / redo order and redo cleared by a new edit", () => {
  const h = new SL.History(10, 0);
  h.push({ kind: "spec", before: { a: 0 }, after: { a: 1 } });
  h.push({ kind: "spec", before: { a: 1 }, after: { a: 2 } });
  assert.deepStrictEqual(h.undo().before, { a: 1 });
  assert.deepStrictEqual(h.undo().before, { a: 0 });
  assert.strictEqual(h.undo(), null);
  assert.deepStrictEqual(h.redo().after, { a: 1 });
  assert.ok(h.canRedo());
  h.push({ kind: "spec", before: { a: 1 }, after: { a: 5 } });
  assert.ok(!h.canRedo());
  assert.strictEqual(h.past.length, 2);
});

test("no-op edits are dropped; slider drags coalesce", () => {
  const h = new SL.History(10, 800);
  assert.strictEqual(h.push({ kind: "spec", before: { a: 1 }, after: { a: 1 } }), false);
  h.push({ kind: "spec", before: { w: 40 }, after: { w: 42 }, key: "width", t: 1000 });
  h.push({ kind: "spec", before: { w: 42 }, after: { w: 44 }, key: "width", t: 1300 });
  h.push({ kind: "spec", before: { w: 44 }, after: { w: 46 }, key: "width", t: 1900 });
  assert.strictEqual(h.past.length, 1);
  assert.deepStrictEqual(h.peekUndo(), { kind: "spec", before: { w: 40 }, after: { w: 46 }, key: "width", t: 1900 });
  h.push({ kind: "spec", before: { w: 46 }, after: { w: 48 }, key: "width", t: 5000 }); // too late: new step
  assert.strictEqual(h.past.length, 2);
  h.push({ kind: "spec", before: { w: 48 }, after: { w: 46 }, key: "width", t: 5100 }); // dragged back: step vanishes
  assert.strictEqual(h.past.length, 1);
});

test("history limit and filter", () => {
  const h = new SL.History(3, 0);
  for (let i = 0; i < 5; i++) h.push({ kind: "spec", before: { i }, after: { i: i + 1 } });
  assert.strictEqual(h.past.length, 3);
  assert.deepStrictEqual(h.past[0].before, { i: 2 });
  h.push({ kind: "paint", id: "abc", cells: [[1, 1, "w"]], old: [[1, 1, "g"]] });
  h.undo();
  h.filter((e) => e.kind !== "paint");
  assert.ok(!h.canRedo());
  assert.ok(h.past.every((e) => e.kind === "spec"));
});

// ------------------------------------------------------------ recent + specs
test("recent list: newest first, deduped, capped", () => {
  let l = [];
  for (let s = 0; s < 12; s++) l = SL.pushRecent(l, { name: "A", seed: s, width: 64, height: 72, biome: "forest" }, 10, s);
  assert.strictEqual(l.length, 10);
  assert.strictEqual(l[0].params.seed, 11);
  l = SL.pushRecent(l, { name: "A", seed: 5, width: 64, height: 72, biome: "forest" }, 10, 99);
  assert.strictEqual(l[0].params.seed, 5);
  assert.strictEqual(l.filter((e) => e.params.seed === 5).length, 1);
  assert.ok(/A .* seed 5 .* 64×72 forest/.test(l[0].label));
  assert.strictEqual(SL.pushRecent(null, { name: "B", seed: 1, width: 1, height: 1 }).length, 1);
});

test("region route YAML spec -> params", () => {
  const spec = { name: "Route 3", id: "Route3", seed: 42, size: [56, 90], biome: "riverland", meander: 0.4,
    exits: [{ side: "south", at: 0.3, width: 3, to: "Kiefernheide" }], special_areas: ["pond", "auto"],
    region: { role: "dead_end", goal: { side: "east" } }, endings: ["cave"], tileset: "general_fortree" };
  const { params, region } = SL.specToParams(spec, { name: "x", branches: 2 });
  assert.strictEqual(params.name, "Route 3");
  assert.strictEqual(params.seed, 42);
  assert.strictEqual(params.width, 56);
  assert.strictEqual(params.height, 90);
  assert.strictEqual(params.biome, "riverland");
  assert.strictEqual(params.branches, 2);           // defaults kept
  assert.strictEqual(params.tileset, undefined);    // not a bench field
  assert.strictEqual(params.exits.length, 1);       // a dead end keeps its one exit
  assert.strictEqual(params.exits[0].to, "Kiefernheide");
  assert.deepStrictEqual(region, { name: "Route 3", region: spec.region, endings: ["cave"] });
});

test("scene exits lead to the routes and towns on the other side", () => {
  const doc = {
    routes: [
      { name: "Route 1", exits: [{ side: "west", to: "Town 1", to_map: "MAP_TOWN1" }, { side: "north", to: "Route 2", to_map: "MAP_ROUTE2" }] },
      { name: "Route 2", exits: [{ side: "south", to: "Route 1", to_map: "MAP_ROUTE1" }] },
    ],
    cities: [{ id: "town1", name: "Town 1" }],
  };
  const scene_exits = [{ side: "west", to: "MAP_TOWN1" }, { side: "north", to: "MAP_ROUTE2" }, { side: "east", to: "" }];
  const [town, route, open_edge] = SL.exitConnections(scene_exits, doc, "Route 1");
  assert.deepStrictEqual([town.kind, town.name, town.town.id], ["town", "Town 1", "town1"]);
  assert.deepStrictEqual([route.kind, route.name, route.route.name], ["route", "Route 2", "Route 2"]);
  assert.strictEqual(open_edge, null);
  const without_doc = SL.exitConnections(scene_exits, null, "Route 1");
  assert.deepStrictEqual(without_doc.map((c) => c && c.name), ["Town 1", "Route 2", null]);
  assert.strictEqual(without_doc[0].kind, "unknown");
  assert.deepStrictEqual(SL.exitConnections([], doc, "Route 1"), []);
});

test("scene exits walk along a route's chain of maps", () => {
  const first_map = { name: "Route 1 (1/2)", id: "Route1_1", exits: [{ side: "west", to: "Town 1", to_map: "MAP_TOWN1" }, { side: "east", to: "Route 1", to_map: "MAP_ROUTE1_2", to_chain_map: "Route 1 (2/2)" }] };
  const second_map = { name: "Route 1 (2/2)", id: "Route1_2", map: "MAP_ROUTE1_2", exits: [{ side: "west", to: "Route 1", to_map: "MAP_ROUTE1_1", to_chain_map: "Route 1 (1/2)" }, { side: "north", to: "Route 2", to_map: "MAP_ROUTE2", to_chain_map: "Route 2" }] };
  const route_two_map = { name: "Route 2", id: "Route2", exits: [{ side: "south", to: "Route 1", to_map: "MAP_ROUTE1_2", to_chain_map: "Route 1 (2/2)" }] };
  const doc = {
    routes: [{ name: "Route 1", chain: [first_map, second_map], exits: [] }, { name: "Route 2", chain: [route_two_map], exits: [] }],
    cities: [{ id: "town1", name: "Town 1" }],
  };
  const [town, next_map] = SL.exitConnections([{ side: "west", to: "MAP_TOWN1" }, { side: "east", to: "MAP_ROUTE1_2" }], doc, "Route 1 (1/2)");
  assert.strictEqual(town.kind, "town");
  assert.deepStrictEqual([next_map.kind, next_map.name, next_map.route.id], ["route", "Route 1 (2/2)", "Route1_2"]);
  const [back_into_chain] = SL.exitConnections([{ side: "south", to: "MAP_ROUTE1_2" }], doc, "Route 2");
  assert.strictEqual(back_into_chain.route.id, "Route1_2");
  assert.deepStrictEqual(SL.chain_of_map(doc, "Route 1 (2/2)").map((chain_map) => chain_map.id), ["Route1_1", "Route1_2"]);
  assert.deepStrictEqual(SL.chain_of_map(doc, "Route 2"), []);
  assert.strictEqual(SL.chain_map_with_constant(doc, "MAP_ROUTE1_2").name, "Route 1 (2/2)");
});

test("an L-shaped chain is laid out at its world positions", () => {
  const l_shaped_chain = [
    { name: "Route 1 (1/4)", rect: [16, 24, 24, 32] }, { name: "Route 1 (2/4)", rect: [24, 24, 32, 32] },
    { name: "Route 1 (3/4)", rect: [32, 24, 40, 32] }, { name: "Route 1 (4/4)", rect: [32, 32, 40, 40] },
  ];
  const layout = SL.chain_layout(l_shaped_chain, 4);
  assert.deepStrictEqual(layout.maps.map((placement) => [placement.offset_x_in_metatiles, placement.offset_y_in_metatiles]), [[0, 0], [32, 0], [64, 0], [64, 32]]);
  assert.deepStrictEqual([layout.width_in_metatiles, layout.height_in_metatiles], [96, 64]);
  assert.deepStrictEqual(layout.maps.map((placement) => [placement.width_in_metatiles, placement.height_in_metatiles]), [[32, 32], [32, 32], [32, 32], [32, 32]]);
  assert.strictEqual(SL.chain_map_index_at(layout, 0, 0), 0);
  assert.strictEqual(SL.chain_map_index_at(layout, 31, 31), 0);
  assert.strictEqual(SL.chain_map_index_at(layout, 32, 5), 1);
  assert.strictEqual(SL.chain_map_index_at(layout, 70, 40), 3);
  assert.strictEqual(SL.chain_map_index_at(layout, 10, 40), -1);
  assert.strictEqual(SL.chain_map_index_at(layout, -1, 0), -1);
  assert.deepStrictEqual(SL.chain_map_offset_between(layout, 1, 3), { x: 32, y: 32 });
  assert.deepStrictEqual(SL.chain_map_offset_between(layout, 3, 0), { x: -64, y: -32 });
});

test("seams lie on the edges shared by touching chain maps", () => {
  const l_shaped_chain = [{ name: "a", rect: [0, 0, 8, 8] }, { name: "b", rect: [8, 0, 16, 8] }, { name: "c", rect: [8, 8, 16, 16] }];
  const seams = SL.chain_seam_segments(SL.chain_layout(l_shaped_chain, 4));
  assert.deepStrictEqual(seams, [{ x0: 32, y0: 0, x1: 32, y1: 32 }, { x0: 32, y0: 32, x1: 64, y1: 32 }]);
  const diagonal_chain = [{ name: "a", rect: [0, 0, 8, 8] }, { name: "b", rect: [8, 8, 16, 16] }];
  assert.deepStrictEqual(SL.chain_seam_segments(SL.chain_layout(diagonal_chain, 4)), []);
  const single_map = SL.chain_layout([{ name: "solo", rect: [5, 5, 13, 13] }], 4);
  assert.deepStrictEqual(SL.chain_seam_segments(single_map), []);
  assert.deepStrictEqual([single_map.maps[0].offset_x_in_metatiles, single_map.width_in_metatiles], [0, 32]);
  const uneven_neighbours = [{ name: "tall", rect: [0, 0, 8, 16] }, { name: "short", rect: [8, 4, 16, 12] }];
  assert.deepStrictEqual(SL.chain_seam_segments(SL.chain_layout(uneven_neighbours, 4)), [{ x0: 32, y0: 16, x1: 32, y1: 48 }]);
});

test("saved spec file round trip", () => {
  const p = { name: "Testweg", seed: 7, width: 64, height: 72, exits: [{ side: "south", at: 0.45, width: 2 }, { side: "north", at: 0.6, width: 2 }], special_areas: ["auto"] };
  const file = SL.paramsToSpecFile(p, { name: "Testweg", region: { role: "link" }, endings: [] });
  assert.strictEqual(file.format, "poke-routes-spec");
  const back = SL.specToParams(Object.assign({}, file.params, { region: file.region }), {});
  assert.deepStrictEqual(back.params, p);
  assert.deepStrictEqual(back.region.region, { role: "link" });
  assert.strictEqual(SL.safeFileName("Route 3 / Süd", ".json"), "Route_3_S_d.json");
  assert.strictEqual(SL.safeFileName("", ".json"), "route.json");
});

// ------------------------------------------------------------ paint brushes
test("brush and stamp cells clip to the map", () => {
  assert.deepStrictEqual(SL.brushCells(0, 0, 1, "w", 10, 10), [[0, 0, "w"]]);
  assert.strictEqual(SL.brushCells(5, 5, 3, "w", 10, 10).length, 9);
  assert.strictEqual(SL.brushCells(0, 0, 3, "w", 10, 10).length, 4);
  assert.strictEqual(SL.stampCells("pond", 0, 0, 10, 10).length, 4);
  assert.strictEqual(SL.stampCells("tree", 9, 9, 10, 10).length, 1);
  assert.deepStrictEqual(SL.stampCells("nope", 1, 1, 10, 10), []);
  for (const s of Object.values(SL.STAMPS)) for (const [, , k] of s.cells) assert.ok("fgptwrsexa".includes(k));
});

test("tidyPaint removes tree slivers and lone water a stroke leaves behind", () => {
  const rows = ["ffffff", "ffffff", "ffffff", "ffffff", "ffffff", "ffffff"];
  // a 1-wide path at x=2 and another at x=4 leave a 1-wide tree wall at x=3
  const stroke = [];
  for (let y = 1; y < 5; y++) stroke.push([2, y, "p"], [4, y, "p"]);
  const fixes = SL.tidyPaint(rows, stroke);
  for (let y = 1; y < 5; y++) assert.ok(fixes.some(([x, yy, k]) => x === 3 && yy === y && k === "g"), `x=3 y=${y}`);
  assert.ok(!fixes.some(([x, y]) => stroke.some(([sx, sy]) => sx === x && sy === y)), "painted cells stay as painted");
  assert.deepStrictEqual(SL.tidyPaint(["gggg", "gwgg", "gggg", "gggg"], [[2, 2, "p"]]), [[1, 1, "g"]]);
});

test("stroke interpolation and paint memory", () => {
  assert.deepStrictEqual(SL.lineCells({ x: 0, y: 0 }, { x: 3, y: 1 }).map((c) => [c.x, c.y]), [[0, 0], [1, 0], [2, 1], [3, 1]]);
  assert.deepStrictEqual(SL.lineCells({ x: 2, y: 2 }, { x: 2, y: 2 }), [{ x: 2, y: 2 }]);
  const m = SL.mergePaint({ "1,1": "g" }, [[1, 1, "w"], [2, 3, "f"]]);
  assert.deepStrictEqual(m, { "1,1": "w", "2,3": "f" });
  assert.deepStrictEqual(SL.paintList(m), [[1, 1, "w"], [2, 3, "f"]]);
  assert.deepStrictEqual(SL.paintList(null), []);
  assert.strictEqual(SL.paintKey({ name: "Testweg", width: 64, height: 72 }), "Testweg|64x72"); // same key as the first paint mode
  assert.strictEqual(SL.PAINT_KEY, "pokeroutes-paint-v1");
});

test("paint palette brushes: kinds, ledges, tree blocks, objects, exact tiles", () => {
  const palette = {
    tiler: "ark", terrain: [
      { code: "g", group: "ground" }, { code: "F", group: "trees", snap_blocks: true },
      { code: "s", group: "ledges" }, { code: "j", group: "ledges" }, { code: "e", group: "ledges" },
    ],
    unavailable: [{ code: "d" }],
    objects: [{ id: "stump", cells: [[1, 2], [3, 4]], classes: [["o", "o"], ["g", "o"]] }],
    tiles: { "7": "w" },
  };
  assert.deepStrictEqual(SL.brush_kind("F"), { kind: "terrain", key: "F" });
  assert.deepStrictEqual(SL.brush_kind(SL.LEDGE_BRUSH), { kind: "ledge", key: "ledge" });
  assert.deepStrictEqual(SL.brush_kind("object:split_oak"), { kind: "object", key: "split_oak" });
  assert.deepStrictEqual(SL.brush_kind("tile:286"), { kind: "tile", key: "286" });
  assert.deepStrictEqual(SL.palette_ledge_codes(palette), ["s", "j", "e"]);
  assert.strictEqual(SL.next_ledge_code(palette, "s"), "j");
  assert.strictEqual(SL.next_ledge_code(palette, "j"), "e");
  assert.strictEqual(SL.next_ledge_code(palette, "e"), "s");
  const four_ledges = Object.assign({}, palette, { terrain: ["e", "j", "x", "s"].map((code) => ({ code, group: "ledges" })) });
  const turns = [SL.next_ledge_code(four_ledges, "s")];
  for (let turn = 0; turn < 3; turn++) turns.push(SL.next_ledge_code(four_ledges, turns[turns.length - 1]));
  assert.deepStrictEqual(turns, ["x", "j", "e", "s"], "R turns ledges clockwise: south, west, north, east");
  assert.strictEqual(SL.next_ledge_code(null, "s"), "s");
  assert.strictEqual(SL.palette_class_codes(palette), "gFsjed");
  assert.strictEqual(SL.palette_terrain_entry(palette, "F").snap_blocks, true);
  assert.strictEqual(SL.palette_terrain_entry(palette, "q"), null);
  const snapped = SL.snap_cells_to_blocks([[3, 3, "F"], [4, 3, "F"]], 6, 5).map(([x, y]) => `${x},${y}`).sort();
  assert.deepStrictEqual(snapped, ["2,2", "2,3", "3,2", "3,3", "4,2", "4,3", "5,2", "5,3"]);
  assert.deepStrictEqual(SL.snap_cells_to_blocks([[4, 4, "f"]], 5, 5), [[4, 4, "f"]]);
  const stump = SL.palette_object_entry(palette, "stump");
  assert.deepStrictEqual(SL.object_stamp_cells(stump, 5, 5, 10, 10), [[5, 5, "o#1"], [6, 5, "o#2"], [5, 6, "g#3"], [6, 6, "o#4"]]);
  assert.deepStrictEqual(SL.object_stamp_cells(stump, 9, 5, 10, 10), [], "an object never sticks out of the map");
  assert.strictEqual(SL.exact_tile_value(palette, 7), "w#7");
  assert.strictEqual(SL.exact_tile_value(palette, 99), "g#99");
  assert.deepStrictEqual(SL.expand_paint_cells([[1, 2, "F#564"], [3, 4, "p"], [5, 6, "o#709"]]),
    [[1, 2, "F"], [1, 2, "#564"], [3, 4, "p"], [5, 6, "o"], [5, 6, "#709"]]);
});

test("whole objects: painting over any cell lifts the whole object; what it covered comes back", () => {
  const palette = { terrain: [], unavailable: [], tiles: {}, objects: [{ id: "pine", cells: [[1, 2], [3, 4]], classes: [["f", "f"], ["f", "f"]] }] };
  const generated = ["gggg", "gppg", "gggg"];
  const placed = SL.apply_paint_stroke({ remembered_cells: { "1,1": "p" }, remembered_objects: [], palette, generated_class_rows: generated,
    cells: { "1,0": "f#1", "2,0": "f#2", "1,1": "f#3", "2,1": "f#4" }, objects: [{ object: "pine", x: 1, y: 0 }] });
  assert.deepStrictEqual(placed.objects, [{ object: "pine", x: 1, y: 0, under: { "1,1": "p" } }]);
  assert.deepStrictEqual(placed.restore_cells, []);
  assert.strictEqual(SL.object_instance_index_at(palette, placed.objects, 2, 1), 0);
  assert.strictEqual(SL.object_instance_index_at(palette, placed.objects, 3, 1), -1);
  const painted_over = SL.apply_paint_stroke({ remembered_cells: placed.cells, remembered_objects: placed.objects, palette, generated_class_rows: generated,
    cells: { "2,1": "t" }, objects: [] });
  assert.deepStrictEqual(painted_over.objects, []);
  assert.deepStrictEqual(painted_over.restore_cells.sort(), [[1, 0, "g"], [1, 1, "p"], [2, 0, "g"]].sort());
  assert.deepStrictEqual(painted_over.cells, { "1,1": "p", "2,1": "t" });
  const deleted = SL.apply_paint_stroke({ remembered_cells: placed.cells, remembered_objects: placed.objects, lift: placed.objects, palette,
    generated_class_rows: generated, cells: {}, objects: [] });
  assert.strictEqual(deleted.restore_cells.length, 4);
  assert.deepStrictEqual(deleted.cells, { "1,1": "p" });
});

test("trees: one whole tree on the 2x2 grid; painting over a tree frees its whole block", () => {
  assert.deepStrictEqual(SL.tree_block_keys_around(3, 4, 10, 10), ["2,4", "3,4", "2,5", "3,5"]);
  assert.deepStrictEqual(SL.tree_block_keys_around(9, 9, 10, 10), ["8,8", "9,8", "8,9", "9,9"]);
  const forest = ["ffff", "ffff", "gggg"];
  const freed = SL.whole_trees_under_stroke([[1, 1, "p"]], forest, "fFR", "g").map(([x, y, code]) => `${x},${y},${code}`).sort();
  assert.deepStrictEqual(freed, ["0,0,g", "0,1,g", "1,0,g"]);
  assert.deepStrictEqual(SL.whole_trees_under_stroke([[1, 1, "f"]], forest, "fFR", "g"), [], "a tree painted on a tree frees nothing");
});

test("event sprite key: npc roles, alternating trainers, other kinds as-is", () => {
  assert.strictEqual(SL.event_sprite_key({ kind: "npc", role: "hiker", x: 1, y: 1 }), "npc_hiker");
  assert.strictEqual(SL.event_sprite_key({ kind: "npc", x: 1, y: 1 }), "npc");
  assert.strictEqual(SL.event_sprite_key({ kind: "trainer", x: 2, y: 4 }), "trainer");
  assert.strictEqual(SL.event_sprite_key({ kind: "trainer", x: 2, y: 5 }), "trainer_f");
  assert.strictEqual(SL.event_sprite_key({ kind: "story_blocker", x: 0, y: 0 }), "story_blocker");
});

function region_document_with_route(route_name, chain_rects, extra) {
  const chain = chain_rects.map((rect, map_index) => ({ name: chain_rects.length > 1 ? `${route_name} (${map_index + 1}/${chain_rects.length})` : route_name, id: "x", rect, exits: [] }));
  const route = Object.assign({ name: route_name, id: route_name.replace(/ /g, ""), rect: chain_rects[0], grid_cells: [], spec: { seed: 1, biome: "forest" }, chain }, extra || {});
  return { cell: 4, map_grid: { cell: 8 }, routes: [route], cities: [] };
}
const open_second_map = { route_name: "Route 3", map_name: "Route 3 (2/2)", rect: [8, 0, 16, 8] };

test("Map sync: an extended route keeps its open map and renames it after the new chain", () => {
  const extended = region_document_with_route("Route 3", [[0, 0, 8, 8], [8, 0, 16, 8], [16, 0, 24, 8]]);
  const sync_target = SL.sync_target_for_open_map(extended, open_second_map);
  assert.strictEqual(sync_target.chain_map.name, "Route 3 (2/3)");
  assert.strictEqual(sync_target.chain_map.id, "Route3_2");
});

test("Map sync: when the open map is erased the nearest map of the route opens", () => {
  const shortened = region_document_with_route("Route 3", [[0, 0, 8, 8]]);
  const sync_target = SL.sync_target_for_open_map(shortened, open_second_map);
  assert.deepStrictEqual([sync_target.chain_map.name, sync_target.chain_map.id], ["Route 3", "Route3"]);
});

test("Map sync: a renamed route is found under the open map; an erased route gives nothing", () => {
  const renamed = region_document_with_route("Coast Road", [[0, 0, 8, 8], [8, 0, 16, 8]]);
  assert.strictEqual(SL.sync_target_for_open_map(renamed, open_second_map).chain_map.name, "Coast Road (2/2)");
  const elsewhere = region_document_with_route("Route 9", [[40, 40, 48, 48]]);
  assert.strictEqual(SL.sync_target_for_open_map(elsewhere, open_second_map), null);
});

test("Map sync: the signature changes with the route's maps and square size but not with its seed", () => {
  const before = region_document_with_route("Route 3", [[0, 0, 8, 8], [8, 0, 16, 8]]);
  const signature = SL.open_map_signature(before, SL.sync_target_for_open_map(before, open_second_map));
  const reseeded = region_document_with_route("Route 3", [[0, 0, 8, 8], [8, 0, 16, 8]], { spec: { seed: 99, biome: "forest" } });
  assert.strictEqual(SL.open_map_signature(reseeded, SL.sync_target_for_open_map(reseeded, open_second_map)), signature);
  const extended = region_document_with_route("Route 3", [[0, 0, 8, 8], [8, 0, 16, 8], [8, 8, 16, 16]]);
  assert.notStrictEqual(SL.open_map_signature(extended, SL.sync_target_for_open_map(extended, open_second_map)), signature);
  const regridded = Object.assign({}, before, { map_grid: { cell: 12 } });
  assert.notStrictEqual(SL.open_map_signature(regridded, SL.sync_target_for_open_map(regridded, open_second_map)), signature);
});

test("Map sync: the seed and settings changed in the Routes view survive, the Map decides size and exits", () => {
  const opened = { name: "Route 3", width: 32, height: 32, seed: 5, tall_grass: 0.3, meander: 0.5, exits: [{ side: "west" }] };
  const current = Object.assign({}, opened, { seed: 77, tall_grass: 0.8 });
  const fresh = { name: "Route 3 (1/2)", width: 32, height: 32, seed: 5, tall_grass: 0.3, meander: 0.6, exits: [{ side: "west" }, { side: "east" }] };
  const synced = SL.params_after_map_sync(opened, current, fresh);
  assert.deepStrictEqual(synced, { name: "Route 3 (1/2)", width: 32, height: 32, seed: 77, tall_grass: 0.8, meander: 0.6, exits: fresh.exits });
});

test("Map sync: painted cells follow maps that are still there and are dropped for removed maps", () => {
  const memory = { "Route 3|32x32": { "1,1": "g" }, "Route 3 (2/2)|32x32": { "2,2": "w" }, "Other|32x32": { "3,3": "f" } };
  const old_chain = [{ name: "Route 3 (1/2)", rect: [0, 0, 8, 8] }, { name: "Route 3 (2/2)", rect: [8, 0, 16, 8] }];
  const memory_with_first_map = Object.assign({}, memory, { "Route 3 (1/2)|32x32": { "1,1": "g" } });
  const new_chain = [{ name: "Route 3", rect: [0, 0, 8, 8] }];
  const followed = SL.paint_memory_after_chain_change(memory_with_first_map, old_chain, new_chain, 4);
  assert.deepStrictEqual(followed, { "Route 3|32x32": { "1,1": "g" }, "Other|32x32": { "3,3": "f" } });
});

console.log(`shell-logic: ${n} tests passed`);

test("placed items: one per cell, edit, remove, remembered per route", () => {
  const ball = { kind: "item_ball", x: 3, y: 4, item: "ITEM_POTION" };
  let items = SL.place_item([], ball);
  items = SL.place_item(items, { kind: "hidden_item", x: 5, y: 6, item: "ITEM_NUGGET" });
  items = SL.place_item(items, { kind: "hidden_item", x: 3, y: 4, item: "ITEM_ETHER" });
  assert.strictEqual(items.length, 2);
  assert.strictEqual(SL.placed_item_index(items, 3, 4), 1);
  assert.strictEqual(items[1].item, "ITEM_ETHER");
  items = SL.update_placed_item(items, 0, { item: "ITEM_PEARL" });
  assert.strictEqual(items[0].item, "ITEM_PEARL");
  assert.strictEqual(SL.remove_placed_item(items, 0).length, 1);
  let memory = SL.remember_placed_items({}, "Route|48x80", items);
  assert.strictEqual(memory["Route|48x80"].length, 2);
  memory = SL.remember_placed_items(memory, "Route|48x80", []);
  assert.deepStrictEqual(memory, {});
});

test("placed items: a regenerate shows what fit and remembers what did not", () => {
  const remembered = [
    { kind: "item_ball", x: 1, y: 1, item: "ITEM_POTION" },
    { kind: "hidden_item", x: 2, y: 2, item: "ITEM_NUGGET" },
    { kind: "item_ball", x: 3, y: 3, item: "ITEM_ETHER" },
  ];
  const result = { placed: [remembered[0], remembered[2]], skipped: [{ item: remembered[1], reason: "cell is not walkable" }] };
  const merged = SL.merge_placement_result(remembered, result);
  assert.deepStrictEqual(merged.shown, [remembered[0], remembered[2]]);
  assert.deepStrictEqual(merged.remembered, remembered);
  assert.deepStrictEqual(SL.merge_placement_result(remembered, null).shown, []);
});
