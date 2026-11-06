// node webui/tests/editor-logic.test.js   (optional: REGION_DOC=path/to/doc.json for a real region)
"use strict";
const assert = require("assert");
const fs = require("fs");
const ED = require("../static/editor/logic.js");

let passed = 0;
function test(name, fn) {
  try { fn(); passed++; console.log("ok  ", name); } catch (e) { console.log("FAIL", name); throw e; }
}

// a small synthetic region: land in the middle, sea around, two cities and two routes
function makeDoc() {
  const W = 40, H = 30, N = W * H;
  const g = {
    height: new Float32Array(N), land: new Uint8Array(N), landmass: new Uint8Array(N), lake: new Uint8Array(N),
    river: new Uint8Array(N), biome: new Uint8Array(N), tiers: new Uint8Array(N), dirty: new Uint8Array(N),
    paint_biome: new Uint8Array(N), paint_water: new Uint8Array(N),
  };
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const i = y * W + x, inside = x >= 4 && x < 36 && y >= 4 && y < 26;
    g.land[i] = inside ? 1 : 0; g.landmass[i] = inside ? 1 : 0;
    g.height[i] = inside ? 0.1 + 0.01 * x : -0.2;
    g.biome[i] = (inside ? "m" : "~").charCodeAt(0);
  }
  const dtypes = {};
  for (const k of Object.keys(g)) dtypes[k] = g[k] instanceof Float32Array ? "f4" : "u1";
  return {
    format: "pokeroutes-region-edit", seed: 1, size: [W, H], W, H, grids: g, dtypes, stale: [], meta: { den_gate: [20, 20] },
    cities: [{ id: "a", name: "Ahlen", rect: [5, 10, 10, 15] }, { id: "b", name: "Bertsch", rect: [28, 10, 33, 15] }],
    landmarks: [{ id: "romowe", name: "Romowe", x: 20, y: 22, footprint: [18, 20, 23, 25] },
      { id: "sky_pillar", name: "Pillar", x: 20, y: 22, footprint: [19, 21, 22, 24] }],
    links: [{ name: "A-B", kind: "main", routes: ["Eins", "Zwei"] }],
    routes: [
      { name: "Eins", id: "Eins", map: "MAP_EINS", rect: [10, 8, 19, 17], link: "A-B", path: [[10, 12], [18, 12]],
        exits: [{ side: "west", cell: [10, 12], to: "Ahlen" }, { side: "east", cell: [18, 12], to: "Zwei" }] },
      { name: "Zwei", id: "Zwei", map: "MAP_ZWEI", rect: [19, 8, 28, 17], link: "A-B", path: [[19, 12], [27, 12]],
        exits: [{ side: "west", cell: [19, 12], to: "Eins" }, { side: "east", cell: [27, 12], to: "Bertsch" }] },
    ],
  };
}

test("grid base64 round trip keeps every value", () => {
  const a = new Float32Array([0, -0.25, 1.5, 3.14159, -7]);
  const b = ED.decodeGrid(ED.encodeGrid(a, "f4"));
  assert.deepStrictEqual(Array.from(b), Array.from(a));
  const c = new Int32Array([-1, 0, 123456, -99]);
  assert.deepStrictEqual(Array.from(ED.decodeGrid(ED.encodeGrid(c, "i4"))), Array.from(c));
});

test("decodeDoc / encodeDoc round trip", () => {
  const d = makeDoc();
  const wire = ED.encodeDoc(d);
  assert.ok(!("W" in wire) && !("dtypes" in wire));
  const back = ED.decodeDoc(wire);
  assert.strictEqual(back.W, 40);
  assert.deepStrictEqual(Array.from(back.grids.height), Array.from(d.grids.height));
});

