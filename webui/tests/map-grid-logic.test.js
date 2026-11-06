"use strict";
const assert = require("assert");
const MAP_GRID = require("../static/editor/map-grid-logic.js");
const ED = require("../static/editor/logic.js");

let passed = 0;
function test(name, check) {
  try { check(); passed++; console.log("ok  ", name); } catch (error) { console.log("FAIL", name); throw error; }
}

function painted_doc() {
  return {
    map_grid: { cell: 8, size: [6, 4], rows: ["......", ".trrt.", "..r...", "..r..."] },
    landmarks: [],
    cities: [
      { id: "ahlen", name: "Ahlen", rect: [8, 8, 16, 16], grid_cells: [[1, 1]] },
      { id: "bertsch", name: "Bertsch", rect: [32, 8, 40, 16], grid_cells: [[4, 1]] },
    ],
    routes: [
      { name: "Route 1", mode: "land", rect: [16, 8, 32, 16], grid_cells: [[2, 1], [3, 1]], goal: null,
        exits: [{ side: "west", cell: [16, 12], to: "Ahlen" }, { side: "east", cell: [31, 12], to: "Bertsch" }] },
      { name: "Route 2", mode: "land", rect: [16, 16, 24, 32], grid_cells: [[2, 2], [2, 3]], goal: [20, 28],
        exits: [{ side: "north", cell: [20, 16], to: "Route 1" }] },
    ],
  };
}

test("a drag fills the cells between pointer samples along straight runs", () => {
  assert.deepStrictEqual(MAP_GRID.cells_between([0, 0], [3, 1]), [[1, 0], [2, 0], [3, 0], [3, 1]]);
  assert.deepStrictEqual(MAP_GRID.cells_between([2, 2], [2, 0]), [[2, 1], [2, 0]]);
  let stroke = MAP_GRID.stroke_with_cell([], [1, 1]);
  stroke = MAP_GRID.stroke_with_cell(stroke, [3, 1]);
  stroke = MAP_GRID.stroke_with_cell(stroke, [3, 1]);
  stroke = MAP_GRID.stroke_with_cell(stroke, [2, 1]);
  assert.deepStrictEqual(stroke, [[1, 1], [2, 1], [3, 1]]);
});

test("the route brush leaves towns alone; only changed cells are sent", () => {
  const grid = MAP_GRID.grid_of_doc(painted_doc());
  assert.strictEqual(MAP_GRID.mark_at(grid, [1, 1]), "t");
  assert.strictEqual(MAP_GRID.mark_at(grid, [9, 9]), ".");
  assert.deepStrictEqual(MAP_GRID.cells_the_brush_changes(grid, [[1, 1], [2, 1], [5, 1]], "route"), [[5, 1]]);
  assert.deepStrictEqual(MAP_GRID.cells_the_brush_changes(grid, [[1, 1], [0, 0]], "erase"), [[1, 1]]);
  assert.deepStrictEqual(MAP_GRID.cells_the_brush_changes(grid, [[2, 1], [1, 1]], "town"), [[2, 1]]);
});

test("pointer positions map to grid cells, off the grid is nothing", () => {
  const grid = MAP_GRID.grid_of_doc(painted_doc());
  assert.deepStrictEqual(MAP_GRID.grid_cell_at(grid, 17.5, 9), [2, 1]);
  assert.strictEqual(MAP_GRID.grid_cell_at(grid, -1, 3), null);
  assert.strictEqual(MAP_GRID.grid_cell_at(grid, 48, 3), null);
});

test("a route fills its squares: neighbouring squares run into each other, the outside keeps a small gap", () => {
  const doc = painted_doc(), grid = MAP_GRID.grid_of_doc(doc);
  assert.deepStrictEqual(MAP_GRID.pieces_of_cells(grid, doc.routes[0].grid_cells, 1), [[17, 9, 25, 15], [23, 9, 31, 15]]);
});

test("a joint bridges the gap where a route meets the place it leads to", () => {
  const doc = painted_doc(), grid = MAP_GRID.grid_of_doc(doc);
  const [into_ahlen, into_bertsch] = MAP_GRID.joints_of_route(grid, doc.routes[0], 1, 0.5);
  assert.deepStrictEqual(into_ahlen, [14, 10, 18, 14]);
  assert.deepStrictEqual(into_bertsch, [30, 10, 34, 14]);
});

