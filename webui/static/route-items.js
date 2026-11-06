/* Items in the Routes view: overworld sprites for events (read by the server from the
   user's own ROM tree, /api/sprite.png), the hidden-item glitter (/api/glitter.png), and
   the Item tool that places Poké Balls and hidden items by hand. bench.js creates one
   with window.RouteItems.create(bench_context) and calls into it from its draw, pick,
   tool, undo and generate code. Placement memory helpers live in shell-logic.js. */
(function () {
  "use strict";
  const SL = window.SL;
  const WALKABLE = "gptdhyvzqn";
  const GLITTER_FRAME_SIZE = 16;
  const GLITTER_TWINKLE = [0, 1, 2, 3, 2, 1];
  const KIND_LABELS = { item_ball: "Poké Ball (visible)", hidden_item: "Hidden item" };

  function load_image(url) {
    const image = new Image();
    image.src = url;
    return image.decode().then(() => image, () => null);
  }

  function create(bench) {
    const { state, $, el } = bench;
    const sprites_by_tiler = {};
    const sprite_loads = {};
    let glitter_strip = null;
    let shown_items = [];
    let route_key = "";

    async function load_assets() {
      glitter_strip = await load_image("/api/glitter.png");
      await load_sprites(route_tiler());
      await load_item_constants();
      bench.draw();
    }

    // the Ark rule tiler gets the Arklarna ROM's field-object art, learned routes the Emerald art
    function route_tiler() {
      return state.result && state.result.tiler === "learned" ? "learned" : "ark";
    }

    function load_sprites(tiler) {
      if (!sprite_loads[tiler]) sprite_loads[tiler] = fetch_sprites(tiler);
      return sprite_loads[tiler];
    }

    async function fetch_sprites(tiler) {
      const sprites = {};
      let listing = {};
      try { listing = (await bench.fetchJSON("/api/sprites?tiler=" + tiler)).sprites || {}; } catch (error) { listing = {}; }
      const kinds = Object.keys(listing).filter((kind) => listing[kind]);
      const images = await Promise.all(kinds.map((kind) => load_image(`/api/sprite.png?kind=${kind}&tiler=${tiler}`)));
      kinds.forEach((kind, index) => { if (images[index]) sprites[kind] = images[index]; });
      sprites_by_tiler[tiler] = sprites;
      if (!sprites.item_ball) bench.log("info", "No item-ball sprite found in a ROM tree on this machine; items use the plain markers.");
    }

    function current_sprites() {
      const tiler = route_tiler();
      if (sprites_by_tiler[tiler]) return sprites_by_tiler[tiler];
      if (!sprite_loads[tiler]) load_sprites(tiler).then(() => bench.draw());
      return {};
    }

    async function load_item_constants() {
      let names = [];
      try { names = (await bench.fetchJSON("/api/item-constants")).items || []; } catch (error) { return; }
      const list = $("itemConstants");
      list.innerHTML = "";
      for (const name of names) list.append(Object.assign(document.createElement("option"), { value: name }));
    }

    // ------------------------------------------------------------ drawing
    function glitter_frame_for(event, animation_frame) {
      return GLITTER_TWINKLE[(animation_frame + event.x + event.y) % GLITTER_TWINKLE.length];
    }

    function draw_glitter(graphics_context, event, left, top, cell_size, animation_frame) {
      const frame = glitter_frame_for(event, animation_frame);
      graphics_context.drawImage(glitter_strip, frame * GLITTER_FRAME_SIZE, 0, GLITTER_FRAME_SIZE, GLITTER_FRAME_SIZE,
        left, top, cell_size, cell_size);
    }

    function draw_overworld_sprite(graphics_context, sprite, left, top, cell_size) {
      const height_in_cells = sprite.height / 16;
      graphics_context.drawImage(sprite, left, top + cell_size - height_in_cells * cell_size, cell_size, height_in_cells * cell_size);
    }

    // true when the event was drawn as a sprite; false leaves it to bench's plain marker
    function draw_event_sprite(graphics_context, event, origin_x, origin_y, cell_size, animation_frame) {
      const left = origin_x + event.x * cell_size, top = origin_y + event.y * cell_size;
      if (event.kind === "hidden_item") {
        if (!glitter_strip) return false;
        draw_glitter(graphics_context, event, left, top, cell_size, animation_frame);
        return true;
      }
      const sprites = current_sprites();
      const sprite = sprites[SL.event_sprite_key(event)] || sprites[event.kind];
      if (!sprite) return false;
      draw_overworld_sprite(graphics_context, sprite, left, top, cell_size);
      return true;
    }

    function on_final_stage() {
      const route = state.result;
      return !!route && state.stage === route.stages.length - 1;
    }

    function visible_items() {
      return on_final_stage() ? shown_items.map((item) => Object.assign({ hand: "1" }, item)) : [];
    }

    function has_glitter() {
      const route = state.result;
      if (!glitter_strip || !route || !state.ov.events || state.ov.markings === false) return false;
      return window.PR.eventsAt(route, state.stage).concat(visible_items()).some((event) => event.kind === "hidden_item");
    }

    // ------------------------------------------------------------ placing
    function index_at(x, y) {
      return on_final_stage() ? SL.placed_item_index(shown_items, x, y) : -1;
    }

    function cell_problem(x, y) {
      const route = state.result;
      const final_stage = route.stages[route.stages.length - 1];
      const cell_class = final_stage.cls[y][x];
      if (!WALKABLE.includes(cell_class)) return `x ${x}, y ${y} is not walkable`;
      if (route.events.some((event) => event.x === x && event.y === y)) return `x ${x}, y ${y} already has a generated event`;
      return "";
    }

    function tool_choice(x, y) {
      const item_name = $("itemName").value.trim().toUpperCase() || "ITEM_POTION";
      return { kind: $("itemKind").value, x, y, item: item_name };
    }

    function click(cell) {
      if (!cell || !state.result) return;
      if (!on_final_stage()) { bench.log("warn", "Step to the last stage to place items."); return; }
      const existing = SL.placed_item_index(shown_items, cell.x, cell.y);
      if (existing >= 0) { bench.select({ type: "placed", i: existing }); return; }
      const problem = cell_problem(cell.x, cell.y);
      if (problem) { bench.log("warn", `Can't place an item there: ${problem}.`); return; }
      const new_item = tool_choice(cell.x, cell.y);
      if (!/^ITEM_[A-Z0-9_]+$/.test(new_item.item)) { bench.log("warn", `${new_item.item} is not an ITEM_* constant.`); return; }
      const after = SL.place_item(shown_items, new_item);
      commit(after, `place ${new_item.item}`);
      bench.select({ type: "placed", i: SL.placed_item_index(after, cell.x, cell.y) });
    }

    function selected_index() {
      const selection = state.sel;
      return selection && selection.type === "placed" && shown_items[selection.i] ? selection.i : -1;
    }

    function update_selected(changes) {
      const index = selected_index();
      if (index < 0) return;
      commit(SL.update_placed_item(shown_items, index, changes), "edit placed item");
      bench.select({ type: "placed", i: index });
    }

    function delete_selected() {
      const index = selected_index();
      if (index < 0) return;
      const removed = shown_items[index];
      commit(SL.remove_placed_item(shown_items, index), `delete ${removed.item}`);
      bench.select(null);
    }

    function commit(after, label) {
      const before = shown_items;
      bench.undoStack.push({ kind: "items", before, after, label });
      restore(after);
    }

    function restore(items) {
      shown_items = items.map((item) => Object.assign({}, item));
      remember(shown_items);
      send_to_server();
      bench.refresh();
    }

    function remember(items) {
      if (!route_key) return;
      bench.sset(SL.PLACED_ITEMS_KEY, SL.remember_placed_items(bench.sget(SL.PLACED_ITEMS_KEY), route_key, items));
    }

    function remembered_items() {
      return (bench.sget(SL.PLACED_ITEMS_KEY) || {})[route_key] || [];
    }

    async function send_to_server(items) {
      const route = state.result;
      if (!route) return null;
      try {
        return await bench.postJSON("/api/route-items", { id: route.id, items: items || shown_items });
      } catch (error) {
        bench.log("error", "Saving placed items failed: " + error.message);
        return null;
      }
    }

    // after every generate: put the remembered items back where the cells are still walkable
    async function on_new_route(route) {
      route_key = SL.paintKey(route);
      shown_items = [];
      const remembered = remembered_items();
      if (!remembered.length) { update_count(); return; }
      const result = await send_to_server(remembered);
      if (!result || state.result !== route) return;
      const merged = SL.merge_placement_result(remembered, result);
      shown_items = merged.shown;
      remember(merged.remembered);
      for (const skipped of result.skipped || []) {
        const item = skipped.item || {};
        bench.log("warn", `Placed item ${item.item || "?"} at x ${item.x}, y ${item.y} doesn't fit this map: ${skipped.reason}. It stays remembered.`);
      }
      bench.log("info", `Put back ${shown_items.length} placed item${shown_items.length === 1 ? "" : "s"} on ${route.name}`);
      bench.refresh();
    }

    function update_count() {
      const count = shown_items.length;
      $("itemN").textContent = count ? `${count} placed` : "";
    }

    // ------------------------------------------------------------ inspector + hierarchy
    function kind_select(value) {
      const select = el("select");
      for (const [kind, label] of Object.entries(KIND_LABELS)) select.append(Object.assign(el("option", "", label), { value: kind }));
      select.value = value;
      select.onchange = () => update_selected({ kind: select.value });
      return select;
    }

    function item_field(value) {
      const input = Object.assign(el("input"), { value, spellcheck: false });
      input.setAttribute("list", "itemConstants");
      input.onchange = () => {
        const item_name = input.value.trim().toUpperCase();
        if (/^ITEM_[A-Z0-9_]+$/.test(item_name)) update_selected({ item: item_name });
        else { bench.log("warn", `${input.value} is not an ITEM_* constant.`); input.value = value; }
      };
      return input;
    }

    function labelled(label, control) {
      const row = el("label", "sel-field");
      row.append(el("span", "", label), control);
      return row;
    }

    function render_selection(box, helpers) {
      const item = shown_items[state.sel.i];
      if (!item) return;
      helpers.title(item.kind === "hidden_item" ? "Hidden item (placed)" : "Poké Ball (placed)", item.kind === "hidden_item" ? "#f2d27a" : "#e0453a");
      helpers.kv([["cell", `x ${item.x}, y ${item.y}`], ["exported as", item.kind === "hidden_item" ? "bg_event hidden_item" : "object_event item ball"]]);
      box.append(labelled("Kind", kind_select(item.kind)), labelled("Item", item_field(item.item)));
      const delete_button = Object.assign(el("button", "btn", "Delete"), { type: "button", title: "Delete this item (Delete)" });
      delete_button.onclick = delete_selected;
      helpers.actions().append(delete_button);
    }

    function tree_nodes() {
      return shown_items.map((item, index) => ({
        id: "p:" + index, label: `${KIND_LABELS[item.kind]} (${item.x}, ${item.y})`, meta: item.item,
        icon: { cls: "dot", color: item.kind === "hidden_item" ? "#f2d27a" : "#e0453a" },
        sel: { type: "placed", i: index }, dim: !on_final_stage(),
      }));
    }

    function selected_box() {
      const item = shown_items[selected_index()];
      return item && [item.x - 2, item.y - 2, item.x + 3, item.y + 3];
    }

    function selected_item() {
      return shown_items[selected_index()] || null;
    }

    function bind_toolbar() {
      $("itemKind").innerHTML = "";
      for (const [kind, label] of Object.entries(KIND_LABELS)) $("itemKind").append(Object.assign(el("option", "", label), { value: kind }));
    }

    bind_toolbar();
    load_assets();
    return {
      draw_event_sprite, visible_items, has_glitter, index_at, click, delete_selected, restore, on_new_route,
      render_selection, tree_nodes, selected_box, selected_item, update_count,
      has_selection: () => selected_index() >= 0,
    };
  }

  window.RouteItems = { create };
})();
