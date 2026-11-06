(function (global) {
  "use strict";

  const metatile_size_in_pixels = 16;
  const most_route_maps_generated_at_once = 3;
  const pixels_per_region_cell_where_tiles_start_to_show = 10;
  const pixels_per_region_cell_where_tiles_fully_show = 26;
  const pixels_per_region_cell_where_generation_starts = 7;
  const region_cells_of_margin_around_view_for_generation = 6;

  function share_of_route_tiles_shown_at_zoom(pixels_per_region_cell) {
    const zoom_span = pixels_per_region_cell_where_tiles_fully_show - pixels_per_region_cell_where_tiles_start_to_show;
    const raw_share = (pixels_per_region_cell - pixels_per_region_cell_where_tiles_start_to_show) / zoom_span;
    const clamped_share = Math.max(0, Math.min(1, raw_share));
    const eased_share = clamped_share * clamped_share * (3 - 2 * clamped_share);
    return eased_share;
  }

  function does_rect_touch_cell_window(region_rect, cell_window) {
    const [left, top, right, bottom] = region_rect;
    const is_touching = right >= cell_window.left && left <= cell_window.right && bottom >= cell_window.top && top <= cell_window.bottom;
    return is_touching;
  }

  function distance_from_rect_centre_to_point(region_rect, point) {
    const centre_x = (region_rect[0] + region_rect[2]) / 2, centre_y = (region_rect[1] + region_rect[3]) / 2;
    const distance_in_region_cells = Math.hypot(centre_x - point.x, centre_y - point.y);
    return distance_in_region_cells;
  }

  function chain_maps_of_document(region_document) {
    const chain_maps = [];
    for (const route of region_document.routes || []) {
      for (const chain_map of route.chain || []) chain_maps.push({ route_name: route.name, map_name: chain_map.name, rect: chain_map.rect });
    }
    return chain_maps;
  }

  function paint_final_tiles_of_route(route_result, atlas) {
    const final_stage = route_result.stages[route_result.stages.length - 1];
    const tile_canvas = document.createElement("canvas");
    tile_canvas.width = route_result.width * metatile_size_in_pixels;
    tile_canvas.height = route_result.height * metatile_size_in_pixels;
    const tile_context = tile_canvas.getContext("2d");
    const { meta, img } = atlas;
    for (let tile_index = 0; tile_index < final_stage.tiles.length; tile_index++) {
      const atlas_cell = meta.index[final_stage.tiles[tile_index]];
      if (atlas_cell == null) continue;
      const left = (tile_index % route_result.width) * metatile_size_in_pixels;
      const top = Math.floor(tile_index / route_result.width) * metatile_size_in_pixels;
      tile_context.drawImage(img, (atlas_cell % meta.columns) * metatile_size_in_pixels, Math.floor(atlas_cell / meta.columns) * metatile_size_in_pixels,
        metatile_size_in_pixels, metatile_size_in_pixels, left, top, metatile_size_in_pixels, metatile_size_in_pixels);
    }
    return tile_canvas;
  }

  function generate_request_of_chain_map_spec(chain_map_spec) {
    const { params: route_params, region } = global.SL.specToParams(chain_map_spec, {});
    const generate_request = Object.assign({}, route_params, { tiler: "ark" });
    if (!region || !region.region) return generate_request;
    generate_request.region = region.region;
    if (region.endings && region.endings.length) generate_request.endings = region.endings;
    return generate_request;
  }

  function make_fly_view_route_tiles(dependencies) {
    const { post_json, get_json, encoded_document, on_tiles_ready } = dependencies;
    const tiles_of_map_name = new Map();
    const chain_specs_of_route_name = new Map();
    const atlas_of_address = new Map();
    let generating_count = 0;
    let document_the_tiles_belong_to = null;

    function forget_tiles_of_another_document(region_document) {
      if (region_document === document_the_tiles_belong_to) return;
      document_the_tiles_belong_to = region_document;
      tiles_of_map_name.clear();
      chain_specs_of_route_name.clear();
    }

    async function atlas_at(atlas_address) {
      if (atlas_of_address.has(atlas_address)) return atlas_of_address.get(atlas_address);
      const meta = await get_json(atlas_address);
      const img = new Image();
      img.src = meta.png;
      await img.decode();
      const atlas = { meta, img };
      atlas_of_address.set(atlas_address, atlas);
      return atlas;
    }

    function chain_specs_of_route(region_document, route_name) {
      if (!chain_specs_of_route_name.has(route_name)) {
        chain_specs_of_route_name.set(route_name, post_json("/api/editor/route-chain-specs", { doc: encoded_document(region_document), id: route_name }));
      }
      return chain_specs_of_route_name.get(route_name);
    }

    async function generate_tiles_of_chain_map(region_document, chain_map) {
      const chain_of_route = await chain_specs_of_route(region_document, chain_map.route_name);
      const chain_map_with_spec = (chain_of_route.maps || []).find((opened_map) => opened_map.name === chain_map.map_name);
      if (!chain_map_with_spec) throw new Error(`no spec for map ${chain_map.map_name} of ${chain_map.route_name}`);
      const route_result = await post_json("/api/generate", generate_request_of_chain_map_spec(chain_map_with_spec.spec));
      const atlas = await atlas_at(route_result.atlas || "/api/atlas");
      const tile_canvas = paint_final_tiles_of_route(route_result, atlas);
      return tile_canvas;
    }

    async function start_generating_chain_map(region_document, chain_map) {
      const tiles_entry = { state: "generating", canvas: null, shown_since_in_milliseconds: 0 };
      tiles_of_map_name.set(chain_map.map_name, tiles_entry);
      generating_count++;
      try {
        tiles_entry.canvas = await generate_tiles_of_chain_map(region_document, chain_map);
        tiles_entry.state = "ready";
        tiles_entry.shown_since_in_milliseconds = performance.now();
      } catch (generation_error) {
        tiles_entry.state = "failed";
        if (global.console) global.console.warn(`fly view: ${chain_map.map_name}: ${generation_error.message}`);
      } finally {
        generating_count--;
        on_tiles_ready();
      }
    }

    function request_tiles_near_view(region_document, cell_window, pixels_per_region_cell) {
      forget_tiles_of_another_document(region_document);
      if (pixels_per_region_cell < pixels_per_region_cell_where_generation_starts) return;
      const margin = region_cells_of_margin_around_view_for_generation;
      const widened_window = { left: cell_window.left - margin, top: cell_window.top - margin, right: cell_window.right + margin, bottom: cell_window.bottom + margin };
      const view_centre = { x: (cell_window.left + cell_window.right) / 2, y: (cell_window.top + cell_window.bottom) / 2 };
      const is_waiting_near_view = (chain_map) => !tiles_of_map_name.has(chain_map.map_name) && does_rect_touch_cell_window(chain_map.rect, widened_window);
      const by_distance_to_view_centre = (first_map, second_map) => distance_from_rect_centre_to_point(first_map.rect, view_centre) - distance_from_rect_centre_to_point(second_map.rect, view_centre);
      const waiting_maps = chain_maps_of_document(region_document).filter(is_waiting_near_view).sort(by_distance_to_view_centre);
      for (const chain_map of waiting_maps) {
        if (generating_count >= most_route_maps_generated_at_once) return;
        start_generating_chain_map(region_document, chain_map);
      }
    }

    function draw_one_chain_map(context, chain_map, screen_rect_of, share_shown, now_in_milliseconds) {
      const [left, top, width, height] = screen_rect_of(chain_map.rect);
      const tiles_entry = tiles_of_map_name.get(chain_map.map_name);
      if (!tiles_entry || tiles_entry.state !== "ready") {
        draw_placeholder(context, left, top, width, height, share_shown, now_in_milliseconds);
        return;
      }
      const fade_in_share = Math.min(1, (now_in_milliseconds - tiles_entry.shown_since_in_milliseconds) / 350);
      context.globalAlpha = share_shown * fade_in_share;
      context.imageSmoothingEnabled = width < tiles_entry.canvas.width;
      context.drawImage(tiles_entry.canvas, left, top, width, height);
      context.globalAlpha = 1;
    }

    function draw_placeholder(context, left, top, width, height, share_shown, now_in_milliseconds) {
      const pulse_share = 0.5 + 0.5 * Math.sin(now_in_milliseconds / 420);
      context.fillStyle = `rgba(255,255,255,${(0.05 + 0.05 * pulse_share) * share_shown})`;
      context.fillRect(left, top, width, height);
      context.strokeStyle = `rgba(255,255,255,${0.25 * share_shown})`;
      context.setLineDash([4, 4]);
      context.lineWidth = 1;
      context.strokeRect(left + 0.5, top + 0.5, width - 1, height - 1);
      context.setLineDash([]);
    }

    function draw_route_tiles(context, region_document, cell_window, pixels_per_region_cell, screen_rect_of) {
      request_tiles_near_view(region_document, cell_window, pixels_per_region_cell);
      const share_shown = share_of_route_tiles_shown_at_zoom(pixels_per_region_cell);
      if (share_shown <= 0) return false;
      const now_in_milliseconds = performance.now();
      const shown_maps = chain_maps_of_document(region_document).filter((chain_map) => does_rect_touch_cell_window(chain_map.rect, cell_window));
      context.save();
      for (const chain_map of shown_maps) draw_one_chain_map(context, chain_map, screen_rect_of, share_shown, now_in_milliseconds);
      context.restore();
      const is_still_animating = shown_maps.some((chain_map) => is_map_still_arriving(tiles_of_map_name.get(chain_map.map_name), now_in_milliseconds));
      return is_still_animating;
    }

    function is_map_still_arriving(tiles_entry, now_in_milliseconds) {
      if (!tiles_entry || tiles_entry.state === "generating") return true;
      const is_fading_in = tiles_entry.state === "ready" && now_in_milliseconds - tiles_entry.shown_since_in_milliseconds < 400;
      return is_fading_in;
    }

    const fly_view_route_tiles = { draw_route_tiles };
    return fly_view_route_tiles;
  }

  const fly_view_api = { make_fly_view_route_tiles, share_of_route_tiles_shown_at_zoom, does_rect_touch_cell_window, chain_maps_of_document };
  global.FLY_VIEW = fly_view_api;
})(typeof window !== "undefined" ? window : globalThis);