test("the outline of a route follows its squares, not a box around them", () => {
  const doc = painted_doc(), grid = MAP_GRID.grid_of_doc(doc);
  const segments = MAP_GRID.outline_segments_of_cells(grid, [[2, 1], [3, 1], [3, 2]]);
  assert.strictEqual(segments.length, 8);
  assert.ok(!segments.some(([[x0, y0], [x1, y1]]) => x0 === 16 && x1 === 16 && Math.max(y0, y1) > 16));
});

test("a route stroke for a chosen route sends every non-town square; a new one only the empty ones", () => {
  const grid = MAP_GRID.grid_of_doc(painted_doc());
  assert.deepStrictEqual(MAP_GRID.cells_a_route_stroke_paints(grid, [[1, 1], [2, 1], [5, 1]], ""), [[5, 1]]);
  assert.deepStrictEqual(MAP_GRID.cells_a_route_stroke_paints(grid, [[1, 1], [2, 1], [5, 1]], "r1"), [[2, 1], [5, 1]]);
});

test("a big town is drawn as one station: pieces reach into each other", () => {
  const grid = MAP_GRID.grid_of_doc(painted_doc());
  const pieces = MAP_GRID.station_pieces_of_town(grid, { grid_cells: [[1, 1], [2, 1]] }, 2);
  assert.deepStrictEqual(pieces, [[10, 10, 18, 14], [14, 10, 22, 14]]);
});

test("labels sit at the middle of a route, beside vertical runs", () => {
  const doc = painted_doc(), grid = MAP_GRID.grid_of_doc(doc);
  assert.deepStrictEqual(MAP_GRID.label_anchor_of_route(grid, doc.routes[0]), { point: [24, 12], runs_across: true });
  assert.strictEqual(MAP_GRID.label_anchor_of_route(grid, doc.routes[1]).runs_across, false);
});

test("clicks find towns and routes by their grid cells, not their bounding boxes", () => {
  const doc = painted_doc();
  doc.routes[1].rect = [0, 0, 48, 32];
  assert.strictEqual(ED.placeAt(doc, 20, 20).id, "Route 2");
  assert.strictEqual(ED.placeAt(doc, 12, 12).id, "ahlen");
  assert.strictEqual(ED.placeAt(doc, 44, 28), null);
  assert.strictEqual(MAP_GRID.town_at_grid_cell(doc, [4, 1]).name, "Bertsch");
  assert.strictEqual(MAP_GRID.route_at_grid_cell(doc, [2, 3]).name, "Route 2");
});

function box(left, top, right, bottom) { return { left, top, right, bottom }; }

test("labels never overlap each other and drop a route label with no free spot", () => {
  const town_box = box(0, 0, 20, 20);
  const town_request = { key: "town", priority: 1, must_show: true, hard_obstacle_boxes: [town_box], soft_obstacle_boxes: [],
    candidate_boxes: MAP_GRID.label_boxes_around_box(town_box, 40, 10, 2) };
  const crowding_route_request = { key: "route", priority: 3, must_show: false, hard_obstacle_boxes: [town_box], soft_obstacle_boxes: [],
    candidate_boxes: [box(-10, 22, 30, 32)] };
  const placed = MAP_GRID.placed_map_labels([crowding_route_request, town_request]);
  assert.deepStrictEqual(placed.map((label) => label.key), ["town"]);
  assert.deepStrictEqual(placed[0].box, box(-10, 22, 30, 32));
});

test("a town label moves off the route leaving downward and off other towns", () => {
  const town_box = box(0, 0, 20, 20), route_below = box(5, 20, 15, 60);
  const request = { key: "town", priority: 1, must_show: true, hard_obstacle_boxes: [town_box], soft_obstacle_boxes: [route_below],
    candidate_boxes: MAP_GRID.label_boxes_around_box(town_box, 30, 10, 2) };
  const [placed] = MAP_GRID.placed_map_labels([request]);
  assert.ok(!MAP_GRID.boxes_overlap(placed.box, route_below) && !MAP_GRID.boxes_overlap(placed.box, town_box));
  assert.ok(placed.box.bottom <= town_box.top);
});

test("a focused route label shows even when crowded, and route labels start from the middle cell", () => {
  assert.deepStrictEqual(MAP_GRID.middle_out_order(["a", "b", "c", "d", "e"]), ["c", "d", "b", "e", "a"]);
  const blocker = box(-100, -100, 100, 100);
  const request = { key: "route", priority: 0, must_show: true, hard_obstacle_boxes: [blocker], soft_obstacle_boxes: [],
    candidate_boxes: MAP_GRID.label_boxes_along_cells([box(0, 0, 10, 10)], 20, 8, 2) };
  assert.strictEqual(MAP_GRID.placed_map_labels([request]).length, 1);
});

console.log(`${passed} passed`);
