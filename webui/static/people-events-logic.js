(function (root) {
  "use strict";

  const PEOPLE_MEMORY_KEY = "pokeroutes-people-signs-v1";
  const EVENTS_MEMORY_KEY = "pokeroutes-events-v1";
  const STANDALONE_PROJECT_KEY = "standalone";
  const PERSON_KIND = "person";
  const SIGN_KIND = "sign";
  const REMOVED_KIND = "removed";
  const DEFAULT_PERSON = { sprite: "OBJ_EVENT_GFX_MAN_3", facing: "down", movement: "still", text: "Hello there!", name: "", trainer: null };
  const DEFAULT_SIGN_TEXT = "...";
  const DEFAULT_TRAINER = { trainer_class: "TRAINER_CLASS_YOUNGSTER", team: [{ species: "SPECIES_ZIGZAGOON", level: 5 }], defeat_text: "I lost...", after_text: "" };
  const TRIGGER_TYPES = ["defeat_trainer", "talk_to", "step_on", "press_button", "field_move", "story_beat"];
  const EFFECT_TYPES = ["remove_object", "show_object", "add_objects", "change_tiles", "set_flag", "set_var"];
  const TRIGGERS_ON_A_PERSON = ["defeat_trainer", "talk_to"];
  const TRIGGERS_ON_A_CELL = ["step_on", "press_button", "field_move"];
  const EFFECTS_ON_A_TARGET = ["remove_object", "show_object"];
  const EFFECTS_ON_CELLS = ["add_objects", "change_tiles"];

  function copy_of(value) {
    const copied_value = JSON.parse(JSON.stringify(value));
    return copied_value;
  }

  function is_at_cell(entry, x, y) {
    return entry.x === x && entry.y === y;
  }

  function entry_index_at(entries, x, y) {
    const entry_index = (entries || []).findIndex((entry) => is_at_cell(entry, x, y));
    return entry_index;
  }

  function next_entry_id(entries, kind) {
    const prefix = kind === PERSON_KIND ? "p" : "s";
    const taken_ids = new Set((entries || []).map((entry) => entry.id).filter(Boolean));
    let entry_number = 1;
    while (taken_ids.has(prefix + entry_number)) entry_number += 1;
    const free_entry_id = prefix + entry_number;
    return free_entry_id;
  }

  function new_person(entries, x, y, sprite) {
    const person = Object.assign(copy_of(DEFAULT_PERSON), { kind: PERSON_KIND, id: next_entry_id(entries, PERSON_KIND), x, y });
    if (sprite) person.sprite = sprite;
    return person;
  }

  function new_sign(entries, x, y) {
    const sign = { kind: SIGN_KIND, id: next_entry_id(entries, SIGN_KIND), x, y, text: DEFAULT_SIGN_TEXT };
    return sign;
  }

  function entries_with_entry_placed(entries, entry) {
    const others = (entries || []).filter((other) => !is_at_cell(other, entry.x, entry.y));
    const entries_after = others.concat([copy_of(entry)]);
    return entries_after;
  }

  function entries_with_entry_changed(entries, x, y, changes) {
    const entries_after = (entries || []).map((entry) => (is_at_cell(entry, x, y) ? Object.assign(copy_of(entry), changes) : entry));
    return entries_after;
  }

  function has_generated_at(generated, x, y) {
    return entry_index_at(generated, x, y) >= 0;
  }
  function entries_without_entry_at(entries, generated, x, y) {
    const others = (entries || []).filter((entry) => !is_at_cell(entry, x, y));
    if (!has_generated_at(generated, x, y)) return others;
    const entries_after = others.concat([{ kind: REMOVED_KIND, x, y }]);
    return entries_after;
  }

  function hand_entry_from_generated(entries, generated_entry) {
    const kind = generated_entry.kind;
    const hand_entry = Object.assign(copy_of(generated_entry), { id: next_entry_id(entries, kind) });
    delete hand_entry.sprite_kind;
    return hand_entry;
  }

  function shown_entry(entry, source) {
    return { source, entry };
  }
  function shown_people_and_signs(entries, generated) {
    const hand_entries = (entries || []).filter((entry) => entry.kind !== REMOVED_KIND);
    const untouched_generated = (generated || []).filter((generated_entry) => entry_index_at(entries, generated_entry.x, generated_entry.y) < 0);
    const shown = hand_entries.map((entry) => shown_entry(entry, "hand")).concat(untouched_generated.map((entry) => shown_entry(entry, "generated")));
    return shown;
  }

  function replaces_generated_event(entries, generated_event) {
    const kind_is_replaceable = generated_event.kind === "npc" || generated_event.kind === "sign";
    const is_replaced = kind_is_replaceable && entry_index_at(entries, generated_event.x, generated_event.y) >= 0;
    return is_replaced;
  }

  function remember_people(memory, route_key, route_name, entries) {
    const memory_after = Object.assign({}, memory || {});
    if (entries && entries.length) memory_after[route_key] = { route_name, entries: copy_of(entries) };
    else delete memory_after[route_key];
    return memory_after;
  }

  function remembered_entries(memory, route_key) {
    const remembered = (memory || {})[route_key];
    const entries = remembered ? copy_of(remembered.entries || []) : [];
    return entries;
  }

  function same_route_name(first_route_name, second_route_name) {
    return String(first_route_name || "").trim().toLowerCase() === String(second_route_name || "").trim().toLowerCase();
  }

  function people_on_route(memory, route_name) {
    const people = [];
    for (const remembered of Object.values(memory || {})) {
      if (!same_route_name(remembered.route_name, route_name)) continue;
      for (const entry of remembered.entries || []) if (entry.kind === PERSON_KIND && !people.some((known) => known.id === entry.id)) people.push(entry);
    }
    return people;
  }

  function person_label(person) {
    const label = person.name ? `${person.name} (${person.id})` : `Person ${person.id}`;
    return label;
  }

  function project_key(region_document) {
    const key = region_document && region_document.name ? "region:" + region_document.name : STANDALONE_PROJECT_KEY;
    return key;
  }

  function remember_rules(memory, key, rules) {
    const memory_after = Object.assign({}, memory || {});
    if (rules && rules.length) memory_after[key] = copy_of(rules);
    else delete memory_after[key];
    return memory_after;
  }

  function next_rule_id(rules) {
    const taken_ids = new Set((rules || []).map((rule) => rule.id));
    let rule_number = 1;
    while (taken_ids.has("e" + rule_number)) rule_number += 1;
    const free_rule_id = "e" + rule_number;
    return free_rule_id;
  }

  function default_trigger(trigger_type, context) {
    const route = context.route || "";
    switch (trigger_type) {
      case "defeat_trainer":
      case "talk_to":
        return { type: trigger_type, route, person: context.person || "" };
      case "field_move":
        return { type: trigger_type, route, move: "rock_smash" };
      case "story_beat":
        return { type: trigger_type, beat: "" };
      default:
        return { type: trigger_type, route };
    }
  }

  function default_effect(effect_type, context) {
    const route = context.route || "";
    switch (effect_type) {
      case "remove_object":
      case "show_object":
        return { type: effect_type, route };
      case "add_objects":
        return { type: effect_type, route, cells: [], object: "rock" };
      case "change_tiles":
        return { type: effect_type, route, cells: [], to: "ground" };
      case "set_flag":
        return { type: effect_type, flag: "FLAG_" };
      default:
        return { type: effect_type, var: "VAR_", value: 1 };
    }
  }

  function new_rule(rules, context) {
    const rule = { id: next_rule_id(rules), when: default_trigger("defeat_trainer", context), then: [default_effect("remove_object", context)] };
    return rule;
  }

  function carried_route(previous, context) {
    return previous && previous.route ? previous.route : context.route;
  }

  function rule_with_trigger_type(rule, trigger_type, context) {
    const route = carried_route(rule.when, context);
    const rule_after = Object.assign(copy_of(rule), { when: default_trigger(trigger_type, Object.assign({}, context, { route })) });
    return rule_after;
  }

  function rule_with_trigger_changes(rule, changes) {
    const rule_after = copy_of(rule);
    Object.assign(rule_after.when, changes);
    return rule_after;
  }

  function rule_with_effect_type(rule, effect_index, effect_type, context) {
    const rule_after = copy_of(rule);
    const route = carried_route(rule.then[effect_index], context);
    rule_after.then[effect_index] = default_effect(effect_type, Object.assign({}, context, { route }));
    return rule_after;
  }

  function rule_with_effect_changes(rule, effect_index, changes) {
    const rule_after = copy_of(rule);
    rule_after.then[effect_index] = Object.assign(rule_after.then[effect_index], changes);
    for (const [field_name, field_value] of Object.entries(changes)) if (field_value === undefined) delete rule_after.then[effect_index][field_name];
    return rule_after;
  }

  function rule_with_effect_added(rule, context) {
    const rule_after = copy_of(rule);
    rule_after.then.push(default_effect("remove_object", context));
    return rule_after;
  }

  function rule_without_effect(rule, effect_index) {
    const rule_after = copy_of(rule);
    rule_after.then.splice(effect_index, 1);
    return rule_after;
  }

  function rules_with_rule(rules, changed_rule) {
    const rules_after = (rules || []).map((rule) => (rule.id === changed_rule.id ? copy_of(changed_rule) : rule));
    return rules_after;
  }

  function rules_without_rule(rules, rule_id) {
    const rules_after = (rules || []).filter((rule) => rule.id !== rule_id);
    return rules_after;
  }

  function cells_toggled(cells, x, y) {
    const has_cell = (cells || []).some(([cell_x, cell_y]) => cell_x === x && cell_y === y);
    const cells_after = has_cell ? cells.filter(([cell_x, cell_y]) => cell_x !== x || cell_y !== y) : (cells || []).concat([[x, y]]);
    return cells_after;
  }

  function marker(rule, role, x, y, label) {
    return { rule_id: rule.id, role, x, y, label };
  }

  function trigger_markers(rule, route_name) {
    const trigger = rule.when;
    if (!TRIGGERS_ON_A_CELL.includes(trigger.type) || trigger.x === undefined || !same_route_name(trigger.route, route_name)) return [];
    const trigger_markers_on_route = [marker(rule, "trigger", trigger.x, trigger.y, trigger.type)];
    return trigger_markers_on_route;
  }

  function effect_markers(rule, effect, route_name) {
    if (!effect.route || !same_route_name(effect.route, route_name)) return [];
    if (EFFECTS_ON_CELLS.includes(effect.type)) return (effect.cells || []).map(([x, y]) => marker(rule, "effect", x, y, effect.type));
    if (EFFECTS_ON_A_TARGET.includes(effect.type) && effect.person === undefined && effect.x !== undefined) return [marker(rule, "effect", effect.x, effect.y, effect.type)];
    return [];
  }
  function rule_markers_on_route(rules, route_name) {
    const markers = [];
    for (const rule of rules || []) {
      markers.push(...trigger_markers(rule, route_name));
      for (const effect of rule.then || []) markers.push(...effect_markers(rule, effect, route_name));
    }
    return markers;
  }

  function rules_mentioning_person(rules, route_name, person_id) {
    const mentions_person = (rule) => (TRIGGERS_ON_A_PERSON.includes(rule.when.type) && rule.when.person === person_id && same_route_name(rule.when.route, route_name))
      || (rule.then || []).some((effect) => effect.person === person_id && same_route_name(effect.route, route_name));
    const mentioning_rules = (rules || []).filter(mentions_person);
    return mentioning_rules;
  }

  const api = {
    PEOPLE_MEMORY_KEY, EVENTS_MEMORY_KEY, STANDALONE_PROJECT_KEY, PERSON_KIND, SIGN_KIND, REMOVED_KIND, DEFAULT_TRAINER,
    TRIGGER_TYPES, EFFECT_TYPES, TRIGGERS_ON_A_PERSON, TRIGGERS_ON_A_CELL, EFFECTS_ON_A_TARGET, EFFECTS_ON_CELLS,
    copy_of, entry_index_at, next_entry_id, new_person, new_sign, entries_with_entry_placed, entries_with_entry_changed,
    entries_without_entry_at, hand_entry_from_generated, shown_people_and_signs, replaces_generated_event,
    remember_people, remembered_entries, same_route_name, people_on_route, person_label,
    project_key, remember_rules, next_rule_id, default_trigger, default_effect, new_rule,
    rule_with_trigger_type, rule_with_trigger_changes, rule_with_effect_type, rule_with_effect_changes,
    rule_with_effect_added, rule_without_effect, rules_with_rule, rules_without_rule, cells_toggled,
    rule_markers_on_route, rules_mentioning_person,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PeopleEventsLogic = api;
})(typeof window !== "undefined" ? window : globalThis);
