"use strict";
const assert = require("assert");
const path = require("path");
const TL = require(path.join(__dirname, "../static/tile-editor-logic.js"));

let passed_test_count = 0;
function test(test_name, test_body) {
  try {
    test_body();
    passed_test_count++;
  } catch (failure) {
    console.error("FAIL " + test_name);
    throw failure;
  }
}

const SOLID_ONE = "1".repeat(64);
const SOLID_TWO = "2".repeat(64);
const LEFT_COLUMN_THREE = Array.from({ length: 8 }, () => "3" + "0".repeat(7)).join("");
const BLANK = "0".repeat(64);
const HORIZONTAL_FLIP_BIT = 0x400;

function small_base() {
  const palettes = Array.from({ length: 16 }, (unused, palette_slot) => Array.from({ length: 16 }, (unused_colour, colour_index) => TL.colour_to_hex([colour_index * 16, palette_slot * 8, 0])));
  const base = {
    primary_tiles: [BLANK, SOLID_ONE, SOLID_TWO, LEFT_COLUMN_THREE],
    secondary_tiles: [SOLID_TWO],
    palettes,
    primary_metatiles: [[1, 1, 1, 1, 0, 0, 0, 0], [3 | HORIZONTAL_FLIP_BIT, 3, 3, 3, 0, 0, 0, 2 | (5 << 12)]],
    secondary_metatiles: [[512 | (6 << 12), 512, 512, 512, 0, 0, 0, 0]],
    primary_attributes: [0x1000, 0x1002],
    secondary_attributes: [0x1000],
  };
  return base;
}

test("entries decode tile, flips and palette", () => {
  assert.deepStrictEqual(TL.decode_tile_entry(3 | HORIZONTAL_FLIP_BIT | (5 << 12)), { tile_index: 3, is_flipped_horizontally: true, is_flipped_vertically: false, palette_slot: 5 });
});

test("overlay replaces tiles, palettes, metatiles and attributes", () => {
  const overlay = { tiles: { "1": SOLID_TWO, "513": SOLID_ONE }, palettes: { "2": Array(16).fill("#abcdef") },
    metatiles: { "513": [513, 513, 513, 513, 0, 0, 0, 0] }, metatile_attributes: { "513": 0x1021 }, custom_metatiles: { "513": { name: "x" } } };
  const model = TL.overlaid_model(small_base(), overlay);
  assert.strictEqual(model.tiles.get(1)[0], 2);
  assert.strictEqual(model.tiles.get(513)[5], 1);
  assert.strictEqual(model.palettes[2][4], "#abcdef");
  assert.deepStrictEqual(TL.sorted_metatile_ids(model), [0, 1, 512, 513]);
  assert.strictEqual(model.attributes.get(513), 0x1021);
  assert.ok(TL.is_custom_or_changed(model, 513));
  assert.ok(!TL.is_custom_or_changed(model, 0));
});

test("layer pixels follow the entry flips", () => {
  const model = TL.overlaid_model(small_base(), {});
  const bottom_pixels = TL.layer_pixels(model, 1, 0);
  assert.strictEqual(bottom_pixels[0 * 16 + 7], 3);
  assert.strictEqual(bottom_pixels[0 * 16 + 0], 0);
  assert.strictEqual(bottom_pixels[0 * 16 + 8], 3);
  assert.deepStrictEqual(TL.layer_palette_slots(model, 1, 1), [0, 0, 0, 5]);
});

test("the top layer is transparent where its colour is 0", () => {
  const model = TL.overlaid_model(small_base(), {});
  const rgba = TL.render_metatile_rgba(model, 1);
  const top_right_bottom_pixel = (15 * 16 + 15) * 4;
  const top_left_top_pixel = 0;
  assert.deepStrictEqual([...rgba.slice(top_right_bottom_pixel, top_right_bottom_pixel + 4)], [32, 40, 0, 255]);
  assert.deepStrictEqual([...rgba.slice(top_left_top_pixel, top_left_top_pixel + 4)], [0, 0, 0, 255]);
  const top_only = TL.layer_rgba(model, 1, 1);
  assert.strictEqual(top_only[3], 0);
});

test("flood fill stays inside one colour region", () => {
  const pixels = new Uint8Array(256);
  for (let row = 0; row < 16; row++) pixels[row * 16 + 8] = 1;
  const writes = TL.flood_fill_writes(pixels, 0, 0, 4);
  assert.strictEqual(writes.length, 8 * 16);
  assert.ok(writes.every(([pixel_x]) => pixel_x < 8));
  assert.deepStrictEqual(TL.flood_fill_writes(pixels, 0, 0, 0), []);
});

test("lines and mirrors", () => {
  assert.deepStrictEqual(TL.line_points(0, 0, 3, 1), [[0, 0], [1, 0], [2, 1], [3, 1]]);
  assert.deepStrictEqual(TL.line_points(2, 2, 2, 2), [[2, 2]]);
  assert.deepStrictEqual(TL.mirrored_points(1, 2, true, true), [[1, 2], [14, 2], [1, 13], [14, 13]]);
  assert.deepStrictEqual(TL.mirrored_points(1, 2, false, false), [[1, 2]]);
  const writes = TL.writes_for_points([[0, 0], [0, 0], [15, 0]], 7, true, false);
  assert.deepStrictEqual(writes, [[0, 0, 7], [15, 0, 7]]);
});

test("pixel writes make a new buffer", () => {
  const pixels = new Uint8Array(256);
  const updated_pixels = TL.pixels_with_writes(pixels, [[2, 3, 9]]);
  assert.strictEqual(updated_pixels[3 * 16 + 2], 9);
  assert.strictEqual(pixels[3 * 16 + 2], 0);
});

test("colours snap to the GBA's 15-bit range", () => {
  assert.strictEqual(TL.gba_colour_hex("#ff8107"), "#f88000");
  assert.deepStrictEqual(TL.colour_from_hex("#0a0b0c"), [10, 11, 12]);
});

test("reference counts and overlay differences", () => {
  const model = TL.overlaid_model(small_base(), {});
  assert.strictEqual(TL.tile_reference_count(model, 3), 4);
  assert.strictEqual(TL.tile_reference_count(model, 0), 11);
  assert.ok(!TL.overlays_differ({ revision: 1, tiles: { "1": SOLID_ONE }, palettes: {} }, { palettes: {}, tiles: { "1": SOLID_ONE }, revision: 4 }));
  assert.ok(TL.overlays_differ({ tiles: {} }, { tiles: { "1": SOLID_ONE } }));
});

console.log(`tile-editor-logic: ${passed_test_count} tests passed`);