test("brush cells: falloff, bounds, single cell at r < 1", () => {
  const c = ED.brushCells(40, 30, 0, 0, 3);
  assert.ok(c.every(([i]) => i >= 0 && i < 1200));
  assert.strictEqual(c.find(([i]) => i === 0)[1], 1);
  assert.deepStrictEqual(ED.brushCells(40, 30, 5, 5, 0.5).map(([i]) => i), [5 * 40 + 5]);
});

test("line cells are 8-connected and include both ends", () => {
  const l = ED.lineCells(0, 0, 7, 3);
  assert.deepStrictEqual(l[0], [0, 0]);
  assert.deepStrictEqual(l[l.length - 1], [7, 3]);
  for (let k = 1; k < l.length; k++) assert.ok(Math.abs(l[k][0] - l[k - 1][0]) <= 1 && Math.abs(l[k][1] - l[k - 1][1]) <= 1);
});

test("raise / lower move height, mark dirty, and cross sea level", () => {
  const d = makeDoc(), h = new ED.History(100);
  const s = h.begin(d);
  const i = 12 * 40 + 20;
  const before = d.grids.height[i];
  ED.brushRaise(d, s, ED.brushCells(40, 30, 20, 12, 2), 0.1);
  assert.ok(d.grids.height[i] > before);
  assert.strictEqual(d.grids.dirty[i], 1);
  // lower a land cell well under sea level: it becomes sea
  ED.brushRaise(d, s, [[i, 1]], -5);
  assert.strictEqual(d.grids.land[i], 0);
  assert.strictEqual(String.fromCharCode(d.grids.biome[i]), "~");
  // raise a sea cell above 0: it becomes land with a landmass label
  const j = 1 * 40 + 20;
  ED.brushRaise(d, s, [[j, 1]], 1);
  assert.strictEqual(d.grids.land[j], 1);
  assert.ok(d.grids.landmass[j] >= 1);
});

test("flatten pulls toward the target, smooth evens out a spike", () => {
  const d = makeDoc(), h = new ED.History();
  let s = h.begin(d);
  const i = 12 * 40 + 20;
  ED.brushFlatten(d, s, [[i, 1]], 0.5, 1);
  assert.ok(Math.abs(d.grids.height[i] - 0.5) < 1e-6);
  s = h.begin(d);
  ED.brushSmooth(d, s, [[i, 1]], 1);
  assert.ok(d.grids.height[i] < 0.5 && d.grids.height[i] > 0.2);
});

test("biome paint sets override on land only; clearing keeps the biome", () => {
  const d = makeDoc(), s = new ED.History().begin(d);
  const cells = ED.brushCells(40, 30, 4, 4, 2);
  const n = ED.paintBiome(d, s, cells, "F");
  assert.ok(n > 0 && n < cells.length);
  const land = cells.find(([i]) => d.grids.land[i])[0];
  assert.strictEqual(String.fromCharCode(d.grids.paint_biome[land]), "F");
  assert.strictEqual(String.fromCharCode(d.grids.biome[land]), "F");
  const sea = cells.find(([i]) => !d.grids.land[i])[0];
  assert.strictEqual(d.grids.paint_biome[sea], 0);
  ED.paintBiome(d, s, cells, "");
  assert.strictEqual(d.grids.paint_biome[land], 0);
});

test("water paint: sea, lake, land", () => {
  const d = makeDoc(), s = new ED.History().begin(d);
  const i = 15 * 40 + 15;
  ED.paintWater(d, s, [[i, 1]], "sea");
  assert.strictEqual(d.grids.land[i], 0);
  assert.ok(d.grids.height[i] <= -0.05);
  assert.strictEqual(d.grids.paint_water[i], 2);
  ED.paintWater(d, s, [[i, 1]], "lake");
  assert.strictEqual(d.grids.land[i], 1);
  assert.strictEqual(d.grids.lake[i], 1);
  ED.paintWater(d, s, [[i, 1]], "land");
  assert.strictEqual(d.grids.lake[i], 0);
  assert.strictEqual(d.grids.paint_water[i], 1);
});


