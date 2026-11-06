"use strict";
const assert = require("assert");
const path = require("path");
const DOCK = require(path.join(__dirname, "../static/dock-layout-logic.js"));

let passed_test_count = 0;
function test(test_name, test_body) {
  try { test_body(); passed_test_count++; } catch (test_error) { console.error("FAIL " + test_name); throw test_error; }
}

function group_holding(tree, panel_id) {
  const holding_group = DOCK.node_at_path(tree, DOCK.group_path_of_panel(tree, panel_id));
  return holding_group;
}

function memory_storage(initial_entries) {
  const stored_entries = new Map(Object.entries(initial_entries || {}));
  const storage = { getItem: (key) => (stored_entries.has(key) ? stored_entries.get(key) : null), setItem: (key, text) => stored_entries.set(key, String(text)) };
  return storage;
}

const throwing_storage = { getItem() { throw new Error("SecurityError"); }, setItem() { throw new Error("QuotaExceeded"); } };

test("the default tree holds every panel once, with one of each view", () => {
  const tree = DOCK.default_dock_tree();
  assert.deepStrictEqual(DOCK.panels_in_tree(tree).slice().sort(), DOCK.PANEL_IDS.slice().sort());
  assert.deepStrictEqual(DOCK.visible_view_panels(tree), ["scene"]);
  assert.deepStrictEqual(DOCK.normalised_tree(tree), tree);
});

test("dropping a tab on another group's tab bar joins it at that place", () => {
  const tree = DOCK.default_dock_tree();
  const inspector_group_path = DOCK.group_path_of_panel(tree, "inspector");
  const moved_tree = DOCK.tree_after_dropping_panel(tree, "console", { kind: "tabs", group_path: inspector_group_path, tab_index: 0 });
  assert.deepStrictEqual(group_holding(moved_tree, "inspector").tabs, ["console", "inspector"]);
  assert.strictEqual(group_holding(moved_tree, "inspector").active, "console");
  assert.deepStrictEqual(group_holding(moved_tree, "events").tabs, ["events", "improve", "agent"]);
  assert.strictEqual(group_holding(moved_tree, "events").active, "events");
});

test("dropping a tab inside its own tab bar reorders it", () => {
  const tree = DOCK.default_dock_tree();
  const views_group_path = DOCK.group_path_of_panel(tree, "scene");
  const reordered_tree = DOCK.tree_after_dropping_panel(tree, "scene", { kind: "tabs", group_path: views_group_path, tab_index: 3 });
  assert.deepStrictEqual(group_holding(reordered_tree, "scene").tabs, ["geography", "region", "scene"]);
  const back_to_front_tree = DOCK.tree_after_dropping_panel(reordered_tree, "scene", { kind: "tabs", group_path: DOCK.group_path_of_panel(reordered_tree, "scene"), tab_index: 0 });
  assert.deepStrictEqual(group_holding(back_to_front_tree, "scene").tabs, ["scene", "geography", "region"]);
});

test("dropping on an edge splits that group; the triple view is two drops away", () => {
  const tree = DOCK.default_dock_tree();
  const views_group_path = DOCK.group_path_of_panel(tree, "scene");
  const geography_right_tree = DOCK.tree_after_dropping_panel(tree, "geography", { kind: "edge", group_path: views_group_path, side: "right" });
  const geography_group_path = DOCK.group_path_of_panel(geography_right_tree, "geography");
  const triple_tree = DOCK.tree_after_dropping_panel(geography_right_tree, "region", { kind: "edge", group_path: geography_group_path, side: "bottom" });
  assert.deepStrictEqual(DOCK.visible_view_panels(triple_tree).sort(), ["geography", "region", "scene"]);
  const geography_and_map_split = DOCK.node_at_path(triple_tree, DOCK.group_path_of_panel(triple_tree, "geography").slice(0, -1));
  assert.strictEqual(geography_and_map_split.direction, "column");
  assert.deepStrictEqual(geography_and_map_split.children.map((child_node) => child_node.tabs), [["geography"], ["region"]]);
  const scene_and_right_split = DOCK.node_at_path(triple_tree, views_group_path.slice(0, -1));
  assert.strictEqual(scene_and_right_split.direction, "row");
  assert.ok(Math.abs(scene_and_right_split.sizes.reduce((sum, size_fraction) => sum + size_fraction, 0) - 1) < 1e-9);
});

