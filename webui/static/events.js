(function () {
  "use strict";
  const PE = window.PeopleEventsLogic;
  const TRIGGER_LABELS = {
    defeat_trainer: "a trainer is beaten", talk_to: "someone is talked to", step_on: "the player steps on",
    press_button: "a button is pressed at", field_move: "a field move is used at", story_beat: "a story beat happens",
  };
  const EFFECT_LABELS = {
    remove_object: "remove", show_object: "reveal", add_objects: "add",
    change_tiles: "turn", set_flag: "set flag", set_var: "set var",
  };
  const MOVE_LABELS = {
    cut: "Cut", surf: "Surf", strength: "Strength", rock_smash: "Rock Smash", waterfall: "Waterfall", dive: "Dive",
    rock_climb: "Rock Climb", flash: "Flash", dig: "Dig", defog: "Defog", earthquake: "Earthquake", headbutt: "Headbutt",
    dynamic_punch: "Dynamic Punch",
  };
  const ADDED_OBJECT_LABELS = { rock: "smashable rocks", boulder: "Strength boulders", cut_tree: "Cut trees" };
  const TILE_TARGET_LABELS = { ground: "ground", path: "path", tall_grass: "tall grass", rock: "rock", tree: "trees", water: "water", sand: "sand" };
  const MARKER_COLOURS = { trigger: "#f0c24a", effect: "#7fdcf0", picking: "#ffffff" };

  function create(bench) {
    const { state, $, el } = bench;
    let rules = [];
    let project = "";
    let rejected_reasons = {};
    let picking = null;
    let should_scroll_to_last_rule = false;

    function region_document() {
      return bench.region_document();
    }

    function current_route_name() {
      return state.result ? state.result.name : "";
    }

    function route_names() {
      const names = ((region_document() || {}).routes || []).map((route) => route.name).filter(Boolean);
      const current = current_route_name();
      if (current && !names.some((name) => PE.same_route_name(name, current))) names.unshift(current);
      return names;
    }

    function people_on(route_name) {
      const remembered = PE.people_on_route(bench.sget(PE.PEOPLE_MEMORY_KEY), route_name);
      if (!PE.same_route_name(route_name, current_route_name())) return remembered;
      const current_people = bench.current_people();
      const people = current_people.concat(remembered.filter((person) => !current_people.some((current) => current.id === person.id)));
      return people;
    }

    function load_project() {
      const key = PE.project_key(region_document());
      if (key === project) return;
      project = key;
      const remembered = (bench.sget(PE.EVENTS_MEMORY_KEY) || {})[key];
      const from_document = (region_document() || {}).events;
      rules = PE.copy_of(Array.isArray(from_document) ? from_document : remembered || []);
      rejected_reasons = {};
    }

    function remember() {
      bench.sset(PE.EVENTS_MEMORY_KEY, PE.remember_rules(bench.sget(PE.EVENTS_MEMORY_KEY), project, rules));
      const document_open = region_document();
      if (document_open) document_open.events = PE.copy_of(rules);
    }

    async function send_to_server() {
      const route = state.result;
      if (!route) return;
      try {
        const answer = await bench.postJSON("/api/route-events", { id: route.id, rules });
        rejected_reasons = {};
        for (const rejection of answer.rejected || []) if (rejection.rule && rejection.rule.id) rejected_reasons[rejection.rule.id] = rejection.reason;
        render();
      } catch (error) {
        bench.log("error", "Saving events failed: " + error.message);
      }
    }

    function commit(rules_after, label) {
      bench.undoStack.push({ kind: "events", before: rules, after: rules_after, label });
      restore(rules_after);
    }

    function restore(restored_rules) {
      rules = PE.copy_of(restored_rules);
      remember();
      send_to_server();
      render();
      bench.draw();
    }

    function on_new_route() {
      load_project();
      send_to_server();
      render();
    }

    function change_rule(changed_rule, label) {
      commit(PE.rules_with_rule(rules, changed_rule), label || "edit event " + changed_rule.id);
    }

    function context() {
      return { route: current_route_name() };
    }

    function option_select(entries, value, on_change, extra_class) {
      const select = el("select", extra_class || "");
      for (const [option_value, label] of entries) select.append(Object.assign(el("option", "", label), { value: option_value }));
      select.value = value == null ? "" : String(value);
      if (select.value !== String(value == null ? "" : value) && value) {
        select.append(Object.assign(el("option", "", String(value)), { value: String(value) }));
        select.value = String(value);
      }
      select.onchange = () => on_change(select.value);
      return select;
    }

    function route_select(value, on_change) {
      const names = route_names();
      const entries = names.map((name) => [name, name]);
      if (!value) entries.unshift(["", "route..."]);
      const select = option_select(entries, value, on_change, "ev-route");
      return select;
    }

    function person_select(route_name, value, on_change) {
      const people = people_on(route_name);
      const entries = [["", people.length ? "person..." : "no people placed"]].concat(people.map((person) => [person.id, PE.person_label(person)]));
      const select = option_select(entries, value, on_change, "ev-person");
      return select;
    }

    function is_picking(rule, slot) {
      return !!picking && picking.rule_id === rule.id && picking.slot === slot;
    }

    function cell_text(x, y) {
      return x === undefined ? "Pick" : `${x}, ${y}`;
    }

    function pick_button(rule, slot, text, multiple) {
      const active = is_picking(rule, slot);
      const button = Object.assign(el("button", "btn ev-pick" + (active ? " on" : ""), active ? (multiple ? "Done" : "Click the map") : text), { type: "button" });
      button.title = multiple ? "Click cells on the map to add or remove them" : "Pick on the map";
      button.onclick = () => toggle_picking(rule, slot, multiple);
      return button;
    }

    function toggle_picking(rule, slot, multiple) {
      if (is_picking(rule, slot)) { stop_picking(); return; }
      if (!state.result) { bench.log("warn", "Open a route in Routes to pick on its map."); return; }
      picking = { rule_id: rule.id, slot, multiple };
      bench.status(multiple ? "Click cells to add or remove them; Esc when done" : "Click a cell on the map; Esc cancels");
      render();
      bench.draw();
    }

    function stop_picking() {
      picking = null;
      bench.status("");
      render();
      bench.draw();
    }

    function picked_trigger(rule, cell) {
      const trigger = rule.when;
      if (PE.TRIGGERS_ON_A_PERSON.includes(trigger.type)) {
        const person = bench.person_at(cell.x, cell.y);
        if (!person) { bench.log("warn", `No placed person at ${cell_text(cell.x, cell.y)}. Place one with People & signs (P).`); return null; }
        return PE.rule_with_trigger_changes(rule, { route: current_route_name(), person: person.id });
      }
      return PE.rule_with_trigger_changes(rule, { route: current_route_name(), x: cell.x, y: cell.y });
    }

    function picked_effect(rule, effect_index, cell) {
      const effect = rule.then[effect_index];
      if (PE.EFFECTS_ON_CELLS.includes(effect.type)) {
        const cells = PE.same_route_name(effect.route, current_route_name()) ? effect.cells : [];
        return PE.rule_with_effect_changes(rule, effect_index, { route: current_route_name(), cells: PE.cells_toggled(cells, cell.x, cell.y) });
      }
      const person = bench.person_at(cell.x, cell.y);
      const target = person ? { person: person.id, x: undefined, y: undefined } : { x: cell.x, y: cell.y, person: undefined };
      return PE.rule_with_effect_changes(rule, effect_index, Object.assign({ route: current_route_name() }, target));
    }

    function pick_cell(cell) {
      if (!picking || !cell) return false;
      const rule = rules.find((candidate) => candidate.id === picking.rule_id);
      if (!rule) { stop_picking(); return true; }
      const changed_rule = picking.slot === "when" ? picked_trigger(rule, cell) : picked_effect(rule, picking.slot, cell);
      if (changed_rule) change_rule(changed_rule, `pick for event ${rule.id}`);
      if (!picking.multiple) stop_picking();
      return true;
    }

    function trigger_detail(rule) {
      const trigger = rule.when;
      if (trigger.type === "story_beat") {
        const beat = Object.assign(el("input", "ev-beat"), { value: trigger.beat || "", placeholder: "beat name", spellcheck: false });
        beat.onchange = () => change_rule(PE.rule_with_trigger_changes(rule, { beat: beat.value.trim() }));
        return [beat];
      }
      const route = route_select(trigger.route, (value) => change_rule(PE.rule_with_trigger_changes(rule, { route: value })));
      if (PE.TRIGGERS_ON_A_PERSON.includes(trigger.type)) {
        const person = person_select(trigger.route, trigger.person, (value) => change_rule(PE.rule_with_trigger_changes(rule, { person: value })));
        return [el("span", "ev-word", "on"), route, person, pick_button(rule, "when", "Pick", false)];
      }
      const parts = [pick_button(rule, "when", cell_text(trigger.x, trigger.y), false), el("span", "ev-word", "on"), route];
      if (trigger.type === "field_move") {
        parts.unshift(option_select(Object.entries(MOVE_LABELS), trigger.move, (value) => change_rule(PE.rule_with_trigger_changes(rule, { move: value }))));
      }
      return parts;
    }

    function effect_target(rule, effect_index) {
      const effect = rule.then[effect_index];
      if (effect.person) {
        const person = people_on(effect.route).find((candidate) => candidate.id === effect.person);
        return person ? PE.person_label(person) : effect.person;
      }
      if (PE.EFFECTS_ON_CELLS.includes(effect.type)) return (effect.cells || []).length ? `${effect.cells.length} cell${effect.cells.length === 1 ? "" : "s"}` : "Pick cells";
      return cell_text(effect.x, effect.y);
    }

    function effect_detail(rule, effect_index) {
      const effect = rule.then[effect_index];
      const change = (changes) => change_rule(PE.rule_with_effect_changes(rule, effect_index, changes));
      if (effect.type === "set_flag") {
        const flag = Object.assign(el("input", "ev-const"), { value: effect.flag || "", spellcheck: false });
        flag.onchange = () => change({ flag: flag.value.trim().toUpperCase() });
        return [flag];
      }
      if (effect.type === "set_var") {
        const variable = Object.assign(el("input", "ev-const"), { value: effect.var || "", spellcheck: false });
        variable.onchange = () => change({ var: variable.value.trim().toUpperCase() });
        const value = Object.assign(el("input", "ev-number"), { type: "number", min: 0, max: 65535, value: effect.value });
        value.onchange = () => change({ value: parseInt(value.value, 10) || 0 });
        return [variable, el("span", "ev-word", "to"), value];
      }
      const parts = [pick_button(rule, effect_index, effect_target(rule, effect_index), PE.EFFECTS_ON_CELLS.includes(effect.type)),
        el("span", "ev-word", "on"), route_select(effect.route, (value) => change({ route: value }))];
      if (effect.type === "add_objects") parts.unshift(option_select(Object.entries(ADDED_OBJECT_LABELS), effect.object, (value) => change({ object: value })), el("span", "ev-word", "at"));
      if (effect.type === "change_tiles") parts.push(el("span", "ev-word", "into"), option_select(Object.entries(TILE_TARGET_LABELS), effect.to, (value) => change({ to: value })));
      return parts;
    }

    function remove_button(title, on_click) {
      const button = Object.assign(el("button", "btn icon ev-remove", "×"), { type: "button", title });
      button.setAttribute("aria-label", title);
      button.onclick = on_click;
      return button;
    }

    function trigger_row(rule) {
      const row = el("div", "ev-line");
      const type = option_select(Object.entries(TRIGGER_LABELS), rule.when.type,
        (value) => change_rule(PE.rule_with_trigger_type(rule, value, context())), "ev-type");
      row.append(el("span", "ev-id", rule.id), el("span", "ev-word ev-lead", "When"), type, ...trigger_detail(rule), el("span", "grow"),
        remove_button("Delete this event", () => commit(PE.rules_without_rule(rules, rule.id), "delete event " + rule.id)));
      return row;
    }

    function effect_row(rule, effect_index) {
      const effect = rule.then[effect_index];
      const row = el("div", "ev-line ev-effect");
      const type = option_select(Object.entries(EFFECT_LABELS), effect.type,
        (value) => change_rule(PE.rule_with_effect_type(rule, effect_index, value, context())), "ev-type");
      row.append(el("span", "ev-word ev-lead", effect_index ? "and" : "then"), type, ...effect_detail(rule, effect_index), el("span", "grow"));
      if (rule.then.length > 1) row.append(remove_button("Remove this effect", () => change_rule(PE.rule_without_effect(rule, effect_index))));
      return row;
    }

    function rule_block(rule) {
      const block = el("div", "ev-rule" + (picking && picking.rule_id === rule.id ? " picking" : ""));
      block.append(trigger_row(rule));
      rule.then.forEach((effect, effect_index) => block.append(effect_row(rule, effect_index)));
      const footer = el("div", "ev-line ev-foot");
      const add_effect = Object.assign(el("button", "ev-link", "+ effect"), { type: "button" });
      add_effect.onclick = () => change_rule(PE.rule_with_effect_added(rule, context()));
      footer.append(add_effect);
      if (rejected_reasons[rule.id]) footer.append(el("span", "ev-problem", rejected_reasons[rule.id]));
      block.append(footer);
      return block;
    }

    function add_rule() {
      should_scroll_to_last_rule = true;
      const rule = PE.new_rule(rules, context());
      commit(rules.concat([rule]), "add event " + rule.id);
    }

    function render() {
      const pane = $("eventsDock");
      if (!pane) return;
      load_project();
      const previous_list = pane.querySelector(".ev-list");
      const previous_scroll_in_pixels = previous_list ? previous_list.scrollTop : 0;
      pane.innerHTML = "";
      const bar = el("div", "pane-bar");
      const add = Object.assign(el("button", "btn", "New event"), { type: "button" });
      add.onclick = add_rule;
      bar.append(add, el("span", "muted ev-count", rules.length ? `${rules.length} event${rules.length === 1 ? "" : "s"}` : ""));
      const list = el("div", "ev-list");
      for (const rule of rules) list.append(rule_block(rule));
      if (!rules.length) list.append(el("p", "muted ev-empty", "No events yet."));
      pane.append(bar, list);
      list.scrollTop = should_scroll_to_last_rule ? list.scrollHeight : previous_scroll_in_pixels;
      should_scroll_to_last_rule = false;
    }

    function draw_marker(graphics_context, marker, origin_x, origin_y, cell_size, shows_label) {
      const left = origin_x + marker.x * cell_size, top = origin_y + marker.y * cell_size;
      const is_active = !!picking && picking.rule_id === marker.rule_id;
      const colour = is_active ? MARKER_COLOURS.picking : MARKER_COLOURS[marker.role];
      graphics_context.fillStyle = marker.role === "effect" ? "rgba(127,220,240,0.22)" : "rgba(240,194,74,0.22)";
      graphics_context.fillRect(left + 1, top + 1, cell_size - 2, cell_size - 2);
      graphics_context.strokeStyle = "rgba(0,0,0,0.55)";
      graphics_context.lineWidth = 3;
      graphics_context.strokeRect(left + 1.5, top + 1.5, cell_size - 3, cell_size - 3);
      graphics_context.strokeStyle = colour;
      graphics_context.lineWidth = is_active ? 2 : 1.5;
      graphics_context.strokeRect(left + 1.5, top + 1.5, cell_size - 3, cell_size - 3);
      if (cell_size < 14 || !shows_label) return;
      graphics_context.font = `600 ${Math.round(Math.min(11, cell_size * 0.42))}px Inter, system-ui, sans-serif`;
      graphics_context.textBaseline = "top";
      graphics_context.fillStyle = "rgba(20,20,20,0.8)";
      const label_width = graphics_context.measureText(marker.rule_id).width + 4;
      graphics_context.fillRect(left, top - 1, label_width, Math.min(12, cell_size * 0.5));
      graphics_context.fillStyle = MARKER_COLOURS[marker.role];
      graphics_context.fillText(marker.rule_id, left + 2, top);
    }

    function draw(graphics_context, origin_x, origin_y, cell_size) {
      if (!state.result || state.stage !== state.result.stages.length - 1) return;
      const labelled_groups = new Set();
      for (const marker of PE.rule_markers_on_route(rules, current_route_name())) {
        const group = marker.rule_id + marker.role;
        draw_marker(graphics_context, marker, origin_x, origin_y, cell_size, !labelled_groups.has(group));
        labelled_groups.add(group);
      }
    }

    function on_key(event) {
      if (event.key !== "Escape" || !picking) return;
      stop_picking();
    }

    window.addEventListener("keydown", on_key);
    return {
      on_new_route, restore, render, draw, pick_cell,
      is_picking: () => !!picking,
      rules: () => rules,
    };
  }

  window.RouteEvents = { create };
})();
