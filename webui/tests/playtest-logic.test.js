"use strict";
const assert = require("assert");
const path = require("path");
const PlaytestLogic = require(path.join(__dirname, "../static/playtest-logic.js"));

let passed_test_count = 0;
function test(test_name, test_body) {
  try { test_body(); passed_test_count++; } catch (error) { console.error("FAIL " + test_name); throw error; }
}

const small_route_class_rows = [
  "ffpff",
  "fgggf",
  "fssgf",
  "fgggf",
  "fgwwf",
  "fgwwf",
  "ffpff",
];
const small_route_collision_rows = [
  "11011",
  "10001",
  "11001",
  "10001",
  "10001",
  "10001",
  "11011",
];
const small_route_exits = [
  { side: "north", to: "", cells: [[0, 2]] },
  { side: "south", to: "", cells: [[6, 2]] },
];

function small_world(extra_events) {
  const play_world = PlaytestLogic.build_play_world({
    width: 5, height: 7, class_rows: small_route_class_rows, collision_rows: small_route_collision_rows,
    events: extra_events || [], texts: [{ x: 3, y: 1, text: "ROUTE 1. NORTH: OLDALE." }], exits: small_route_exits,
  });
  return play_world;
}

test("walking onto open grass moves one tile", () => {
  const move = PlaytestLogic.resolve_step(small_world(), { x: 1, y: 1 }, "right", {});
  assert.deepStrictEqual(move, { kind: "walk", to: { x: 2, y: 1 } });
});

test("trees block by class and collision", () => {
  const move = PlaytestLogic.resolve_step(small_world(), { x: 1, y: 1 }, "left", {});
  assert.deepStrictEqual(move, { kind: "blocked", reason: "wall" });
});

test("collision grid blocks even on a walkable class", () => {
  const play_world = PlaytestLogic.build_play_world({
    width: 2, height: 1, class_rows: ["gg"], collision_rows: ["01"], events: [], exits: [],
  });
  assert.strictEqual(PlaytestLogic.resolve_step(play_world, { x: 0, y: 0 }, "right", {}).kind, "blocked");
});

test("a south ledge is jumped going south and lands two tiles on", () => {
  const move = PlaytestLogic.resolve_step(small_world(), { x: 1, y: 1 }, "down", {});
  assert.deepStrictEqual(move, { kind: "jump", to: { x: 1, y: 3 } });
});

test("a ledge cannot be climbed from below or from the side", () => {
  assert.deepStrictEqual(PlaytestLogic.resolve_step(small_world(), { x: 1, y: 3 }, "up", {}), { kind: "blocked", reason: "ledge" });
  assert.deepStrictEqual(PlaytestLogic.resolve_step(small_world(), { x: 3, y: 2 }, "left", {}), { kind: "blocked", reason: "ledge" });
});

test("water needs Surf, and Surf mounts, swims and lands", () => {
  const play_world = small_world();
  assert.deepStrictEqual(PlaytestLogic.resolve_step(play_world, { x: 2, y: 3 }, "down", {}), { kind: "blocked", reason: "water" });
  assert.deepStrictEqual(PlaytestLogic.resolve_step(play_world, { x: 2, y: 3 }, "down", { surf: true }), { kind: "mount_surf", to: { x: 2, y: 4 } });
  assert.deepStrictEqual(PlaytestLogic.resolve_step(play_world, { x: 2, y: 4, is_surfing: true }, "right", { surf: true }), { kind: "swim", to: { x: 3, y: 4 } });
  assert.deepStrictEqual(PlaytestLogic.resolve_step(play_world, { x: 2, y: 4, is_surfing: true }, "left", { surf: true }), { kind: "dismount", to: { x: 1, y: 4 } });
});

test("waterfalls need Waterfall while surfing", () => {
  const play_world = PlaytestLogic.build_play_world({ width: 1, height: 3, class_rows: ["a", "w", "w"], events: [], exits: [] });
  assert.deepStrictEqual(PlaytestLogic.resolve_step(play_world, { x: 0, y: 1, is_surfing: true }, "up", { surf: true }), { kind: "blocked", reason: "waterfall" });
  assert.strictEqual(PlaytestLogic.resolve_step(play_world, { x: 0, y: 1, is_surfing: true }, "up", { surf: true, waterfall: true }).kind, "swim");
});

test("walking off an exit cell leaves the route; elsewhere the edge blocks", () => {
  const play_world = small_world();
  assert.deepStrictEqual(PlaytestLogic.resolve_step(play_world, { x: 2, y: 0 }, "up", {}), { kind: "leave", exit_index: 0 });
  assert.deepStrictEqual(PlaytestLogic.resolve_step(play_world, { x: 2, y: 6 }, "down", {}), { kind: "leave", exit_index: 1 });
  assert.deepStrictEqual(PlaytestLogic.resolve_step(play_world, { x: 2, y: 0 }, "left", {}), { kind: "blocked", reason: "wall" });
});