test("a same-direction split folds into its parent instead of nesting", () => {
  const tree = DOCK.default_dock_tree();
  const views_group_path = DOCK.group_path_of_panel(tree, "scene");
  const split_tree = DOCK.tree_after_dropping_panel(tree, "geography", { kind: "edge", group_path: views_group_path, side: "right" });
  const parent_row = DOCK.node_at_path(split_tree, views_group_path.slice(0, -1));
  assert.strictEqual(parent_row.children.length, 3);
  assert.deepStrictEqual(parent_row.children.map((child_node) => child_node.tabs), [["hierarchy", "tiles"], ["scene", "region"], ["geography"]]);
  assert.ok(Math.abs(parent_row.sizes[1] - 0.4) < 1e-9 && Math.abs(parent_row.sizes[2] - 0.4) < 1e-9);
});

test("dragging the last tab out of a group removes the group and its split", () => {
  const tree = DOCK.default_dock_tree();
  const moved_tree = DOCK.tree_after_dropping_panel(tree, "inspector", { kind: "center", group_path: DOCK.group_path_of_panel(tree, "console") });
  assert.strictEqual(moved_tree.direction, "column");
  assert.deepStrictEqual(group_holding(moved_tree, "inspector").tabs, ["console", "events", "improve", "agent", "inspector"]);
  assert.strictEqual(DOCK.groups_of_tree(moved_tree).length, 3);
});

test("an edge drop onto a group holding only the dragged tab changes nothing", () => {
  const tree = DOCK.default_dock_tree();
  const inspector_group_path = DOCK.group_path_of_panel(tree, "inspector");
  assert.strictEqual(DOCK.tree_after_dropping_panel(tree, "inspector", { kind: "edge", group_path: inspector_group_path, side: "left" }), tree);
  assert.deepStrictEqual(DOCK.tree_after_dropping_panel(tree, "inspector", { kind: "center", group_path: inspector_group_path }), tree);
});

test("bad drops leave the tree alone", () => {
  const tree = DOCK.default_dock_tree();
  assert.strictEqual(DOCK.tree_after_dropping_panel(tree, "nowhere", { kind: "center", group_path: [1] }), tree);
  assert.strictEqual(DOCK.tree_after_dropping_panel(tree, "scene", { kind: "edge", group_path: [1], side: "middle" }), tree);
  assert.strictEqual(DOCK.tree_after_dropping_panel(tree, "scene", { kind: "center", group_path: [0] }), tree);
  assert.strictEqual(DOCK.tree_after_dropping_panel(tree, "scene", null), tree);
});

test("a root edge drop puts the panel along the whole side", () => {
  const tree = DOCK.default_dock_tree();
  const root_split_tree = DOCK.tree_after_dropping_panel(tree, "hierarchy", { kind: "root_edge", side: "left" });
  assert.strictEqual(root_split_tree.direction, "row");
  assert.deepStrictEqual(root_split_tree.children[0].tabs, ["hierarchy"]);
  const bottom_tree = DOCK.tree_after_dropping_panel(tree, "agent", { kind: "root_edge", side: "bottom" });
  assert.strictEqual(bottom_tree.direction, "column");
  assert.deepStrictEqual(bottom_tree.children[1].tabs, ["agent"]);
});

test("removing and reopening a panel brings it back beside its default neighbours", () => {
  const tree = DOCK.default_dock_tree();
  const without_console = DOCK.tree_without_panel(tree, "console");
  assert.ok(!DOCK.panels_in_tree(without_console).includes("console"));
  assert.strictEqual(group_holding(without_console, "events").active, "events");
  const reopened_tree = DOCK.tree_with_panel_opened(without_console, "console");
  assert.ok(group_holding(reopened_tree, "console").tabs.includes("events"));
  assert.strictEqual(group_holding(reopened_tree, "console").active, "console");
  const without_inspector = DOCK.tree_without_panel(tree, "inspector");
  const inspector_back = DOCK.tree_with_panel_opened(without_inspector, "inspector");
  assert.deepStrictEqual(inspector_back.children[inspector_back.children.length - 1].tabs, ["inspector"]);
  assert.deepStrictEqual(DOCK.tree_with_panel_opened(null, "scene"), { kind: "group", tabs: ["scene"], active: "scene" });
});

