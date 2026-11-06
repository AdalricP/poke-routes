(function (root) {
  "use strict";
  const EMPTY_MARK = ".", ROUTE_MARK = "r", TOWN_MARK = "t";
  const MARK_OF_BRUSH = { route: ROUTE_MARK, town: TOWN_MARK, erase: EMPTY_MARK };
  const STEP_OF_SIDE = { east: [1, 0], west: [-1, 0], south: [0, 1], north: [0, -1] };

  function grid_of_doc(doc) {
    const map_grid = doc.map_grid || {};
    const size = map_grid.size || [0, 0];
    const grid = { cell: map_grid.cell || 8, width: size[0], height: size[1], rows: (map_grid.rows || []).slice() };
    return grid;
  }

  function is_inside_grid(grid, cell) {
    const inside = cell[0] >= 0 && cell[1] >= 0 && cell[0] < grid.width && cell[1] < grid.height;
    return inside;
  }

  function grid_cell_at(grid, region_x, region_y) {
    const cell = [Math.floor(region_x / grid.cell), Math.floor(region_y / grid.cell)];
    if (!is_inside_grid(grid, cell)) return null;
    return cell;
  }

  function mark_at(grid, cell) {
    if (!is_inside_grid(grid, cell)) return EMPTY_MARK;
    const mark = (grid.rows[cell[1]] || "")[cell[0]] || EMPTY_MARK;
    return mark;
  }

  function same_cell(cell, other_cell) {
    const is_same = !!cell && !!other_cell && cell[0] === other_cell[0] && cell[1] === other_cell[1];
    return is_same;
  }

  function cells_between(from_cell, to_cell) {
    const cells = [];
    let [column, row] = from_cell;
    const goes_across_first = Math.abs(to_cell[0] - from_cell[0]) >= Math.abs(to_cell[1] - from_cell[1]);
    const step_column = () => { column += to_cell[0] > column ? 1 : -1; cells.push([column, row]); };
    const step_row = () => { row += to_cell[1] > row ? 1 : -1; cells.push([column, row]); };
    if (goes_across_first) { while (column !== to_cell[0]) step_column(); while (row !== to_cell[1]) step_row(); }
    else { while (row !== to_cell[1]) step_row(); while (column !== to_cell[0]) step_column(); }
    return cells;
  }

  function stroke_with_cell(stroke_cells, cell) {
    if (!stroke_cells.length) return [cell.slice()];
    const last_cell = stroke_cells[stroke_cells.length - 1];
    if (same_cell(last_cell, cell)) return stroke_cells;
    const fresh_cells = cells_between(last_cell, cell).filter((fresh_cell) => !stroke_cells.some((stroke_cell) => same_cell(stroke_cell, fresh_cell)));
    const longer_stroke = stroke_cells.concat(fresh_cells);
    return longer_stroke;
  }

  function mark_after_brush(current_mark, brush) {
    if (brush === "route" && current_mark === TOWN_MARK) return current_mark;
    const painted_mark = MARK_OF_BRUSH[brush];
    return painted_mark;
  }

  function cells_the_brush_changes(grid, stroke_cells, brush) {
    const changed_cells = stroke_cells.filter((cell) => is_inside_grid(grid, cell) && mark_after_brush(mark_at(grid, cell), brush) !== mark_at(grid, cell));
    return changed_cells;
  }

  function region_centre_of_grid_cell(grid, cell) {
    const centre = [(cell[0] + 0.5) * grid.cell, (cell[1] + 0.5) * grid.cell];
    return centre;
  }

  function grid_cell_of_exit(grid, exit) {
    const cell = [Math.floor(exit.cell[0] / grid.cell), Math.floor(exit.cell[1] / grid.cell)];
    return cell;
  }

  function neighbour_across_exit(grid, exit) {
    const [step_column, step_row] = STEP_OF_SIDE[exit.side];
    const [column, row] = grid_cell_of_exit(grid, exit);
    const neighbour = [column + step_column, row + step_row];
    return neighbour;
  }

  function is_turn(previous_point, point, next_point) {
    const turns = (point[0] - previous_point[0]) * (next_point[1] - point[1]) - (point[1] - previous_point[1]) * (next_point[0] - point[0]) !== 0;
    return turns;
  }

  function without_points_in_line(points) {
    if (points.length < 3) return points.slice();
    const middle_turns = points.slice(1, -1).filter((point, index) => is_turn(points[index], point, points[index + 2]));
    const corner_points = [points[0]].concat(middle_turns, [points[points.length - 1]]);
    return corner_points;
  }

  function label_anchor_of_route(grid, route) {
    const cells = route.grid_cells || [];
    if (!cells.length) return null;
    const middle_index = Math.floor((cells.length - 1) / 2);
    const middle_cell = cells[middle_index];
    const next_cell = cells[middle_index + 1] || cells[middle_index - 1] || null;
    const through_exit = !next_cell ? (route.exits || [])[0] : null;
    const runs_across = next_cell ? next_cell[1] === middle_cell[1] : !through_exit || through_exit.side === "east" || through_exit.side === "west";
    const centre = region_centre_of_grid_cell(grid, middle_cell);
    const between_cells = next_cell && cells.length % 2 === 0 ? region_centre_of_grid_cell(grid, next_cell) : centre;
    const anchor = { point: [(centre[0] + between_cells[0]) / 2, (centre[1] + between_cells[1]) / 2], runs_across };
    return anchor;
  }

  function cell_key(cell) {
    const key = cell[0] + "," + cell[1];
    return key;
  }

  function cell_key_set(cells) {
    const keys = new Set((cells || []).map(cell_key));
    return keys;
  }

  function pieces_of_cells(grid, cells, inset_in_region_cells) {
    const keys = cell_key_set(cells);
    const has_neighbour = (cell, step) => keys.has(cell_key([cell[0] + step[0], cell[1] + step[1]]));
    const inset_towards = (cell, step) => (has_neighbour(cell, step) ? -inset_in_region_cells : inset_in_region_cells);
    const piece_of_cell = (cell) => [
      cell[0] * grid.cell + inset_towards(cell, [-1, 0]), cell[1] * grid.cell + inset_towards(cell, [0, -1]),
      (cell[0] + 1) * grid.cell - inset_towards(cell, [1, 0]), (cell[1] + 1) * grid.cell - inset_towards(cell, [0, 1]),
    ];
    const pieces = (cells || []).map(piece_of_cell);
    return pieces;
  }

  function station_pieces_of_town(grid, town, inset_in_region_cells) {
    const pieces = pieces_of_cells(grid, town.grid_cells, inset_in_region_cells);
    return pieces;
  }

  function joint_of_exit(grid, exit, inset_in_region_cells, joint_share) {
    const cell = grid_cell_of_exit(grid, exit);
    const [step_column, step_row] = STEP_OF_SIDE[exit.side];
    const [centre_x, centre_y] = region_centre_of_grid_cell(grid, cell);
    const half_width = grid.cell * joint_share / 2;
    const reach = inset_in_region_cells * 2;
    const edge_x = centre_x + step_column * grid.cell / 2, edge_y = centre_y + step_row * grid.cell / 2;
    const joint = step_column
      ? [edge_x - reach, centre_y - half_width, edge_x + reach, centre_y + half_width]
      : [centre_x - half_width, edge_y - reach, centre_x + half_width, edge_y + reach];
    return joint;
  }

  function joints_of_route(grid, route, inset_in_region_cells, joint_share) {
    const joints = (route.exits || []).map((exit) => joint_of_exit(grid, exit, inset_in_region_cells, joint_share));
    return joints;
  }

  function outline_segments_of_cells(grid, cells) {
    const keys = cell_key_set(cells);
    const size = grid.cell;
    const sides = [
      { step: [0, -1], from: [0, 0], to: [1, 0] }, { step: [1, 0], from: [1, 0], to: [1, 1] },
      { step: [0, 1], from: [1, 1], to: [0, 1] }, { step: [-1, 0], from: [0, 1], to: [0, 0] },
    ];
    const open_sides_of = (cell) => sides.filter((side) => !keys.has(cell_key([cell[0] + side.step[0], cell[1] + side.step[1]])));
    const segment_of = (cell, side) => [[(cell[0] + side.from[0]) * size, (cell[1] + side.from[1]) * size], [(cell[0] + side.to[0]) * size, (cell[1] + side.to[1]) * size]];
    const segments = (cells || []).flatMap((cell) => open_sides_of(cell).map((side) => segment_of(cell, side)));
    return segments;
  }

  function route_key_options(doc) {
    const options = doc.routes.filter((route) => route.grid_key).map((route) => ({ key: route.grid_key, name: route.name }));
    return options;
  }

  function cells_a_route_stroke_paints(grid, stroke_cells, route_key) {
    if (!route_key) return cells_the_brush_changes(grid, stroke_cells, "route");
    const paintable_cells = stroke_cells.filter((cell) => is_inside_grid(grid, cell) && mark_at(grid, cell) !== TOWN_MARK);
    return paintable_cells;
  }

  function town_at_grid_cell(doc, cell) {
    const town = doc.cities.find((candidate) => (candidate.grid_cells || []).some((town_cell) => same_cell(town_cell, cell))) || null;
    return town;
  }

  function route_at_grid_cell(doc, cell) {
    const route = doc.routes.find((candidate) => (candidate.grid_cells || []).some((route_cell) => same_cell(route_cell, cell))) || null;
    return route;
  }

  function grid_cells_of_place(doc, place) {
    if (!place) return [];
    const cells = (place.grid_cells || []).map((cell) => cell.slice());
    return cells;
  }

  function boxes_overlap(first_box, second_box) {
    const overlap = first_box.left < second_box.right && second_box.left < first_box.right &&
      first_box.top < second_box.bottom && second_box.top < first_box.bottom;
    return overlap;
  }

  function is_box_clear_of(box, obstacle_boxes) {
    const clear = !obstacle_boxes.some((obstacle_box) => boxes_overlap(box, obstacle_box));
    return clear;
  }

  function box_centred_at(centre_x, centre_y, label_width, label_height) {
    const box = { left: centre_x - label_width / 2, top: centre_y - label_height / 2, right: centre_x + label_width / 2, bottom: centre_y + label_height / 2 };
    return box;
  }

  function label_boxes_around_box(around_box, label_width, label_height, gap) {
    const centre_x = (around_box.left + around_box.right) / 2, centre_y = (around_box.top + around_box.bottom) / 2;
    const half_width = label_width / 2, half_height = label_height / 2;
    const candidate_boxes = [
      box_centred_at(centre_x, around_box.bottom + gap + half_height, label_width, label_height),
      box_centred_at(centre_x, around_box.top - gap - half_height, label_width, label_height),
      box_centred_at(around_box.right + gap + half_width, centre_y, label_width, label_height),
      box_centred_at(around_box.left - gap - half_width, centre_y, label_width, label_height),
      box_centred_at(around_box.right + gap + half_width, around_box.bottom + gap + half_height, label_width, label_height),
      box_centred_at(around_box.left - gap - half_width, around_box.bottom + gap + half_height, label_width, label_height),
      box_centred_at(around_box.right + gap + half_width, around_box.top - gap - half_height, label_width, label_height),
      box_centred_at(around_box.left - gap - half_width, around_box.top - gap - half_height, label_width, label_height),
    ];
    return candidate_boxes;
  }

  function middle_out_order(cells) {
    const middle_index = Math.floor((cells.length - 1) / 2);
    const distance_from_middle = (index) => Math.abs(index - middle_index) * 2 + (index < middle_index ? 1 : 0);
    const indices_from_the_middle = cells.map((_cell, index) => index).sort((first_index, second_index) => distance_from_middle(first_index) - distance_from_middle(second_index));
    const ordered_cells = indices_from_the_middle.map((index) => cells[index]);
    return ordered_cells;
  }

  function label_boxes_along_cells(cell_boxes, label_width, label_height, gap) {
    const ordered_cell_boxes = middle_out_order(cell_boxes);
    const boxes_on_cells = ordered_cell_boxes.map((cell_box) => box_centred_at((cell_box.left + cell_box.right) / 2, (cell_box.top + cell_box.bottom) / 2, label_width, label_height));
    const boxes_beside_cells = ordered_cell_boxes.flatMap((cell_box) => label_boxes_around_box(cell_box, label_width, label_height, gap).slice(0, 4));
    const candidate_boxes = boxes_on_cells.concat(boxes_beside_cells);
    return candidate_boxes;
  }

  function first_box_clear_of(candidate_boxes, obstacle_boxes) {
    const clear_box = candidate_boxes.find((candidate_box) => is_box_clear_of(candidate_box, obstacle_boxes)) || null;
    return clear_box;
  }

  function chosen_label_box(request, placed_boxes) {
    const every_obstacle = placed_boxes.concat(request.hard_obstacle_boxes || [], request.soft_obstacle_boxes || []);
    const box_clear_of_everything = first_box_clear_of(request.candidate_boxes, every_obstacle);
    if (box_clear_of_everything) return box_clear_of_everything;
    if (!request.must_show) return null;
    const box_clear_of_labels_and_towns = first_box_clear_of(request.candidate_boxes, placed_boxes.concat(request.hard_obstacle_boxes || []));
    if (box_clear_of_labels_and_towns) return box_clear_of_labels_and_towns;
    const box_clear_of_labels = first_box_clear_of(request.candidate_boxes, placed_boxes);
    const fallback_box = box_clear_of_labels || request.candidate_boxes[0] || null;
    return fallback_box;
  }

  function placed_map_labels(label_requests) {
    const requests_by_priority = label_requests.slice().sort((first_request, second_request) => first_request.priority - second_request.priority);
    const placed_labels = [];
    for (const request of requests_by_priority) {
      const box = chosen_label_box(request, placed_labels.map((placed_label) => placed_label.box));
      if (box) placed_labels.push({ key: request.key, box });
    }
    return placed_labels;
  }

  const api = {
    boxes_overlap, label_boxes_around_box, label_boxes_along_cells, middle_out_order, placed_map_labels,
    EMPTY_MARK, ROUTE_MARK, TOWN_MARK,
    grid_of_doc, is_inside_grid, grid_cell_at, mark_at, same_cell, cells_between, stroke_with_cell, mark_after_brush,
    cells_the_brush_changes, cells_a_route_stroke_paints, region_centre_of_grid_cell, grid_cell_of_exit, neighbour_across_exit,
    without_points_in_line, label_anchor_of_route, pieces_of_cells, station_pieces_of_town, joints_of_route, outline_segments_of_cells,
    route_key_options, town_at_grid_cell, route_at_grid_cell, grid_cells_of_place,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.MAP_GRID = api;
})(typeof window !== "undefined" ? window : globalThis);
