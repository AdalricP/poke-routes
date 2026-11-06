(function (global_scope) {
  "use strict";

  const PIXELS_PER_TILE_SIDE = 8;
  const METATILE_SIDE_IN_PIXELS = 16;
  const QUADRANTS_PER_LAYER = 4;
  const FIRST_SECONDARY_TILE_INDEX = 512;
  const FIRST_SECONDARY_METATILE_ID = 512;
  const TILE_INDEX_MASK = 0x3ff;
  const HORIZONTAL_FLIP_BIT = 0x400;
  const VERTICAL_FLIP_BIT = 0x800;
  const PALETTE_SLOT_SHIFT = 12;
  const TRANSPARENT_COLOUR_INDEX = 0;

  function decode_tile_entry(packed_entry) {
    const decoded_entry = {
      tile_index: packed_entry & TILE_INDEX_MASK,
      is_flipped_horizontally: (packed_entry & HORIZONTAL_FLIP_BIT) !== 0,
      is_flipped_vertically: (packed_entry & VERTICAL_FLIP_BIT) !== 0,
      palette_slot: (packed_entry >> PALETTE_SLOT_SHIFT) & 0xf,
    };
    return decoded_entry;
  }

  function tile_pixels_from_hex(tile_pixels_hex) {
    const tile_pixels = Uint8Array.from(tile_pixels_hex, (hex_digit) => parseInt(hex_digit, 16));
    return tile_pixels;
  }

  function colour_from_hex(colour_hex) {
    const red_green_blue = [1, 3, 5].map((offset) => parseInt(colour_hex.slice(offset, offset + 2), 16));
    return red_green_blue;
  }

  function colour_to_hex(red_green_blue) {
    const colour_hex = "#" + red_green_blue.map((channel) => channel.toString(16).padStart(2, "0")).join("");
    return colour_hex;
  }

  function gba_colour_hex(colour_hex) {
    const five_bit_channels = colour_from_hex(colour_hex).map((channel) => (channel >> 3) << 3);
    const snapped_hex = colour_to_hex(five_bit_channels);
    return snapped_hex;
  }

  function sheet_tiles(base_tile_hexes, first_global_index) {
    const tiles_by_global_index = new Map();
    base_tile_hexes.forEach((tile_pixels_hex, local_index) => tiles_by_global_index.set(first_global_index + local_index, tile_pixels_from_hex(tile_pixels_hex)));
    return tiles_by_global_index;
  }

  function metatile_rows(base_rows, first_metatile_id) {
    const entries_by_metatile_id = new Map();
    base_rows.forEach((tile_entries, local_index) => entries_by_metatile_id.set(first_metatile_id + local_index, tile_entries.slice()));
    return entries_by_metatile_id;
  }

  function overlaid_model(base, overlay) {
    const applied_overlay = overlay || {};
    const tiles = new Map([...sheet_tiles(base.primary_tiles, 0), ...sheet_tiles(base.secondary_tiles, FIRST_SECONDARY_TILE_INDEX)]);
    Object.entries(applied_overlay.tiles || {}).forEach(([tile_index_text, tile_pixels_hex]) => tiles.set(Number(tile_index_text), tile_pixels_from_hex(tile_pixels_hex)));
    const palettes = base.palettes.map((palette) => palette.slice());
    Object.entries(applied_overlay.palettes || {}).forEach(([palette_slot_text, colour_hexes]) => { palettes[Number(palette_slot_text)] = colour_hexes.slice(); });
    const metatiles = new Map([...metatile_rows(base.primary_metatiles, 0), ...metatile_rows(base.secondary_metatiles, FIRST_SECONDARY_METATILE_ID)]);
    Object.entries(applied_overlay.metatiles || {}).forEach(([metatile_id_text, tile_entries]) => metatiles.set(Number(metatile_id_text), tile_entries.slice()));
    const attributes = overlaid_attributes(base, applied_overlay);
    const model = { tiles, palettes, metatiles, attributes, custom: applied_overlay.custom_metatiles || {},
      overridden_tiles: new Set(Object.keys(applied_overlay.tiles || {}).map(Number)),
      overridden_metatiles: new Set(Object.keys(applied_overlay.metatiles || {}).map(Number)) };
    return model;
  }

  function overlaid_attributes(base, applied_overlay) {
    const attributes = new Map();
    base.primary_attributes.forEach((attribute_value, metatile_id) => attributes.set(metatile_id, attribute_value));
    base.secondary_attributes.forEach((attribute_value, local_index) => attributes.set(FIRST_SECONDARY_METATILE_ID + local_index, attribute_value));
    Object.entries(applied_overlay.metatile_attributes || {}).forEach(([metatile_id_text, attribute_value]) => attributes.set(Number(metatile_id_text), attribute_value));
    return attributes;
  }

  function sorted_metatile_ids(model) {
    const metatile_ids = [...model.metatiles.keys()].sort((first_id, second_id) => first_id - second_id);
    return metatile_ids;
  }

  function blank_tile_pixels() {
    const blank_pixels = new Uint8Array(PIXELS_PER_TILE_SIDE * PIXELS_PER_TILE_SIDE);
    return blank_pixels;
  }

  function shown_tile_pixel(tile_pixels, tile_entry, shown_x, shown_y) {
    const stored_x = tile_entry.is_flipped_horizontally ? PIXELS_PER_TILE_SIDE - 1 - shown_x : shown_x;
    const stored_y = tile_entry.is_flipped_vertically ? PIXELS_PER_TILE_SIDE - 1 - shown_y : shown_y;
    const colour_index = tile_pixels[stored_y * PIXELS_PER_TILE_SIDE + stored_x];
    return colour_index;
  }

  function quadrant_of_pixel(pixel_x, pixel_y) {
    const quadrant_position = (pixel_y >= PIXELS_PER_TILE_SIDE ? 2 : 0) + (pixel_x >= PIXELS_PER_TILE_SIDE ? 1 : 0);
    return quadrant_position;
  }

  function layer_entries(model, metatile_id, layer_index) {
    const tile_entries = model.metatiles.get(metatile_id) || [];
    const entries_of_layer = tile_entries.slice(layer_index * QUADRANTS_PER_LAYER, (layer_index + 1) * QUADRANTS_PER_LAYER).map(decode_tile_entry);
    return entries_of_layer;
  }

  function layer_pixels(model, metatile_id, layer_index) {
    const entries_of_layer = layer_entries(model, metatile_id, layer_index);
    const pixels = new Uint8Array(METATILE_SIDE_IN_PIXELS * METATILE_SIDE_IN_PIXELS);
    if (entries_of_layer.length < QUADRANTS_PER_LAYER) return pixels;
    for (let pixel_y = 0; pixel_y < METATILE_SIDE_IN_PIXELS; pixel_y++) {
      for (let pixel_x = 0; pixel_x < METATILE_SIDE_IN_PIXELS; pixel_x++) {
        const tile_entry = entries_of_layer[quadrant_of_pixel(pixel_x, pixel_y)];
        const tile_pixels = model.tiles.get(tile_entry.tile_index) || blank_tile_pixels();
        pixels[pixel_y * METATILE_SIDE_IN_PIXELS + pixel_x] = shown_tile_pixel(tile_pixels, tile_entry, pixel_x % PIXELS_PER_TILE_SIDE, pixel_y % PIXELS_PER_TILE_SIDE);
      }
    }
    return pixels;
  }

  function layer_palette_slots(model, metatile_id, layer_index) {
    const palette_slots = layer_entries(model, metatile_id, layer_index).map((tile_entry) => tile_entry.palette_slot);
    return palette_slots;
  }

  function render_metatile_rgba(model, metatile_id, visible_layers) {
    const shown_layers = visible_layers || [true, true];
    const rgba = new Uint8ClampedArray(METATILE_SIDE_IN_PIXELS * METATILE_SIDE_IN_PIXELS * 4);
    for (const layer_index of [0, 1]) {
      if (!shown_layers[layer_index]) continue;
      paint_layer_into_rgba(rgba, model, metatile_id, layer_index);
    }
    return rgba;
  }

  function layer_rgba(model, metatile_id, layer_index, pixels_to_show) {
    const rgba = new Uint8ClampedArray(METATILE_SIDE_IN_PIXELS * METATILE_SIDE_IN_PIXELS * 4);
    paint_layer_into_rgba(rgba, model, metatile_id, layer_index, pixels_to_show);
    return rgba;
  }

  function paint_layer_into_rgba(rgba, model, metatile_id, layer_index, pixels_to_show) {
    const pixels = pixels_to_show || layer_pixels(model, metatile_id, layer_index);
    const palette_slots = layer_palette_slots(model, metatile_id, layer_index);
    for (let pixel_offset = 0; pixel_offset < pixels.length; pixel_offset++) {
      const colour_index = pixels[pixel_offset];
      const is_transparent = layer_index === 1 && colour_index === TRANSPARENT_COLOUR_INDEX;
      if (is_transparent) continue;
      const palette_slot = palette_slots[quadrant_of_pixel(pixel_offset % METATILE_SIDE_IN_PIXELS, Math.floor(pixel_offset / METATILE_SIDE_IN_PIXELS))];
      const [red, green, blue] = colour_from_hex((model.palettes[palette_slot] || [])[colour_index] || "#000000");
      rgba.set([red, green, blue, 255], pixel_offset * 4);
    }
  }

  function flood_fill_writes(pixels, start_x, start_y, colour_index) {
    const side = METATILE_SIDE_IN_PIXELS;
    const replaced_colour = pixels[start_y * side + start_x];
    if (replaced_colour === colour_index) return [];
    const visited_offsets = new Set();
    const pending_points = [[start_x, start_y]];
    const pixel_writes = [];
    while (pending_points.length) {
      const [point_x, point_y] = pending_points.pop();
      const point_offset = point_y * side + point_x;
      const is_fillable = point_x >= 0 && point_y >= 0 && point_x < side && point_y < side && !visited_offsets.has(point_offset) && pixels[point_offset] === replaced_colour;
      if (!is_fillable) continue;
      visited_offsets.add(point_offset);
      pixel_writes.push([point_x, point_y, colour_index]);
      pending_points.push([point_x + 1, point_y], [point_x - 1, point_y], [point_x, point_y + 1], [point_x, point_y - 1]);
    }
    return pixel_writes;
  }

  function line_points(start_x, start_y, end_x, end_y) {
    const step_x = Math.sign(end_x - start_x), step_y = Math.sign(end_y - start_y);
    const distance_x = Math.abs(end_x - start_x), distance_y = -Math.abs(end_y - start_y);
    const points = [];
    let point_x = start_x, point_y = start_y, accumulated_error = distance_x + distance_y;
    for (;;) {
      points.push([point_x, point_y]);
      if (point_x === end_x && point_y === end_y) break;
      const doubled_error = 2 * accumulated_error;
      if (doubled_error >= distance_y) { accumulated_error += distance_y; point_x += step_x; }
      if (doubled_error <= distance_x) { accumulated_error += distance_x; point_y += step_y; }
    }
    return points;
  }

  function mirrored_points(point_x, point_y, mirror_horizontally, mirror_vertically) {
    const last = METATILE_SIDE_IN_PIXELS - 1;
    const candidate_points = [[point_x, point_y]];
    if (mirror_horizontally) candidate_points.push([last - point_x, point_y]);
    if (mirror_vertically) candidate_points.push([point_x, last - point_y]);
    if (mirror_horizontally && mirror_vertically) candidate_points.push([last - point_x, last - point_y]);
    const distinct_points = [...new Map(candidate_points.map((point) => [point.join(","), point])).values()];
    return distinct_points;
  }

  function writes_for_points(points, colour_index, mirror_horizontally, mirror_vertically) {
    const writes_by_position = new Map();
    for (const [point_x, point_y] of points) {
      for (const [mirrored_x, mirrored_y] of mirrored_points(point_x, point_y, mirror_horizontally, mirror_vertically)) {
        writes_by_position.set(`${mirrored_x},${mirrored_y}`, [mirrored_x, mirrored_y, colour_index]);
      }
    }
    const pixel_writes = [...writes_by_position.values()];
    return pixel_writes;
  }

  function pixels_with_writes(pixels, pixel_writes) {
    const updated_pixels = Uint8Array.from(pixels);
    pixel_writes.forEach(([pixel_x, pixel_y, colour_index]) => { updated_pixels[pixel_y * METATILE_SIDE_IN_PIXELS + pixel_x] = colour_index; });
    return updated_pixels;
  }

  function references_in_entries(tile_entries, tile_index) {
    const reference_count = tile_entries.filter((packed_entry) => (packed_entry & TILE_INDEX_MASK) === tile_index).length;
    return reference_count;
  }

  function tile_reference_count(model, tile_index) {
    const reference_count = [...model.metatiles.values()].reduce((running_total, tile_entries) => running_total + references_in_entries(tile_entries, tile_index), 0);
    return reference_count;
  }

  function is_custom_or_changed(model, metatile_id) {
    const is_marked = metatile_id in model.custom || model.overridden_metatiles.has(metatile_id);
    return is_marked;
  }

  function sorted_deep(value) {
    if (Array.isArray(value)) return value.map(sorted_deep);
    if (!value || typeof value !== "object") return value;
    const sorted_object = Object.fromEntries(Object.keys(value).sort().map((key) => [key, sorted_deep(value[key])]));
    return sorted_object;
  }

  function overlay_content_fingerprint(overlay) {
    const content_without_revision = Object.assign({}, overlay || {}, { revision: 0 });
    const fingerprint = JSON.stringify(sorted_deep(content_without_revision));
    return fingerprint;
  }

  function overlays_differ(first_overlay, second_overlay) {
    const differ = overlay_content_fingerprint(first_overlay) !== overlay_content_fingerprint(second_overlay);
    return differ;
  }

  const api = {
    PIXELS_PER_TILE_SIDE, METATILE_SIDE_IN_PIXELS, QUADRANTS_PER_LAYER, FIRST_SECONDARY_METATILE_ID,
    decode_tile_entry, tile_pixels_from_hex, colour_from_hex, colour_to_hex, gba_colour_hex,
    overlaid_model, sorted_metatile_ids, layer_pixels, layer_rgba, layer_palette_slots, quadrant_of_pixel, render_metatile_rgba,
    flood_fill_writes, line_points, mirrored_points, writes_for_points, pixels_with_writes, tile_reference_count,
    is_custom_or_changed, overlays_differ,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else global_scope.TileEditorLogic = api;
})(typeof window !== "undefined" ? window : globalThis);