test("undo / redo with copy-on-write grids, 120 steps, limit honoured", () => {
  const d = makeDoc(), h = new ED.History(100);
  const orig = d.grids.height;
  const i = 12 * 40 + 20;
  const v0 = d.grids.height[i];
  for (let k = 0; k < 120; k++) {
    const s = h.begin(d, "raise " + k);
    ED.brushRaise(d, s, [[i, 1]], 0.001);
  }
  assert.strictEqual(orig[i], v0, "the original array is never written to");
  assert.strictEqual(h.undos.length, 100);
  const top = d.grids.height[i];
  let n = 0;
  while (h.undo(d)) n++;
  assert.strictEqual(n, 100);
  assert.ok(Math.abs(d.grids.height[i] - (v0 + 0.02)) < 1e-5); // 20 steps fell off the end
  while (h.redo(d)) n--;
  assert.strictEqual(n, 0);
  assert.ok(Math.abs(d.grids.height[i] - top) < 1e-6);
  // a new edit clears redo
  h.undo(d);
  h.begin(d);
  assert.strictEqual(h.redos.length, 0);
});

test("cancel of a no-op edit does not wipe the redo stack", () => {
  const d = makeDoc(), h = new ED.History();
  h.begin(d, "move city"); ED.moveCity(d, "a", 1, 0);
  h.undo(d);
  assert.strictEqual(h.redos.length, 1);
  h.begin(d, "no-op rename");
  h.cancel(); // the rename turned out to be a no-op, so it's discarded
  assert.strictEqual(h.redos.length, 1, "the redo entry from before survives a cancelled edit");
  assert.strictEqual(h.redo(d), "move city");
});

test("undo restores object edits (rename, move, delete)", () => {
  const d = makeDoc(), h = new ED.History();
  h.begin(d); ED.renameRoute(d, "Eins", "Möwenstraße");
  h.begin(d); ED.moveCity(d, "a", 1, 2);
  h.begin(d); ED.deleteRoute(d, "Zwei");
  assert.strictEqual(d.routes.length, 1);
  h.undo(d); h.undo(d); h.undo(d);
  assert.deepStrictEqual(d.routes.map((r) => r.name), ["Eins", "Zwei"]);
  assert.deepStrictEqual(d.cities[0].rect, [5, 10, 10, 15]);
});

test("stale marking and which stages a button runs", () => {
  let st = ED.markStale([], "biome");
  assert.deepStrictEqual(st, ["routes"]);
  st = ED.markStale(st, "terrain");
  assert.deepStrictEqual(st, ["water", "climate", "biomes", "routes"]);
  assert.deepStrictEqual(ED.stagesFor("water", st), ["water", "climate", "biomes"]);
  assert.deepStrictEqual(ED.stagesFor("biomes", st), ["biomes"]);
  assert.deepStrictEqual(ED.stagesFor("routes", st), ["routes"]);
  assert.deepStrictEqual(ED.stagesFor("stale", ["routes", "climate"]), ["climate", "routes"]);
});

test("rename route updates ids, neighbours' exits and links", () => {
  const d = makeDoc();
  assert.ok(ED.renameRoute(d, "Eins", "Möwenstraße"));
  const r = d.routes[0];
  assert.strictEqual(r.id, "Moewenstrasse");
  assert.strictEqual(r.map, "MAP_MOEWENSTRASSE");
  assert.strictEqual(d.routes[1].exits[0].to, "Möwenstraße");
  assert.deepStrictEqual(d.links[0].routes, ["Möwenstraße", "Zwei"]);
  assert.ok(r.pinned && r.manual.name);
  assert.ok(!ED.renameRoute(d, "Zwei", "Ahlen"), "can't take a city's name");
});

test("city rename follows into exits", () => {
  const d = makeDoc();
  ED.renamePlace(d, "city", "a", "Altstadt");
  assert.strictEqual(d.routes[0].exits[0].to, "Altstadt");
});

