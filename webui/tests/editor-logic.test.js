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
    height: new Float32Array(N), land: new Uint8Array(N), half: new Uint8Array(N), lake: new Uint8Array(N),
    river: new Uint8Array(N), biome: new Uint8Array(N), tiers: new Uint8Array(N), dirty: new Uint8Array(N),
    paint_biome: new Uint8Array(N), paint_water: new Uint8Array(N),
  };
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const i = y * W + x, inside = x >= 4 && x < 36 && y >= 4 && y < 26;
    g.land[i] = inside ? 1 : 0; g.half[i] = inside ? 1 : 0;
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
  // raise a sea cell above 0: it becomes land with a half
  const j = 1 * 40 + 20;
  ED.brushRaise(d, s, [[j, 1]], 1);
  assert.strictEqual(d.grids.land[j], 1);
  assert.ok(d.grids.half[j] >= 1);
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