test("objects block, signs read their text, items are picked up once", () => {
  const play_world = small_world([
    { kind: "sign", x: 3, y: 1 },
    { kind: "item_ball", x: 2, y: 1, item: "ITEM_ESCAPE_ROPE" },
  ]);
  assert.deepStrictEqual(PlaytestLogic.resolve_step(play_world, { x: 1, y: 1 }, "right", {}), { kind: "blocked", reason: "object" });
  assert.deepStrictEqual(PlaytestLogic.resolve_interaction(play_world, { x: 3, y: 2 }, "up", {}), { kind: "text", text: "ROUTE 1. NORTH: OLDALE." });
  const pick_up = PlaytestLogic.resolve_interaction(play_world, { x: 1, y: 1 }, "right", {});
  assert.deepStrictEqual(pick_up, { kind: "pick_up", key: "2,1", text: "You found one ESCAPE ROPE!" });
  const world_after = PlaytestLogic.world_after_interaction(play_world, pick_up);
  assert.strictEqual(PlaytestLogic.resolve_step(world_after, { x: 1, y: 1 }, "right", {}).kind, "walk");
  assert.strictEqual(play_world.objects_by_cell.size, 2);
});

test("Cut and Rock Smash clear their objects only when toggled on", () => {
  const play_world = small_world([{ kind: "cut_tree", x: 2, y: 1 }, { kind: "rock_smash", x: 3, y: 3 }]);
  assert.strictEqual(PlaytestLogic.resolve_interaction(play_world, { x: 1, y: 1 }, "right", {}).kind, "text");
  const cut = PlaytestLogic.resolve_interaction(play_world, { x: 1, y: 1 }, "right", { cut: true });
  assert.strictEqual(cut.kind, "cut");
  assert.strictEqual(PlaytestLogic.world_after_interaction(play_world, cut).objects_by_cell.has("2,1"), false);
  assert.strictEqual(PlaytestLogic.resolve_interaction(play_world, { x: 2, y: 3 }, "right", { rock_smash: true }).kind, "smash");
});

test("Strength pushes a boulder onto free ground and not into walls", () => {
  const play_world = small_world([{ kind: "strength_boulder", x: 2, y: 1 }]);
  assert.deepStrictEqual(PlaytestLogic.resolve_step(play_world, { x: 1, y: 1 }, "right", {}), { kind: "blocked", reason: "object" });
  const push = PlaytestLogic.resolve_step(play_world, { x: 1, y: 1 }, "right", { strength: true });
  assert.deepStrictEqual(push, { kind: "push_boulder", boulder_from: { x: 2, y: 1 }, boulder_to: { x: 3, y: 1 } });
  const pushed_world = PlaytestLogic.world_with_boulder_pushed(play_world, push.boulder_from, push.boulder_to);
  assert.strictEqual(pushed_world.objects_by_cell.get("3,1").kind, "strength_boulder");
  assert.deepStrictEqual(PlaytestLogic.resolve_step(pushed_world, { x: 2, y: 1 }, "right", { strength: true }), { kind: "blocked", reason: "object" });
});

test("hidden items are found with A on the cell or facing it", () => {
  const play_world = small_world([{ kind: "hidden_item", x: 1, y: 3, item: "ITEM_POKE_BALL" }]);
  assert.strictEqual(PlaytestLogic.resolve_step(play_world, { x: 2, y: 3 }, "left", {}).kind, "walk");
  assert.strictEqual(PlaytestLogic.resolve_interaction(play_world, { x: 1, y: 3 }, "down", {}).kind, "find_hidden");
  const found = PlaytestLogic.resolve_interaction(play_world, { x: 2, y: 3 }, "left", {});
  assert.strictEqual(found.text, "You found one POKE BALL!");
  assert.strictEqual(PlaytestLogic.resolve_interaction(PlaytestLogic.world_after_interaction(play_world, found), { x: 2, y: 3 }, "left", {}).kind, "nothing");
});

test("wild encounters roll from the Emerald table and only in tall grass or water", () => {
  const play_world = PlaytestLogic.build_play_world({ width: 3, height: 1, class_rows: ["gtw"], events: [], exits: [] });
  assert.strictEqual(PlaytestLogic.is_encounter_cell(play_world, { x: 0, y: 0 }, false), false);
  assert.strictEqual(PlaytestLogic.is_encounter_cell(play_world, { x: 1, y: 0 }, false), true);
  assert.strictEqual(PlaytestLogic.is_encounter_cell(play_world, { x: 2, y: 0 }, true), true);
  const land_table = { encounter_rate: 20, mons: [{ species: "SPECIES_PIDGEY", min_level: 2, max_level: 4 }, { species: "SPECIES_ZIGZAGOON", min_level: 2, max_level: 4 }] };
  const always_low_rolls = () => 0;
  const encounter = PlaytestLogic.roll_wild_encounter(land_table, false, always_low_rolls);
  assert.deepStrictEqual(encounter, { species_name: "PIDGEY", level: 2 });
  assert.strictEqual(PlaytestLogic.wild_encounter_text(encounter), "Wild PIDGEY appeared!");
  assert.strictEqual(PlaytestLogic.roll_wild_encounter(land_table, false, () => 0.5), null);
  assert.strictEqual(PlaytestLogic.roll_wild_encounter({ encounter_rate: 20, mons: [] }, false, always_low_rolls), null);
});