test("rename route or place carries story beats to the new name", () => {
  const d = makeDoc();
  d.story = [{ name: "start", at: "Ahlen", unlocks: "" }, { name: "road", at: "Eins", unlocks: "" }];
  ED.renamePlace(d, "city", "a", "Altstadt");
  assert.strictEqual(d.story[0].at, "Altstadt");
  ED.renameRoute(d, "Eins", "Möwenstraße");
  assert.strictEqual(d.story[1].at, "Möwenstraße");
});

test("city rename rejects a name already used by a route or city", () => {
  const d = makeDoc();
  assert.ok(!ED.renamePlace(d, "city", "a", "Bertsch"), "can't take another city's name");
  assert.ok(!ED.renamePlace(d, "city", "a", "Eins"), "can't take a route's name");
  assert.strictEqual(d.cities[0].name, "Ahlen");
});

test("move exit stays on the shared edge and drags the neighbour's exit", () => {
  const d = makeDoc();
  assert.ok(ED.moveExit(d, "Eins", 1, 14));
  assert.deepStrictEqual(d.routes[0].exits[1].cell, [18, 14]);
  assert.deepStrictEqual(d.routes[1].exits[0].cell, [19, 14]);
  ED.moveExit(d, "Eins", 1, 999); // clamped to the shared stretch, off the corners
  assert.deepStrictEqual(d.routes[0].exits[1].cell, [18, 15]);
  // city exit is limited by the city's edge (y 10..14)
  ED.moveExit(d, "Eins", 0, 30);
  assert.deepStrictEqual(d.routes[0].exits[0].cell, [10, 14]);
  assert.deepStrictEqual(ED.routeWarnings(d, d.routes[0]), []);
});

test("resize moves exits with their edge, clips the path, warns when not flush", () => {
  const d = makeDoc();
  assert.ok(ED.resizeRoute(d, "Eins", "x1", 17));
  const r = d.routes[0];
  assert.deepStrictEqual(r.rect, [10, 8, 17, 17]);
  assert.deepStrictEqual(r.exits[1].cell, [16, 12]);
  assert.ok(r.path.every(([x]) => x < 17));
  assert.ok(ED.routeWarnings(d, r).some((w) => /flush/.test(w)));
  assert.ok(!ED.resizeRoute(d, "Eins", "x1", 11) || r.rect[2] - r.rect[0] >= 6, "min width");
  ED.resizeRoute(d, "Eins", "x1", 21);
  assert.ok(ED.routeWarnings(d, r).some((w) => /overlaps Zwei/.test(w)));
});

test("delete route drops its link and pins what's left of it", () => {
  const d = makeDoc();
  ED.deleteRoute(d, "Eins");
  assert.deepStrictEqual(d.routes.map((r) => r.name), ["Zwei"]);
  assert.ok(d.links[0].dropped);
  assert.ok(d.routes[0].pinned);
  assert.ok(!d.routes[0].exits.some((e) => e.to === "Eins"));
});

test("delete route clears beats pinned to it", () => {
  const d = makeDoc();
  d.story = [{ name: "start", at: "Ahlen", unlocks: "" }, { name: "road", at: "Eins", unlocks: "" }];
  ED.deleteRoute(d, "Eins");
  assert.strictEqual(d.story[1].at, "");
  assert.strictEqual(d.story[0].at, "Ahlen");
});

test("landmark move carries the pillar and the gate", () => {
  const d = makeDoc();
  ED.moveLandmark(d, "romowe", 2, -1);
  assert.deepStrictEqual([d.landmarks[0].x, d.landmarks[0].y], [22, 21]);
  assert.deepStrictEqual([d.landmarks[1].x, d.landmarks[1].y], [22, 21]);
  assert.deepStrictEqual(d.meta.den_gate, [22, 19]);
});