test("normalising repairs duplicates, unknown panels, empty groups and bad sizes", () => {
  const messy_tree = { kind: "split", direction: "sideways", sizes: [5, -1, "a"], children: [
    { kind: "group", tabs: ["scene", "scene", "bogus"], active: "bogus" },
    { kind: "group", tabs: [], active: null },
    { kind: "split", direction: "row", sizes: [1, 1], children: [{ kind: "group", tabs: ["scene", "console"], active: "console" }, { kind: "group", tabs: ["inspector"] }] },
  ] };
  const tidy_tree = DOCK.normalised_tree(messy_tree);
  assert.strictEqual(tidy_tree.direction, "row");
  assert.deepStrictEqual(tidy_tree.children.map((child_node) => child_node.tabs), [["scene"], ["console"], ["inspector"]]);
  assert.strictEqual(tidy_tree.children[0].active, "scene");
  assert.ok(Math.abs(tidy_tree.sizes.reduce((sum, size_fraction) => sum + size_fraction, 0) - 1) < 1e-9);
  assert.strictEqual(DOCK.normalised_tree({ kind: "group", tabs: ["nothing"] }), null);
});

test("divider drags keep both sides at least the smallest size", () => {
  assert.deepStrictEqual(DOCK.sizes_after_divider_drag([0.5, 0.5], 0, 0.1, 0.05).map((size_fraction) => Math.round(size_fraction * 100)), [60, 40]);
  assert.deepStrictEqual(DOCK.sizes_after_divider_drag([0.2, 0.3, 0.5], 1, 0.9, 0.05).map((size_fraction) => Math.round(size_fraction * 100)), [20, 75, 5]);
  assert.deepStrictEqual(DOCK.sizes_after_divider_drag([0.5, 0.5], 0, -2, 0.1).map((size_fraction) => Math.round(size_fraction * 100)), [10, 90]);
  const tree = DOCK.default_dock_tree();
  const resized_tree = DOCK.tree_with_split_sizes(tree, [], [3, 1]);
  assert.deepStrictEqual(resized_tree.sizes, [0.75, 0.25]);
  assert.strictEqual(DOCK.tree_with_split_sizes(tree, [], [1, 2, 3]), tree);
});

test("tabs can be activated and unavailable panels hidden", () => {
  const tree = DOCK.default_dock_tree();
  const map_tree = DOCK.tree_with_active_tab(tree, "region");
  assert.deepStrictEqual(DOCK.visible_view_panels(map_tree), ["region"]);
  const without_improve = DOCK.tree_without_panels(tree, ["improve"]);
  assert.deepStrictEqual(group_holding(without_improve, "console").tabs, ["console", "events", "agent"]);
  const improve_alone = DOCK.tree_after_dropping_panel(tree, "improve", { kind: "root_edge", side: "top" });
  assert.strictEqual(DOCK.groups_of_tree(DOCK.tree_without_panels(improve_alone, ["improve"])).length, 4);
});

const geometry_of_two_groups = [
  { group_path: [0], rect: { x: 0, y: 0, w: 400, h: 300 }, tab_bar_rect: { x: 0, y: 0, w: 400, h: 26 }, body_rect: { x: 0, y: 26, w: 400, h: 274 },
    tab_rects: [{ x: 0, y: 0, w: 80, h: 26 }, { x: 80, y: 0, w: 80, h: 26 }] },
  { group_path: [1], rect: { x: 400, y: 0, w: 400, h: 300 }, tab_bar_rect: { x: 400, y: 0, w: 400, h: 26 }, body_rect: { x: 400, y: 26, w: 400, h: 274 },
    tab_rects: [{ x: 400, y: 0, w: 80, h: 26 }] },
];
const dock_area_rect = { x: 0, y: 0, w: 800, h: 300 };

