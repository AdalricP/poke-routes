(function () {
  "use strict";
  const TL = window.TileEditorLogic;
  const WINDOW_STATE_KEY = "pokeroutes.tile_editor.window.v1";
  const TILESET_SAVED_EVENT = "pokeroutes:tileset-saved";
  const PICKER_COLUMNS = 8;
  const PICKER_CELL_IN_PIXELS = 32;
  const PICKER_SECTION_GAP_IN_PIXELS = 6;
  const ZOOM_STEPS = [8, 12, 16, 20, 24, 32];
  const DEFAULT_ZOOM_INDEX = 3;
  const GHOST_LAYER_OPACITY = 0.35;
  const PREVIEW_SCALES = [1, 2, 4];
  const SIDE = TL.METATILE_SIDE_IN_PIXELS;
  const LAYER_NAMES = ["bottom", "top"];

  const ICONS = {
    pencil: '<path d="M11.4 1.8l2.8 2.8-8.3 8.3-3.5.7.7-3.5z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><path d="M9.6 3.6l2.8 2.8" stroke="currentColor" stroke-width="1.4"/>',
    fill: '<path d="M7 2.5l5.5 5.5-4.8 4.8a1.4 1.4 0 01-2 0L2.2 9.3a1.4 1.4 0 010-2z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><path d="M2.6 8.2h9.4" stroke="currentColor" stroke-width="1.2"/><path d="M13.6 10.2c.8 1.2 1.2 2 1.2 2.6a1.2 1.2 0 01-2.4 0c0-.6.4-1.4 1.2-2.6z" fill="currentColor"/>',
    eyedropper: '<path d="M10.6 2.4a1.9 1.9 0 012.8 0l.2.2a1.9 1.9 0 010 2.8l-1.5 1.5.8.8-1.1 1.1-4.2-4.2 1.1-1.1.8.8z" fill="currentColor"/><path d="M8 6.4l-5.2 5.2-.6 2.2 2.2-.6L9.6 8" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>',
    line: '<path d="M3 13L13 3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><rect x="1.5" y="11.5" width="3" height="3" fill="currentColor"/><rect x="11.5" y="1.5" width="3" height="3" fill="currentColor"/>',
    mirror_x: '<path d="M8 1.5v13" stroke="currentColor" stroke-width="1.1" stroke-dasharray="1.6 1.4"/><path d="M6.4 4L2 8l4.4 4z" fill="currentColor"/><path d="M9.6 4L14 8l-4.4 4z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>',
    mirror_y: '<path d="M1.5 8h13" stroke="currentColor" stroke-width="1.1" stroke-dasharray="1.6 1.4"/><path d="M4 6.4L8 2l4 4.4z" fill="currentColor"/><path d="M4 9.6L8 14l4-4.4z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>',
    flip_horizontal: '<path d="M8 1.5v13" stroke="currentColor" stroke-width="1.2"/><path d="M6.2 3.5v9L1.8 12.5z" fill="currentColor"/><path d="M9.8 3.5v9l4.4 0z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>',
    flip_vertical: '<path d="M1.5 8h13" stroke="currentColor" stroke-width="1.2"/><path d="M3.5 6.2h9L12.5 1.8z" fill="currentColor"/><path d="M3.5 9.8h9l0 4.4z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>',
    rotate_clockwise: '<path d="M12.6 8.6A4.8 4.8 0 113.4 6.6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><path d="M13.8 3.2v4.2H9.6z" fill="currentColor"/>',
    shared: '<path d="M6.6 9.4l2.8-2.8" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/><path d="M7.2 4.6l1.3-1.3a2.6 2.6 0 013.7 3.7l-1.3 1.3M8.8 11.4l-1.3 1.3a2.6 2.6 0 01-3.7-3.7l1.3-1.3" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>',
    new_tile: '<rect x="2.5" y="2.5" width="11" height="11" rx="1" fill="none" stroke="currentColor" stroke-width="1.2"/><path d="M8 5v6M5 8h6" stroke="currentColor" stroke-width="1.5"/>',
    duplicate: '<rect x="5.5" y="5.5" width="8" height="8" rx="1" fill="none" stroke="currentColor" stroke-width="1.2"/><path d="M10.5 3.5v-1h-8v8h1" fill="none" stroke="currentColor" stroke-width="1.2"/>',
    edited_only: '<path d="M8 1.8l1.8 3.9 4.2.5-3.1 2.9.8 4.2L8 11.2l-3.7 2.1.8-4.2L2 6.2l4.2-.5z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>',
    undo: '<path d="M5.5 3.5L2 7l3.5 3.5V8.2h4.2a2.6 2.6 0 010 5.2H7v1.6h2.7a4.2 4.2 0 000-8.4H5.5z" fill="currentColor"/>',
    redo: '<path d="M10.5 3.5L14 7l-3.5 3.5V8.2H6.3a2.6 2.6 0 000 5.2H9v1.6H6.3a4.2 4.2 0 010-8.4h4.2z" fill="currentColor"/>',
    zoom_in: '<path d="M7.2 3h1.6v4.2H13v1.6H8.8V13H7.2V8.8H3V7.2h4.2z" fill="currentColor"/>',
    zoom_out: '<rect x="3" y="7.2" width="10" height="1.6" fill="currentColor"/>',
    dock: '<rect x="1.5" y="2.5" width="13" height="11" fill="none" stroke="currentColor" stroke-width="1.2"/><rect x="1.5" y="2.5" width="13" height="3" fill="currentColor"/>',
    float: '<rect x="4.5" y="1.5" width="10" height="8" fill="none" stroke="currentColor" stroke-width="1.2"/><path d="M1.5 6.5v8h8" fill="none" stroke="currentColor" stroke-width="1.2"/>',
    close: '<path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>',
    lock: '<rect x="3.5" y="7" width="9" height="7" rx="1" fill="currentColor"/><path d="M5.5 7V5a2.5 2.5 0 015 0v2" fill="none" stroke="currentColor" stroke-width="1.4"/>',
    palette: '<path d="M8 1.8a6.2 6.2 0 100 12.4c1 0 1.4-.6 1.4-1.3 0-1-.9-1.2-.9-2s.6-1.3 1.6-1.3h1.6a2.6 2.6 0 002.5-2.6C14.2 4.1 11.4 1.8 8 1.8z" fill="none" stroke="currentColor" stroke-width="1.2"/><circle cx="5" cy="6.4" r="1" fill="currentColor"/><circle cx="8" cy="4.6" r="1" fill="currentColor"/><circle cx="4.8" cy="9.6" r="1" fill="currentColor"/>',
  };

  const DRAWING_TOOLS = [
    { id: "pencil", key: "b", label: "Pencil (B) · right-click picks a colour" },
    { id: "fill", key: "g", label: "Fill (G)" },
    { id: "eyedropper", key: "i", label: "Pick colour (I)" },
    { id: "line", key: "l", label: "Line (L)" },
  ];

  const state = {
    window_element: null, elements: {}, tilesets: [], tileset_id: "", tileset: null, base: null,
    overlay: null, saved_overlay: null, revision: 0, model: null, metatile_ids: [],
    selected_metatile_id: 0, active_layer: 0, focused_quadrant: 0, tool: "pencil", colour_index: 1,
    mirror_horizontally: false, mirror_vertically: false, edit_shared_tiles: false, edited_only: false,
    zoom_index: DEFAULT_ZOOM_INDEX, undo_overlays: [], redo_overlays: [], pending_edit: Promise.resolve(),
    stroke: null, hover_pixel: null, live_palette_colour: null, preview_writes: null,
    placement: read_window_placement(), picker_cells: [], status_note: "",
  };

  function read_window_placement() {
    try {
      const stored_placement = JSON.parse(window.localStorage.getItem(WINDOW_STATE_KEY) || "null");
      return stored_placement && typeof stored_placement === "object" ? stored_placement : { docked: false, rect: null };
    } catch (storage_error) {
      return { docked: false, rect: null };
    }
  }

  function remember_window_placement() {
    try {
      window.localStorage.setItem(WINDOW_STATE_KEY, JSON.stringify(state.placement));
    } catch (storage_error) {
      state.placement_is_not_remembered = true;
    }
  }

  function make_element(tag_name, class_name, text_content) {
    const element = document.createElement(tag_name);
    if (class_name) element.className = class_name;
    if (text_content != null) element.textContent = text_content;
    return element;
  }

  function icon_button(icon_name, label, on_click) {
    const button = make_element("button", "te-icon");
    button.type = "button";
    button.title = label;
    button.setAttribute("aria-label", label);
    button.innerHTML = `<svg viewBox="0 0 16 16" aria-hidden="true">${ICONS[icon_name]}</svg>`;
    button.addEventListener("click", on_click);
    return button;
  }

  function labelled_row(label_text, control_element) {
    const row = make_element("label", "te-row");
    row.append(make_element("span", "te-row-label", label_text), control_element);
    return row;
  }

  function select_element(options, on_change) {
    const select = make_element("select");
    options.forEach((option) => select.append(new Option(option.name, String(option.value))));
    select.addEventListener("change", () => on_change(select.value));
    return select;
  }

  function log_to_console(level, message) {
    if (window.Shell && window.Shell.log) window.Shell.log(level, message);
  }

  async function fetch_json(url, request_options) {
    const response = await fetch(url, request_options);
    const response_body = await response.json().catch(() => ({}));
    if (!response.ok) throw Object.assign(new Error(response_body.error || response.statusText), { status: response.status, body: response_body });
    return response_body;
  }

  function post_json(url, request_body) {
    const response_promise = fetch_json(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request_body) });
    return response_promise;
  }

  function build_window() {
    const window_element = make_element("section", "te-window");
    window_element.tabIndex = -1;
    window_element.setAttribute("role", "dialog");
    window_element.setAttribute("aria-label", "Tile editor");
    window_element.hidden = true;
    window_element.append(build_head(), build_body(), build_foot());
    window_element.addEventListener("keydown", handle_window_key);
    window_element.addEventListener("pointerdown", () => raise_window(), true);
    new ResizeObserver(remember_floating_size).observe(window_element);
    document.body.append(window_element);
    state.window_element = window_element;
  }

  function build_head() {
    const head = make_element("header", "te-head");
    const tileset_select = make_element("select", "te-tileset");
    tileset_select.setAttribute("aria-label", "Tileset");
    tileset_select.title = "Tileset";
    tileset_select.addEventListener("change", () => load_tileset(tileset_select.value));
    const lock_mark = make_element("span", "te-lock");
    lock_mark.innerHTML = `<svg viewBox="0 0 16 16" aria-hidden="true">${ICONS.lock}</svg>`;
    lock_mark.title = "Read-only";
    lock_mark.hidden = true;
    const dock_button = icon_button("dock", "Dock in the centre", toggle_docked);
    head.append(make_element("span", "te-title", "Tile editor"), tileset_select, lock_mark, make_element("span", "te-grow"),
      dock_button, icon_button("close", "Close", close));
    head.addEventListener("pointerdown", start_window_drag);
    head.addEventListener("dblclick", (double_click) => { if (!double_click.target.closest("button, select")) toggle_docked(); });
    Object.assign(state.elements, { tileset_select, lock_mark, dock_button });
    return head;
  }

  function build_body() {
    const body = make_element("div", "te-body");
    body.append(build_picker(), build_tool_column(), build_stage(), build_side());
    return body;
  }

  function build_picker() {
    const picker = make_element("aside", "te-picker");
    const bar = make_element("div", "te-bar");
    const new_button = icon_button("new_tile", "New tile", () => run_operation({ kind: "create" }, select_created_metatile));
    const duplicate_button = icon_button("duplicate", "Copy this tile into a new one", () => run_operation({ kind: "create", copy_from: state.selected_metatile_id }, select_created_metatile));
    const edited_only_button = icon_button("edited_only", "Only new and edited tiles", toggle_edited_only);
    bar.append(new_button, duplicate_button, make_element("span", "te-grow"), edited_only_button);
    const scroller = make_element("div", "te-picker-scroll");
    const picker_canvas = make_element("canvas", "te-picker-canvas");
    picker_canvas.addEventListener("click", choose_metatile_from_picker);
    picker_canvas.addEventListener("mousemove", describe_picker_hover);
    picker_canvas.addEventListener("mouseleave", () => set_status_note(""));
    scroller.append(picker_canvas);
    picker.append(bar, scroller);
    Object.assign(state.elements, { new_button, duplicate_button, edited_only_button, picker_canvas, picker_scroller: scroller });
    return picker;
  }

  function build_tool_column() {
    const column = make_element("div", "te-tools");
    column.setAttribute("role", "toolbar");
    column.setAttribute("aria-orientation", "vertical");
    state.elements.tool_buttons = {};
    for (const tool of DRAWING_TOOLS) {
      const tool_button = icon_button(tool.id, tool.label, () => choose_tool(tool.id));
      state.elements.tool_buttons[tool.id] = tool_button;
      column.append(tool_button);
    }
    const mirror_x_button = icon_button("mirror_x", "Mirror left/right (X)", () => toggle_setting("mirror_horizontally"));
    const mirror_y_button = icon_button("mirror_y", "Mirror top/bottom (Y)", () => toggle_setting("mirror_vertically"));
    const flip_horizontal_button = icon_button("flip_horizontal", "Flip tile left/right", () => transform_selected("flip_horizontal"));
    const flip_vertical_button = icon_button("flip_vertical", "Flip tile top/bottom", () => transform_selected("flip_vertical"));
    const rotate_button = icon_button("rotate_clockwise", "Rotate tile clockwise (R)", () => transform_selected("rotate_clockwise"));
    const shared_button = icon_button("shared", "Edit shared 8×8 tiles in place: changes every tile that uses them", () => toggle_setting("edit_shared_tiles"));
    column.append(make_element("span", "te-sep"), mirror_x_button, mirror_y_button, make_element("span", "te-sep"),
      flip_horizontal_button, flip_vertical_button, rotate_button, make_element("span", "te-sep"), shared_button);
    Object.assign(state.elements, { mirror_x_button, mirror_y_button, flip_horizontal_button, flip_vertical_button, rotate_button, shared_button });
    return column;
  }

  function build_stage() {
    const stage = make_element("main", "te-stage");
    const bar = make_element("div", "te-bar");
    const layer_switch = make_element("div", "te-seg");
    layer_switch.setAttribute("role", "tablist");
    state.elements.layer_buttons = LAYER_NAMES.map((layer_name, layer_index) => {
      const layer_button = make_element("button", "seg-btn", layer_name === "bottom" ? "Bottom" : "Top");
      layer_button.type = "button";
      layer_button.title = `${layer_button.textContent} layer (T switches)`;
      layer_button.addEventListener("click", () => choose_layer(layer_index));
      layer_switch.append(layer_button);
      return layer_button;
    });
    const undo_button = icon_button("undo", "Undo", undo);
    const redo_button = icon_button("redo", "Redo", redo);
    const zoom_label = make_element("span", "te-zoom");
    bar.append(undo_button, redo_button, make_element("span", "te-sep-v"), layer_switch, make_element("span", "te-grow"),
      icon_button("zoom_out", "Zoom out (−)", () => change_zoom(-1)), zoom_label, icon_button("zoom_in", "Zoom in (+)", () => change_zoom(1)));
    const canvas_wrap = make_element("div", "te-canvas-wrap");
    const edit_canvas = make_element("canvas", "te-canvas");
    edit_canvas.addEventListener("pointerdown", start_stroke);
    edit_canvas.addEventListener("pointermove", continue_stroke);
    edit_canvas.addEventListener("pointerup", finish_stroke);
    edit_canvas.addEventListener("pointercancel", cancel_stroke);
    edit_canvas.addEventListener("pointerleave", () => { state.hover_pixel = null; render_edit_canvas(); render_status(); });
    edit_canvas.addEventListener("contextmenu", (context_event) => context_event.preventDefault());
    edit_canvas.addEventListener("wheel", zoom_with_wheel, { passive: false });
    canvas_wrap.append(edit_canvas);
    stage.append(bar, canvas_wrap);
    Object.assign(state.elements, { undo_button, redo_button, zoom_label, edit_canvas, canvas_wrap });
    return stage;
  }

  function build_side() {
    const side = make_element("aside", "te-side");
    side.append(build_palette_section(), build_preview_section(), build_properties_section());
    return side;
  }

  function build_palette_section() {
    const section = make_element("section", "te-section");
    const head = make_element("div", "te-section-head");
    const palette_icon = make_element("span", "te-glyph");
    palette_icon.innerHTML = `<svg viewBox="0 0 16 16" aria-hidden="true">${ICONS.palette}</svg>`;
    const palette_slot_select = select_element(Array.from({ length: 13 }, (unused, palette_slot) => ({ value: palette_slot, name: `Palette ${palette_slot}` })),
      (palette_slot_text) => set_focused_quadrant_palette(Number(palette_slot_text)));
    palette_slot_select.title = "Palette of the highlighted 8×8 quarter";
    head.append(palette_icon, palette_slot_select);
    const swatches = make_element("div", "te-swatches");
    state.elements.swatch_buttons = Array.from({ length: 16 }, (unused, colour_index) => build_swatch(colour_index, swatches));
    const colour_input = make_element("input", "te-colour");
    colour_input.type = "color";
    colour_input.title = "Edit this colour (GBA 15-bit)";
    colour_input.addEventListener("input", () => preview_palette_colour(colour_input.value));
    colour_input.addEventListener("change", () => commit_palette_colour(colour_input.value));
    const colour_hex_input = make_element("input", "te-hex");
    colour_hex_input.spellcheck = false;
    colour_hex_input.maxLength = 7;
    colour_hex_input.setAttribute("aria-label", "Colour hex");
    colour_hex_input.addEventListener("change", () => commit_palette_colour(colour_hex_input.value));
    const colour_row = make_element("div", "te-colour-row");
    colour_row.append(colour_input, colour_hex_input, make_element("span", "te-colour-index"));
    section.append(head, swatches, colour_row);
    Object.assign(state.elements, { palette_slot_select, colour_input, colour_hex_input, colour_index_label: colour_row.lastChild });
    return section;
  }

  function build_swatch(colour_index, swatches) {
    const swatch_button = make_element("button", "te-swatch");
    swatch_button.type = "button";
    swatch_button.title = `Colour ${colour_index}`;
    swatch_button.setAttribute("aria-label", `Colour ${colour_index}`);
    swatch_button.addEventListener("click", () => choose_colour(colour_index));
    swatches.append(swatch_button);
    return swatch_button;
  }

  function build_preview_section() {
    const section = make_element("section", "te-section te-previews");
    const single_row = make_element("div", "te-preview-row");
    state.elements.preview_canvases = PREVIEW_SCALES.map((scale) => {
      const preview_canvas = make_element("canvas", "te-preview");
      preview_canvas.width = SIDE * scale;
      preview_canvas.height = SIDE * scale;
      preview_canvas.title = `${scale}×`;
      single_row.append(preview_canvas);
      return preview_canvas;
    });
    const tiled_canvas = make_element("canvas", "te-preview te-tiled");
    tiled_canvas.width = SIDE * 3 * 2;
    tiled_canvas.height = SIDE * 3 * 2;
    tiled_canvas.title = "Tiled 3×3: check the seams";
    section.append(single_row, tiled_canvas);
    state.elements.tiled_canvas = tiled_canvas;
    return section;
  }

  function build_properties_section() {
    const section = make_element("section", "te-section te-props");
    const behaviour_select = make_element("select");
    behaviour_select.addEventListener("change", () => set_property("behaviour", Number(behaviour_select.value)));
    const layer_type_select = make_element("select");
    layer_type_select.addEventListener("change", () => set_property("layer_type", Number(layer_type_select.value)));
    const in_palette_checkbox = make_element("input");
    in_palette_checkbox.type = "checkbox";
    in_palette_checkbox.addEventListener("change", () => set_property("in_palette", in_palette_checkbox.checked));
    const name_input = make_element("input");
    name_input.maxLength = 40;
    name_input.spellcheck = false;
    name_input.addEventListener("change", () => set_property("name", name_input.value.trim()));
    const class_select = make_element("select");
    class_select.addEventListener("change", () => set_property("class", class_select.value));
    const blocked_checkbox = make_element("input");
    blocked_checkbox.type = "checkbox";
    blocked_checkbox.addEventListener("change", () => set_property("collision", blocked_checkbox.checked));
    const custom_rows = make_element("div", "te-custom-rows");
    custom_rows.append(labelled_row("Name", name_input), labelled_row("Class", class_select), labelled_row("Blocked", blocked_checkbox));
    section.append(labelled_row("Behaviour", behaviour_select), labelled_row("Layer", layer_type_select),
      labelled_row("Paint palette", in_palette_checkbox), custom_rows);
    Object.assign(state.elements, { behaviour_select, layer_type_select, in_palette_checkbox, name_input, class_select, blocked_checkbox, custom_rows, properties_section: section });
    return section;
  }

  function build_foot() {
    const foot = make_element("footer", "te-foot");
    const status_text = make_element("span", "te-status");
    const reload_button = make_element("button", "btn", "Reload");
    reload_button.type = "button";
    reload_button.hidden = true;
    reload_button.addEventListener("click", () => load_tileset(state.tileset_id));
    const revert_button = make_element("button", "btn", "Revert");
    revert_button.type = "button";
    revert_button.addEventListener("click", revert_to_saved);
    const save_button = make_element("button", "btn primary", "Save");
    save_button.type = "button";
    save_button.title = "Save to tilesets/overrides (⌘S)";
    save_button.addEventListener("click", save_overlay);
    foot.append(status_text, make_element("span", "te-grow"), reload_button, revert_button, save_button);
    Object.assign(state.elements, { status_text, reload_button, revert_button, save_button });
    return foot;
  }

  function default_floating_rect() {
    const width_in_pixels = Math.min(1080, window.innerWidth - 40);
    const height_in_pixels = Math.min(640, window.innerHeight - 100);
    const floating_rect = { x: Math.round((window.innerWidth - width_in_pixels) / 2), y: 70, w: width_in_pixels, h: height_in_pixels };
    return floating_rect;
  }

  function clamped_rect(rect) {
    const width_in_pixels = Math.max(560, Math.min(rect.w, window.innerWidth));
    const height_in_pixels = Math.max(360, Math.min(rect.h, window.innerHeight));
    const left = Math.max(0, Math.min(rect.x, window.innerWidth - 120));
    const top = Math.max(0, Math.min(rect.y, window.innerHeight - 40));
    return { x: left, y: top, w: width_in_pixels, h: height_in_pixels };
  }

  function apply_window_placement() {
    const window_element = state.window_element;
    const center_rect = center_dock_rect();
    const is_docked = state.placement.docked && !!center_rect;
    const rect = is_docked ? center_rect : clamped_rect(state.placement.rect || default_floating_rect());
    window_element.classList.toggle("docked", !!is_docked);
    Object.assign(window_element.style, { left: rect.x + "px", top: rect.y + "px", width: rect.w + "px", height: rect.h + "px" });
    const dock_label = is_docked ? "Float" : "Dock in the centre";
    state.elements.dock_button.title = dock_label;
    state.elements.dock_button.setAttribute("aria-label", dock_label);
    state.elements.dock_button.innerHTML = `<svg viewBox="0 0 16 16" aria-hidden="true">${ICONS[is_docked ? "float" : "dock"]}</svg>`;
  }

  function center_dock_rect() {
    if (!window.Shell || !window.Shell.focused_view_group_rect) return null;
    const rect = window.Shell.focused_view_group_rect();
    return rect;
  }

  function toggle_docked() {
    state.placement.docked = !state.placement.docked;
    remember_window_placement();
    apply_window_placement();
  }

  function remember_floating_size() {
    if (state.window_element.hidden || state.placement.docked) return;
    const window_rect = state.window_element.getBoundingClientRect();
    state.placement.rect = { x: Math.round(window_rect.left), y: Math.round(window_rect.top), w: Math.round(window_rect.width), h: Math.round(window_rect.height) };
    remember_window_placement();
    render_edit_canvas();
  }

  function start_window_drag(pointer_event) {
    if (pointer_event.button !== 0 || pointer_event.target.closest("button, select, input")) return;
    const start_rect = state.window_element.getBoundingClientRect();
    const grab_offset_x = pointer_event.clientX - start_rect.left, grab_offset_y = pointer_event.clientY - start_rect.top;
    const move_window = (move_event) => {
      state.placement = { docked: false, rect: { x: move_event.clientX - grab_offset_x, y: move_event.clientY - grab_offset_y, w: start_rect.width, h: start_rect.height } };
      apply_window_placement();
    };
    const stop_dragging = () => {
      window.removeEventListener("pointermove", move_window);
      window.removeEventListener("pointerup", stop_dragging);
      document.body.classList.remove("moving");
      remember_window_placement();
    };
    document.body.classList.add("moving");
    window.addEventListener("pointermove", move_window);
    window.addEventListener("pointerup", stop_dragging);
  }

  function raise_window() {
    state.window_element.classList.add("raised");
  }

  async function open() {
    if (!state.window_element) build_window();
    state.window_element.hidden = false;
    apply_window_placement();
    state.window_element.focus({ preventScroll: true });
    if (!state.tilesets.length) await load_tileset_list();
    render_everything();
  }

  function close() {
    if (state.window_element) state.window_element.hidden = true;
  }

  function is_open() {
    const window_is_open = !!state.window_element && !state.window_element.hidden;
    return window_is_open;
  }

  async function load_tileset_list() {
    try {
      state.tilesets = (await fetch_json("/api/tile-editor/tilesets")).tilesets;
    } catch (load_error) {
      set_status_note("No tilesets: " + load_error.message);
      return;
    }
    const tileset_select = state.elements.tileset_select;
    tileset_select.innerHTML = "";
    state.tilesets.forEach((tileset) => tileset_select.append(new Option(tileset.label, tileset.id)));
    const first_tileset = state.tilesets.find((tileset) => tileset.editable) || state.tilesets[0];
    if (first_tileset) await load_tileset(first_tileset.id);
  }

  async function load_tileset(tileset_id) {
    set_status_note("Loading…");
    await state.pending_edit;
    let loaded_tileset;
    try {
      loaded_tileset = await fetch_json(`/api/tile-editor/tileset?id=${encodeURIComponent(tileset_id)}`);
    } catch (load_error) {
      set_status_note(load_error.message);
      return;
    }
    state.tileset = loaded_tileset;
    state.pending_edit = Promise.resolve();
    state.tileset_id = tileset_id;
    state.elements.tileset_select.value = tileset_id;
    state.base = state.tileset.base;
    state.saved_overlay = state.tileset.overlay || { revision: 0 };
    state.overlay = JSON.parse(JSON.stringify(state.saved_overlay));
    state.revision = state.saved_overlay.revision || 0;
    state.undo_overlays = [];
    state.redo_overlays = [];
    state.elements.reload_button.hidden = true;
    fill_property_options();
    rebuild_model();
    if (!state.model.metatiles.has(state.selected_metatile_id)) state.selected_metatile_id = state.metatile_ids[0] || 0;
    set_status_note("");
    render_everything();
  }

  function fill_property_options() {
    const tileset = state.tileset;
    replace_options(state.elements.behaviour_select, tileset.behaviours.map((behaviour) => ({ value: behaviour.value, name: behaviour.name })));
    replace_options(state.elements.layer_type_select, tileset.layer_types.map((layer_type) => ({ value: layer_type.value, name: layer_type.name })));
    replace_options(state.elements.class_select, tileset.class_options.map((class_option) => ({ value: class_option.code, name: `${class_option.name} (${class_option.code})` })));
  }

  function replace_options(select, options) {
    select.innerHTML = "";
    options.forEach((option) => select.append(new Option(option.name, String(option.value))));
  }

  function rebuild_model() {
    state.model = TL.overlaid_model(state.base, state.overlay);
    state.metatile_ids = TL.sorted_metatile_ids(state.model);
  }

  function is_editable() {
    const editable = !!state.tileset && state.tileset.editable;
    return editable;
  }

  function queue_overlay_edit(task) {
    const edited_tileset_id = state.tileset_id;
    state.pending_edit = state.pending_edit.then(() => {
      if (state.tileset_id !== edited_tileset_id) return;
      return task(edited_tileset_id);
    });
    return state.pending_edit;
  }

  function run_operation(operation, after_success) {
    if (!is_editable()) return Promise.resolve();
    return queue_overlay_edit((tileset_id) => send_operation(tileset_id, operation, after_success));
  }

  async function send_operation(tileset_id, operation, after_success) {
    try {
      const response_body = await post_json("/api/tile-editor/edit", { id: tileset_id, overlay: state.overlay, operation });
      if (state.tileset_id !== tileset_id) return;
      replace_overlay_with_undo(response_body.overlay);
      if (after_success) after_success(response_body);
    } catch (edit_error) {
      if (state.tileset_id !== tileset_id) return;
      set_status_note(edit_error.message);
      log_to_console("warn", "Tile editor: " + edit_error.message);
      render_everything();
    }
  }

  function replace_overlay_with_undo(new_overlay) {
    state.undo_overlays.push(state.overlay);
    state.redo_overlays = [];
    state.overlay = new_overlay;
    rebuild_model();
    render_everything();
  }

  function undo() {
    queue_overlay_edit(() => {
      if (!state.undo_overlays.length) return;
      state.redo_overlays.push(state.overlay);
      state.overlay = state.undo_overlays.pop();
      rebuild_model();
      render_everything();
    });
  }

  function redo() {
    queue_overlay_edit(() => {
      if (!state.redo_overlays.length) return;
      state.undo_overlays.push(state.overlay);
      state.overlay = state.redo_overlays.pop();
      rebuild_model();
      render_everything();
    });
  }

  function revert_to_saved() {
    if (!TL.overlays_differ(state.overlay, state.saved_overlay)) return;
    replace_overlay_with_undo(JSON.parse(JSON.stringify(state.saved_overlay)));
  }

  async function save_overlay() {
    if (!is_editable() || !TL.overlays_differ(state.overlay, state.saved_overlay)) return;
    await state.pending_edit;
    try {
      const response_body = await post_json("/api/tile-editor/save", { id: state.tileset_id, overlay: state.overlay, revision: state.revision });
      state.saved_overlay = response_body.overlay;
      state.overlay = JSON.parse(JSON.stringify(response_body.overlay));
      state.revision = response_body.revision;
      rebuild_model();
      set_status_note("Saved");
      log_to_console("info", `Tile editor: saved ${state.tileset.label} tiles (revision ${state.revision}). Generate to see them on the route.`);
      window.dispatchEvent(new CustomEvent(TILESET_SAVED_EVENT, { detail: { id: state.tileset_id } }));
    } catch (save_error) {
      const is_conflict = save_error.status === 409;
      set_status_note(is_conflict ? "Changed elsewhere since you opened it" : save_error.message);
      state.elements.reload_button.hidden = !is_conflict;
    }
    render_everything();
  }

  function select_created_metatile(response_body) {
    state.selected_metatile_id = response_body.metatile_id;
    render_everything();
    scroll_picker_to_selection();
  }

  function transform_selected(transform_name) {
    run_operation({ kind: "transform", metatile_id: state.selected_metatile_id, layers: LAYER_NAMES, transform: transform_name });
  }

  function set_focused_quadrant_palette(palette_slot) {
    run_operation({ kind: "quadrant_palette", metatile_id: state.selected_metatile_id, layer: LAYER_NAMES[state.active_layer], quadrant: state.focused_quadrant, palette_slot });
  }

  function set_property(property_name, property_value) {
    run_operation({ kind: "properties", metatile_id: state.selected_metatile_id, properties: { [property_name]: property_value } });
  }

  function focused_palette_slot() {
    const palette_slots = TL.layer_palette_slots(state.model, state.selected_metatile_id, state.active_layer);
    const palette_slot = palette_slots[state.focused_quadrant] || 0;
    return palette_slot;
  }

  function preview_palette_colour(colour_hex) {
    state.live_palette_colour = { palette_slot: focused_palette_slot(), colour_index: state.colour_index, colour_hex: TL.gba_colour_hex(colour_hex) };
    state.model.palettes[state.live_palette_colour.palette_slot][state.colour_index] = state.live_palette_colour.colour_hex;
    render_drawing_views();
    render_palette();
  }

  function commit_palette_colour(colour_text) {
    const is_hex_colour = /^#?[0-9a-f]{6}$/i.test(colour_text.trim());
    state.live_palette_colour = null;
    if (!is_hex_colour) { render_palette(); return; }
    const colour_hex = TL.gba_colour_hex("#" + colour_text.trim().replace("#", "").toLowerCase());
    run_operation({ kind: "palette_colours", palette_slot: focused_palette_slot(), colours: { [state.colour_index]: colour_hex } });
  }

  function canvas_pixel(pointer_event) {
    const canvas_rect = state.elements.edit_canvas.getBoundingClientRect();
    const pixel_size = canvas_rect.width / SIDE;
    const pixel_x = Math.floor((pointer_event.clientX - canvas_rect.left) / pixel_size);
    const pixel_y = Math.floor((pointer_event.clientY - canvas_rect.top) / pixel_size);
    const is_inside = pixel_x >= 0 && pixel_y >= 0 && pixel_x < SIDE && pixel_y < SIDE;
    return is_inside ? [pixel_x, pixel_y] : null;
  }

  function active_layer_pixels() {
    const pixels = TL.layer_pixels(state.model, state.selected_metatile_id, state.active_layer);
    return pixels;
  }

  function pick_colour_at(pixel_x, pixel_y) {
    state.colour_index = active_layer_pixels()[pixel_y * SIDE + pixel_x];
    state.focused_quadrant = TL.quadrant_of_pixel(pixel_x, pixel_y);
    render_palette();
    render_status();
  }

  function start_stroke(pointer_event) {
    const pixel = canvas_pixel(pointer_event);
    if (!pixel || !state.model) return;
    const is_picking = pointer_event.button === 2 || state.tool === "eyedropper";
    if (is_picking) { pick_colour_at(pixel[0], pixel[1]); return; }
    if (pointer_event.button !== 0 || !is_editable()) return;
    state.focused_quadrant = TL.quadrant_of_pixel(pixel[0], pixel[1]);
    if (state.tool === "fill") { commit_pixel_writes(TL.flood_fill_writes(active_layer_pixels(), pixel[0], pixel[1], state.colour_index)); return; }
    state.elements.edit_canvas.setPointerCapture(pointer_event.pointerId);
    state.stroke = { start: pixel, last: pixel, points: [pixel] };
    render_drawing_views();
  }

  function continue_stroke(pointer_event) {
    const pixel = canvas_pixel(pointer_event);
    state.hover_pixel = pixel;
    if (state.stroke && pixel) extend_stroke(pixel);
    render_edit_canvas();
    render_status();
  }

  function extend_stroke(pixel) {
    const stroke = state.stroke;
    if (state.tool === "line") {
      stroke.points = TL.line_points(stroke.start[0], stroke.start[1], pixel[0], pixel[1]);
    } else {
      stroke.points = stroke.points.concat(TL.line_points(stroke.last[0], stroke.last[1], pixel[0], pixel[1]).slice(1));
    }
    stroke.last = pixel;
    render_previews();
  }

  function finish_stroke() {
    if (!state.stroke) return;
    const pixel_writes = stroke_writes();
    state.stroke = null;
    commit_pixel_writes(pixel_writes);
  }

  function cancel_stroke() {
    state.stroke = null;
    render_drawing_views();
  }

  function stroke_writes() {
    if (!state.stroke) return [];
    const pixel_writes = TL.writes_for_points(state.stroke.points, state.colour_index, state.mirror_horizontally, state.mirror_vertically);
    return pixel_writes;
  }

  function commit_pixel_writes(pixel_writes) {
    const current_pixels = active_layer_pixels();
    const changing_writes = pixel_writes.filter(([pixel_x, pixel_y, colour_index]) => current_pixels[pixel_y * SIDE + pixel_x] !== colour_index);
    if (!changing_writes.length) { render_drawing_views(); return; }
    state.preview_writes = changing_writes;
    run_operation({ kind: "write_pixels", metatile_id: state.selected_metatile_id, layer: LAYER_NAMES[state.active_layer],
      pixels: changing_writes, edit_shared_tiles: state.edit_shared_tiles }).then(() => { state.preview_writes = null; render_drawing_views(); });
    render_drawing_views();
  }

  function shown_layer_pixels(layer_index) {
    const pixels = TL.layer_pixels(state.model, state.selected_metatile_id, layer_index);
    if (layer_index !== state.active_layer) return pixels;
    const pending_writes = (state.preview_writes || []).concat(stroke_writes());
    const shown_pixels = pending_writes.length ? TL.pixels_with_writes(pixels, pending_writes) : pixels;
    return shown_pixels;
  }

  function render_everything() {
    if (!is_open() || !state.model) { render_controls(); return; }
    render_controls();
    render_picker();
    render_drawing_views();
    render_palette();
    render_properties();
    render_status();
  }

  function render_drawing_views() {
    if (!state.model) return;
    render_edit_canvas();
    render_previews();
  }

  function render_controls() {
    const elements = state.elements;
    const editable = is_editable();
    const is_dirty = editable && !!state.overlay && TL.overlays_differ(state.overlay, state.saved_overlay);
    Object.entries(elements.tool_buttons || {}).forEach(([tool_id, tool_button]) => tool_button.classList.toggle("on", state.tool === tool_id));
    elements.mirror_x_button.classList.toggle("on", state.mirror_horizontally);
    elements.mirror_y_button.classList.toggle("on", state.mirror_vertically);
    elements.shared_button.classList.toggle("on", state.edit_shared_tiles);
    elements.edited_only_button.classList.toggle("on", state.edited_only);
    elements.layer_buttons.forEach((layer_button, layer_index) => layer_button.classList.toggle("on", layer_index === state.active_layer));
    const edit_only_controls = [elements.new_button, elements.duplicate_button, elements.flip_horizontal_button, elements.flip_vertical_button, elements.rotate_button, elements.shared_button,
      elements.tool_buttons.pencil, elements.tool_buttons.fill, elements.tool_buttons.line, elements.mirror_x_button, elements.mirror_y_button, elements.palette_slot_select, elements.colour_input, elements.colour_hex_input];
    edit_only_controls.forEach((control) => { control.disabled = !editable; });
    elements.lock_mark.hidden = editable || !state.tileset;
    elements.save_button.hidden = !editable;
    elements.revert_button.hidden = !editable;
    elements.save_button.disabled = !is_dirty;
    elements.revert_button.disabled = !is_dirty;
    elements.save_button.textContent = is_dirty ? "Save •" : "Save";
    elements.undo_button.disabled = !state.undo_overlays.length;
    elements.redo_button.disabled = !state.redo_overlays.length;
    elements.zoom_label.textContent = pixel_size_on_screen() + "×";
    state.window_element.classList.toggle("read-only", !editable);
  }

  function shown_metatile_ids() {
    const metatile_ids = state.edited_only ? state.metatile_ids.filter((metatile_id) => TL.is_custom_or_changed(state.model, metatile_id)) : state.metatile_ids;
    return metatile_ids;
  }

  function picker_layout(metatile_ids) {
    const cells = [];
    let row_index = 0, column_index = 0, extra_top_in_pixels = 0, previous_was_primary = true;
    for (const metatile_id of metatile_ids) {
      const is_primary = metatile_id < TL.FIRST_SECONDARY_METATILE_ID;
      if (previous_was_primary && !is_primary && cells.length) {
        if (column_index) row_index++;
        column_index = 0;
        extra_top_in_pixels = PICKER_SECTION_GAP_IN_PIXELS;
      }
      previous_was_primary = is_primary;
      cells.push({ metatile_id, x: column_index * PICKER_CELL_IN_PIXELS, y: row_index * PICKER_CELL_IN_PIXELS + extra_top_in_pixels });
      column_index++;
      if (column_index === PICKER_COLUMNS) { column_index = 0; row_index++; }
    }
    return cells;
  }

  function metatile_canvas(metatile_id) {
    const small_canvas = document.createElement("canvas");
    small_canvas.width = SIDE;
    small_canvas.height = SIDE;
    small_canvas.getContext("2d").putImageData(new ImageData(TL.render_metatile_rgba(state.model, metatile_id), SIDE, SIDE), 0, 0);
    return small_canvas;
  }

  function render_picker() {
    const picker_canvas = state.elements.picker_canvas;
    const cells = picker_layout(shown_metatile_ids());
    state.picker_cells = cells;
    const last_cell = cells[cells.length - 1];
    picker_canvas.width = PICKER_COLUMNS * PICKER_CELL_IN_PIXELS;
    picker_canvas.height = last_cell ? last_cell.y + PICKER_CELL_IN_PIXELS : PICKER_CELL_IN_PIXELS;
    const drawing_context = picker_canvas.getContext("2d");
    drawing_context.imageSmoothingEnabled = false;
    cells.forEach((cell) => drawing_context.drawImage(metatile_canvas(cell.metatile_id), cell.x, cell.y, PICKER_CELL_IN_PIXELS, PICKER_CELL_IN_PIXELS));
    cells.filter((cell) => TL.is_custom_or_changed(state.model, cell.metatile_id)).forEach((cell) => draw_edited_mark(drawing_context, cell));
    const selected_cell = cells.find((cell) => cell.metatile_id === state.selected_metatile_id);
    if (selected_cell) draw_selection_frame(drawing_context, selected_cell);
  }

  function draw_edited_mark(drawing_context, cell) {
    drawing_context.fillStyle = "#e2c46a";
    drawing_context.fillRect(cell.x + PICKER_CELL_IN_PIXELS - 5, cell.y + 1, 4, 4);
  }

  function draw_selection_frame(drawing_context, cell) {
    drawing_context.strokeStyle = "#ffffff";
    drawing_context.lineWidth = 2;
    drawing_context.strokeRect(cell.x + 1, cell.y + 1, PICKER_CELL_IN_PIXELS - 2, PICKER_CELL_IN_PIXELS - 2);
    drawing_context.strokeStyle = "#4d90d6";
    drawing_context.lineWidth = 1;
    drawing_context.strokeRect(cell.x + 2.5, cell.y + 2.5, PICKER_CELL_IN_PIXELS - 5, PICKER_CELL_IN_PIXELS - 5);
  }

  function picker_cell_at(mouse_event) {
    const canvas_rect = state.elements.picker_canvas.getBoundingClientRect();
    const point_x = mouse_event.clientX - canvas_rect.left, point_y = mouse_event.clientY - canvas_rect.top;
    const hit_cell = state.picker_cells.find((cell) => point_x >= cell.x && point_y >= cell.y && point_x < cell.x + PICKER_CELL_IN_PIXELS && point_y < cell.y + PICKER_CELL_IN_PIXELS);
    return hit_cell || null;
  }

  function choose_metatile_from_picker(mouse_event) {
    const hit_cell = picker_cell_at(mouse_event);
    if (!hit_cell) return;
    select_metatile(hit_cell.metatile_id);
  }

  function select_metatile(metatile_id) {
    state.selected_metatile_id = metatile_id;
    state.stroke = null;
    render_everything();
  }

  function describe_picker_hover(mouse_event) {
    const hit_cell = picker_cell_at(mouse_event);
    set_status_note(hit_cell ? metatile_label(hit_cell.metatile_id) : "");
  }

  function metatile_label(metatile_id) {
    const custom_entry = state.model.custom[String(metatile_id)];
    const hex_id = "0x" + metatile_id.toString(16).toUpperCase().padStart(3, "0");
    const label = custom_entry && custom_entry.name ? `${hex_id} · ${custom_entry.name}` : hex_id;
    return label;
  }

  function scroll_picker_to_selection() {
    const selected_cell = state.picker_cells.find((cell) => cell.metatile_id === state.selected_metatile_id);
    if (!selected_cell) return;
    const scroller = state.elements.picker_scroller;
    const is_hidden = selected_cell.y < scroller.scrollTop || selected_cell.y + PICKER_CELL_IN_PIXELS > scroller.scrollTop + scroller.clientHeight;
    if (is_hidden) scroller.scrollTop = selected_cell.y - scroller.clientHeight / 2;
  }

  function layer_canvas(layer_index) {
    const layer_drawing = document.createElement("canvas");
    layer_drawing.width = SIDE;
    layer_drawing.height = SIDE;
    const rgba = TL.layer_rgba(state.model, state.selected_metatile_id, layer_index, shown_layer_pixels(layer_index));
    layer_drawing.getContext("2d").putImageData(new ImageData(rgba, SIDE, SIDE), 0, 0);
    return layer_drawing;
  }

  function pixel_size_on_screen() {
    const pixel_size = ZOOM_STEPS[state.zoom_index];
    return pixel_size;
  }

  function render_edit_canvas() {
    if (!state.model) return;
    const edit_canvas = state.elements.edit_canvas;
    const pixel_size = pixel_size_on_screen();
    edit_canvas.width = SIDE * pixel_size;
    edit_canvas.height = SIDE * pixel_size;
    const drawing_context = edit_canvas.getContext("2d");
    drawing_context.imageSmoothingEnabled = false;
    draw_checkerboard(drawing_context, pixel_size);
    const layer_drawings = [layer_canvas(0), layer_canvas(1)];
    [0, 1].forEach((layer_index) => draw_layer(drawing_context, layer_drawings[layer_index], layer_index, pixel_size));
    draw_pixel_grid(drawing_context, pixel_size);
    draw_focused_quadrant(drawing_context, pixel_size);
    draw_hover_pixel(drawing_context, pixel_size);
  }

  function draw_layer(drawing_context, layer_drawing, layer_index, pixel_size) {
    drawing_context.globalAlpha = layer_index === state.active_layer ? 1 : GHOST_LAYER_OPACITY;
    drawing_context.drawImage(layer_drawing, 0, 0, SIDE * pixel_size, SIDE * pixel_size);
    drawing_context.globalAlpha = 1;
  }

  function draw_checkerboard(drawing_context, pixel_size) {
    const square_in_pixels = Math.max(4, pixel_size / 2);
    drawing_context.fillStyle = "#3a3a3a";
    drawing_context.fillRect(0, 0, SIDE * pixel_size, SIDE * pixel_size);
    drawing_context.fillStyle = "#444444";
    for (let square_y = 0; square_y < SIDE * pixel_size; square_y += square_in_pixels) {
      for (let square_x = (square_y / square_in_pixels) % 2 ? square_in_pixels : 0; square_x < SIDE * pixel_size; square_x += square_in_pixels * 2) {
        drawing_context.fillRect(square_x, square_y, square_in_pixels, square_in_pixels);
      }
    }
  }

  function draw_pixel_grid(drawing_context, pixel_size) {
    const total_in_pixels = SIDE * pixel_size;
    drawing_context.beginPath();
    for (let line_index = 1; line_index < SIDE; line_index++) {
      if (line_index === TL.PIXELS_PER_TILE_SIDE) continue;
      drawing_context.moveTo(line_index * pixel_size + 0.5, 0);
      drawing_context.lineTo(line_index * pixel_size + 0.5, total_in_pixels);
      drawing_context.moveTo(0, line_index * pixel_size + 0.5);
      drawing_context.lineTo(total_in_pixels, line_index * pixel_size + 0.5);
    }
    drawing_context.strokeStyle = "rgba(0,0,0,0.16)";
    drawing_context.lineWidth = 1;
    drawing_context.stroke();
    const middle = TL.PIXELS_PER_TILE_SIDE * pixel_size + 0.5;
    drawing_context.beginPath();
    drawing_context.moveTo(middle, 0);
    drawing_context.lineTo(middle, total_in_pixels);
    drawing_context.moveTo(0, middle);
    drawing_context.lineTo(total_in_pixels, middle);
    drawing_context.strokeStyle = "rgba(255,255,255,0.32)";
    drawing_context.stroke();
  }

  function draw_focused_quadrant(drawing_context, pixel_size) {
    const quadrant_size = TL.PIXELS_PER_TILE_SIDE * pixel_size;
    const origin_x = (state.focused_quadrant % 2) * quadrant_size, origin_y = Math.floor(state.focused_quadrant / 2) * quadrant_size;
    drawing_context.strokeStyle = "rgba(77,144,214,0.9)";
    drawing_context.lineWidth = 2;
    drawing_context.strokeRect(origin_x + 1, origin_y + 1, quadrant_size - 2, quadrant_size - 2);
  }

  function draw_hover_pixel(drawing_context, pixel_size) {
    if (!state.hover_pixel || state.stroke) return;
    const hovered_points = TL.mirrored_points(state.hover_pixel[0], state.hover_pixel[1], state.mirror_horizontally, state.mirror_vertically);
    drawing_context.strokeStyle = "rgba(255,255,255,0.85)";
    drawing_context.lineWidth = 1;
    hovered_points.forEach(([pixel_x, pixel_y]) => drawing_context.strokeRect(pixel_x * pixel_size + 0.5, pixel_y * pixel_size + 0.5, pixel_size - 1, pixel_size - 1));
  }

  function composite_canvas() {
    const composite = document.createElement("canvas");
    composite.width = SIDE;
    composite.height = SIDE;
    const drawing_context = composite.getContext("2d");
    drawing_context.drawImage(layer_canvas(0), 0, 0);
    drawing_context.drawImage(layer_canvas(1), 0, 0);
    return composite;
  }

  function render_previews() {
    if (!state.model || !state.elements.preview_canvases) return;
    const composite = composite_canvas();
    state.elements.preview_canvases.forEach((preview_canvas) => draw_scaled(preview_canvas, composite, 1));
    draw_scaled(state.elements.tiled_canvas, composite, 3);
  }

  function draw_scaled(target_canvas, composite, repeat_count) {
    const drawing_context = target_canvas.getContext("2d");
    const cell_in_pixels = target_canvas.width / repeat_count;
    drawing_context.imageSmoothingEnabled = false;
    for (let repeat_y = 0; repeat_y < repeat_count; repeat_y++) {
      for (let repeat_x = 0; repeat_x < repeat_count; repeat_x++) drawing_context.drawImage(composite, repeat_x * cell_in_pixels, repeat_y * cell_in_pixels, cell_in_pixels, cell_in_pixels);
    }
  }

  function render_palette() {
    if (!state.model) return;
    const palette_slot = focused_palette_slot();
    const palette_colours = state.model.palettes[palette_slot] || [];
    state.elements.palette_slot_select.value = String(palette_slot);
    state.elements.swatch_buttons.forEach((swatch_button, colour_index) => paint_swatch(swatch_button, palette_colours[colour_index], colour_index));
    const selected_colour = palette_colours[state.colour_index] || "#000000";
    if (document.activeElement !== state.elements.colour_input) state.elements.colour_input.value = selected_colour;
    if (document.activeElement !== state.elements.colour_hex_input) state.elements.colour_hex_input.value = selected_colour;
    state.elements.colour_index_label.textContent = state.colour_index === 0 && state.active_layer === 1 ? `${state.colour_index} · clear` : String(state.colour_index);
  }

  function paint_swatch(swatch_button, colour_hex, colour_index) {
    swatch_button.style.background = colour_hex || "#000";
    swatch_button.classList.toggle("on", colour_index === state.colour_index);
    swatch_button.classList.toggle("clear", colour_index === 0 && state.active_layer === 1);
  }

  function render_properties() {
    if (!state.model) return;
    const elements = state.elements;
    const metatile_id = state.selected_metatile_id;
    const attribute_value = state.model.attributes.get(metatile_id) || 0;
    ensure_option(elements.behaviour_select, attribute_value & 0xff);
    ensure_option(elements.layer_type_select, attribute_value >> 12);
    elements.behaviour_select.value = String(attribute_value & 0xff);
    elements.layer_type_select.value = String(attribute_value >> 12);
    const custom_entry = state.model.custom[String(metatile_id)];
    const is_in_palette = !!custom_entry && custom_entry.in_palette !== false;
    elements.in_palette_checkbox.checked = is_in_palette;
    elements.custom_rows.hidden = !is_in_palette;
    elements.name_input.value = (custom_entry && custom_entry.name) || "";
    ensure_option(elements.class_select, (custom_entry && custom_entry.class) || "g");
    elements.class_select.value = (custom_entry && custom_entry.class) || "g";
    elements.blocked_checkbox.checked = !!(custom_entry && custom_entry.collision);
    const editable = is_editable();
    [elements.behaviour_select, elements.layer_type_select, elements.in_palette_checkbox, elements.name_input, elements.class_select, elements.blocked_checkbox]
      .forEach((control) => { control.disabled = !editable; });
  }

  function ensure_option(select, option_value) {
    const has_option = [...select.options].some((option) => option.value === String(option_value));
    if (!has_option) select.append(new Option(typeof option_value === "number" ? "0x" + option_value.toString(16).toUpperCase().padStart(2, "0") : String(option_value), String(option_value)));
  }

  function set_status_note(note_text) {
    state.status_note = note_text;
    render_status();
  }

  function render_status() {
    if (!state.elements.status_text) return;
    state.elements.status_text.textContent = state.status_note || describe_focus();
  }

  function describe_focus() {
    if (!state.model) return "";
    const tile_entries = state.model.metatiles.get(state.selected_metatile_id) || [];
    const quadrant_entry = TL.decode_tile_entry(tile_entries[state.active_layer * TL.QUADRANTS_PER_LAYER + state.focused_quadrant] || 0);
    const share_count = TL.tile_reference_count(state.model, quadrant_entry.tile_index);
    const shared_note = share_count > 1 ? ` ×${share_count}` : "";
    const hover_note = state.hover_pixel ? ` · ${state.hover_pixel[0]},${state.hover_pixel[1]}` : "";
    const focus_text = `${metatile_label(state.selected_metatile_id)} · 8×8 #${quadrant_entry.tile_index}${shared_note}${hover_note}`;
    return focus_text;
  }

  function choose_tool(tool_id) {
    state.tool = tool_id;
    render_controls();
  }

  function choose_layer(layer_index) {
    state.active_layer = layer_index;
    render_everything();
  }

  function choose_colour(colour_index) {
    state.colour_index = colour_index;
    if (state.tool === "eyedropper") state.tool = "pencil";
    render_controls();
    render_palette();
  }

  function toggle_setting(setting_name) {
    state[setting_name] = !state[setting_name];
    render_controls();
    render_edit_canvas();
  }

  function toggle_edited_only() {
    state.edited_only = !state.edited_only;
    render_controls();
    render_picker();
  }

  function change_zoom(zoom_step) {
    state.zoom_index = Math.max(0, Math.min(ZOOM_STEPS.length - 1, state.zoom_index + zoom_step));
    render_controls();
    render_edit_canvas();
  }

  function zoom_with_wheel(wheel_event) {
    if (!wheel_event.ctrlKey && !wheel_event.metaKey) return;
    wheel_event.preventDefault();
    change_zoom(wheel_event.deltaY < 0 ? 1 : -1);
  }

  function move_selection(step) {
    const metatile_ids = shown_metatile_ids();
    const current_position = metatile_ids.indexOf(state.selected_metatile_id);
    const next_position = Math.max(0, Math.min(metatile_ids.length - 1, current_position + step));
    if (metatile_ids[next_position] == null) return;
    select_metatile(metatile_ids[next_position]);
    scroll_picker_to_selection();
  }

  function is_typing_in_field(key_event) {
    const is_field = !!key_event.target.closest("input:not([type=checkbox]):not([type=color]), select, textarea");
    return is_field;
  }

  function command_for_key(key_event) {
    const has_modifier = key_event.metaKey || key_event.ctrlKey;
    const pressed_key = key_event.key.toLowerCase();
    if (has_modifier && pressed_key === "z") return key_event.shiftKey ? redo : undo;
    if (has_modifier && pressed_key === "y") return redo;
    if (has_modifier && pressed_key === "s") return save_overlay;
    if (has_modifier || key_event.altKey || is_typing_in_field(key_event)) return null;
    const drawing_tool = DRAWING_TOOLS.find((tool) => tool.key === pressed_key);
    if (drawing_tool) return () => choose_tool(drawing_tool.id);
    return plain_key_command(pressed_key);
  }

  function plain_key_command(pressed_key) {
    switch (pressed_key) {
      case "t": return () => choose_layer(1 - state.active_layer);
      case "x": return () => toggle_setting("mirror_horizontally");
      case "y": return () => toggle_setting("mirror_vertically");
      case "r": return () => transform_selected("rotate_clockwise");
      case "+": case "=": return () => change_zoom(1);
      case "-": return () => change_zoom(-1);
      case "[": return () => choose_colour((state.colour_index + 15) % 16);
      case "]": return () => choose_colour((state.colour_index + 1) % 16);
      case "arrowleft": return () => move_selection(-1);
      case "arrowright": return () => move_selection(1);
      case "arrowup": return () => move_selection(-PICKER_COLUMNS);
      case "arrowdown": return () => move_selection(PICKER_COLUMNS);
      case "escape": return close;
      default: return null;
    }
  }

  function handle_window_key(key_event) {
    const command = command_for_key(key_event);
    if (!command) return;
    key_event.preventDefault();
    key_event.stopPropagation();
    command();
  }

  function follow_center_dock() {
    if (is_open() && state.placement.docked) apply_window_placement();
  }

  if (window.Shell && window.Shell.onResize) window.Shell.onResize(follow_center_dock);
  window.addEventListener("resize", () => { if (is_open()) { apply_window_placement(); render_edit_canvas(); } });

  window.TileEditor = { open, close, is_open, TILESET_SAVED_EVENT };
})();