test("moveBeat swaps route gates along with the beats", () => {
  const d = makeDoc();
  d.story = [{ name: "a", at: "Ahlen", unlocks: "" }, { name: "b", at: "Ahlen", unlocks: "" }];
  d.routes[0].gate = { after: 0 };
  d.routes[1].gate = { after: 1 };
  assert.ok(ED.moveBeat(d, 0, 1));
  assert.deepStrictEqual(d.story.map((b) => b.name), ["b", "a"]);
  assert.strictEqual(d.routes[0].gate.after, 1);
  assert.strictEqual(d.routes[1].gate.after, 0);
  assert.ok(!ED.moveBeat(d, 0, -1), "out of range move is rejected");
});

test("removeBeat renumbers or clears route gates", () => {
  const d = makeDoc();
  d.story = [{ name: "a", at: "Ahlen", unlocks: "" }, { name: "b", at: "Ahlen", unlocks: "" }, { name: "c", at: "Ahlen", unlocks: "" }];
  d.routes[0].gate = { after: 1 }; // points at "b", the one being removed
  d.routes[1].gate = { after: 2 }; // points at "c", which shifts down to index 1
  assert.ok(ED.removeBeat(d, 1));
  assert.deepStrictEqual(d.story.map((b) => b.name), ["a", "c"]);
  assert.ok(!("gate" in d.routes[0]), "gate on the removed beat is cleared");
  assert.strictEqual(d.routes[1].gate.after, 1);
});

test("landmark move clamped at the map edge keeps its footprint attached", () => {
  const d = makeDoc();
  const l = d.landmarks[0]; // romowe at (20, 22), W=40 H=30
  const fpBefore = l.footprint.slice();
  ED.moveLandmark(d, "romowe", 5000, 0); // clamps x to W - 1 = 39
  assert.strictEqual(l.x, d.W - 1);
  const dx = l.x - 20;
  assert.deepStrictEqual(l.footprint, [fpBefore[0] + dx, fpBefore[1], fpBefore[2] + dx, fpBefore[3]]);
});

test("placeAt priorities and refs", () => {
  const d = makeDoc();
  assert.strictEqual(ED.placeAt(d, 20, 22).kind, "landmark");
  assert.strictEqual(ED.placeAt(d, 6, 11).kind, "city");
  assert.strictEqual(ED.placeAt(d, 12, 9).id, "Eins");
  assert.strictEqual(ED.placeAt(d, 1, 1), null);
  assert.deepStrictEqual(ED.refOf(ED.placeAt(d, 6, 11)), { city: "a" });
});

test("spec overrides set, clear, and pin", () => {
  const r = {};
  ED.setSpecField(r, "special_area_chance", 0.1);
  assert.deepStrictEqual(r.spec_overrides, { special_area_chance: 0.1 });
  assert.ok(r.pinned);
  ED.setSpecField(r, "special_area_chance", undefined);
  assert.ok(!("spec_overrides" in r));
});

test("bench params from a spec", () => {
  const p = ED.benchParams({ name: "X", seed: 5, size: [48, 64], biome: "forest", meander: 0.5, special_area_chance: 0.2,
    exits: [{ side: "west", at: 0.4, width: 3, to: "MAP_A" }], region: { goal: { side: "east" } }, special_areas: ["auto"] });
  assert.strictEqual(p.width, 48);
  assert.strictEqual(p.special_area_chance, 0.2);
  assert.strictEqual(p.exits.length, 2);
  assert.strictEqual(p.exits[1].side, "east");
  assert.ok(!("region" in p));
});

test("renderBase fills every pixel of the rect, sea darker than land", () => {
  const d = makeDoc();
  const S = 3, data = new Uint8ClampedArray(40 * S * 30 * S * 4);
  ED.renderBase(d, { height: true, biome: true, water: true }, S, data, [0, 0, 40, 30]);
  let unset = 0;
  for (let k = 3; k < data.length; k += 4) if (data[k] !== 255) unset++;
  assert.strictEqual(unset, 0);
  const px = (x, y) => { const o = ((y * S + 1) * 40 * S + x * S + 1) * 4; return data[o] + data[o + 1] + data[o + 2]; };
  assert.ok(px(1, 1) < px(20, 12));
});

