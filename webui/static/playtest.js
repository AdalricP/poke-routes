(function () {
  "use strict";
  const Logic = window.PlaytestLogic;
  const Bench = window.Bench;
  const Shell = window.Shell;
  const hooks = Bench.playtest_hooks;

  const tile_size = Logic.tile_size_in_pixels;
  const screen_width_in_pixels = Logic.viewport_width_in_tiles * tile_size;
  const screen_height_in_pixels = Logic.viewport_height_in_tiles * tile_size;
  const smallest_display_scale = 1;
  const largest_display_scale = 8;
  const toast_duration_in_milliseconds = 1800;
  const text_reveal_milliseconds_per_character = 18;
  const route_load_timeout_in_milliseconds = 30000;
  const fade_duration_in_milliseconds = 220;
  const surf_bob_period_in_milliseconds = 1100;
  const text_box_lines_per_page = 2;
  const text_box_font = '600 11px "Pixelify Sans", ui-monospace, monospace';
  const toast_font = '600 9px "Pixelify Sans", ui-monospace, monospace';
  const sheet_poses = ["walking", "running", "surfing", "surf_blob", "tall_grass", "jump_shadow"];
  const field_move_buttons = [["surf", "Surf"], ["cut", "Cut"], ["strength", "Strength"], ["rock_smash", "Rock Smash"], ["waterfall", "Waterfall"]];
  const direction_by_key_code = {
    ArrowUp: "up", KeyW: "up", ArrowDown: "down", KeyS: "down", ArrowLeft: "left", KeyA: "left", ArrowRight: "right", KeyD: "right",
  };
  const scene_pointer_event_names = ["pointerdown", "pointermove", "pointerup", "pointercancel", "pointerleave", "dblclick", "wheel", "contextmenu"];
  const a_button_key_codes = ["KeyZ", "Space", "Enter"];
  const b_button_key_codes = ["KeyX", "ShiftLeft", "ShiftRight"];

  const session = {
    is_open: false, overlay: null, canvas: null, context: null, display_scale: 3,
    route_id: "", play_world: null, encounter_tables: { land: null, water: null },
    player: { x: 0, y: 0, facing: "down", is_surfing: false }, current_move: null, step_parity: 0,
    held_directions: [], is_b_held: false, turn_lock_until: 0,
    text_box: null, toast: null, grass_rustles: new Map(),
    pending_arrival: null, is_loading_route: false, loading_started_at: 0, fade_started_at: 0,
    pending_seam_crossing: null, tile_canvas: null, neighbouring_maps: [], map_offset: { x: 0, y: 0 },
    sheets_by_tiler: {}, particle_set: { effect: "none", particles: [] }, last_frame_time: 0, animation_frame_request: 0,
    field_moves: { surf: false, cut: false, strength: false, rock_smash: false, waterfall: false },
  };

  function element(tag_name, class_name, text_content) {
    const created_element = document.createElement(tag_name);
    if (class_name) created_element.className = class_name;
    if (text_content != null) created_element.textContent = text_content;
    return created_element;
  }

  function load_image(image_url) {
    const image = new Image();
    const image_load = new Promise((resolve) => {
      image.onload = () => resolve(image);
      image.onerror = () => resolve(null);
    });
    image.src = image_url;
    return image_load;
  }

  async function load_sheets_for_tiler(tiler) {
    if (session.sheets_by_tiler[tiler]) return session.sheets_by_tiler[tiler];
    const sheet_images = await Promise.all(sheet_poses.map((pose) => load_image(`/api/playtest/sheet.png?pose=${pose}&tiler=${tiler}`)));
    session.sheets_by_tiler[tiler] = Object.fromEntries(sheet_poses.map((pose, pose_index) => [pose, sheet_images[pose_index]]));
    return session.sheets_by_tiler[tiler];
  }

  function current_sheets() {
    const route = Bench.state.result;
    const sheets = (route && session.sheets_by_tiler[route.tiler]) || {};
    return sheets;
  }

  async function fetch_playtest_route_data(route_id) {
    try {
      return await hooks.fetchJSON(`/api/playtest/route?id=${encodeURIComponent(route_id)}`);
    } catch (error) {
      Shell.log("warn", `Play-test: no collision grid or sign text for this route (${error.message}); using the class grid only.`);
      return { collision: null, texts: [] };
    }
  }

  function encounter_tables_of_route(route) {
    const emerald_encounters = route.emerald_encounters || {};
    const encounter_tables = { land: emerald_encounters.land_mons || null, water: emerald_encounters.water_mons || null };
    return encounter_tables;
  }

  function play_world_of_route(route, playtest_route_data) {
    const final_stage = route.stages[route.stages.length - 1];
    const play_world = Logic.build_play_world({
      width: route.width, height: route.height, class_rows: final_stage.cls, collision_rows: playtest_route_data.collision,
      events: hooks.final_route_events(), texts: playtest_route_data.texts, exits: route.exits,
    });
    return play_world;
  }

  function placement_for_new_route(play_world) {
    const arrival = session.pending_arrival;
    if (!arrival) return Logic.starting_placement(play_world);
    const arrival_index = Logic.arrival_exit_index(play_world.exits, hooks.scene_connections(), arrival.from_route_name, arrival.left_by_side);
    const placement = Logic.arrival_placement(play_world, arrival_index);
    return placement;
  }

  async function load_current_route() {
    const route = Bench.state.result;
    const seam_crossing = session.pending_seam_crossing;
    session.route_id = route.id;
    if (!seam_crossing) session.is_loading_route = true;
    hooks.show_final_stage();
    const [playtest_route_data] = await Promise.all([fetch_playtest_route_data(route.id), load_sheets_for_tiler(route.tiler)]);
    if (Bench.state.result !== route) return;
    const play_world = play_world_of_route(route, playtest_route_data);
    const seam_placement = seam_crossing ? placement_across_seam(play_world, seam_crossing) : null;
    take_over_map_of_route(play_world, route);
    place_player(seam_placement || placement_for_new_route(play_world));
    if (seam_placement) start_move(seam_placement.entry_move, seam_placement.facing, performance.now());
    session.pending_arrival = null;
    session.pending_seam_crossing = null;
    session.is_loading_route = false;
    if (!seam_placement) session.fade_started_at = performance.now();
  }

  function take_over_map_of_route(play_world, route) {
    session.play_world = play_world;
    session.encounter_tables = encounter_tables_of_route(route);
    session.tile_canvas = hooks.final_tile_canvas();
    session.neighbouring_maps = hooks.neighbouring_tile_canvases();
    session.map_offset = hooks.chain_map_offset_in_metatiles();
  }

  function placement_across_seam(play_world, seam_crossing) {
    const entered_cell = Logic.cell_across_chain_seam(seam_crossing.cell, seam_crossing.direction, seam_crossing.map_offset, hooks.chain_map_offset_in_metatiles());
    const placement = Logic.seam_entry_placement(play_world, entered_cell, seam_crossing.direction, seam_crossing.is_surfing, session.field_moves);
    return placement;
  }

  function place_player(placement) {
    session.player = { x: placement.cell.x, y: placement.cell.y, facing: placement.facing, is_surfing: !!placement.is_surfing };
    session.current_move = null;
    session.text_box = null;
    session.grass_rustles = new Map();
    announce_player_position();
  }

  function player_position_on_the_map() {
    if (!session.is_open || !session.play_world || !session.player) return null;
    const facing_frame = Logic.player_sheet_frame(session.player.facing, null, 0);
    const position = {
      route_name: hooks.playtest_route_name(), x: session.player.x, y: session.player.y,
      width: session.play_world.width, height: session.play_world.height,
      sheet: current_sheets().walking || null, frame_index: facing_frame.frame_index, is_mirrored: facing_frame.is_mirrored,
    };
    return position;
  }

  function announce_player_position() {
    window.dispatchEvent(new CustomEvent("pokeroutes-player-on-map", { detail: player_position_on_the_map() }));
  }

  function show_toast(toast_text) {
    session.toast = { text: toast_text, shown_at: performance.now() };
  }

  function show_text_box(text_box_text) {
    session.text_box = { pages: text_pages_for(text_box_text), page_index: 0, shown_at: performance.now() };
  }

  function wrapped_lines_of(paragraph_text, maximum_line_width_in_pixels) {
    session.context.font = text_box_font;
    const words = paragraph_text.split(/\s+/).filter(Boolean);
    const wrapped_lines = [];
    let line_so_far = "";
    for (const word of words) {
      const candidate_line = line_so_far ? `${line_so_far} ${word}` : word;
      if (line_so_far && session.context.measureText(candidate_line).width > maximum_line_width_in_pixels) {
        wrapped_lines.push(line_so_far);
        line_so_far = word;
        continue;
      }
      line_so_far = candidate_line;
    }
    if (line_so_far) wrapped_lines.push(line_so_far);
    return wrapped_lines;
  }

  function text_pages_for(text_box_text) {
    const wrapped_lines = wrapped_lines_of(text_box_text, screen_width_in_pixels - 28);
    const pages = [];
    for (let line_index = 0; line_index < wrapped_lines.length; line_index += text_box_lines_per_page) pages.push(wrapped_lines.slice(line_index, line_index + text_box_lines_per_page));
    return pages.length ? pages : [[""]];
  }

  function characters_on_page(page_lines) {
    const character_count = page_lines.join("").length;
    return character_count;
  }

  function revealed_character_count(now) {
    const text_box = session.text_box;
    const revealed_count = Math.floor((now - text_box.shown_at) / text_reveal_milliseconds_per_character);
    return revealed_count;
  }

  function advance_text_box(now) {
    const text_box = session.text_box;
    const page_lines = text_box.pages[text_box.page_index];
    if (revealed_character_count(now) < characters_on_page(page_lines)) {
      text_box.shown_at = now - characters_on_page(page_lines) * text_reveal_milliseconds_per_character;
      return;
    }
    if (text_box.page_index + 1 < text_box.pages.length) {
      text_box.page_index += 1;
      text_box.shown_at = now;
      return;
    }
    session.text_box = null;
  }

  function press_a_button(now) {
    if (session.text_box) { advance_text_box(now); return; }
    if (session.current_move || session.is_loading_route) return;
    const interaction = Logic.resolve_interaction(session.play_world, session.player, session.player.facing, session.field_moves);
    session.play_world = Logic.world_after_interaction(session.play_world, interaction);
    present_interaction(interaction);
  }

  function present_interaction(interaction) {
    switch (interaction.kind) {
      case "pick_up":
      case "find_hidden":
      case "cut":
      case "smash":
        show_toast(interaction.text);
        break;
      case "text":
        show_text_box(interaction.text);
        break;
      default:
        break;
    }
  }

  function press_b_button(now) {
    if (session.text_box) advance_text_box(now);
  }

  function held_direction() {
    const latest_direction = session.held_directions[session.held_directions.length - 1] || null;
    return latest_direction;
  }

  function press_direction(direction, now) {
    if (!session.held_directions.includes(direction)) session.held_directions.push(direction);
    if (session.current_move || session.text_box || session.player.facing === direction) return;
    session.player.facing = direction;
    session.turn_lock_until = now + Logic.turn_in_place_duration_in_milliseconds;
    announce_player_position();
  }

  function release_direction(direction) {
    session.held_directions = session.held_directions.filter((held) => held !== direction);
  }

  function start_move(resolved_move, direction, now) {
    const from_cell = { x: session.player.x, y: session.player.y };
    const is_running = session.is_b_held && !session.player.is_surfing && resolved_move.kind === "walk";
    session.current_move = {
      kind: resolved_move.kind, from: from_cell, to: resolved_move.to || from_cell, started_at: now,
      duration: Logic.step_duration_for_move(resolved_move.kind, is_running), is_running,
      boulder_from: resolved_move.boulder_from || null, boulder_to: resolved_move.boulder_to || null,
    };
    session.player.facing = direction;
    if (resolved_move.to && Logic.is_encounter_cell(session.play_world, resolved_move.to, false)) session.grass_rustles.set(Logic.cell_key(resolved_move.to.x, resolved_move.to.y), now);
  }

  function bump_in_place(direction, now) {
    start_move({ kind: "bump" }, direction, now);
  }

  function cross_chain_seam(exit_index, direction) {
    session.pending_seam_crossing = {
      cell: { x: session.player.x, y: session.player.y }, direction, is_surfing: session.player.is_surfing, map_offset: session.map_offset,
    };
    session.held_directions = session.held_directions.filter((held) => held === direction);
    hooks.follow_connection(exit_index);
  }

  function leave_through_exit(exit_index, direction, now) {
    if (hooks.leads_to_another_map_of_this_route(exit_index)) { cross_chain_seam(exit_index, direction); return; }
    const connection = hooks.scene_connections()[exit_index];
    if (!connection || connection.kind === "unknown") { bump_in_place(direction, now); show_toast(connection ? "Open the Map to follow this exit" : "Nothing connected here"); return; }
    if (connection.kind === "town") { bump_in_place(direction, now); show_toast(`${connection.name}: towns can't be played yet`); return; }
    session.pending_arrival = { from_route_name: hooks.playtest_route_name(), left_by_side: session.play_world.exits[exit_index].side };
    session.is_loading_route = true;
    session.loading_started_at = now;
    session.loading_route_name = connection.name;
    hooks.follow_connection(exit_index);
  }

  function try_to_step(now) {
    const direction = held_direction();
    if (!direction || session.text_box || session.is_loading_route || session.pending_seam_crossing || now < session.turn_lock_until) return;
    const resolved_move = Logic.resolve_step(session.play_world, session.player, direction, session.field_moves);
    switch (resolved_move.kind) {
      case "leave":
        leave_through_exit(resolved_move.exit_index, direction, now);
        break;
      case "blocked":
        bump_in_place(direction, now);
        break;
      default:
        start_move(resolved_move, direction, now);
    }
  }

  function finish_move(now) {
    const finished_move = session.current_move;
    session.current_move = null;
    session.step_parity += 1;
    session.player.x = finished_move.to.x;
    session.player.y = finished_move.to.y;
    if (finished_move.kind === "mount_surf") session.player.is_surfing = true;
    if (finished_move.kind === "dismount") session.player.is_surfing = false;
    if (finished_move.kind === "push_boulder") session.play_world = Logic.world_with_boulder_pushed(session.play_world, finished_move.boulder_from, finished_move.boulder_to);
    if (finished_move.kind !== "bump" && finished_move.kind !== "push_boulder") roll_for_wild_encounter();
    forget_grass_rustles_away_from_player(now);
    announce_player_position();
  }

  function forget_grass_rustles_away_from_player(now) {
    const player_key = Logic.cell_key(session.player.x, session.player.y);
    for (const [rustle_key, rustle_started_at] of session.grass_rustles) {
      if (rustle_key !== player_key && now - rustle_started_at > 1000) session.grass_rustles.delete(rustle_key);
    }
  }

  function roll_for_wild_encounter() {
    if (!Logic.is_encounter_cell(session.play_world, session.player, session.player.is_surfing)) return;
    const encounter_table = session.player.is_surfing ? session.encounter_tables.water : session.encounter_tables.land;
    const wild_encounter = Logic.roll_wild_encounter(encounter_table, session.player.is_surfing, Math.random);
    if (!wild_encounter) return;
    session.held_directions = [];
    show_text_box(Logic.wild_encounter_text(wild_encounter));
  }

  function advance_movement(now) {
    if (session.current_move && now - session.current_move.started_at >= session.current_move.duration) finish_move(now);
    if (!session.current_move) try_to_step(now);
  }

  function step_progress(now) {
    if (!session.current_move) return null;
    const progress = Math.min(1, (now - session.current_move.started_at) / session.current_move.duration);
    return progress;
  }

  function player_pixel_position(now) {
    const progress = step_progress(now);
    const current_move = session.current_move;
    if (progress === null) return { x: session.player.x * tile_size, y: session.player.y * tile_size };
    const pixel_position = {
      x: Math.round((current_move.from.x + (current_move.to.x - current_move.from.x) * progress) * tile_size),
      y: Math.round((current_move.from.y + (current_move.to.y - current_move.from.y) * progress) * tile_size),
    };
    return pixel_position;
  }

  function check_for_new_route(now) {
    const route = Bench.state.result;
    if (!route || !hooks.final_tile_canvas()) return;
    if (route.id !== session.route_id) { load_current_route(); return; }
    if (session.is_loading_route && session.pending_arrival && now - session.loading_started_at > route_load_timeout_in_milliseconds) {
      session.is_loading_route = false;
      session.pending_arrival = null;
      show_toast("The next route didn't load");
    }
  }

  function tick(now) {
    session.animation_frame_request = 0;
    if (!session.is_open) return;
    if (Shell.layout.center !== "scene") { close_playtest(); return; }
    const elapsed_in_seconds = Math.min(0.1, (now - (session.last_frame_time || now)) / 1000);
    session.last_frame_time = now;
    check_for_new_route(now);
    if (session.play_world && !session.is_loading_route) advance_movement(now);
    draw_frame(now, elapsed_in_seconds);
    session.animation_frame_request = requestAnimationFrame(tick);
  }

  function draw_frame(now, elapsed_in_seconds) {
    const context = session.context;
    const device_pixel_ratio = window.devicePixelRatio || 1;
    context.setTransform(session.display_scale * device_pixel_ratio, 0, 0, session.display_scale * device_pixel_ratio, 0, 0);
    context.imageSmoothingEnabled = false;
    context.fillStyle = "#000";
    context.fillRect(0, 0, screen_width_in_pixels, screen_height_in_pixels);
    if (session.play_world && !session.is_loading_route) draw_world(context, now, elapsed_in_seconds);
    draw_fade(context, now);
    draw_toast(context, now);
    if (session.text_box) draw_text_box(context, now);
  }

  function draw_world(context, now, elapsed_in_seconds) {
    const camera = Logic.camera_top_left_in_pixels(player_pixel_position(now));
    draw_map_tiles(context, camera, session.tile_canvas, true);
    draw_people_and_objects(context, camera, now);
    draw_map_tiles(context, camera, hooks.final_top_layer_canvas(), false);
    draw_grass_rustles(context, camera, now);
    hooks.draw_route_effect_particles_into(context, session.particle_set, { left: 0, top: 0, width: screen_width_in_pixels, height: screen_height_in_pixels }, 1, elapsed_in_seconds);
  }

  function draw_map_tiles(context, camera, tile_canvas, with_neighbouring_maps) {
    if (!tile_canvas) return;
    const play_world = session.play_world;
    const first_cell_x = Math.floor(camera.x / tile_size), first_cell_y = Math.floor(camera.y / tile_size);
    for (let cell_y = first_cell_y; cell_y <= first_cell_y + Logic.viewport_height_in_tiles; cell_y++) {
      for (let cell_x = first_cell_x; cell_x <= first_cell_x + Logic.viewport_width_in_tiles; cell_x++) {
        const source_x = Math.max(0, Math.min(play_world.width - 1, cell_x)), source_y = Math.max(0, Math.min(play_world.height - 1, cell_y));
        context.drawImage(tile_canvas, source_x * tile_size, source_y * tile_size, tile_size, tile_size, cell_x * tile_size - camera.x, cell_y * tile_size - camera.y, tile_size, tile_size);
      }
    }
    if (with_neighbouring_maps) for (const neighbouring_map of session.neighbouring_maps) draw_neighbouring_map(context, camera, neighbouring_map);
  }

  function draw_neighbouring_map(context, camera, neighbouring_map) {
    if (!neighbouring_map.canvas) return;
    context.drawImage(neighbouring_map.canvas, neighbouring_map.offset_x_in_metatiles * tile_size - camera.x, neighbouring_map.offset_y_in_metatiles * tile_size - camera.y);
  }

  function boulder_draw_position(play_object, now) {
    const current_move = session.current_move;
    const is_this_boulder_moving = current_move && current_move.kind === "push_boulder" && current_move.boulder_from.x === play_object.x && current_move.boulder_from.y === play_object.y;
    if (!is_this_boulder_moving) return { x: play_object.x, y: play_object.y };
    const progress = step_progress(now);
    const sliding_position = {
      x: current_move.boulder_from.x + (current_move.boulder_to.x - current_move.boulder_from.x) * progress,
      y: current_move.boulder_from.y + (current_move.boulder_to.y - current_move.boulder_from.y) * progress,
    };
    return sliding_position;
  }

  function drawable_of_object(play_object, now) {
    const draw_position = boulder_draw_position(play_object, now);
    const drawable = { sort_y: draw_position.y, draw: (context, camera) => draw_play_object(context, camera, play_object, draw_position) };
    return drawable;
  }

  function draw_people_and_objects(context, camera, now) {
    const object_drawables = [...session.play_world.objects_by_cell.values()].map((play_object) => drawable_of_object(play_object, now));
    const player_drawable = { sort_y: player_pixel_position(now).y / tile_size + 0.01, draw: (drawing_context, drawing_camera) => draw_player(drawing_context, drawing_camera, now) };
    const drawables_in_depth_order = [...object_drawables, player_drawable].sort((first, second) => first.sort_y - second.sort_y);
    for (const drawable of drawables_in_depth_order) drawable.draw(context, camera);
  }

  function draw_play_object(context, camera, play_object, draw_position) {
    const drawn_event = Object.assign({}, play_object.source_event, { x: draw_position.x, y: draw_position.y });
    if (hooks.draw_event_sprite(context, drawn_event, -camera.x, -camera.y, tile_size)) return;
    if (play_object.kind === "sign") { draw_sign_post(context, draw_position.x * tile_size - camera.x, draw_position.y * tile_size - camera.y); return; }
    draw_plain_marker(context, draw_position.x * tile_size - camera.x, draw_position.y * tile_size - camera.y);
  }

  function draw_sign_post(context, left, top) {
    context.fillStyle = "#5a3a1a";
    context.fillRect(left + 7, top + 8, 2, 7);
    context.fillRect(left + 2, top + 2, 12, 8);
    context.fillStyle = "#c89858";
    context.fillRect(left + 3, top + 3, 10, 6);
    context.fillStyle = "#8a6030";
    context.fillRect(left + 4, top + 5, 8, 1);
  }

  function draw_plain_marker(context, left, top) {
    context.fillStyle = "#1b1a17";
    context.fillRect(left + 4, top + 4, 8, 8);
    context.fillStyle = "#b9b2a3";
    context.fillRect(left + 5, top + 5, 6, 6);
  }

  function walking_sheet_for_move() {
    const sheets = current_sheets();
    const is_running = session.current_move && session.current_move.is_running;
    const walking_sheet = (is_running && sheets.running) || sheets.walking || null;
    return walking_sheet;
  }

  function draw_sheet_frame(context, sheet, frame_index, frame_width, left, top, is_mirrored) {
    const frame_height = sheet.height;
    if (!is_mirrored) { context.drawImage(sheet, frame_index * frame_width, 0, frame_width, frame_height, left, top, frame_width, frame_height); return; }
    context.save();
    context.translate(left + frame_width, top);
    context.scale(-1, 1);
    context.drawImage(sheet, frame_index * frame_width, 0, frame_width, frame_height, 0, 0, frame_width, frame_height);
    context.restore();
  }

  function draw_player(context, camera, now) {
    const pixel_position = player_pixel_position(now);
    const left = pixel_position.x - camera.x, top = pixel_position.y - camera.y;
    const move_kind = session.current_move ? session.current_move.kind : "";
    const hop_height = session.current_move ? Logic.hop_height_for_move(move_kind, step_progress(now)) : 0;
    if (hop_height > 0) draw_jump_shadow(context, left, top);
    const is_riding_surf_blob = session.player.is_surfing ? move_kind !== "mount_surf" : move_kind === "dismount";
    if (is_riding_surf_blob || move_kind === "mount_surf") draw_surf_blob(context, left, top, now, move_kind);
    if (is_riding_surf_blob && current_sheets().surfing) { draw_surfing_rider(context, left, top - hop_height, now); return; }
    draw_walking_player(context, left, top - hop_height, now);
  }

  function draw_jump_shadow(context, left, top) {
    const shadow_sheet = current_sheets().jump_shadow;
    if (!shadow_sheet) return;
    context.drawImage(shadow_sheet, left + (tile_size - shadow_sheet.width) / 2, top + tile_size - shadow_sheet.height - 1);
  }

  function surf_bob_offset(now) {
    const bob_offset = Math.sin((now % surf_bob_period_in_milliseconds) / surf_bob_period_in_milliseconds * Math.PI * 2) > 0 ? 0 : 1;
    return bob_offset;
  }

  function draw_surf_blob(context, left, top, now, move_kind) {
    const blob_sheet = current_sheets().surf_blob;
    if (!blob_sheet) return;
    const blob_cell = move_kind === "mount_surf" ? session.current_move.to : move_kind === "dismount" ? session.current_move.from : null;
    const camera_offset = blob_cell ? { left: left + (blob_cell.x * tile_size - player_pixel_position(now).x), top: top + (blob_cell.y * tile_size - player_pixel_position(now).y) } : { left, top };
    const frames = Logic.surfing_sheet_frames(session.player.facing);
    draw_sheet_frame(context, blob_sheet, frames.blob_frame_index, 32, camera_offset.left - 8, camera_offset.top - 12 + surf_bob_offset(now), frames.is_mirrored);
  }

  function draw_surfing_rider(context, left, top, now) {
    const frames = Logic.surfing_sheet_frames(session.player.facing);
    draw_sheet_frame(context, current_sheets().surfing, frames.rider_frame_index, 32, left - 8, top - 18 + surf_bob_offset(now), frames.is_mirrored);
  }

  function draw_walking_player(context, left, top, now) {
    const walking_sheet = walking_sheet_for_move();
    if (!walking_sheet) { draw_fallback_player_marker(context, left, top); return; }
    const progress = session.current_move && session.current_move.kind !== "jump" ? step_progress(now) : null;
    const sheet_frame = Logic.player_sheet_frame(session.player.facing, progress, session.step_parity);
    draw_sheet_frame(context, walking_sheet, sheet_frame.frame_index, 16, left, top - 16, sheet_frame.is_mirrored);
  }

  function draw_fallback_player_marker(context, left, top) {
    context.fillStyle = "#1b1a17";
    context.fillRect(left + 3, top - 4, 10, 18);
    context.fillStyle = "#e4574a";
    context.fillRect(left + 4, top - 3, 8, 8);
    context.fillStyle = "#f4f4f4";
    context.fillRect(left + 4, top + 5, 8, 8);
  }

  function draw_grass_rustles(context, camera, now) {
    const grass_sheet = current_sheets().tall_grass;
    if (!grass_sheet) return;
    const player_cell_key = Logic.cell_key(session.player.x, session.player.y);
    const is_mid_hop = session.current_move && Logic.hop_height_for_move(session.current_move.kind, step_progress(now)) > 0;
    for (const [rustle_key, rustle_started_at] of session.grass_rustles) {
      const frame_index = Logic.tall_grass_frame_for_age(now - rustle_started_at);
      const is_cover_frame_away_from_player = frame_index === 0 && (rustle_key !== player_cell_key || is_mid_hop || !!session.current_move);
      if (is_cover_frame_away_from_player) continue;
      const [cell_x, cell_y] = rustle_key.split(",").map(Number);
      context.drawImage(grass_sheet, frame_index * 16, 0, 16, 16, cell_x * tile_size - camera.x, cell_y * tile_size - camera.y, 16, 16);
    }
  }

  function draw_fade(context, now) {
    const fade_progress = session.is_loading_route ? 1 : Math.max(0, 1 - (now - session.fade_started_at) / fade_duration_in_milliseconds);
    if (fade_progress <= 0) return;
    context.fillStyle = `rgba(0,0,0,${fade_progress})`;
    context.fillRect(0, 0, screen_width_in_pixels, screen_height_in_pixels);
    if (!session.is_loading_route || !session.loading_route_name) return;
    context.font = toast_font;
    context.fillStyle = "#f4f4f4";
    context.textAlign = "center";
    context.fillText(session.loading_route_name, screen_width_in_pixels / 2, screen_height_in_pixels / 2);
    context.textAlign = "left";
  }

  function draw_toast(context, now) {
    if (!session.toast) return;
    if (now - session.toast.shown_at > toast_duration_in_milliseconds) { session.toast = null; return; }
    context.font = toast_font;
    const toast_width = Math.ceil(context.measureText(session.toast.text).width) + 10;
    context.fillStyle = "#283040";
    context.fillRect(4, 4, toast_width + 2, 16);
    context.fillStyle = "#f8f8f0";
    context.fillRect(5, 5, toast_width, 14);
    context.fillStyle = "#383838";
    context.textBaseline = "middle";
    context.fillText(text_without_ligatures(session.toast.text), 10, 12.5);
    context.textBaseline = "alphabetic";
  }

  function draw_text_box_frame(context, box_top) {
    const box_left = 2, box_width = screen_width_in_pixels - 4, box_height = screen_height_in_pixels - box_top - 2;
    context.fillStyle = "#283848";
    context.fillRect(box_left, box_top, box_width, box_height);
    context.fillStyle = "#98c0e8";
    context.fillRect(box_left + 1, box_top + 1, box_width - 2, box_height - 2);
    context.fillStyle = "#f8f8f8";
    context.fillRect(box_left + 3, box_top + 3, box_width - 6, box_height - 6);
  }

  function draw_text_box(context, now) {
    const text_box = session.text_box;
    const page_lines = text_box.pages[text_box.page_index];
    const box_top = screen_height_in_pixels - 46;
    draw_text_box_frame(context, box_top);
    context.font = text_box_font;
    let characters_left_to_show = revealed_character_count(now);
    page_lines.forEach((page_line, line_index) => {
      const shown_part = page_line.slice(0, Math.max(0, characters_left_to_show));
      characters_left_to_show -= page_line.length;
      draw_shadowed_text(context, shown_part, 12, box_top + 18 + line_index * 16);
    });
    if (characters_left_to_show >= 0 && text_box.page_index + 1 < text_box.pages.length) draw_more_arrow(context, box_top, now);
  }

  function text_without_ligatures(text) {
    const separated_text = text.replace(/f(?=[fil])/g, "f\u200c");
    return separated_text;
  }

  function draw_shadowed_text(context, text, left, baseline) {
    const drawn_text = text_without_ligatures(text);
    context.fillStyle = "#d0d0c8";
    context.fillText(drawn_text, left + 1, baseline + 1);
    context.fillStyle = "#484848";
    context.fillText(drawn_text, left, baseline);
  }

  function draw_more_arrow(context, box_top, now) {
    const arrow_bounce = Math.floor(now / 300) % 2;
    const arrow_left = screen_width_in_pixels - 18, arrow_top = box_top + 32 + arrow_bounce;
    context.fillStyle = "#e05040";
    context.beginPath();
    context.moveTo(arrow_left, arrow_top);
    context.lineTo(arrow_left + 6, arrow_top);
    context.lineTo(arrow_left + 3, arrow_top + 4);
    context.closePath();
    context.fill();
  }

  function largest_scale_fitting(available_width, available_height) {
    const fitting_scale = Math.floor(Math.min(available_width / screen_width_in_pixels, available_height / screen_height_in_pixels));
    return Math.max(smallest_display_scale, Math.min(largest_display_scale, fitting_scale));
  }

  function apply_display_scale(display_scale) {
    session.display_scale = Math.max(smallest_display_scale, Math.min(largest_display_scale, display_scale));
    const device_pixel_ratio = window.devicePixelRatio || 1;
    session.canvas.width = screen_width_in_pixels * session.display_scale * device_pixel_ratio;
    session.canvas.height = screen_height_in_pixels * session.display_scale * device_pixel_ratio;
    session.canvas.style.width = `${screen_width_in_pixels * session.display_scale}px`;
    session.canvas.style.height = `${screen_height_in_pixels * session.display_scale}px`;
    session.overlay.querySelector(".pt-scale").textContent = `${session.display_scale}×`;
  }

  function fit_display_scale() {
    const overlay_box = session.overlay.getBoundingClientRect();
    apply_display_scale(largest_scale_fitting(overlay_box.width - 32, overlay_box.height - 72));
  }

  function toggle_field_move(field_move_name, toggle_button) {
    session.field_moves[field_move_name] = !session.field_moves[field_move_name];
    toggle_button.setAttribute("aria-pressed", String(session.field_moves[field_move_name]));
    toggle_button.blur();
  }

  function field_move_toggle(field_move_name, label) {
    const toggle_button = element("button", "pt-move", label);
    toggle_button.type = "button";
    toggle_button.setAttribute("aria-pressed", String(session.field_moves[field_move_name]));
    toggle_button.onclick = () => toggle_field_move(field_move_name, toggle_button);
    return toggle_button;
  }

  function bar_button(class_name, label, title, on_click) {
    const button = element("button", class_name, label);
    button.type = "button";
    button.title = title;
    button.setAttribute("aria-label", title);
    button.onclick = () => { on_click(); button.blur(); };
    return button;
  }

  function build_overlay() {
    const overlay = element("div", "pt-overlay");
    overlay.setAttribute("role", "application");
    overlay.setAttribute("aria-label", "Play-test");
    const canvas = element("canvas", "pt-screen");
    const field_moves_group = element("div", "pt-moves");
    field_move_buttons.forEach(([field_move_name, label]) => field_moves_group.append(field_move_toggle(field_move_name, label)));
    const view_group = element("div", "pt-view");
    view_group.append(
      bar_button("pt-icon", "−", "Smaller", () => apply_display_scale(session.display_scale - 1)),
      element("span", "pt-scale"),
      bar_button("pt-icon", "+", "Bigger", () => apply_display_scale(session.display_scale + 1)),
      bar_button("pt-icon pt-close", "×", "Close play-test (Esc)", close_playtest),
    );
    const bar = element("div", "pt-bar");
    bar.append(field_moves_group, view_group);
    overlay.append(canvas, bar);
    scene_pointer_event_names.forEach((event_name) => overlay.addEventListener(event_name, keep_event_inside_overlay));
    return overlay;
  }

  function keep_event_inside_overlay(pointer_event) {
    pointer_event.stopPropagation();
  }

  function is_plain_key(keyboard_event) {
    const is_plain = !keyboard_event.metaKey && !keyboard_event.ctrlKey && !keyboard_event.altKey;
    return is_plain;
  }

  function handle_key_down(keyboard_event) {
    if (!session.is_open || !is_plain_key(keyboard_event)) return;
    const now = performance.now();
    const direction = direction_by_key_code[keyboard_event.code];
    keyboard_event.preventDefault();
    keyboard_event.stopPropagation();
    if (direction) { press_direction(direction, now); return; }
    if (keyboard_event.code === "Escape") { if (session.text_box) session.text_box = null; else close_playtest(); return; }
    if (keyboard_event.repeat) return;
    if (a_button_key_codes.includes(keyboard_event.code)) { press_a_button(now); return; }
    if (b_button_key_codes.includes(keyboard_event.code)) { session.is_b_held = true; press_b_button(now); return; }
    if (keyboard_event.code === "Minus") apply_display_scale(session.display_scale - 1);
    if (keyboard_event.code === "Equal") apply_display_scale(session.display_scale + 1);
  }

  function handle_key_up(keyboard_event) {
    if (!session.is_open) return;
    const direction = direction_by_key_code[keyboard_event.code];
    if (direction) release_direction(direction);
    if (b_button_key_codes.includes(keyboard_event.code)) session.is_b_held = false;
  }

  function release_all_keys() {
    session.held_directions = [];
    session.is_b_held = false;
  }

  function open_playtest() {
    if (session.is_open || !Bench.state.result) return;
    session.overlay = build_overlay();
    document.getElementById("viewScene").append(session.overlay);
    session.canvas = session.overlay.querySelector("canvas");
    session.context = session.canvas.getContext("2d");
    session.is_open = true;
    session.route_id = "";
    session.pending_arrival = null;
    session.pending_seam_crossing = null;
    session.is_loading_route = false;
    session.last_frame_time = 0;
    fit_display_scale();
    session.overlay.tabIndex = -1;
    session.overlay.focus();
    session.animation_frame_request = requestAnimationFrame(tick);
  }

  function close_playtest() {
    if (!session.is_open) return;
    session.is_open = false;
    if (session.animation_frame_request) cancelAnimationFrame(session.animation_frame_request);
    session.animation_frame_request = 0;
    session.overlay.remove();
    session.overlay = null;
    session.play_world = null;
    release_all_keys();
    announce_player_position();
  }

  window.addEventListener("keydown", handle_key_down, true);
  window.addEventListener("keyup", handle_key_up, true);
  window.addEventListener("blur", release_all_keys);
  window.Playtest = { open: open_playtest, close: close_playtest, session };
})();