test("the drop target under the pointer: tab bar, edges, middle, whole-area edge", () => {
  assert.deepStrictEqual(DOCK.drop_target_at_point(geometry_of_two_groups, { x: 100, y: 10 }, dock_area_rect), { kind: "tabs", group_path: [0], tab_index: 1 });
  assert.deepStrictEqual(DOCK.drop_target_at_point(geometry_of_two_groups, { x: 300, y: 10 }, dock_area_rect), { kind: "tabs", group_path: [0], tab_index: 2 });
  assert.deepStrictEqual(DOCK.drop_target_at_point(geometry_of_two_groups, { x: 420, y: 160 }, dock_area_rect), { kind: "edge", group_path: [1], side: "left" });
  assert.deepStrictEqual(DOCK.drop_target_at_point(geometry_of_two_groups, { x: 600, y: 280 }, dock_area_rect), { kind: "edge", group_path: [1], side: "bottom" });
  assert.deepStrictEqual(DOCK.drop_target_at_point(geometry_of_two_groups, { x: 200, y: 160 }, dock_area_rect), { kind: "center", group_path: [0] });
  assert.deepStrictEqual(DOCK.drop_target_at_point(geometry_of_two_groups, { x: 795, y: 160 }, dock_area_rect), { kind: "root_edge", side: "right" });
  assert.strictEqual(DOCK.drop_target_at_point(geometry_of_two_groups, { x: 900, y: 160 }, dock_area_rect), null);
});

test("the highlight shows where the panel will land", () => {
  const edge_target = { kind: "edge", group_path: [1], side: "right" };
  assert.deepStrictEqual(DOCK.highlight_rect_of_drop_target(edge_target, geometry_of_two_groups, dock_area_rect), { x: 600, y: 26, w: 200, h: 274 });
  assert.deepStrictEqual(DOCK.highlight_rect_of_drop_target({ kind: "center", group_path: [0] }, geometry_of_two_groups, dock_area_rect), { x: 0, y: 26, w: 400, h: 274 });
  assert.deepStrictEqual(DOCK.highlight_rect_of_drop_target({ kind: "tabs", group_path: [0], tab_index: 2 }, geometry_of_two_groups, dock_area_rect), { x: 159, y: 0, w: 2, h: 26 });
  assert.deepStrictEqual(DOCK.highlight_rect_of_drop_target({ kind: "root_edge", side: "top" }, geometry_of_two_groups, dock_area_rect), { x: 0, y: 0, w: 800, h: 75 });
  assert.strictEqual(DOCK.highlight_rect_of_drop_target(null, geometry_of_two_groups, dock_area_rect), null);
});

test("the layout round-trips through storage and survives junk and blocked storage", () => {
  const storage = memory_storage();
  const layout = DOCK.default_layout();
  layout.dock = DOCK.tree_after_dropping_panel(layout.dock, "region", { kind: "edge", group_path: DOCK.group_path_of_panel(layout.dock, "scene"), side: "right" });
  layout.center = "region";
  layout.inspector.shape = true;
  layout.closed_panels = ["agent", "scene"];
  assert.strictEqual(DOCK.save_layout(storage, layout), true);
  const restored_layout = DOCK.load_layout(storage);
  assert.deepStrictEqual(restored_layout.dock, layout.dock);
  assert.strictEqual(restored_layout.center, "region");
  assert.deepStrictEqual(restored_layout.inspector, { shape: true });
  assert.deepStrictEqual(restored_layout.closed_panels, [], "panels still in the tree are not closed");
  assert.deepStrictEqual(DOCK.load_layout(throwing_storage), DOCK.default_layout());
  assert.strictEqual(DOCK.save_layout(throwing_storage, layout), false);
  assert.deepStrictEqual(DOCK.load_layout(null), DOCK.default_layout());
  assert.deepStrictEqual(DOCK.load_layout(memory_storage({ [DOCK.LAYOUT_STORAGE_KEY]: "{not json" })), DOCK.default_layout());
  assert.deepStrictEqual(DOCK.load_layout(memory_storage({ [DOCK.LAYOUT_STORAGE_KEY]: JSON.stringify({ v: 1, dock: {} }) })), DOCK.default_layout());
  const junk_layout = DOCK.sanitized_layout({ v: 2, dock: { kind: "group", tabs: ["bogus"] }, center: "story", inspector: { "a b<": true, fine: false, number: 1 }, pins: { left: "<x>", right: "imp-queue" } });
  assert.deepStrictEqual(junk_layout.dock, DOCK.default_dock_tree());
  assert.strictEqual(junk_layout.center, "scene");
  assert.deepStrictEqual(junk_layout.inspector, { fine: false });
  assert.deepStrictEqual(junk_layout.pins, { left: null, right: "imp-queue" });
});

console.log(`dock-layout-logic: ${passed_test_count} tests passed`);
