(function (root) {
  "use strict";

  const viewport_width_in_tiles = 15;
  const viewport_height_in_tiles = 10;
  const tile_size_in_pixels = 16;
  const player_cell_left_on_screen_in_pixels = 112;
  const player_cell_top_on_screen_in_pixels = 72;
  const frames_per_second_on_gba = 60;
  const walk_step_duration_in_milliseconds = 16 * 1000 / frames_per_second_on_gba;
  const run_step_duration_in_milliseconds = 8 * 1000 / frames_per_second_on_gba;
  const surf_step_duration_in_milliseconds = 8 * 1000 / frames_per_second_on_gba;
  const ledge_jump_duration_in_milliseconds = 32 * 1000 / frames_per_second_on_gba;
  const surf_hop_duration_in_milliseconds = 16 * 1000 / frames_per_second_on_gba;
  const turn_in_place_duration_in_milliseconds = 8 * 1000 / frames_per_second_on_gba;
  const ledge_jump_peak_height_in_pixels = 10;
  const surf_hop_peak_height_in_pixels = 6;
  const emerald_encounter_chance_divisor = 2880;
  const emerald_encounter_rate_multiplier = 16;
  const land_encounter_slot_rates = [20, 20, 10, 10, 10, 10, 5, 5, 4, 4, 1, 1];
  const water_encounter_slot_rates = [60, 30, 5, 4, 1];

  const walkable_class_codes = "gptdhyvzqn";
  const surfable_class_codes = "wb";
  const waterfall_class_code = "a";
  const encounter_grass_class_codes = "ty";
  const ledge_direction_by_class_code = { s: "down", e: "right", x: "left", j: "up" };
  const direction_vectors = { up: { x: 0, y: -1 }, down: { x: 0, y: 1 }, left: { x: -1, y: 0 }, right: { x: 1, y: 0 } };
  const direction_of_exit_side = { north: "up", south: "down", west: "left", east: "right" };
  const blocking_object_kinds = ["sign", "npc", "trainer", "story_blocker", "item_ball", "berry_tree", "cut_tree", "rock_smash", "strength_boulder"];
  const no_field_moves = { surf: false, cut: false, strength: false, rock_smash: false, waterfall: false };

  const standing_frame_by_direction = { down: 0, up: 1, left: 2, right: 2 };
  const walking_frames_by_direction = { down: [3, 4], up: [5, 6], left: [7, 8], right: [7, 8] };
  const surfing_frame_by_direction = { down: 0, up: 2, left: 4, right: 4 };
  const surf_blob_frame_by_direction = { down: 0, up: 1, left: 2, right: 2 };
  const tall_grass_rustle_frames = [1, 2, 3, 4];
  const tall_grass_rustle_frame_duration_in_milliseconds = 4 * 1000 / frames_per_second_on_gba * 2;
  const tall_grass_cover_frame = 0;

  function cell_key(cell_x, cell_y) {
    const key_of_cell = `${cell_x},${cell_y}`;
    return key_of_cell;
  }

  function cell_in_direction(cell, direction, distance_in_cells) {
    const direction_vector = direction_vectors[direction];
    const neighbouring_cell = { x: cell.x + direction_vector.x * distance_in_cells, y: cell.y + direction_vector.y * distance_in_cells };
    return neighbouring_cell;
  }

  function is_blocking_object_kind(event_kind) {
    const is_blocking = blocking_object_kinds.includes(event_kind);
    return is_blocking;
  }

  function text_for_event_at(texts, route_event) {
    const matching_text = (texts || []).find((text_entry) => text_entry.x === route_event.x && text_entry.y === route_event.y);
    return matching_text ? matching_text.text : "";
  }

  function object_from_route_event(route_event, texts) {
    const play_object = {
      kind: route_event.kind, x: Number(route_event.x), y: Number(route_event.y),
      item: route_event.item || "", role: route_event.role || "", text: text_for_event_at(texts, route_event),
      source_event: route_event,
    };
    return play_object;
  }

  function objects_by_cell_from_events(route_events, texts) {
    const blocking_events = route_events.filter((route_event) => is_blocking_object_kind(route_event.kind));
    const objects_by_cell = new Map(blocking_events.map((route_event) => [cell_key(Number(route_event.x), Number(route_event.y)), object_from_route_event(route_event, texts)]));
    return objects_by_cell;
  }

  function hidden_items_by_cell_from_events(route_events) {
    const hidden_item_events = route_events.filter((route_event) => route_event.kind === "hidden_item");
    const hidden_items_by_cell = new Map(hidden_item_events.map((route_event) => [cell_key(Number(route_event.x), Number(route_event.y)), { x: Number(route_event.x), y: Number(route_event.y), item: route_event.item || "" }]));
    return hidden_items_by_cell;
  }

  function build_play_world(route_description) {
    const route_events = route_description.events || [];
    const play_world = {
      width: route_description.width,
      height: route_description.height,
      class_rows: route_description.class_rows,
      collision_rows: route_description.collision_rows || null,
      objects_by_cell: objects_by_cell_from_events(route_events, route_description.texts),
      hidden_items_by_cell: hidden_items_by_cell_from_events(route_events),
      exits: route_description.exits || [],
    };
    return play_world;
  }

  function is_inside_world(play_world, cell) {
    const is_inside = cell.x >= 0 && cell.y >= 0 && cell.x < play_world.width && cell.y < play_world.height;
    return is_inside;
  }

  function class_code_at(play_world, cell) {
    if (!is_inside_world(play_world, cell)) return "";
    const class_code = play_world.class_rows[cell.y][cell.x];
    return class_code;
  }

  function is_collision_blocked_at(play_world, cell) {
    if (!play_world.collision_rows) return false;
    const is_blocked = play_world.collision_rows[cell.y][cell.x] === "1";
    return is_blocked;
  }

  function is_walkable_ground_at(play_world, cell) {
    const class_code = class_code_at(play_world, cell);
    if (!walkable_class_codes.includes(class_code)) return false;
    const is_walkable = !is_collision_blocked_at(play_world, cell);
    return is_walkable;
  }

  function is_surfable_water_at(play_world, cell, field_moves) {
    const class_code = class_code_at(play_world, cell);
    const is_surfable = surfable_class_codes.includes(class_code) || (class_code === waterfall_class_code && field_moves.waterfall);
    return is_surfable;
  }

  function object_at(play_world, cell) {
    const play_object = play_world.objects_by_cell.get(cell_key(cell.x, cell.y)) || null;
    return play_object;
  }

  function exit_index_leaving_from(play_world, cell, direction) {
    const leaving_exit_index = play_world.exits.findIndex((scene_exit) => direction_of_exit_side[scene_exit.side] === direction && exit_contains_cell(scene_exit, cell));
    return leaving_exit_index;
  }

  function exit_contains_cell(scene_exit, cell) {
    const contains_cell = scene_exit.cells.some((exit_cell) => exit_cell[0] === cell.y && exit_cell[1] === cell.x);
    return contains_cell;
  }

  function step_off_the_map(play_world, player, direction) {
    const exit_index = exit_index_leaving_from(play_world, player, direction);
    if (exit_index < 0) return { kind: "blocked", reason: "edge" };
    const leaving_move = { kind: "leave", exit_index };
    return leaving_move;
  }

  function step_while_surfing(play_world, target_cell, field_moves) {
    if (object_at(play_world, target_cell)) return { kind: "blocked", reason: "object" };
    if (is_surfable_water_at(play_world, target_cell, field_moves)) return { kind: "swim", to: target_cell };
    if (is_walkable_ground_at(play_world, target_cell)) return { kind: "dismount", to: target_cell };
    const blocked_move = { kind: "blocked", reason: class_code_at(play_world, target_cell) === waterfall_class_code ? "waterfall" : "wall" };
    return blocked_move;
  }

  function step_onto_ledge(play_world, player, direction, ledge_direction) {
    if (ledge_direction !== direction) return { kind: "blocked", reason: "ledge" };
    const landing_cell = cell_in_direction(player, direction, 2);
    if (!is_walkable_ground_at(play_world, landing_cell) || object_at(play_world, landing_cell)) return { kind: "blocked", reason: "ledge" };
    const jump_move = { kind: "jump", to: landing_cell };
    return jump_move;
  }

  function step_against_boulder(play_world, target_cell, direction, field_moves) {
    if (!field_moves.strength) return { kind: "blocked", reason: "object" };
    const boulder_destination = cell_in_direction(target_cell, direction, 1);
    if (!is_walkable_ground_at(play_world, boulder_destination) || object_at(play_world, boulder_destination)) return { kind: "blocked", reason: "object" };
    const push_move = { kind: "push_boulder", boulder_from: target_cell, boulder_to: boulder_destination };
    return push_move;
  }

  function step_on_foot(play_world, player, direction, target_cell, field_moves) {
    const target_object = object_at(play_world, target_cell);
    if (target_object && target_object.kind === "strength_boulder") return step_against_boulder(play_world, target_cell, direction, field_moves);
    if (target_object) return { kind: "blocked", reason: "object" };
    const ledge_direction = ledge_direction_by_class_code[class_code_at(play_world, target_cell)];
    if (ledge_direction) return step_onto_ledge(play_world, player, direction, ledge_direction);
    if (is_walkable_ground_at(play_world, target_cell)) return { kind: "walk", to: target_cell };
    if (surfable_class_codes.includes(class_code_at(play_world, target_cell))) return field_moves.surf ? { kind: "mount_surf", to: target_cell } : { kind: "blocked", reason: "water" };
    const blocked_move = { kind: "blocked", reason: class_code_at(play_world, target_cell) === waterfall_class_code ? "waterfall" : "wall" };
    return blocked_move;
  }

  function resolve_step(play_world, player, direction, field_moves) {
    const usable_field_moves = Object.assign({}, no_field_moves, field_moves);
    const target_cell = cell_in_direction(player, direction, 1);
    if (!is_inside_world(play_world, target_cell)) return step_off_the_map(play_world, player, direction);
    if (player.is_surfing) return step_while_surfing(play_world, target_cell, usable_field_moves);
    const resolved_move = step_on_foot(play_world, player, direction, target_cell, usable_field_moves);
    return resolved_move;
  }

  function readable_item_name(item_constant) {
    const readable_name = String(item_constant || "ITEM").replace(/^ITEM_/, "").replace(/_/g, " ");
    return readable_name;
  }

  function readable_species_name(species_constant) {
    const readable_name = String(species_constant || "").replace(/^SPECIES_/, "").replace(/_/g, " ");
    return readable_name;
  }

  function found_item_text(item_constant) {
    const found_text = `You found one ${readable_item_name(item_constant)}!`;
    return found_text;
  }

  function interaction_with_field_move_object(play_object, object_key, field_moves) {
    switch (play_object.kind) {
      case "cut_tree":
        return field_moves.cut ? { kind: "cut", key: object_key, text: "Your POKéMON used CUT!" } : { kind: "text", text: "This tree looks like it can be CUT down!" };
      case "rock_smash":
        return field_moves.rock_smash ? { kind: "smash", key: object_key, text: "Your POKéMON used ROCK SMASH!" } : { kind: "text", text: "It's a rugged rock, but a POKéMON may be able to smash it." };
      default:
        return field_moves.strength ? { kind: "text", text: "STRENGTH made it possible to move boulders around." } : { kind: "text", text: "It's a big boulder, but a POKéMON may be able to push it aside." };
    }
  }

  function interaction_with_object(play_object, object_key, field_moves) {
    switch (play_object.kind) {
      case "item_ball":
        return { kind: "pick_up", key: object_key, text: found_item_text(play_object.item) };
      case "berry_tree":
        return { kind: "text", text: "It's a soft, loamy spot. A BERRY tree grows here." };
      case "cut_tree":
      case "rock_smash":
      case "strength_boulder":
        return interaction_with_field_move_object(play_object, object_key, field_moves);
      default:
        return { kind: "text", text: play_object.text || "..." };
    }
  }

  function hidden_item_interaction(play_world, cells_to_search) {
    const searched_keys = cells_to_search.map((cell) => cell_key(cell.x, cell.y));
    const found_key = searched_keys.find((searched_key) => play_world.hidden_items_by_cell.has(searched_key));
    if (!found_key) return null;
    const hidden_interaction = { kind: "find_hidden", key: found_key, text: found_item_text(play_world.hidden_items_by_cell.get(found_key).item) };
    return hidden_interaction;
  }

  function resolve_interaction(play_world, player, facing_direction, field_moves) {
    const usable_field_moves = Object.assign({}, no_field_moves, field_moves);
    const faced_cell = cell_in_direction(player, facing_direction, 1);
    const faced_object = object_at(play_world, faced_cell);
    if (faced_object) return interaction_with_object(faced_object, cell_key(faced_cell.x, faced_cell.y), usable_field_moves);
    const hidden_interaction = hidden_item_interaction(play_world, [faced_cell, player]);
    const resolved_interaction = hidden_interaction || { kind: "nothing" };
    return resolved_interaction;
  }

  function world_with_objects(play_world, objects_by_cell) {
    const updated_world = Object.assign({}, play_world, { objects_by_cell });
    return updated_world;
  }

  function world_without_object(play_world, object_key) {
    const remaining_objects = new Map(play_world.objects_by_cell);
    remaining_objects.delete(object_key);
    const updated_world = world_with_objects(play_world, remaining_objects);
    return updated_world;
  }

  function world_without_hidden_item(play_world, hidden_item_key) {
    const remaining_hidden_items = new Map(play_world.hidden_items_by_cell);
    remaining_hidden_items.delete(hidden_item_key);
    const updated_world = Object.assign({}, play_world, { hidden_items_by_cell: remaining_hidden_items });
    return updated_world;
  }

  function world_with_boulder_pushed(play_world, boulder_from, boulder_to) {
    const from_key = cell_key(boulder_from.x, boulder_from.y);
    const boulder = play_world.objects_by_cell.get(from_key);
    const moved_objects = new Map(play_world.objects_by_cell);
    moved_objects.delete(from_key);
    moved_objects.set(cell_key(boulder_to.x, boulder_to.y), Object.assign({}, boulder, { x: boulder_to.x, y: boulder_to.y }));
    const updated_world = world_with_objects(play_world, moved_objects);
    return updated_world;
  }

  function world_after_interaction(play_world, interaction) {
    switch (interaction.kind) {
      case "pick_up":
      case "cut":
      case "smash":
        return world_without_object(play_world, interaction.key);
      case "find_hidden":
        return world_without_hidden_item(play_world, interaction.key);
      default:
        return play_world;
    }
  }

  function is_encounter_cell(play_world, cell, is_surfing) {
    const class_code = class_code_at(play_world, cell);
    const is_encounter_ground = encounter_grass_class_codes.includes(class_code) || (is_surfing && surfable_class_codes.includes(class_code));
    return is_encounter_ground;
  }

  function slot_index_for_roll(slot_rates, slot_roll_out_of_hundred) {
    let running_total_of_rates = 0;
    for (let slot_index = 0; slot_index < slot_rates.length; slot_index++) {
      running_total_of_rates += slot_rates[slot_index];
      if (slot_roll_out_of_hundred < running_total_of_rates) return slot_index;
    }
    return slot_rates.length - 1;
  }

  function roll_wild_encounter(encounter_table, is_water_table, random_fraction) {
    if (!encounter_table || !encounter_table.mons || !encounter_table.mons.length) return null;
    const chance_out_of_divisor = Number(encounter_table.encounter_rate || 0) * emerald_encounter_rate_multiplier;
    if (random_fraction() * emerald_encounter_chance_divisor >= chance_out_of_divisor) return null;
    const slot_rates = (is_water_table ? water_encounter_slot_rates : land_encounter_slot_rates).slice(0, encounter_table.mons.length);
    const total_of_rates = slot_rates.reduce((sum_of_rates, slot_rate) => sum_of_rates + slot_rate, 0);
    const wild_slot = encounter_table.mons[slot_index_for_roll(slot_rates, random_fraction() * total_of_rates)];
    const level_span = wild_slot.max_level - wild_slot.min_level + 1;
    const wild_encounter = {
      species_name: readable_species_name(wild_slot.species),
      level: wild_slot.min_level + Math.floor(random_fraction() * level_span),
    };
    return wild_encounter;
  }

  function wild_encounter_text(wild_encounter) {
    const encounter_text = `Wild ${wild_encounter.species_name} appeared!`;
    return encounter_text;
  }

  function step_duration_for_move(move_kind, is_running) {
    switch (move_kind) {
      case "jump":
        return ledge_jump_duration_in_milliseconds;
      case "mount_surf":
      case "dismount":
        return surf_hop_duration_in_milliseconds;
      case "swim":
        return surf_step_duration_in_milliseconds;
      default:
        return is_running ? run_step_duration_in_milliseconds : walk_step_duration_in_milliseconds;
    }
  }

  function hop_height_for_move(move_kind, step_progress) {
    const peak_height_in_pixels = move_kind === "jump" ? ledge_jump_peak_height_in_pixels : ["mount_surf", "dismount"].includes(move_kind) ? surf_hop_peak_height_in_pixels : 0;
    const hop_height_in_pixels = Math.round(Math.sin(Math.PI * step_progress) * peak_height_in_pixels);
    return hop_height_in_pixels;
  }

  function player_sheet_frame(facing_direction, step_progress, step_parity) {
    const is_mid_stride = step_progress !== null && step_progress < 0.5;
    const frame_index = is_mid_stride ? walking_frames_by_direction[facing_direction][step_parity % 2] : standing_frame_by_direction[facing_direction];
    const sheet_frame = { frame_index, is_mirrored: facing_direction === "right" };
    return sheet_frame;
  }

  function surfing_sheet_frames(facing_direction) {
    const sheet_frames = {
      rider_frame_index: surfing_frame_by_direction[facing_direction],
      blob_frame_index: surf_blob_frame_by_direction[facing_direction],
      is_mirrored: facing_direction === "right",
    };
    return sheet_frames;
  }

  function tall_grass_frame_for_age(rustle_age_in_milliseconds) {
    const rustle_frame_position = Math.floor(rustle_age_in_milliseconds / tall_grass_rustle_frame_duration_in_milliseconds);
    const tall_grass_frame = rustle_frame_position < tall_grass_rustle_frames.length ? tall_grass_rustle_frames[rustle_frame_position] : tall_grass_cover_frame;
    return tall_grass_frame;
  }

  function camera_top_left_in_pixels(player_pixel_position) {
    const camera_top_left = {
      x: player_pixel_position.x - player_cell_left_on_screen_in_pixels,
      y: player_pixel_position.y - player_cell_top_on_screen_in_pixels,
    };
    return camera_top_left;
  }

  function opposite_side(exit_side) {
    const opposite_by_side = { north: "south", south: "north", east: "west", west: "east" };
    const opposite = opposite_by_side[exit_side] || "";
    return opposite;
  }

  function exit_index_toward_name(connections, previous_route_name) {
    const matching_index = connections.findIndex((connection) => !!connection && connection.name === previous_route_name);
    return matching_index;
  }

  function arrival_exit_index(scene_exits, connections, previous_route_name, left_by_side) {
    const exit_back_index = exit_index_toward_name(connections || [], previous_route_name);
    if (exit_back_index >= 0) return exit_back_index;
    const opposite_index = scene_exits.findIndex((scene_exit) => scene_exit.side === opposite_side(left_by_side));
    const arrival_index = opposite_index >= 0 ? opposite_index : (scene_exits.length ? 0 : -1);
    return arrival_index;
  }

  function middle_exit_cell(scene_exit) {
    const middle_cell = scene_exit.cells[Math.floor(scene_exit.cells.length / 2)];
    const cell = { x: middle_cell[1], y: middle_cell[0] };
    return cell;
  }

  function first_walkable_cell(play_world) {
    for (let cell_y = 0; cell_y < play_world.height; cell_y++) {
      const walkable_x = [...play_world.class_rows[cell_y]].findIndex((_class_code, cell_x) => is_walkable_ground_at(play_world, { x: cell_x, y: cell_y }) && !object_at(play_world, { x: cell_x, y: cell_y }));
      if (walkable_x >= 0) return { x: walkable_x, y: cell_y };
    }
    return { x: 0, y: 0 };
  }

  function arrival_placement(play_world, arrival_index) {
    if (arrival_index < 0 || !play_world.exits[arrival_index] || !play_world.exits[arrival_index].cells.length) return { cell: first_walkable_cell(play_world), facing: "down" };
    const scene_exit = play_world.exits[arrival_index];
    const facing_into_route = direction_of_exit_side[opposite_side(scene_exit.side)] || "down";
    const placement = { cell: middle_exit_cell(scene_exit), facing: facing_into_route };
    return placement;
  }

  function starting_placement(play_world) {
    const south_exit_index = play_world.exits.findIndex((scene_exit) => scene_exit.side === "south");
    const placement = arrival_placement(play_world, south_exit_index >= 0 ? south_exit_index : 0);
    return placement;
  }

  function cell_across_chain_seam(cell_on_map_left, direction, offset_of_map_left, offset_of_map_entered) {
    const cell_in_chain = cell_in_direction({ x: cell_on_map_left.x + offset_of_map_left.x, y: cell_on_map_left.y + offset_of_map_left.y }, direction, 1);
    const cell_on_map_entered = { x: cell_in_chain.x - offset_of_map_entered.x, y: cell_in_chain.y - offset_of_map_entered.y };
    return cell_on_map_entered;
  }

  function seam_entry_move(play_world, entered_cell, is_surfing, field_moves) {
    if (!is_inside_world(play_world, entered_cell) || object_at(play_world, entered_cell)) return null;
    if (is_surfing && is_surfable_water_at(play_world, entered_cell, field_moves || no_field_moves)) return { kind: "swim", to: entered_cell };
    if (is_walkable_ground_at(play_world, entered_cell)) return { kind: "walk", to: entered_cell };
    return null;
  }

  function seam_entry_placement(play_world, entered_cell, direction, is_surfing, field_moves) {
    const entry_move = seam_entry_move(play_world, entered_cell, is_surfing, field_moves);
    if (!entry_move) return null;
    const placement = {
      cell: cell_in_direction(entered_cell, direction, -1), facing: direction, is_surfing: entry_move.kind === "swim", entry_move,
    };
    return placement;
  }

  function transition_timed_out(transition, now, timeout_in_milliseconds) {
    return transition.kind !== "none" && now - transition.started_at > timeout_in_milliseconds;
  }

  const api = {
    cell_across_chain_seam, seam_entry_placement, transition_timed_out,
    viewport_width_in_tiles, viewport_height_in_tiles, tile_size_in_pixels, turn_in_place_duration_in_milliseconds,
    direction_vectors, cell_key, cell_in_direction, build_play_world, resolve_step, resolve_interaction,
    world_after_interaction, world_with_boulder_pushed, is_encounter_cell, roll_wild_encounter, wild_encounter_text,
    readable_item_name, readable_species_name, step_duration_for_move, hop_height_for_move, player_sheet_frame,
    surfing_sheet_frames, tall_grass_frame_for_age, camera_top_left_in_pixels, arrival_exit_index, arrival_placement,
    starting_placement, class_code_at, object_at,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PlaytestLogic = api;
})(typeof window !== "undefined" ? window : globalThis);