test("walking frames alternate feet mid-stride and mirror for right", () => {
  assert.deepStrictEqual(PlaytestLogic.player_sheet_frame("down", null, 0), { frame_index: 0, is_mirrored: false });
  assert.deepStrictEqual(PlaytestLogic.player_sheet_frame("down", 0.2, 0), { frame_index: 3, is_mirrored: false });
  assert.deepStrictEqual(PlaytestLogic.player_sheet_frame("down", 0.2, 1), { frame_index: 4, is_mirrored: false });
  assert.deepStrictEqual(PlaytestLogic.player_sheet_frame("right", 0.7, 1), { frame_index: 2, is_mirrored: true });
});

test("step timing matches the GBA: running is twice as fast, ledges hop", () => {
  const walk_duration = PlaytestLogic.step_duration_for_move("walk", false);
  assert.strictEqual(PlaytestLogic.step_duration_for_move("walk", true) * 2, walk_duration);
  assert.strictEqual(PlaytestLogic.step_duration_for_move("jump", false), walk_duration * 2);
  assert.strictEqual(PlaytestLogic.hop_height_for_move("jump", 0.5), 10);
  assert.strictEqual(PlaytestLogic.hop_height_for_move("walk", 0.5), 0);
});

test("camera keeps the player at the GBA centre cell", () => {
  assert.deepStrictEqual(PlaytestLogic.camera_top_left_in_pixels({ x: 160, y: 160 }), { x: 48, y: 88 });
});

test("arrival uses the exit leading back, else the opposite side", () => {
  const scene_exits = [{ side: "north", cells: [[0, 2]] }, { side: "south", cells: [[6, 2]] }];
  assert.strictEqual(PlaytestLogic.arrival_exit_index(scene_exits, [null, { name: "Route 1" }], "Route 1", "north"), 1);
  assert.strictEqual(PlaytestLogic.arrival_exit_index(scene_exits, [null, null], "Route 9", "north"), 1);
  const placement = PlaytestLogic.arrival_placement(small_world(), 0);
  assert.deepStrictEqual(placement, { cell: { x: 2, y: 0 }, facing: "down" });
});

test("crossing a chain seam lands on the matching cell of the next map", () => {
  const cell_on_east_map = PlaytestLogic.cell_across_chain_seam({ x: 31, y: 12 }, "right", { x: 0, y: 0 }, { x: 32, y: 0 });
  assert.deepStrictEqual(cell_on_east_map, { x: 0, y: 12 });
  const cell_on_south_map = PlaytestLogic.cell_across_chain_seam({ x: 5, y: 31 }, "down", { x: 64, y: 0 }, { x: 64, y: 32 });
  assert.deepStrictEqual(cell_on_south_map, { x: 5, y: 0 });
  const cell_back_west = PlaytestLogic.cell_across_chain_seam({ x: 0, y: 7 }, "left", { x: 32, y: 0 }, { x: 0, y: 0 });
  assert.deepStrictEqual(cell_back_west, { x: 31, y: 7 });
});

test("entering through a seam keeps walking or swimming from just outside the map", () => {
  const walking_in = PlaytestLogic.seam_entry_placement(small_world(), { x: 2, y: 0 }, "down", false, {});
  assert.deepStrictEqual(walking_in, { cell: { x: 2, y: -1 }, facing: "down", is_surfing: false, entry_move: { kind: "walk", to: { x: 2, y: 0 } } });
  const swimming_in = PlaytestLogic.seam_entry_placement(small_world(), { x: 2, y: 4 }, "left", true, {});
  assert.deepStrictEqual([swimming_in.is_surfing, swimming_in.entry_move.kind, swimming_in.cell], [true, "swim", { x: 3, y: 4 }]);
  assert.strictEqual(PlaytestLogic.seam_entry_placement(small_world(), { x: 0, y: 0 }, "down", false, {}), null);
  assert.strictEqual(PlaytestLogic.seam_entry_placement(small_world(), { x: 9, y: 0 }, "down", false, {}), null);
});

test("a transition only times out once it has been stuck past the timeout", () => {
  assert.strictEqual(PlaytestLogic.transition_timed_out({ kind: "none", started_at: 0 }, 100000, 30000), false);
  assert.strictEqual(PlaytestLogic.transition_timed_out({ kind: "leaving_route", started_at: 0 }, 20000, 30000), false);
  assert.strictEqual(PlaytestLogic.transition_timed_out({ kind: "leaving_route", started_at: 0 }, 40000, 30000), true);
  assert.strictEqual(PlaytestLogic.transition_timed_out({ kind: "crossing_seam", started_at: 1000 }, 40000, 30000), true);
});

console.log(`playtest-logic: ${passed_test_count} tests passed`);
