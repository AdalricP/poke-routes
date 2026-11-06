(function () {
  "use strict";
  const PE = window.PeopleEventsLogic;
  const SL = window.SL;
  const WALKABLE = "gptdhyvzqn";
  const SELECTION_TYPE = "person_or_sign";
  const SPRITE_TILE_SIZE_IN_PIXELS = 28;
  const MOVEMENT_LABELS = { still: "Stands still", look_around: "Looks around", wander: "Wanders" };
  const FACING_ARROWS = { down: "↓", up: "↑", left: "←", right: "→" };
  const SIGN_COLOURS = { board: "#b07a42", edge: "#5a3a1c", post: "#6b4422" };
  const FIRERED_SIGN_SPRITE_ENTRY = { sprite_kind: "sign" };
  const MAXIMUM_TEAM_SIZE = 6;

  function load_image(url) {
    const image = new Image();
    image.src = url;
    return image.decode().then(() => image, () => null);
  }

  function create(bench) {
    const { state, $, el } = bench;
    let entries = [];
    let generated = [];
    let route_key = "";
    let route_name = "";
    let placing_kind = PE.PERSON_KIND;
    let options = { sprites: [], trainer_classes: [], species: [], default_sprite: "OBJ_EVENT_GFX_MAN_3" };
    const sprite_images = {};
    const sprite_loads = {};

    function route_tiler() {
      return state.result && state.result.tiler === "learned" ? "learned" : "ark";
    }

    async function load_options() {
      try { options = Object.assign(options, await bench.fetchJSON("/api/people-options?tiler=" + route_tiler())); } catch (error) { return; }
      fill_species_list();
    }

    function fill_species_list() {
      const list = $("peopleSpecies");
      if (!list) return;
      list.innerHTML = "";
      for (const species of options.species || []) list.append(Object.assign(document.createElement("option"), { value: species }));
    }

    function sprite_url(entry) {
      if (entry.sprite_kind) return `/api/sprite.png?kind=${entry.sprite_kind}&tiler=${route_tiler()}`;
      const sprite_address = `/api/sprite.png?kind=gfx_${entry.sprite}&facing=${entry.facing || "down"}&tiler=${route_tiler()}`;
      return sprite_address;
    }

    function sprite_image(entry) {
      const url = sprite_url(entry);
      if (sprite_images[url] !== undefined) return sprite_images[url];
      if (!sprite_loads[url]) {
        sprite_loads[url] = load_image(url).then((image) => { sprite_images[url] = image; bench.draw(); });
      }
      return null;
    }

    function on_final_stage() {
      const route = state.result;
      return !!route && state.stage === route.stages.length - 1;
    }

    function shown() {
      return on_final_stage() ? PE.shown_people_and_signs(entries, generated) : [];
    }

    function shown_at(x, y) {
      const match = shown().find((item) => item.entry.x === x && item.entry.y === y);
      return match || null;
    }

    function selected_item() {
      const selection = state.sel;
      if (!selection || selection.type !== SELECTION_TYPE) return null;
      return shown_at(selection.x, selection.y);
    }

    function hides_generated_event(event) {
      return on_final_stage() && (event.kind === "npc" || event.kind === "sign");
    }

    function draw_sign(graphics_context, left, top, cell_size) {
      const firered_sign_image = sprite_image(FIRERED_SIGN_SPRITE_ENTRY);
      if (firered_sign_image) { graphics_context.drawImage(firered_sign_image, left, top, cell_size, cell_size); return; }
      const board_height = cell_size * 0.5, board_top = top + cell_size * 0.12;
      graphics_context.fillStyle = SIGN_COLOURS.post;
      graphics_context.fillRect(left + cell_size * 0.44, board_top + board_height, cell_size * 0.12, cell_size * 0.36);
      graphics_context.fillStyle = SIGN_COLOURS.board;
      graphics_context.fillRect(left + cell_size * 0.12, board_top, cell_size * 0.76, board_height);
      graphics_context.strokeStyle = SIGN_COLOURS.edge;
      graphics_context.lineWidth = Math.max(1, cell_size / 16);
      graphics_context.strokeRect(left + cell_size * 0.12, board_top, cell_size * 0.76, board_height);
    }

    function draw_person(graphics_context, entry, left, top, cell_size) {
      const image = sprite_image(entry);
      if (!image) {
        graphics_context.fillStyle = "#d9cfb8";
        graphics_context.beginPath();
        graphics_context.arc(left + cell_size / 2, top + cell_size / 2, cell_size * 0.3, 0, Math.PI * 2);
        graphics_context.fill();
        return;
      }
      const height_in_cells = image.height / 16;
      graphics_context.drawImage(image, left, top + cell_size - height_in_cells * cell_size, cell_size, height_in_cells * cell_size);
    }

    function draw_entry(graphics_context, entry, origin_x, origin_y, cell_size) {
      const left = origin_x + entry.x * cell_size, top = origin_y + entry.y * cell_size;
      if (entry.kind === PE.SIGN_KIND) draw_sign(graphics_context, left, top, cell_size);
      else draw_person(graphics_context, entry, left, top, cell_size);
    }

    function draw(graphics_context, origin_x, origin_y, cell_size) {
      for (const { entry } of shown()) draw_entry(graphics_context, entry, origin_x, origin_y, cell_size);
      draw_selection_outline(graphics_context, origin_x, origin_y, cell_size);
    }

    function draw_selection_outline(graphics_context, origin_x, origin_y, cell_size) {
      const item = selected_item();
      if (!item) return;
      graphics_context.strokeStyle = "#4d90d6";
      graphics_context.lineWidth = 2;
      graphics_context.strokeRect(origin_x + item.entry.x * cell_size - 1.5, origin_y + item.entry.y * cell_size - 1.5, cell_size + 3, cell_size + 3);
    }

    function final_classes() {
      const route = state.result;
      return route.stages[route.stages.length - 1].cls;
    }

    function cell_problem(x, y, kind) {
      const route = state.result;
      const cell_class = final_classes()[y][x];
      if (kind === PE.PERSON_KIND && !WALKABLE.includes(cell_class)) return `x ${x}, y ${y} is not walkable`;
      const other_event = route.events.find((event) => event.x === x && event.y === y && event.kind !== "npc" && event.kind !== "sign");
      if (other_event) return `x ${x}, y ${y} already has a ${other_event.kind.replace(/_/g, " ")}`;
      if (bench.has_placed_item_at(x, y)) return `x ${x}, y ${y} already has a placed item`;
      return "";
    }

    function selection_for(x, y) {
      return { type: SELECTION_TYPE, x, y };
    }

    function pick_at(cell) {
      if (!cell || !shown_at(cell.x, cell.y)) return null;
      return selection_for(cell.x, cell.y);
    }

    function click(cell) {
      if (!cell || !state.result) return;
      if (!on_final_stage()) { bench.log("warn", "Step to the last stage to place people and signs."); return; }
      if (shown_at(cell.x, cell.y)) { bench.select(selection_for(cell.x, cell.y)); return; }
      const problem = cell_problem(cell.x, cell.y, placing_kind);
      if (problem) { bench.log("warn", `Can't place a ${placing_kind} there: ${problem}.`); return; }
      const new_entry = placing_kind === PE.SIGN_KIND ? PE.new_sign(entries, cell.x, cell.y)
        : PE.new_person(entries, cell.x, cell.y, last_sprite());
      commit(PE.entries_with_entry_placed(entries, new_entry), `place ${placing_kind}`);
      bench.select(selection_for(cell.x, cell.y));
    }

    function last_sprite() {
      const people = entries.filter((entry) => entry.kind === PE.PERSON_KIND);
      const sprite = people.length ? people[people.length - 1].sprite : options.default_sprite;
      return sprite;
    }

    function change_selected(changes, label) {
      const item = selected_item();
      if (!item) return;
      const { x, y } = item.entry;
      const base_entries = item.source === "generated" ? PE.entries_with_entry_placed(entries, PE.hand_entry_from_generated(entries, item.entry)) : entries;
      commit(PE.entries_with_entry_changed(base_entries, x, y, changes), label || "edit " + item.entry.kind);
      bench.select(selection_for(x, y));
    }

    function delete_selected() {
      const item = selected_item();
      if (!item) return;
      commit(PE.entries_without_entry_at(entries, generated, item.entry.x, item.entry.y), "delete " + item.entry.kind);
      bench.select(null);
    }

    function commit(entries_after, label) {
      bench.undoStack.push({ kind: "people", before: entries, after: entries_after, label });
      restore(entries_after);
    }

    function restore(restored_entries) {
      entries = PE.copy_of(restored_entries);
      remember();
      send_to_server();
      bench.refresh();
    }

    function remember() {
      if (!route_key) return;
      bench.sset(PE.PEOPLE_MEMORY_KEY, PE.remember_people(bench.sget(PE.PEOPLE_MEMORY_KEY), route_key, route_name, entries));
    }

    function report_skipped(answer) {
      for (const skipped of answer.skipped || []) {
        const entry = skipped.entry || {};
        bench.log("warn", `${entry.kind || "Entry"} at x ${entry.x}, y ${entry.y} doesn't fit this map: ${skipped.reason}. It stays remembered.`);
      }
    }

    async function send_people_of_route_to_server(route, people_and_signs) {
      try {
        const answer = await bench.postJSON("/api/route-people", { id: route.id, entries: people_and_signs });
        report_skipped(answer);
        return answer;
      } catch (error) {
        bench.log("error", "Saving people and signs failed: " + error.message);
        return null;
      }
    }

    async function send_to_server() {
      const route = state.result;
      if (!route) return null;
      const answer = await send_people_of_route_to_server(route, entries);
      if (!answer || state.result !== route) return null;
      resume(people_snapshot_after_answer(snapshot(), answer));
      bench.refresh();
      return answer;
    }

    function people_snapshot_after_answer(people_snapshot, answer) {
      const has_skipped_entries = (answer.skipped || []).length > 0;
      const snapshot_after_answer = Object.assign({}, people_snapshot, {
        generated: answer.generated || [], entries: has_skipped_entries ? people_snapshot.entries : answer.placed,
      });
      return snapshot_after_answer;
    }

    async function people_put_back_on(route) {
      const people_route_key = SL.paintKey(route);
      const remembered_snapshot = {
        route_key: people_route_key, route_name: route.name, generated: [],
        entries: PE.remembered_entries(bench.sget(PE.PEOPLE_MEMORY_KEY), people_route_key),
      };
      if (!options.sprites.length) await load_options();
      const answer = await send_people_of_route_to_server(route, remembered_snapshot.entries);
      if (!answer) return remembered_snapshot;
      if (remembered_snapshot.entries.length) bench.log("info", `Put back ${remembered_snapshot.entries.length} people and signs on ${route.name}`);
      const put_back = people_snapshot_after_answer(remembered_snapshot, answer);
      return put_back;
    }

    async function on_new_route(route) {
      resume({ route_key: SL.paintKey(route), route_name: route.name, generated: [], entries: PE.remembered_entries(bench.sget(PE.PEOPLE_MEMORY_KEY), SL.paintKey(route)) });
      const put_back = await people_put_back_on(route);
      if (state.result !== route) return;
      resume(put_back);
      bench.refresh();
    }

    function snapshot() {
      const people_snapshot = { route_key, route_name, entries, generated };
      return people_snapshot;
    }

    function resume(people_snapshot) {
      route_key = people_snapshot.route_key;
      route_name = people_snapshot.route_name;
      entries = people_snapshot.entries;
      generated = people_snapshot.generated;
    }

    function people() {
      return entries.filter((entry) => entry.kind === PE.PERSON_KIND);
    }

    function person_at(x, y) {
      const person = people().find((entry) => entry.x === x && entry.y === y);
      return person || null;
    }

    function labelled(label, control, extra_class) {
      const row = el("label", "sel-field" + (extra_class ? " " + extra_class : ""));
      row.append(el("span", "", label), control);
      return row;
    }

    function text_input(value, on_change) {
      const input = Object.assign(el("input"), { value: value || "", spellcheck: false });
      input.onchange = () => on_change(input.value);
      return input;
    }

    function text_area(value, on_change, rows) {
      const area = Object.assign(el("textarea", "people-text"), { value: value || "", rows: rows || 3, spellcheck: true });
      area.onchange = () => on_change(area.value);
      return area;
    }

    function sprite_button(sprite_option, entry) {
      const button = Object.assign(el("button", "people-sprite" + (sprite_option.id === entry.sprite ? " on" : "")), { type: "button", title: sprite_option.label });
      button.setAttribute("aria-label", sprite_option.label);
      button.style.backgroundImage = `url("/api/sprite.png?kind=gfx_${sprite_option.id}&tiler=${route_tiler()}")`;
      button.onclick = () => change_selected({ sprite: sprite_option.id }, "change sprite");
      return button;
    }

    function sprite_grid(entry) {
      const grid = el("div", "people-sprites");
      grid.style.setProperty("--tile", SPRITE_TILE_SIZE_IN_PIXELS + "px");
      for (const sprite_option of options.sprites) grid.append(sprite_button(sprite_option, entry));
      return grid;
    }

    function facing_buttons(entry) {
      const group = el("span", "people-facing");
      for (const [facing, arrow] of Object.entries(FACING_ARROWS)) {
        const button = Object.assign(el("button", "seg-btn" + (entry.facing === facing ? " on" : ""), arrow), { type: "button", title: "Faces " + facing });
        button.onclick = () => change_selected({ facing }, "turn person");
        group.append(button);
      }
      return group;
    }

    function movement_select(entry) {
      const select = el("select");
      for (const [movement, label] of Object.entries(MOVEMENT_LABELS)) select.append(Object.assign(el("option", "", label), { value: movement }));
      select.value = entry.movement || "still";
      select.onchange = () => change_selected({ movement: select.value }, "change movement");
      return select;
    }

    function trainer_class_label(trainer_class) {
      return trainer_class.replace("TRAINER_CLASS_", "").replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (letter) => letter.toUpperCase());
    }

    function trainer_class_select(trainer) {
      const select = el("select");
      for (const trainer_class of options.trainer_classes) select.append(Object.assign(el("option", "", trainer_class_label(trainer_class)), { value: trainer_class }));
      select.value = trainer.trainer_class;
      select.onchange = () => change_trainer({ trainer_class: select.value });
      return select;
    }

    function change_trainer(changes) {
      const item = selected_item();
      if (!item || !item.entry.trainer) return;
      change_selected({ trainer: Object.assign(PE.copy_of(item.entry.trainer), changes) }, "edit trainer");
    }

    function species_label(species) {
      return species.replace("SPECIES_", "").replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (letter) => letter.toUpperCase());
    }

    function team_row(trainer, member_index) {
      const member = trainer.team[member_index];
      const row = el("div", "people-team-row");
      const species = Object.assign(el("input"), { value: species_label(member.species), spellcheck: false });
      species.setAttribute("list", "peopleSpecies");
      species.onchange = () => change_team_member(trainer, member_index, { species: species.value.trim() });
      const level = Object.assign(el("input"), { type: "number", min: 1, max: 100, value: member.level, title: "Level" });
      level.onchange = () => change_team_member(trainer, member_index, { level: Math.max(1, Math.min(100, parseInt(level.value, 10) || 5)) });
      const remove = Object.assign(el("button", "btn icon", "×"), { type: "button", title: "Remove from the team" });
      remove.onclick = () => change_trainer({ team: trainer.team.filter((other, other_index) => other_index !== member_index) });
      row.append(species, el("span", "muted", "Lv"), level, remove);
      return row;
    }

    function change_team_member(trainer, member_index, changes) {
      const team = trainer.team.map((member, other_index) => (other_index === member_index ? Object.assign({}, member, changes) : member));
      change_trainer({ team });
    }

    function team_editor(trainer) {
      const box = el("div", "people-team");
      trainer.team.forEach((member, member_index) => box.append(team_row(trainer, member_index)));
      if (trainer.team.length < MAXIMUM_TEAM_SIZE) {
        const add = Object.assign(el("button", "btn", "Add Pokémon"), { type: "button" });
        add.onclick = () => change_trainer({ team: trainer.team.concat([{ species: "SPECIES_ZIGZAGOON", level: 5 }]) });
        box.append(add);
      }
      return box;
    }

    function trainer_toggle(entry) {
      const checkbox = Object.assign(el("input"), { type: "checkbox", checked: !!entry.trainer });
      checkbox.onchange = () => change_selected({ trainer: checkbox.checked ? PE.copy_of(PE.DEFAULT_TRAINER) : null }, checkbox.checked ? "make trainer" : "stop being a trainer");
      return checkbox;
    }

    function trainer_fields(box, entry) {
      const trainer = entry.trainer;
      box.append(labelled("Class", trainer_class_select(trainer)));
      box.append(labelled("Team", team_editor(trainer), "top"));
      box.append(labelled("Beaten", text_area(trainer.defeat_text, (value) => change_trainer({ defeat_text: value }), 2), "top"));
      box.append(labelled("After", text_area(trainer.after_text, (value) => change_trainer({ after_text: value }), 2), "top"));
    }

    function render_person(box, entry, helpers) {
      helpers.title(entry.name || (entry.trainer ? "Trainer" : "Person"), entry.trainer ? "#e0453a" : "#d9cfb8");
      box.append(labelled("Name", text_input(entry.name, (value) => change_selected({ name: value.trim() }, "rename person"))));
      box.append(sprite_grid(entry));
      box.append(labelled("Faces", facing_buttons(entry)));
      box.append(labelled("Moves", movement_select(entry)));
      box.append(labelled(entry.trainer ? "Intro" : "Says", text_area(entry.text, (value) => change_selected({ text: value }, "edit text")), "top"));
      box.append(labelled("Trainer", trainer_toggle(entry)));
      if (entry.trainer) trainer_fields(box, entry);
    }

    function render_sign(box, entry, helpers) {
      helpers.title("Sign", SIGN_COLOURS.board);
      box.append(labelled("Text", text_area(entry.text, (value) => change_selected({ text: value }, "edit sign"), 4), "top"));
    }

    function events_mentioning(entry) {
      if (entry.kind !== PE.PERSON_KIND || !entry.id) return [];
      return PE.rules_mentioning_person(bench.event_rules(), route_name, entry.id).map((rule) => rule.id);
    }

    function render_selection(box, helpers) {
      const item = selected_item();
      if (!item) return;
      const entry = item.entry;
      if (entry.kind === PE.SIGN_KIND) render_sign(box, entry, helpers);
      else render_person(box, entry, helpers);
      helpers.kv([["cell", `x ${entry.x}, y ${entry.y}`], ["id", entry.id], ["events", events_mentioning(entry).join(", ")]]);
      const delete_button = Object.assign(el("button", "btn", "Delete"), { type: "button", title: "Delete (Delete)" });
      delete_button.onclick = delete_selected;
      helpers.actions().append(delete_button);
    }

    function tree_label(entry) {
      if (entry.kind === PE.SIGN_KIND) return `Sign (${entry.x}, ${entry.y})`;
      return `${entry.name || (entry.trainer ? "Trainer" : "Person")} (${entry.x}, ${entry.y})`;
    }

    function tree_nodes() {
      return shown().map(({ entry, source }) => ({
        id: `ps:${entry.x},${entry.y}`, label: tree_label(entry), meta: source === "generated" ? "generated" : entry.id,
        icon: { cls: "dot", color: entry.kind === PE.SIGN_KIND ? SIGN_COLOURS.board : entry.trainer ? "#e0453a" : "#d9cfb8" },
        sel: selection_for(entry.x, entry.y),
      }));
    }

    function tree_id(selection) {
      return `ps:${selection.x},${selection.y}`;
    }

    function selected_box() {
      const item = selected_item();
      return item && [item.entry.x - 2, item.entry.y - 2, item.entry.x + 3, item.entry.y + 3];
    }

    function bind_toolbar() {
      document.querySelectorAll("[data-people-kind]").forEach((button) => {
        button.onclick = () => {
          placing_kind = button.dataset.peopleKind;
          document.querySelectorAll("[data-people-kind]").forEach((other) => other.classList.toggle("on", other === button));
        };
      });
    }

    bind_toolbar();
    return {
      SELECTION_TYPE, on_new_route, people_put_back_on, snapshot, resume, restore, click, pick_at, draw, hides_generated_event, render_selection,
      tree_nodes, tree_id, selected_box, delete_selected, person_at, people, shown, draw_entry, draw_selection_outline,
      has_selection: () => !!selected_item(),
      is_selection: (selection) => !!selection && selection.type === SELECTION_TYPE,
    };
  }

  window.RoutePeople = { create };
})();