test("story: a gated route opens once a beat hands out the move", () => {
  const d = makeDoc();
  d.routes[1].gate = { needs: "cut" };
  assert.deepStrictEqual([...ED.reachableAt(d, -1)].sort(), ["Ahlen", "Eins"]);
  d.story = [{ name: "starter", at: "Ahlen", unlocks: "" }, { name: "badge 1", at: "Bertsch", unlocks: "" }];
  assert.strictEqual(ED.storyWarnings(d).length, 1);
  d.story[0].unlocks = "cut";
  assert.ok(ED.reachableAt(d, 0).has("Bertsch"));
  assert.strictEqual(ED.storyWarnings(d).length, 0);
  d.routes[0].gate = { after: 1 };
  assert.ok(!ED.reachableAt(d, 0).has("Eins"));
  assert.ok(ED.reachableAt(d, 1).has("Eins"));
  assert.strictEqual(ED.routeNeeds({ mode: "sea" }), "surf");
  assert.strictEqual(ED.routeNeeds({ mode: "sea", gate: { needs: "" } }), "");
});

test("towns placed by hand: next free name, only on dry land away from other towns", () => {
  const d = makeDoc();
  assert.strictEqual(ED.next_town_name(d), "Town 1");
  d.cities.push({ id: "town1", name: "Town 1", rect: [15, 18, 25, 27] });
  assert.strictEqual(ED.next_town_name(d), "Town 2");
  assert.strictEqual(ED.town_spot_problem(d, 1, 1), "a town has to stand on dry land");
  assert.strictEqual(ED.town_spot_problem(d, 7, 12), "too close to another town");
  assert.strictEqual(ED.town_spot_problem(d, 99, 1), "that is off the map");
  assert.strictEqual(ED.town_spot_problem(d, 20, 6), "");
});

function place_of(doc, kind, id) {
  const obj = kind === "city" ? doc.cities.find((c) => c.id === id) : ED.routeByName(doc, id);
  return { kind, id, obj };
}

test("drawn lines: a leg goes along the longer axis first, Shift flips it", () => {
  assert.strictEqual(ED.leg_toward([0, 0], [5, 2], false), "h");
  assert.strictEqual(ED.leg_toward([0, 0], [5, 2], true), "v");
  assert.strictEqual(ED.leg_toward([0, 0], [1, 5], false), "v");
});

test("drawn lines: waypoints and legs snap to right-angle corners", () => {
  assert.deepStrictEqual(ED.corners_of_line([[0, 0], [5, 3]], ["h"]), [[0, 0], [5, 0], [5, 3]]);
  assert.deepStrictEqual(ED.corners_of_line([[0, 0], [5, 3]], ["v"]), [[0, 0], [0, 3], [5, 3]]);
  assert.deepStrictEqual(ED.corners_of_line([[0, 0], [4, 0], [4, 0]], ["h", "h"]), [[0, 0], [4, 0]]);
  assert.deepStrictEqual(ED.corners_of_line([[2, 2], [6, 6], [9, 2]], ["h", "v"]), [[2, 2], [6, 2], [6, 6], [6, 2], [9, 2]]);
});

test("drawn lines: corners become 4-connected cells, both ends included", () => {
  const cells = ED.cells_of_corners([[0, 0], [3, 0], [3, -2]]);
  assert.deepStrictEqual(cells, [[0, 0], [1, 0], [2, 0], [3, 0], [3, -1], [3, -2]]);
  for (let k = 1; k < cells.length; k++) assert.strictEqual(Math.abs(cells[k][0] - cells[k - 1][0]) + Math.abs(cells[k][1] - cells[k - 1][1]), 1);
});

