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

test("a metro line runs through cell centres into the towns it joins, corners only", () => {
  const doc = painted_doc(), grid = MAP_GRID.grid_of_doc(doc);
  const [main_line] = MAP_GRID.metro_lines_of_route(grid, doc.routes[0]);
  assert.deepStrictEqual(main_line, [[12, 12], [36, 12]]);
  const [branch_line] = MAP_GRID.metro_lines_of_route(grid, doc.routes[1]);
  assert.deepStrictEqual(branch_line, [[20, 12], [20, 28]]);
});

test("a dead end gets a terminus bar across its last cell", () => {
  const doc = painted_doc(), grid = MAP_GRID.grid_of_doc(doc);
  assert.deepStrictEqual(MAP_GRID.terminus_of_route(grid, doc.routes[1]), { point: [20, 28], across: [1, 0] });
  assert.strictEqual(MAP_GRID.terminus_of_route(grid, doc.routes[0]), null);
});

test("joined routes get different line colours that stay put when others change", () => {
  const doc = painted_doc();
  const colours = MAP_GRID.route_line_colours(doc);
  assert.notStrictEqual(colours["Route 1"], colours["Route 2"]);
  const reordered = Object.assign({}, doc, { routes: doc.routes.slice().reverse() });
  assert.deepStrictEqual(MAP_GRID.route_line_colours(reordered), colours);
  const sea = Object.assign({}, doc, { routes: [Object.assign({}, doc.routes[0], { mode: "sea" })] });
  assert.strictEqual(MAP_GRID.route_line_colours(sea)["Route 1"], MAP_GRID.SEA_LINE_COLOUR);
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

console.log(`${passed} passed`);
