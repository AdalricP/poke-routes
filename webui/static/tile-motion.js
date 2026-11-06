/* Per-tile animation in the Routes view. Press M over a tile on the map, or right-click a
   tile or object in the Tiles palette, to pick how that tile moves in the preview (motion and
   speed). Settings are stored per tileset by the server (/api/tile-looks, webui/tile_looks.py),
   which bakes them into the atlas frames; the scene then reloads its atlas. */
(function () {
  "use strict";

  const SPEED_LABELS = { 0.5: "slow", 1: "normal", 2: "fast" };
  const MOTION_LABELS = {
    default: "default", off: "still", game: "in-game frames", sway: "sway", tips: "grass tips", nod: "nod",
    bop: "bop", dance: "dance", roll: "roll", wobble: "wobble",
  };
  const MOTION_KEY = "m";
  const POPOVER_OFFSET_IN_PIXELS = 12;
  const PREVIEW_SCALE = 3;
  const CELL_SIZE_IN_PIXELS = 16;

  function motion_label(motion) {
    const label = MOTION_LABELS[motion] || motion;
    return label;
  }

  function speed_label(speed) {
    const label = SPEED_LABELS[speed] || `${speed}×`;
    return label;
  }

  function effective_motion(looks, metatile_id) {
    const key = String(metatile_id);
    const override = looks.overrides[key];
    const fallback = looks.defaults[key] || { motion: "off", speed: 1 };
    const motion = override ? Object.assign({ is_override: true }, override) : Object.assign({ is_override: false }, fallback);
    return motion;
  }

  function is_choosable_motion(looks, metatile_id, motion) {
    const is_game_animated = (looks.defaults[String(metatile_id)] || {}).motion === "game";
    if (motion === "default" || motion === "off") return true;
    const is_allowed = is_game_animated ? motion === "game" : motion !== "game";
    return is_allowed;
  }

  function metatiles_of_palette_brush(palette, brush) {
    if (!palette || !brush) return [];
    if (brush.startsWith("object:")) {
      const object_entry = palette.objects.find((candidate) => candidate.id === brush.slice("object:".length));
      return object_entry ? object_entry.cells.flat() : [];
    }
    if (brush.startsWith("tile:")) return [Number(brush.slice("tile:".length))];
    const terrain_entry = palette.terrain.find((candidate) => candidate.code === brush);
    const sample_metatiles = terrain_entry ? Array.from(new Set(terrain_entry.sample)) : [];
    return sample_metatiles;
  }

  function hovered_metatile(state) {
    const route_result = state.result, hovered_cell = state.hover;
    if (!route_result || !hovered_cell) return null;
    const shown_tiles = route_result.stages[state.stage].tiles;
    const metatile_id = shown_tiles[hovered_cell.y * route_result.width + hovered_cell.x];
    return metatile_id;
  }

  function is_typing_in_a_field(event_target) {
    const tag_name = event_target && event_target.tagName;
    const is_field = tag_name === "INPUT" || tag_name === "SELECT" || tag_name === "TEXTAREA" || (event_target && event_target.isContentEditable);
    return !!is_field;
  }

  function create(bench) {
    const looks_by_tileset = {};
    let popover = null;

    function tileset_key() {
      const route_result = bench.state.result;
      return route_result ? route_result.tileset : "";
    }

    async function load_looks(key, should_refresh) {
      if (looks_by_tileset[key] && !should_refresh) return looks_by_tileset[key];
      looks_by_tileset[key] = await bench.fetchJSON(`/api/tile-looks?tileset=${encodeURIComponent(key)}`);
      return looks_by_tileset[key];
    }

    function close_popover() {
      if (!popover) return;
      popover.remove();
      popover = null;
    }

    function preview_canvas(metatile_ids) {
      const atlas = bench.state.atlas;
      const preview = document.createElement("canvas");
      const shown_metatiles = metatile_ids.slice(0, 6);
      preview.width = Math.max(1, shown_metatiles.length) * CELL_SIZE_IN_PIXELS;
      preview.height = CELL_SIZE_IN_PIXELS;
      preview.style.width = `${preview.width * PREVIEW_SCALE}px`;
      preview.style.height = `${preview.height * PREVIEW_SCALE}px`;
      const graphics_context = preview.getContext("2d");
      if (!atlas) return preview;
      shown_metatiles.forEach((metatile_id, position) => {
        const atlas_cell = atlas.meta.index[metatile_id];
        if (atlas_cell == null) return;
        graphics_context.drawImage(atlas.img, (atlas_cell % atlas.meta.columns) * CELL_SIZE_IN_PIXELS, Math.floor(atlas_cell / atlas.meta.columns) * CELL_SIZE_IN_PIXELS,
          CELL_SIZE_IN_PIXELS, CELL_SIZE_IN_PIXELS, position * CELL_SIZE_IN_PIXELS, 0, CELL_SIZE_IN_PIXELS, CELL_SIZE_IN_PIXELS);
      });
      return preview;
    }

    function choice_select(choices, chosen, label_of, is_enabled) {
      const select = document.createElement("select");
      for (const choice of choices) {
        const option = Object.assign(document.createElement("option"), { value: String(choice), textContent: label_of(choice) });
        option.disabled = !is_enabled(choice);
        select.append(option);
      }
      select.value = String(chosen);
      return select;
    }

    function labelled_row(label_text, control) {
      const row = document.createElement("label");
      row.className = "tile-motion-row";
      row.append(Object.assign(document.createElement("span"), { textContent: label_text }), control);
      return row;
    }

    async function apply_motion(key, metatile_ids, motion, speed) {
      const changed_looks = await bench.postJSON("/api/tile-looks", { tileset: key, metatiles: metatile_ids, motion, speed: Number(speed) });
      looks_by_tileset[key] = changed_looks;
      await bench.reload_atlas_at_version(changed_looks.version);
      bench.log("info", `Tile animation: ${metatile_ids.length} tile(s) set to ${motion_label(motion)}, ${speed_label(Number(speed))}`);
    }

    function popover_body(key, looks, metatile_ids, title_text) {
      const current = effective_motion(looks, metatile_ids[0]);
      const together = looks.together[String(metatile_ids[0])];
      const affected_metatiles = Array.from(new Set(metatile_ids.concat(together || [])));
      const motion_select = choice_select(looks.motions, current.is_override ? current.motion : "default",
        (motion) => (motion === "default" ? `default (${motion_label((looks.defaults[String(metatile_ids[0])] || { motion: "off" }).motion)})` : motion_label(motion)),
        (motion) => is_choosable_motion(looks, metatile_ids[0], motion));
      const speed_select = choice_select(looks.speeds, current.speed, speed_label, () => true);
      const send = () => apply_motion(key, affected_metatiles, motion_select.value, speed_select.value)
        .catch((error) => bench.log("error", "Could not change the tile animation", error.message));
      motion_select.onchange = send;
      speed_select.onchange = send;
      const heading = document.createElement("div");
      heading.className = "tile-motion-head";
      heading.append(preview_canvas(affected_metatiles), Object.assign(document.createElement("span"), { textContent: title_text }));
      return [heading, labelled_row("Motion", motion_select), labelled_row("Speed", speed_select)];
    }

    async function open_popover(metatile_ids, screen_x, screen_y, title_text) {
      const key = tileset_key();
      if (!key || !metatile_ids.length) return;
      close_popover();
      let looks;
      try { looks = await load_looks(key, false); } catch (error) { bench.log("error", "Could not load tile animations", error.message); return; }
      popover = document.createElement("div");
      popover.className = "tile-motion-popover";
      popover.setAttribute("role", "dialog");
      popover.setAttribute("aria-label", "Tile animation");
      popover.append(...popover_body(key, looks, metatile_ids, title_text));
      document.body.append(popover);
      const popover_box = popover.getBoundingClientRect();
      popover.style.left = `${Math.min(window.innerWidth - popover_box.width - 4, screen_x + POPOVER_OFFSET_IN_PIXELS)}px`;
      popover.style.top = `${Math.min(window.innerHeight - popover_box.height - 4, screen_y + POPOVER_OFFSET_IN_PIXELS)}px`;
      const first_control = popover.querySelector("select");
      if (first_control) first_control.focus();
    }

    let last_pointer = { x: 0, y: 0 };
    function remember_pointer(pointer_event) {
      last_pointer = { x: pointer_event.clientX, y: pointer_event.clientY };
    }

    function open_for_hovered_tile(key_event) {
      if (key_event.key.toLowerCase() !== MOTION_KEY || key_event.metaKey || key_event.ctrlKey || key_event.altKey) return;
      if (is_typing_in_a_field(key_event.target) || window.Shell.layout.center !== "scene") return;
      const metatile_id = hovered_metatile(bench.state);
      if (metatile_id == null) return;
      key_event.preventDefault();
      open_popover([metatile_id], last_pointer.x, last_pointer.y, `tile 0x${metatile_id.toString(16).toUpperCase().padStart(3, "0")}`);
    }

    function open_for_palette_button(mouse_event) {
      const brush_button = mouse_event.target.closest && mouse_event.target.closest("[data-brush]");
      if (!brush_button) return;
      const metatile_ids = metatiles_of_palette_brush(bench.state.palette, brush_button.dataset.brush);
      if (!metatile_ids.length) return;
      mouse_event.preventDefault();
      open_popover(metatile_ids, mouse_event.clientX, mouse_event.clientY, brush_button.textContent.trim());
    }

    function close_on_outside_press(pointer_event) {
      if (popover && !popover.contains(pointer_event.target)) close_popover();
    }

    function close_on_escape(key_event) {
      if (popover && key_event.key === "Escape") { key_event.stopPropagation(); close_popover(); }
    }

    function attach() {
      window.addEventListener("pointermove", remember_pointer, { passive: true });
      window.addEventListener("keydown", close_on_escape, true);
      window.addEventListener("keydown", open_for_hovered_tile);
      window.addEventListener("pointerdown", close_on_outside_press, true);
      const palette_sections = document.getElementById("paletteSections");
      if (palette_sections) palette_sections.addEventListener("contextmenu", open_for_palette_button);
    }

    return { attach, open_popover, close_popover };
  }

  const api = { create, motion_label, speed_label, effective_motion, is_choosable_motion, metatiles_of_palette_brush, hovered_metatile };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof window === "undefined") return;
  window.TileMotion = api;
  window.addEventListener("DOMContentLoaded", () => { if (window.Bench) create(window.Bench).attach(); });
})();