test("drawn lines: a town anchors at its centre, a route where it was clicked", () => {
  const d = makeDoc();
  assert.deepStrictEqual(ED.anchor_of_place(place_of(d, "city", "a"), 6.2, 11.7), [7, 12]);
  assert.deepStrictEqual(ED.anchor_of_place(place_of(d, "route", "Eins"), 12.6, 9.1), [12, 9]);
});

test("drawn lines: problems are lakes, the map edge and other places in the way", () => {
  const d = makeDoc();
  const ahlen = place_of(d, "city", "a");
  const down_and_across = (y) => ED.cells_of_corners(ED.corners_of_line([[7, 12], [7, y], [20, y]], ["v", "h"]));
  assert.strictEqual(ED.drawn_line_problem(d, down_and_across(20), ahlen, null), "");
  d.grids.lake[18 * d.W + 7] = 1;
  assert.match(ED.drawn_line_problem(d, down_and_across(20), ahlen, null), /lake/);
  d.grids.lake[18 * d.W + 7] = 0;
  assert.match(ED.drawn_line_problem(d, ED.cells_of_corners([[7, 12], [7, 28]]), ahlen, null), /map edge/);
  const east = ED.cells_of_corners([[7, 12], [30, 12]]);
  assert.strictEqual(ED.drawn_line_problem(d, east, ahlen, null), "crosses Eins");
  assert.strictEqual(ED.drawn_line_problem(d, east, ahlen, place_of(d, "route", "Eins")), "");
  assert.match(ED.drawn_line_problem(d, ED.cells_of_corners([[20, 20], [20, 22]]), ahlen, null), /start on/);
});

function doc_with_drawn_line() {
  const d = makeDoc();
  d.links.push({ name: "Drawn line 1", kind: "drawn", routes: ["Drei"], waypoints: [[7, 12], [7, 20], [20, 20]], legs: ["v", "h"],
    src_ref: { city: "a" }, dst_ref: { route: "Zwei" } });
  d.routes.push({ name: "Drei", id: "Drei", map: "MAP_DREI", rect: [4, 15, 22, 24], link: "Drawn line 1", drawn: true, pinned: true,
    path: [[7, 15], [7, 20], [20, 20]], exits: [{ side: "north", cell: [7, 15], to: "Ahlen" }, { side: "east", cell: [21, 20], to: "Zwei" }] });
  d.routes[1].exits.push({ side: "south", cell: [20, 16], to: "Drei" });
  d.story = [{ name: "rival", at: "Drei" }];
  return d;
}

test("drawn lines: a drawn route finds its line; others have none", () => {
  const d = doc_with_drawn_line();
  assert.strictEqual(ED.drawn_line_of_route(d, ED.routeByName(d, "Drei")).name, "Drawn line 1");
  assert.strictEqual(ED.drawn_line_of_route(d, ED.routeByName(d, "Eins")), null);
});

test("drawn lines: moving a bend copies the waypoints", () => {
  const waypoints = [[1, 1], [5, 1], [5, 5]];
  const moved = ED.with_waypoint_moved(waypoints, 1, 6, 2);
  assert.deepStrictEqual(moved, [[1, 1], [6, 2], [5, 5]]);
  assert.deepStrictEqual(waypoints[1], [5, 1]);
});

test("drawn lines: deleting one removes its routes, the exits into them, the line and story places", () => {
  const d = doc_with_drawn_line();
  assert.ok(ED.delete_drawn_line(d, "Drawn line 1"));
  assert.ok(!ED.routeByName(d, "Drei"));
  assert.ok(!d.routes.some((r) => r.exits.some((e) => e.to === "Drei")));
  assert.ok(!d.links.some((l) => l.name === "Drawn line 1"));
  assert.strictEqual(d.story[0].at, "");
  assert.strictEqual(ED.delete_drawn_line(d, "Drawn line 1"), false);
});

