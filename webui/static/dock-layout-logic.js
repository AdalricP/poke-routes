(function (root) {
  "use strict";

  const LAYOUT_STORAGE_KEY = "pokeroutes-shell-layout-v2";
  const LAYOUT_VERSION = 2;
  const VIEW_PANEL_IDS = ["scene", "geography", "region"];
  const PANEL_IDS = ["scene", "geography", "region", "hierarchy", "tiles", "inspector", "console", "events", "improve", "agent"];
  const SPLIT_DIRECTIONS = ["row", "column"];
  const EDGE_SIDES = ["left", "right", "top", "bottom"];
  const SMALLEST_SIZE_FRACTION = 0.04;
  const EDGE_ZONE_FRACTION_OF_GROUP = 0.28;
  const ROOT_EDGE_ZONE_IN_PIXELS = 14;

  function default_dock_tree() {
    const tree = {
      kind: "split", direction: "row", sizes: [0.78, 0.22], children: [
        { kind: "split", direction: "column", sizes: [0.78, 0.22], children: [
          { kind: "split", direction: "row", sizes: [0.2, 0.8], children: [
            { kind: "group", tabs: ["hierarchy", "tiles"], active: "hierarchy" },
            { kind: "group", tabs: ["scene", "geography", "region"], active: "scene" },
          ] },
          { kind: "group", tabs: ["console", "events", "improve", "agent"], active: "console" },
        ] },
        { kind: "group", tabs: ["inspector"], active: "inspector" },
      ],
    };
    return tree;
  }

  function default_layout() {
    const layout = { v: LAYOUT_VERSION, dock: default_dock_tree(), center: "scene", closed_panels: [], inspector: {}, pins: { left: null, right: null } };
    return layout;
  }

  function deep_copy(json_value) {
    const copied_value = JSON.parse(JSON.stringify(json_value));
    return copied_value;
  }

  function is_group(dock_node) {
    const node_is_group = !!dock_node && dock_node.kind === "group";
    return node_is_group;
  }

  function is_split(dock_node) {
    const node_is_split = !!dock_node && dock_node.kind === "split";
    return node_is_split;
  }

  function node_at_path(tree, node_path) {
    const found_node = node_path.reduce((current_node, child_index) => (is_split(current_node) ? current_node.children[child_index] : undefined), tree);
    return found_node || null;
  }

  function group_paths_of_tree(tree, path_so_far) {
    const current_path = path_so_far || [];
    if (is_group(tree)) return [current_path];
    if (!is_split(tree)) return [];
    const nested_group_paths = tree.children.flatMap((child_node, child_index) => group_paths_of_tree(child_node, current_path.concat([child_index])));
    return nested_group_paths;
  }

  function groups_of_tree(tree) {
    const groups = group_paths_of_tree(tree).map((group_path) => node_at_path(tree, group_path));
    return groups;
  }

  function panels_in_tree(tree) {
    const panel_ids = groups_of_tree(tree).flatMap((group) => group.tabs);
    return panel_ids;
  }

  function group_path_of_panel(tree, panel_id) {
    const group_path = group_paths_of_tree(tree).find((candidate_path) => node_at_path(tree, candidate_path).tabs.includes(panel_id));
    return group_path || null;
  }

  function visible_panels(tree) {
    const shown_panel_ids = groups_of_tree(tree).map((group) => group.active).filter(Boolean);
    return shown_panel_ids;
  }

  function visible_view_panels(tree) {
    const shown_views = visible_panels(tree).filter((panel_id) => VIEW_PANEL_IDS.includes(panel_id));
    return shown_views;
  }

  function sizes_summing_to_one(sizes, child_count) {
    const usable_sizes = Array.from({ length: child_count }, (unused, child_index) => (Number.isFinite(sizes && sizes[child_index]) && sizes[child_index] > 0 ? sizes[child_index] : 1 / child_count));
    const total_of_sizes = usable_sizes.reduce((sum_so_far, size_fraction) => sum_so_far + size_fraction, 0);
    const normalised_sizes = usable_sizes.map((size_fraction) => size_fraction / total_of_sizes);
    return normalised_sizes;
  }

  function is_unplaced_known_panel(panel_id, panels_already_placed, tabs_so_far) {
    const is_unplaced = PANEL_IDS.includes(panel_id) && !panels_already_placed.has(panel_id) && !tabs_so_far.includes(panel_id);
    return is_unplaced;
  }

  function normalised_group(group, panels_already_placed) {
    const unique_known_tabs = group.tabs.reduce((tabs_so_far, panel_id) => (is_unplaced_known_panel(panel_id, panels_already_placed, tabs_so_far) ? tabs_so_far.concat([panel_id]) : tabs_so_far), []);
    unique_known_tabs.forEach((panel_id) => panels_already_placed.add(panel_id));
    if (!unique_known_tabs.length) return null;
    const active_tab = unique_known_tabs.includes(group.active) ? group.active : unique_known_tabs[0];
    const tidy_group = { kind: "group", tabs: unique_known_tabs, active: active_tab };
    if (group.is_drop_target === true) tidy_group.is_drop_target = true;
    return tidy_group;
  }

  function children_flattened_into_parent(direction, children_with_sizes) {
    const flattened_children_with_sizes = children_with_sizes.flatMap(([child_node, size_fraction]) => child_with_size_flattened(direction, child_node, size_fraction));
    return flattened_children_with_sizes;
  }

  function child_with_size_flattened(direction, child_node, size_fraction) {
    if (!is_split(child_node) || child_node.direction !== direction) return [[child_node, size_fraction]];
    const grandchildren_with_sizes = child_node.children.map((grandchild_node, grandchild_index) => [grandchild_node, size_fraction * child_node.sizes[grandchild_index]]);
    return grandchildren_with_sizes;
  }

  function normalised_split(split, panels_already_placed) {
    const direction = SPLIT_DIRECTIONS.includes(split.direction) ? split.direction : "row";
    const raw_children = Array.isArray(split.children) ? split.children : [];
    const raw_sizes = sizes_summing_to_one(split.sizes, raw_children.length);
    const tidy_children_with_sizes = raw_children.map((child_node, child_index) => [normalised_node(child_node, panels_already_placed), raw_sizes[child_index]]).filter(([child_node]) => child_node);
    if (!tidy_children_with_sizes.length) return null;
    if (tidy_children_with_sizes.length === 1) return tidy_children_with_sizes[0][0];
    const flattened_children_with_sizes = children_flattened_into_parent(direction, tidy_children_with_sizes);
    const tidy_split = {
      kind: "split", direction,
      children: flattened_children_with_sizes.map(([child_node]) => child_node),
      sizes: sizes_summing_to_one(flattened_children_with_sizes.map(([, size_fraction]) => size_fraction), flattened_children_with_sizes.length),
    };
    return tidy_split;
  }

  function normalised_node(dock_node, panels_already_placed) {
    if (is_group(dock_node) && Array.isArray(dock_node.tabs)) return normalised_group(dock_node, panels_already_placed);
    if (is_split(dock_node)) return normalised_split(dock_node, panels_already_placed);
    return null;
  }

  function normalised_tree(tree) {
    const tidy_tree = normalised_node(tree, new Set());
    return tidy_tree;
  }

  function tree_with_active_tab(tree, panel_id) {
    const group_path = group_path_of_panel(tree, panel_id);
    if (!group_path) return tree;
    const updated_tree = deep_copy(tree);
    node_at_path(updated_tree, group_path).active = panel_id;
    return updated_tree;
  }

  function tree_without_panel(tree, panel_id) {
    const updated_tree = deep_copy(tree);
    for (const group of groups_of_tree(updated_tree)) remove_tab_from_group(group, panel_id);
    const tidy_tree = normalised_tree(updated_tree);
    return tidy_tree;
  }

  function remove_tab_from_group(group, panel_id) {
    const tab_index = group.tabs.indexOf(panel_id);
    if (tab_index < 0) return;
    group.tabs.splice(tab_index, 1);
    if (group.active === panel_id) group.active = group.tabs[Math.min(tab_index, group.tabs.length - 1)] || null;
  }

  function direction_of_edge_side(edge_side) {
    const split_direction = edge_side === "left" || edge_side === "right" ? "row" : "column";
    return split_direction;
  }

  function is_leading_edge_side(edge_side) {
    const is_leading = edge_side === "left" || edge_side === "top";
    return is_leading;
  }

  function split_with_new_group_beside(existing_node, panel_id, edge_side, new_group_fraction) {
    const new_group = { kind: "group", tabs: [panel_id], active: panel_id };
    const children = is_leading_edge_side(edge_side) ? [new_group, existing_node] : [existing_node, new_group];
    const sizes = is_leading_edge_side(edge_side) ? [new_group_fraction, 1 - new_group_fraction] : [1 - new_group_fraction, new_group_fraction];
    const new_split = { kind: "split", direction: direction_of_edge_side(edge_side), children, sizes };
    return new_split;
  }

  function tree_with_node_replaced(tree, node_path, replacement_node) {
    if (!node_path.length) return replacement_node;
    const updated_tree = deep_copy(tree);
    const parent_node = node_at_path(updated_tree, node_path.slice(0, -1));
    parent_node.children[node_path[node_path.length - 1]] = replacement_node;
    return updated_tree;
  }

  function tree_with_marked_group(tree, group_path) {
    const marked_tree = deep_copy(tree);
    node_at_path(marked_tree, group_path).is_drop_target = true;
    return marked_tree;
  }

  function path_of_marked_group(tree) {
    const marked_path = group_paths_of_tree(tree).find((group_path) => node_at_path(tree, group_path).is_drop_target);
    return marked_path || null;
  }

  function tree_without_drop_mark(tree) {
    const clean_tree = deep_copy(tree);
    for (const group of groups_of_tree(clean_tree)) delete group.is_drop_target;
    return clean_tree;
  }

  function tab_index_after_removal(tree, panel_id, drop_target) {
    const target_group = node_at_path(tree, drop_target.group_path);
    const original_index = target_group.tabs.indexOf(panel_id);
    const requested_index = Number.isFinite(drop_target.tab_index) ? drop_target.tab_index : target_group.tabs.length;
    const adjusted_index = original_index >= 0 && original_index < requested_index ? requested_index - 1 : requested_index;
    return adjusted_index;
  }

  function insert_tab_into_group(group, panel_id, tab_index) {
    const clamped_index = Math.max(0, Math.min(group.tabs.length, tab_index));
    group.tabs.splice(clamped_index, 0, panel_id);
    group.active = panel_id;
  }

  function is_drop_onto_own_lone_group(tree, panel_id, drop_target) {
    if (!drop_target.group_path) return false;
    const target_group = node_at_path(tree, drop_target.group_path);
    const is_own_lone_group = !!target_group && target_group.tabs.length === 1 && target_group.tabs[0] === panel_id;
    return is_own_lone_group;
  }

  function tree_after_dropping_into_group(tree, panel_id, drop_target) {
    const tab_index = drop_target.kind === "tabs" ? tab_index_after_removal(tree, panel_id, drop_target) : Infinity;
    const marked_tree = tree_without_panel(tree_with_marked_group(tree, drop_target.group_path), panel_id);
    const marked_path = path_of_marked_group(marked_tree);
    if (!marked_path) return tree;
    const updated_tree = deep_copy(marked_tree);
    const target_group = node_at_path(updated_tree, marked_path);
    insert_tab_into_group(target_group, panel_id, tab_index === Infinity ? target_group.tabs.length : tab_index);
    const finished_tree = normalised_tree(tree_without_drop_mark(updated_tree));
    return finished_tree;
  }

  function tree_after_splitting_group(tree, panel_id, drop_target) {
    const marked_tree = tree_without_panel(tree_with_marked_group(tree, drop_target.group_path), panel_id);
    const marked_path = path_of_marked_group(marked_tree);
    if (!marked_path) return tree;
    const target_group = node_at_path(marked_tree, marked_path);
    const split_tree = tree_with_node_replaced(marked_tree, marked_path, split_with_new_group_beside(target_group, panel_id, drop_target.side, 0.5));
    const finished_tree = normalised_tree(tree_without_drop_mark(split_tree));
    return finished_tree;
  }

  function tree_after_splitting_root(tree, panel_id, drop_target) {
    const tree_without_dragged_panel = tree_without_panel(tree, panel_id);
    if (!tree_without_dragged_panel) return normalised_tree({ kind: "group", tabs: [panel_id], active: panel_id });
    const split_tree = split_with_new_group_beside(tree_without_dragged_panel, panel_id, drop_target.side, 0.25);
    const finished_tree = normalised_tree(split_tree);
    return finished_tree;
  }

  function is_valid_drop_target(tree, drop_target) {
    if (!drop_target) return false;
    if (drop_target.kind === "root_edge") return EDGE_SIDES.includes(drop_target.side);
    const target_node = Array.isArray(drop_target.group_path) ? node_at_path(tree, drop_target.group_path) : null;
    if (!is_group(target_node)) return false;
    const has_valid_side = drop_target.kind !== "edge" || EDGE_SIDES.includes(drop_target.side);
    return has_valid_side;
  }

  function tree_after_dropping_panel(tree, panel_id, drop_target) {
    if (!PANEL_IDS.includes(panel_id) || !is_valid_drop_target(tree, drop_target)) return tree;
    switch (drop_target.kind) {
      case "tabs":
      case "center":
        return tree_after_dropping_into_group(tree, panel_id, drop_target);
      case "edge":
        if (is_drop_onto_own_lone_group(tree, panel_id, drop_target)) return tree;
        return tree_after_splitting_group(tree, panel_id, drop_target);
      case "root_edge":
        return tree_after_splitting_root(tree, panel_id, drop_target);
      default:
        return tree;
    }
  }

  function tree_with_split_sizes(tree, split_path, sizes) {
    const split_node = node_at_path(tree, split_path);
    if (!is_split(split_node) || !Array.isArray(sizes) || sizes.length !== split_node.children.length) return tree;
    const updated_tree = deep_copy(tree);
    node_at_path(updated_tree, split_path).sizes = sizes_summing_to_one(sizes, sizes.length);
    return updated_tree;
  }

  function sizes_after_divider_drag(sizes, divider_index, moved_fraction, smallest_fraction) {
    const smallest_allowed = Number.isFinite(smallest_fraction) ? smallest_fraction : SMALLEST_SIZE_FRACTION;
    const pair_total = sizes[divider_index] + sizes[divider_index + 1];
    const leading_size = Math.max(smallest_allowed, Math.min(pair_total - smallest_allowed, sizes[divider_index] + moved_fraction));
    const new_sizes = sizes.slice();
    new_sizes[divider_index] = leading_size;
    new_sizes[divider_index + 1] = pair_total - leading_size;
    return new_sizes;
  }

  function default_neighbours_of_panel(panel_id) {
    const default_group = groups_of_tree(default_dock_tree()).find((group) => group.tabs.includes(panel_id));
    const neighbour_panel_ids = default_group ? default_group.tabs.filter((other_panel_id) => other_panel_id !== panel_id) : [];
    return neighbour_panel_ids;
  }

  function tree_with_panel_opened(tree, panel_id) {
    if (!PANEL_IDS.includes(panel_id)) return tree;
    if (!tree) return normalised_tree({ kind: "group", tabs: [panel_id], active: panel_id });
    if (group_path_of_panel(tree, panel_id)) return tree_with_active_tab(tree, panel_id);
    const neighbour_with_a_group = default_neighbours_of_panel(panel_id).find((neighbour_panel_id) => group_path_of_panel(tree, neighbour_panel_id));
    if (neighbour_with_a_group) return tree_after_dropping_panel(tree, panel_id, { kind: "center", group_path: group_path_of_panel(tree, neighbour_with_a_group) });
    const fallback_side = VIEW_PANEL_IDS.includes(panel_id) ? "left" : "right";
    const opened_tree = tree_after_dropping_panel(tree, panel_id, { kind: "root_edge", side: fallback_side });
    return opened_tree;
  }

  function tree_without_panels(tree, unavailable_panel_ids) {
    const effective_tree = unavailable_panel_ids.reduce((tree_so_far, panel_id) => (tree_so_far ? tree_without_panel(tree_so_far, panel_id) : tree_so_far), tree);
    return effective_tree;
  }

  function is_point_inside(point, rect) {
    const is_inside = point.x >= rect.x && point.x < rect.x + rect.w && point.y >= rect.y && point.y < rect.y + rect.h;
    return is_inside;
  }

  function root_edge_side_at_point(point, dock_area_rect) {
    if (!is_point_inside(point, dock_area_rect)) return null;
    const distances_to_edges = [
      ["left", point.x - dock_area_rect.x], ["right", dock_area_rect.x + dock_area_rect.w - point.x],
      ["top", point.y - dock_area_rect.y], ["bottom", dock_area_rect.y + dock_area_rect.h - point.y],
    ];
    const nearest_edge = distances_to_edges.reduce((nearest_so_far, candidate) => (candidate[1] < nearest_so_far[1] ? candidate : nearest_so_far));
    const root_edge_side = nearest_edge[1] < ROOT_EDGE_ZONE_IN_PIXELS ? nearest_edge[0] : null;
    return root_edge_side;
  }

  function tab_index_at_point(group_geometry, point) {
    const tab_after_pointer = group_geometry.tab_rects.findIndex((tab_rect) => point.x < tab_rect.x + tab_rect.w / 2);
    const tab_index = tab_after_pointer < 0 ? group_geometry.tab_rects.length : tab_after_pointer;
    return tab_index;
  }

  function edge_side_inside_group(group_body_rect, point) {
    const fraction_across = (point.x - group_body_rect.x) / Math.max(1, group_body_rect.w);
    const fraction_down = (point.y - group_body_rect.y) / Math.max(1, group_body_rect.h);
    const distances_to_edges = [["left", fraction_across], ["right", 1 - fraction_across], ["top", fraction_down], ["bottom", 1 - fraction_down]];
    const nearest_edge = distances_to_edges.reduce((nearest_so_far, candidate) => (candidate[1] < nearest_so_far[1] ? candidate : nearest_so_far));
    const edge_side = nearest_edge[1] < EDGE_ZONE_FRACTION_OF_GROUP ? nearest_edge[0] : null;
    return edge_side;
  }

  function drop_target_inside_group(group_geometry, point) {
    if (is_point_inside(point, group_geometry.tab_bar_rect)) return { kind: "tabs", group_path: group_geometry.group_path, tab_index: tab_index_at_point(group_geometry, point) };
    const edge_side = edge_side_inside_group(group_geometry.body_rect, point);
    if (edge_side) return { kind: "edge", group_path: group_geometry.group_path, side: edge_side };
    const center_target = { kind: "center", group_path: group_geometry.group_path };
    return center_target;
  }

  function drop_target_at_point(group_geometries, point, dock_area_rect) {
    const group_with_tab_bar_under_pointer = group_geometries.find((group_geometry) => is_point_inside(point, group_geometry.tab_bar_rect));
    if (group_with_tab_bar_under_pointer) return drop_target_inside_group(group_with_tab_bar_under_pointer, point);
    const root_edge_side = root_edge_side_at_point(point, dock_area_rect);
    if (root_edge_side) return { kind: "root_edge", side: root_edge_side };
    const group_under_pointer = group_geometries.find((group_geometry) => is_point_inside(point, group_geometry.rect));
    if (!group_under_pointer) return null;
    const drop_target = drop_target_inside_group(group_under_pointer, point);
    return drop_target;
  }

  function rect_part_on_side(rect, edge_side, fraction) {
    switch (edge_side) {
      case "left": return { x: rect.x, y: rect.y, w: rect.w * fraction, h: rect.h };
      case "right": return { x: rect.x + rect.w * (1 - fraction), y: rect.y, w: rect.w * fraction, h: rect.h };
      case "top": return { x: rect.x, y: rect.y, w: rect.w, h: rect.h * fraction };
      default: return { x: rect.x, y: rect.y + rect.h * (1 - fraction), w: rect.w, h: rect.h * fraction };
    }
  }

  function same_path(path, other_path) {
    const paths_match = path.length === other_path.length && path.every((child_index, position) => child_index === other_path[position]);
    return paths_match;
  }

  function tab_insertion_marker_rect(group_geometry, tab_index) {
    const tab_rects = group_geometry.tab_rects;
    const bar_rect = group_geometry.tab_bar_rect;
    const marker_x = tab_index < tab_rects.length ? tab_rects[tab_index].x : tab_rects.length ? tab_rects[tab_rects.length - 1].x + tab_rects[tab_rects.length - 1].w : bar_rect.x;
    const marker_rect = { x: marker_x - 1, y: bar_rect.y, w: 2, h: bar_rect.h };
    return marker_rect;
  }

  function highlight_rect_of_drop_target(drop_target, group_geometries, dock_area_rect) {
    if (!drop_target) return null;
    if (drop_target.kind === "root_edge") return rect_part_on_side(dock_area_rect, drop_target.side, 0.25);
    const target_geometry = group_geometries.find((group_geometry) => same_path(group_geometry.group_path, drop_target.group_path));
    if (!target_geometry) return null;
    switch (drop_target.kind) {
      case "tabs": return tab_insertion_marker_rect(target_geometry, drop_target.tab_index);
      case "edge": return rect_part_on_side(target_geometry.body_rect, drop_target.side, 0.5);
      default: return target_geometry.body_rect;
    }
  }

  function sanitized_layout(candidate_layout) {
    const layout = default_layout();
    if (!candidate_layout || typeof candidate_layout !== "object" || candidate_layout.v !== LAYOUT_VERSION) return layout;
    const tidy_tree = candidate_layout.dock ? normalised_tree(candidate_layout.dock) : null;
    if (tidy_tree) layout.dock = tidy_tree;
    if (VIEW_PANEL_IDS.includes(candidate_layout.center)) layout.center = candidate_layout.center;
    layout.closed_panels = sanitized_closed_panels(candidate_layout.closed_panels, layout.dock);
    layout.inspector = sanitized_inspector_folds(candidate_layout.inspector);
    layout.pins = sanitized_pins(candidate_layout.pins);
    return layout;
  }

  function sanitized_closed_panels(candidate_closed_panels, tree) {
    if (!Array.isArray(candidate_closed_panels)) return [];
    const open_panel_ids = panels_in_tree(tree);
    const closed_panel_ids = PANEL_IDS.filter((panel_id) => candidate_closed_panels.includes(panel_id) && !open_panel_ids.includes(panel_id));
    return closed_panel_ids;
  }

  function is_tidy_identifier(candidate_text) {
    const is_tidy = typeof candidate_text === "string" && /^[\w-]{1,40}$/.test(candidate_text);
    return is_tidy;
  }

  function sanitized_inspector_folds(candidate_folds) {
    if (!candidate_folds || typeof candidate_folds !== "object") return {};
    const tidy_entries = Object.entries(candidate_folds).filter(([fold_name, is_collapsed]) => typeof is_collapsed === "boolean" && is_tidy_identifier(fold_name));
    const tidy_folds = Object.fromEntries(tidy_entries);
    return tidy_folds;
  }

  function sanitized_pins(candidate_pins) {
    const pins = { left: null, right: null };
    if (!candidate_pins || typeof candidate_pins !== "object") return pins;
    for (const pin_side of ["left", "right"]) pins[pin_side] = is_tidy_identifier(candidate_pins[pin_side]) ? candidate_pins[pin_side] : null;
    return pins;
  }

  function load_layout(storage) {
    try {
      const stored_text = storage && storage.getItem(LAYOUT_STORAGE_KEY);
      const loaded_layout = sanitized_layout(stored_text ? JSON.parse(stored_text) : null);
      return loaded_layout;
    } catch (storage_error) {
      return default_layout();
    }
  }

  function save_layout(storage, layout) {
    try {
      storage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(sanitized_layout(layout)));
      return true;
    } catch (storage_error) {
      return false;
    }
  }

  const api = {
    LAYOUT_STORAGE_KEY, VIEW_PANEL_IDS, PANEL_IDS, EDGE_SIDES,
    default_dock_tree, default_layout, normalised_tree, node_at_path, group_paths_of_tree, groups_of_tree, panels_in_tree,
    group_path_of_panel, visible_panels, visible_view_panels, tree_with_active_tab, tree_without_panel, tree_after_dropping_panel,
    tree_with_split_sizes, sizes_after_divider_drag, tree_with_panel_opened, tree_without_panels,
    drop_target_at_point, highlight_rect_of_drop_target, sanitized_layout, load_layout, save_layout,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.DOCK_LAYOUT = api;
})(typeof window !== "undefined" ? window : globalThis);
