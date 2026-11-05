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
    { label: "View", items: [{ head: "x" }].concat(ids(["zin", "zout", "zfit", "z100", "frame", "ov-areas", "ov-items", "ov-classes", "ov-grid"])) },
    { label: "Generate", items: ids(["gen", "regen", "gen-region", "play", "next", "prev", "first", "last"]) },
    { label: "Window", items: ids(["w-scene", "w-reset"]) },
    { label: "Help", items: ids(["keys", "about"]) },
  ];
  const extra = ids(["tool-select", "tool-pan", "tool-paint", "tool-stamp", "tool-tile", "x-zin", "x-zout", "x-zfit", "x-help", "x-ledge-rot"]);
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
  const s = ev({ key: "s", code: "KeyS" });
  assert.strictEqual(SL.findShortcut(menus, s, "none", true, extra).id, "tool-stamp");
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
  assert.strictEqual(params.exits.length, 2);       // a stand-in exit on the goal side
  assert.deepStrictEqual(params.exits[1], { side: "east", at: 0.5, width: 2 });
  assert.strictEqual(params.exits[0].to, "Kiefernheide");
  assert.deepStrictEqual(region, { name: "Route 3", region: spec.region, endings: ["cave"] });
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

console.log(`shell-logic: ${n} tests passed`);