test("town map: route bands keep only their corners and run into the towns they reach", () => {
  assert.deepStrictEqual(ED.without_straight_runs([[0, 0], [1, 0], [2, 0], [2, 1], [2, 2]]), [[0, 0], [2, 0], [2, 2]]);
  const d = makeDoc();
  const lines = ED.town_map_route_lines(d);
  const into_ahlen = lines.filter((l) => l.route.name === "Eins" && l.points[l.points.length - 1][0] === 7.5);
  assert.strictEqual(into_ahlen.length, 1);
  assert.deepStrictEqual(into_ahlen[0].points[into_ahlen[0].points.length - 1], [7.5, 12.5]);
  ED.routeByName(d, "Zwei").path = [[21, 10], [22, 10]];
  const zwei_to_eins = ED.town_map_route_lines(d).find((l) => l.route.name === "Zwei" && l.points.length > 3);
  assert.deepStrictEqual(zwei_to_eins.points, [[21.5, 10.5], [21.5, 12.5], [19.5, 12.5], [18.5, 12.5]]);
  assert.deepStrictEqual(ED.point_halfway_along([[0, 0], [4, 0], [4, 4]]), [4, 0]);
});

test("town map: the sea is lighter along the coast, land and lakes get their own colours", () => {
  const d = makeDoc();
  const distance = ED.sea_distance_to_land(d, 3);
  assert.strictEqual(distance[10 * d.W + 4], 0);
  assert.strictEqual(distance[10 * d.W + 3], 1);
  assert.strictEqual(distance[10 * d.W + 0], 4);
  const layers = { height: true, biome: true, water: true };
  assert.deepStrictEqual(ED.town_map_cell_color(d, 10 * d.W + 3, layers, 1, distance), ED.TOWN_MAP_RGB.halo);
  assert.deepStrictEqual(ED.town_map_cell_color(d, 10 * d.W + 0, layers, 1, distance), ED.TOWN_MAP_RGB.sea);
  d.grids.lake[12 * d.W + 20] = 1;
  assert.deepStrictEqual(ED.town_map_cell_color(d, 12 * d.W + 20, layers, 1, distance), ED.TOWN_MAP_RGB.lake);
  const data = new Uint8ClampedArray(d.W * 2 * d.H * 2 * 4);
  ED.renderTownMap(d, layers, 2, data, null, null, distance);
  for (let o = 3; o < data.length; o += 4) assert.strictEqual(data[o], 255);
  const halo_pixel = ((10 * 2) * d.W * 2 + 3 * 2 + 1) * 4;
  assert.deepStrictEqual(Array.from(data.slice(halo_pixel, halo_pixel + 3)), ED.TOWN_MAP_RGB.halo);
});

if (process.env.REGION_DOC) {
  test("real region document: decode, edit, encode", () => {
    const wire = JSON.parse(fs.readFileSync(process.env.REGION_DOC, "utf8"));
    const d = ED.decodeDoc(wire);
    assert.strictEqual(d.grids.height.length, d.W * d.H);
    const h = new ED.History();
    const s = h.begin(d);
    ED.brushRaise(d, s, ED.brushCells(d.W, d.H, 60, 60, 5), 0.2);
    const back = ED.encodeDoc(d);
    assert.strictEqual(Object.keys(back.grids).length, Object.keys(wire.grids).length);
    assert.notStrictEqual(back.grids.height.b64, wire.grids.height.b64);
    assert.strictEqual(back.grids.biome.b64, wire.grids.biome.b64);
    const data = new Uint8ClampedArray(d.W * 2 * d.H * 2 * 4);
    const t = Date.now();
    ED.renderBase(d, { height: true, biome: true, water: true }, 2, data);
    console.log(`      full render at 2 px/cell: ${Date.now() - t} ms`);
  });
}

console.log(`${passed} passed`);
