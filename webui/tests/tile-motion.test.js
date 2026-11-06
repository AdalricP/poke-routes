// node webui/tests/tile-motion.test.js
// Pure parts of the per-tile animation popover (webui/static/tile-motion.js).
"use strict";
const assert = require("assert");
const path = require("path");
const TM = require(path.join(__dirname, "../static/tile-motion.js"));

let passed_test_count = 0;
function test(test_name, test_body) {
  try { test_body(); passed_test_count++; } catch (error) { console.error("FAIL " + test_name); throw error; }
}

const looks = {
  overrides: { 715: { motion: "dance", speed: 2 } },
  defaults: { 715: { motion: "bop", speed: 1 }, 716: { motion: "bop", speed: 1 }, 20: { motion: "game", speed: 1 } },
};

test("an override wins over the tileset default", () => {
  assert.deepStrictEqual(TM.effective_motion(looks, 715), { is_override: true, motion: "dance", speed: 2 });
  assert.deepStrictEqual(TM.effective_motion(looks, 716), { is_override: false, motion: "bop", speed: 1 });
});

test("a tile with neither override nor default stays still", () => {
  assert.deepStrictEqual(TM.effective_motion(looks, 999), { is_override: false, motion: "off", speed: 1 });
});

test("in-game animated tiles only play their own frames or stop", () => {
  assert.strictEqual(TM.is_choosable_motion(looks, 20, "game"), true);
  assert.strictEqual(TM.is_choosable_motion(looks, 20, "off"), true);
  assert.strictEqual(TM.is_choosable_motion(looks, 20, "bop"), false);
  assert.strictEqual(TM.is_choosable_motion(looks, 715, "game"), false);
  assert.strictEqual(TM.is_choosable_motion(looks, 715, "roll"), true);
});

test("palette brushes name the metatiles they paint", () => {
  const palette = {
    objects: [{ id: "log", cells: [[713, 714]] }],
    terrain: [{ code: "c", sample: [777, 777, 1, 1] }],
  };
  assert.deepStrictEqual(TM.metatiles_of_palette_brush(palette, "object:log"), [713, 714]);
  assert.deepStrictEqual(TM.metatiles_of_palette_brush(palette, "tile:42"), [42]);
  assert.deepStrictEqual(TM.metatiles_of_palette_brush(palette, "c"), [777, 1]);
  assert.deepStrictEqual(TM.metatiles_of_palette_brush(palette, "object:none"), []);
  assert.deepStrictEqual(TM.metatiles_of_palette_brush(null, "c"), []);
});

test("the hovered metatile comes from the shown stage", () => {
  const state = { result: { width: 2, stages: [{ tiles: [1, 2, 3, 4] }, { tiles: [5, 6, 7, 8] }] }, stage: 1, hover: { x: 1, y: 1 } };
  assert.strictEqual(TM.hovered_metatile(state), 8);
  assert.strictEqual(TM.hovered_metatile(Object.assign({}, state, { hover: null })), null);
});

test("labels", () => {
  assert.strictEqual(TM.speed_label(0.5), "slow");
  assert.strictEqual(TM.speed_label(3), "3×");
  assert.strictEqual(TM.motion_label("off"), "still");
});

console.log(`tile-motion: ${passed_test_count} tests passed`);
