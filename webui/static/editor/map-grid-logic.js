(function (root) {
  "use strict";
  const EMPTY_MARK = ".", ROUTE_MARK = "r", TOWN_MARK = "t";
  const MARK_OF_BRUSH = { route: ROUTE_MARK, town: TOWN_MARK, erase: EMPTY_MARK };
  const STEP_OF_SIDE = { east: [1, 0], west: [-1, 0], south: [0, 1], north: [0, -1] };
  const METRO_LINE_COLOURS = ["#f39700", "#e60012", "#00a7db", "#009944", "#d7c447", "#9b7cb6", "#00ada9", "#bb641d", "#e85298", "#6cbb5a", "#b6007a", "#0079c2"];
  const SEA_LINE_COLOUR = "#3d8fd1";

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

  function through_exits_of_route(grid, route) {
    const cells = route.grid_cells || [];
    const exits = route.exits || [];
    if (cells.length === 1) return exits.slice(0, 2);
    const first_exit = exits.find((exit) => same_cell(grid_cell_of_exit(grid, exit), cells[0]));
    const last_exit = exits.find((exit) => exit !== first_exit && same_cell(grid_cell_of_exit(grid, exit), cells[cells.length - 1]));
    const through_exits = [first_exit, last_exit].filter(Boolean);
    return through_exits;
  }

  function first_and_last_through_exit(grid, route, through_exits) {
    const cells = route.grid_cells || [];
    const starts_at_first_cell = (exit) => same_cell(grid_cell_of_exit(grid, exit), cells[0]);
    const first_exit = through_exits.find(starts_at_first_cell) || null;
    const last_exit = through_exits.find((exit) => exit !== first_exit) || null;
    const end_exits = { first_exit, last_exit };
    return end_exits;
  }

  function metro_lines_of_route(grid, route) {
    const cells = route.grid_cells || [];
    if (!cells.length) return [];
    const through_exits = through_exits_of_route(grid, route);
    const { first_exit, last_exit } = first_and_last_through_exit(grid, route, through_exits);
    const centre_of = (cell) => region_centre_of_grid_cell(grid, cell);
    const lead_in = first_exit ? [centre_of(neighbour_across_exit(grid, first_exit))] : [];
    const lead_out = last_exit ? [centre_of(neighbour_across_exit(grid, last_exit))] : [];
    const main_line = without_points_in_line(lead_in.concat(cells.map(centre_of), lead_out));
    const side_exits = (route.exits || []).filter((exit) => !through_exits.includes(exit));
    const side_lines = side_exits.map((exit) => [centre_of(grid_cell_of_exit(grid, exit)), centre_of(neighbour_across_exit(grid, exit))]);
    const lines = [main_line].concat(side_lines);
    return lines;
  }

  function terminus_of_route(grid, route) {
    const cells = route.grid_cells || [];
    if (!route.goal || !cells.length) return null;
    const end_cell = cells[cells.length - 1];
    const before_cell = cells.length > 1 ? cells[cells.length - 2] : null;
    const exit_cell = before_cell ? null : (route.exits || []).map((exit) => neighbour_across_exit(grid, exit))[0];
    const towards_cell = before_cell || exit_cell;
    const heading = towards_cell ? [end_cell[0] - towards_cell[0], end_cell[1] - towards_cell[1]] : [1, 0];
    const terminus = { point: region_centre_of_grid_cell(grid, end_cell), across: [heading[1], heading[0]] };
    return terminus;
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

  function neighbour_route_names(doc, route) {
    const names = (route.exits || []).map((exit) => exit.to).filter((name) => doc.routes.some((other) => other.name === name));
    const incoming = doc.routes.filter((other) => (other.exits || []).some((exit) => exit.to === route.name)).map((other) => other.name);
    const neighbour_names = new Set(names.concat(incoming));
    return neighbour_names;
  }

  function stable_number_of_name(name) {
    const number = [...String(name)].reduce((total, character) => (total * 31 + character.charCodeAt(0)) % 9973, 7);
    return number;
  }

  function line_colour_for_route(doc, route, colours_so_far) {
    if (route.mode === "sea") return SEA_LINE_COLOUR;
    const taken = new Set([...neighbour_route_names(doc, route)].map((name) => colours_so_far[name]).filter(Boolean));
    const first_choice = stable_number_of_name(route.name);
    const offsets = METRO_LINE_COLOURS.map((_colour, offset) => METRO_LINE_COLOURS[(first_choice + offset) % METRO_LINE_COLOURS.length]);
    const colour = offsets.find((candidate) => !taken.has(candidate)) || offsets[0];
    return colour;
  }

  function route_line_colours(doc) {
    const colours = {};
    const routes_by_name = doc.routes.slice().sort((route, other) => (route.name < other.name ? -1 : route.name > other.name ? 1 : 0));
    for (const route of routes_by_name) colours[route.name] = line_colour_for_route(doc, route, colours);
    return colours;
  }

  function town_cell_set(town) {
    const cell_keys = new Set((town.grid_cells || []).map((cell) => cell[0] + "," + cell[1]));
    return cell_keys;
  }

  function station_pieces_of_town(grid, town, inset_in_region_cells) {
    const cell_keys = town_cell_set(town);
    const has_neighbour = (cell, step) => cell_keys.has((cell[0] + step[0]) + "," + (cell[1] + step[1]));
    const piece_of_cell = (cell) => {
      const left = cell[0] * grid.cell + (has_neighbour(cell, [-1, 0]) ? -inset_in_region_cells : inset_in_region_cells);
      const top = cell[1] * grid.cell + (has_neighbour(cell, [0, -1]) ? -inset_in_region_cells : inset_in_region_cells);
      const right = (cell[0] + 1) * grid.cell + (has_neighbour(cell, [1, 0]) ? inset_in_region_cells : -inset_in_region_cells);
      const bottom = (cell[1] + 1) * grid.cell + (has_neighbour(cell, [0, 1]) ? inset_in_region_cells : -inset_in_region_cells);
      return [left, top, right, bottom];
    };
    const pieces = (town.grid_cells || []).map(piece_of_cell);
    return pieces;
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

  const api = {
    EMPTY_MARK, ROUTE_MARK, TOWN_MARK, METRO_LINE_COLOURS, SEA_LINE_COLOUR,
    grid_of_doc, is_inside_grid, grid_cell_at, mark_at, same_cell, cells_between, stroke_with_cell, mark_after_brush,
    cells_the_brush_changes, region_centre_of_grid_cell, grid_cell_of_exit, neighbour_across_exit, without_points_in_line,
    metro_lines_of_route, terminus_of_route, label_anchor_of_route, route_line_colours, station_pieces_of_town, town_at_grid_cell, route_at_grid_cell,
    grid_cells_of_place,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.MAP_GRID = api;
})(typeof window !== "undefined" ? window : globalThis);
