"use strict";
const assert = require("assert");
const path = require("path");
const PE = require(path.join(__dirname, "../static/people-events-logic.js"));

let passed_test_count = 0;
function test(test_name, test_body) {
  try { test_body(); passed_test_count += 1; } catch (error) { console.error("FAIL " + test_name); throw error; }
}

test("placing people and signs gives them ids and one entry per cell", () => {
  const person = PE.new_person([], 4, 5, "OBJ_EVENT_GFX_HIKER");
  const entries = PE.entries_with_entry_placed([], person);
  const sign = PE.new_sign(entries, 6, 5);
  const both = PE.entries_with_entry_placed(entries, sign);
  assert.deepStrictEqual(both.map((entry) => entry.id), ["p1", "s1"]);
  assert.strictEqual(both[0].sprite, "OBJ_EVENT_GFX_HIKER");
  const replaced = PE.entries_with_entry_placed(both, PE.new_sign(both, 4, 5));
  assert.deepStrictEqual(replaced.map((entry) => entry.kind), ["sign", "sign"]);
});

test("generated people and signs are replaced, edited into hand entries, or removed", () => {
  const generated = [{ kind: "sign", x: 1, y: 1, text: "Route 1" }, { kind: "person", x: 2, y: 2, text: "Hi", sprite_kind: "npc_hiker", sprite: "OBJ_EVENT_GFX_HIKER" }];
  const shown_before = PE.shown_people_and_signs([], generated);
  assert.deepStrictEqual(shown_before.map((item) => item.source), ["generated", "generated"]);
  const edited = PE.entries_with_entry_changed(PE.entries_with_entry_placed([], PE.hand_entry_from_generated([], generated[1])), 2, 2, { text: "Hello" });
  assert.strictEqual(edited[0].id, "p1");
  assert.strictEqual(edited[0].sprite_kind, undefined);
  const removed = PE.entries_without_entry_at(edited, generated, 1, 1);
  const shown_after = PE.shown_people_and_signs(removed, generated);
  assert.deepStrictEqual(shown_after.map((item) => [item.source, item.entry.text]), [["hand", "Hello"]]);
  assert.strictEqual(PE.replaces_generated_event(removed, { kind: "sign", x: 1, y: 1 }), true);
  assert.strictEqual(PE.replaces_generated_event(removed, { kind: "cut_tree", x: 1, y: 1 }), false);
  assert.deepStrictEqual(PE.entries_without_entry_at(edited, generated, 2, 2), [{ kind: "removed", x: 2, y: 2 }]);
});

test("people are remembered per route and found by route name", () => {
  const person = PE.new_person([], 3, 3);
  const memory = PE.remember_people({}, "Route 1|40x40", "Route 1", [Object.assign(person, { name: "Ann" })]);
  assert.strictEqual(PE.remembered_entries(memory, "Route 1|40x40")[0].name, "Ann");
  assert.deepStrictEqual(PE.people_on_route(memory, "route 1").map(PE.person_label), ["Ann (p1)"]);
  assert.deepStrictEqual(PE.remember_people(memory, "Route 1|40x40", "Route 1", []), {});
});

test("rules: changing the trigger keeps the route, effects add and go", () => {
  const context = { route: "Route 2" };
  const rule = PE.new_rule([], context);
  assert.strictEqual(rule.id, "e1");
  const button_rule = PE.rule_with_trigger_type(rule, "press_button", { route: "Elsewhere" });
  assert.deepStrictEqual(button_rule.when, { type: "press_button", route: "Route 2" });
  const with_two_effects = PE.rule_with_effect_added(PE.rule_with_effect_type(button_rule, 0, "add_objects", context), context);
  assert.deepStrictEqual(with_two_effects.then.map((effect) => effect.type), ["add_objects", "remove_object"]);
  const toggled = PE.cells_toggled(PE.cells_toggled([], 1, 2), 3, 4);
  assert.deepStrictEqual(PE.cells_toggled(toggled, 1, 2), [[3, 4]]);
  const targeted = PE.rule_with_effect_changes(with_two_effects, 1, { person: "p1", x: undefined, y: undefined });
  assert.deepStrictEqual(targeted.then[1], { type: "remove_object", route: "Route 2", person: "p1" });
  assert.strictEqual(PE.rule_without_effect(targeted, 0).then.length, 1);
  assert.strictEqual(rule.then.length, 1);
});

test("markers: only the cells on the open route, and who mentions a person", () => {
  const rules = [
    { id: "e1", when: { type: "step_on", route: "A", x: 1, y: 1 }, then: [{ type: "add_objects", route: "B", cells: [[5, 5], [6, 5]], object: "rock" }] },
    { id: "e2", when: { type: "defeat_trainer", route: "B", person: "p1" }, then: [{ type: "remove_object", route: "A", x: 9, y: 9 }] },
  ];
  assert.deepStrictEqual(PE.rule_markers_on_route(rules, "a").map((marker) => [marker.rule_id, marker.role, marker.x, marker.y]),
    [["e1", "trigger", 1, 1], ["e2", "effect", 9, 9]]);
  assert.strictEqual(PE.rule_markers_on_route(rules, "B").length, 2);
  assert.deepStrictEqual(PE.rules_mentioning_person(rules, "B", "p1").map((rule) => rule.id), ["e2"]);
  assert.strictEqual(PE.project_key({ name: "seed-4" }), "region:seed-4");
  assert.strictEqual(PE.project_key(null), PE.STANDALONE_PROJECT_KEY);
});

console.log(`people-events-logic: ${passed_test_count} tests passed`);
