// Region editor: pure helpers (no DOM). Used by editor.js and by the node tests
// (webui/tests/editor-logic.test.js). Grids are typed arrays indexed y * W + x.
(function (root) {
  "use strict";

  // ------------------------------------------------------------ constants
  const STAGES = ["water", "climate", "biomes", "routes"];
  // which stages an edit makes stale
  const STALE_BY = {
    terrain: ["water", "climate", "biomes", "routes"],
    coast: ["water", "climate", "biomes", "routes"],
    biome: ["routes"],
    places: ["routes"],
  };
  const BIOME_NAMES = {
    "~": "sea", "=": "deep sea", o: "lake", b: "beach", d: "desert", m: "meadow", f: "forest", F: "deep forest",
    w: "marsh", M: "mountain", S: "snow peaks", v: "volcanic",
  };
  const PAINT_BIOMES = "mfFwbdMSv";
  const BIOME_RGB = {
    "~": [58, 104, 150], "=": [34, 62, 104], o: [78, 132, 170], b: [226, 210, 160], d: [210, 178, 118],
    m: [156, 182, 98], f: [86, 136, 70], F: [52, 98, 56], w: [108, 140, 104], M: [128, 118, 110],
    S: [232, 234, 238], v: [92, 72, 70],
  };
  const ROLE_RGB = {
    straight: [250, 250, 250], turn: [255, 214, 92], crossroads: [255, 120, 90], dead_end: [190, 140, 255],
    coastal: [120, 220, 255], mountain_pass: [230, 190, 160], river_crossing: [110, 200, 255],
    loop: [130, 255, 160], sea: [150, 220, 255],
  };
  const ROLES = Object.keys(ROLE_RGB);
  const SPEC_BIOMES = ["forest", "meadow", "plains", "riverland", "beach", "desert", "mountain", "volcanic"];
  const SIDES = ["north", "east", "south", "west"];
  const DELTA = { east: [1, 0], west: [-1, 0], south: [0, 1], north: [0, -1] };
  const OPP = { east: "west", west: "east", north: "south", south: "north" };
  const CELL = 4, MAX_W = 24, MAX_H = 28, MAP_BUDGET = 10240;
  const HYPSO = [[92, 140, 74], [150, 170, 96], [196, 180, 120], [150, 118, 88], [236, 236, 236]];

  // ------------------------------------------------------------ base64 grids
  function b64ToBytes(s) {
    if (typeof Buffer !== "undefined" && typeof atob === "undefined") return new Uint8Array(Buffer.from(s, "base64"));
    const bin = atob(s);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  function bytesToB64(u8) {
    if (typeof Buffer !== "undefined" && typeof btoa === "undefined") return Buffer.from(u8.buffer, u8.byteOffset, u8.byteLength).toString("base64");
    let s = "";
    for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
    return btoa(s);
  }
  const CTOR = { f4: Float32Array, u1: Uint8Array, i4: Int32Array };
  function decodeGrid(g) {
    const bytes = b64ToBytes(g.b64);
    const C = CTOR[g.dtype];
    if (!C) throw new Error("unknown grid dtype " + g.dtype);
    return new C(bytes.buffer, bytes.byteOffset, bytes.byteLength / C.BYTES_PER_ELEMENT).slice();
  }
  function encodeGrid(a, dtype) {
    return { dtype, b64: bytesToB64(new Uint8Array(a.buffer, a.byteOffset, a.byteLength)) };
  }
  function decodeDoc(wire) {
    const doc = Object.assign({}, wire);
    doc.grids = {};
    doc.dtypes = {};
    for (const [k, g] of Object.entries(wire.grids)) {
      doc.grids[k] = decodeGrid(g);
      doc.dtypes[k] = g.dtype;
    }
    doc.W = wire.size[0];
    doc.H = wire.size[1];
    doc.stale = (wire.stale || []).slice();
    return doc;
  }
  function encodeDoc(doc) {
    const out = {};
    for (const [k, v] of Object.entries(doc)) {
      if (k === "grids" || k === "dtypes" || k === "W" || k === "H") continue;
      out[k] = v;
    }
    out.grids = {};
    for (const [k, a] of Object.entries(doc.grids)) out.grids[k] = encodeGrid(a, doc.dtypes[k]);
    return out;
  }

  // ------------------------------------------------------------ history (undo / redo)
  // Snapshots share grid arrays; an edit copies a grid the first time it touches it
  // (copy on write), so 100 steps cost only the grids that actually changed.
  const DATA_KEYS = ["cities", "landmarks", "links", "routes", "stale", "meta", "name", "user_note", "story", "map_grid"];
  function snapshot(doc) {
    const data = {};
    for (const k of DATA_KEYS) if (doc[k] !== undefined) data[k] = JSON.parse(JSON.stringify(doc[k]));
    return { grids: Object.assign({}, doc.grids), data };
  }
  function restore(doc, snap) {
    doc.grids = Object.assign({}, snap.grids);
    for (const k of DATA_KEYS) {
      if (snap.data[k] !== undefined) doc[k] = JSON.parse(JSON.stringify(snap.data[k]));
      else delete doc[k];
    }
  }
  class History {
    constructor(limit) { this.limit = limit || 100; this.undos = []; this.redos = []; }
    begin(doc, label) {
      this.undos.push(Object.assign(snapshot(doc), { label: label || "edit" }));
      if (this.undos.length > this.limit) this.undos.shift();
      this._cancelledRedos = this.redos;
      this.redos = [];
      return { cloned: new Set() };
    }
    // drop the last begin() (an edit that turned out to change nothing)
    cancel() {
      this.undos.pop();
      if (this._cancelledRedos !== undefined) {
        this.redos = this._cancelledRedos;
        this._cancelledRedos = undefined;
      }
    }
    undo(doc) {
      if (!this.undos.length) return null;
      const s = this.undos.pop();
      this.redos.push(Object.assign(snapshot(doc), { label: s.label }));
      restore(doc, s);
      return s.label;
    }
    redo(doc) {
      if (!this.redos.length) return null;
      const s = this.redos.pop();
      this.undos.push(Object.assign(snapshot(doc), { label: s.label }));
      restore(doc, s);
      return s.label;
    }
  }
  function touch(doc, session, name) {
    if (!session.cloned.has(name)) {
      doc.grids[name] = doc.grids[name].slice();
      session.cloned.add(name);
    }
    return doc.grids[name];
  }

  // ------------------------------------------------------------ staleness
  function markStale(stale, cause) {
    const add = STALE_BY[cause] || [];
    const set = new Set(stale.concat(add));
    return STAGES.filter((s) => set.has(s));
  }
  // stages a "recompute <stage>" button runs: the terrain chain from that stage on
  // (water -> climate -> biomes); routes only when asked for, since they move things
  function stagesFor(button, stale) {
    if (button === "stale") return STAGES.filter((s) => stale.includes(s));
    if (button === "routes") return ["routes"];
    const i = STAGES.indexOf(button);
    if (i < 0) return [];
    return STAGES.slice(i, 3);
  }

  // ------------------------------------------------------------ brushes
  // cells within radius r of (cx, cy) with a smooth falloff weight (1 at the centre)
  function brushCells(W, H, cx, cy, r) {
    const out = [];
    const R = Math.max(0.5, r);
    const x0 = Math.max(0, Math.floor(cx - R)), x1 = Math.min(W - 1, Math.ceil(cx + R));
    const y0 = Math.max(0, Math.floor(cy - R)), y1 = Math.min(H - 1, Math.ceil(cy + R));
    for (let y = y0; y <= y1; y++) {
      for (let x = x0; x <= x1; x++) {
        const d = Math.hypot(x - cx, y - cy);
        if (d > R) continue;
        const t = d / R;
        out.push([y * W + x, r < 1 ? 1 : 0.5 + 0.5 * Math.cos(Math.PI * t)]);
      }
    }
    return out;
  }
  // Bresenham: integer cells from a to b inclusive (strokes don't skip cells)
  function lineCells(x0, y0, x1, y1) {
    const out = [];
    let dx = Math.abs(x1 - x0), dy = -Math.abs(y1 - y0);
    const sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
    let err = dx + dy;
    for (let n = 0; n < 10000; n++) {
      out.push([x0, y0]);
      if (x0 === x1 && y0 === y1) break;
      const e2 = 2 * err;
      if (e2 >= dy) { err += dy; x0 += sx; }
      if (e2 <= dx) { err += dx; y0 += sy; }
    }
    return out;
  }
  function bbox(cells, W) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const [i] of cells) {
      const x = i % W, y = (i / W) | 0;
      if (x < x0) x0 = x; if (y < y0) y0 = y; if (x > x1) x1 = x; if (y > y1) y1 = y;
    }
    return [x0, y0, x1 + 1, y1 + 1];
  }

  const LAND_MIN = 0.008, SEA_MAX = -0.01;
  // a land cell that appears (raised out of the sea / painted): take a neighbour's landmass label
  function becomeLand(doc, s, i) {
    const g = doc.grids, W = doc.W;
    touch(doc, s, "land")[i] = 1;
    const half = touch(doc, s, "landmass");
    if (!half[i]) {
      let h = 1;
      const x = i % W, y = (i / W) | 0;
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1], [2, 0], [-2, 0], [0, 2], [0, -2]]) {
        const j = (y + dy) * W + (x + dx);
        if (x + dx >= 0 && x + dx < W && y + dy >= 0 && y + dy < doc.H && g.landmass[j]) { h = g.landmass[j]; break; }
      }
      half[i] = h;
    }
    const b = touch(doc, s, "biome");
    if (b[i] === 126 || b[i] === 61) b[i] = 98; // ~ or = -> beach until biomes are recomputed
  }
  function becomeSea(doc, s, i) {
    touch(doc, s, "land")[i] = 0;
    touch(doc, s, "lake")[i] = 0;
    touch(doc, s, "river")[i] = 0;
    touch(doc, s, "landmass")[i] = 0;
    touch(doc, s, "biome")[i] = 126; // ~
  }

  // raise (amt > 0) or lower (amt < 0); crossing sea level turns sea into land and back
  function brushRaise(doc, s, cells, amt) {
    const h = touch(doc, s, "height"), land = doc.grids.land, dirty = touch(doc, s, "dirty");
    let flips = 0;
    for (const [i, wt] of cells) {
      const before = h[i];
      h[i] = before + amt * wt;
      dirty[i] = 1;
      if (land[i] && h[i] < 0 && before >= 0) { becomeSea(doc, s, i); flips++; }
      else if (!doc.grids.land[i] && h[i] > 0 && before <= 0) { becomeLand(doc, s, i); flips++; }
      if (doc.grids.land[i]) h[i] = Math.max(LAND_MIN, h[i]); else h[i] = Math.min(SEA_MAX, h[i]);
    }
    return flips;
  }
  function brushFlatten(doc, s, cells, target, rate) {
    const h = touch(doc, s, "height"), land = doc.grids.land, dirty = touch(doc, s, "dirty");
    for (const [i, wt] of cells) {
      h[i] += (target - h[i]) * (rate || 0.35) * wt;
      h[i] = land[i] ? Math.max(LAND_MIN, h[i]) : Math.min(SEA_MAX, h[i]);
      dirty[i] = 1;
    }
  }
  function brushSmooth(doc, s, cells, rate) {
    const W = doc.W, H = doc.H;
    const src = doc.grids.height.slice();
    const h = touch(doc, s, "height"), land = doc.grids.land, dirty = touch(doc, s, "dirty");
    for (const [i, wt] of cells) {
      const x = i % W, y = (i / W) | 0;
      let sum = 0, n = 0;
      for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
        const xx = x + dx, yy = y + dy;
        if (xx < 0 || yy < 0 || xx >= W || yy >= H) continue;
        const j = yy * W + xx;
        if (land[j] !== land[i]) continue; // smooth within land or within sea
        sum += src[j]; n++;
      }
      h[i] += (sum / n - src[i]) * (rate || 0.5) * wt;
      h[i] = land[i] ? Math.max(LAND_MIN, h[i]) : Math.min(SEA_MAX, h[i]);
      dirty[i] = 1;
    }
  }
  // code: a biome char, or "" to clear the hand-painted override
  function paintBiome(doc, s, cells, code) {
    const land = doc.grids.land, lake = doc.grids.lake;
    const pb = touch(doc, s, "paint_biome"), b = touch(doc, s, "biome");
    let n = 0;
    for (const [i] of cells) {
      if (!land[i] || lake[i]) continue;
      if (code) { pb[i] = code.charCodeAt(0); b[i] = pb[i]; } else pb[i] = 0;
      n++;
    }
    return n;
  }
  // mode: land | sea | lake
  function paintWater(doc, s, cells, mode) {
    const pw = touch(doc, s, "paint_water"), dirty = touch(doc, s, "dirty");
    const h = touch(doc, s, "height");
    const code = { land: 1, sea: 2, lake: 3 }[mode];
    for (const [i] of cells) {
      pw[i] = code;
      dirty[i] = 1;
      if (mode === "sea") {
        becomeSea(doc, s, i);
        h[i] = Math.min(h[i], -0.05);
      } else if (mode === "land") {
        if (!doc.grids.land[i]) becomeLand(doc, s, i);
        touch(doc, s, "lake")[i] = 0;
        if (doc.grids.biome[i] === 111) touch(doc, s, "biome")[i] = 109; // o -> m
        h[i] = Math.max(h[i], 0.02);
      } else {
        if (!doc.grids.land[i]) becomeLand(doc, s, i);
        h[i] = Math.max(h[i], 0.012);
        touch(doc, s, "lake")[i] = 1;
        touch(doc, s, "river")[i] = 0;
        touch(doc, s, "biome")[i] = 111; // o
      }
    }
  }

  // ------------------------------------------------------------ base raster
  // hillshade per cell from the height grid (like render.hillshade at 1 px / cell)
  function shadeGrid(h, W, H, z) {
    z = z || 9;
    const out = new Float32Array(W * H);
    const az = (315 * Math.PI) / 180, alt = (40 * Math.PI) / 180;
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        const xl = Math.max(0, x - 1), xr = Math.min(W - 1, x + 1);
        const yu = Math.max(0, y - 1), yd = Math.min(H - 1, y + 1);
        const gx = ((h[y * W + xr] - h[y * W + xl]) / Math.max(1, xr - xl)) * z;
        const gy = ((h[yd * W + x] - h[yu * W + x]) / Math.max(1, yd - yu)) * z;
        const slope = Math.PI / 2 - Math.atan(Math.hypot(gx, gy));
        const aspect = Math.atan2(-gx, gy);
        const v = Math.sin(alt) * Math.sin(slope) + Math.cos(alt) * Math.cos(slope) * Math.cos(az - aspect);
        out[y * W + x] = Math.max(0, Math.min(1, v));
      }
    }
    return out;
  }

  // Colour of one cell before shading. layers: {height, biome, water}
  function cellColor(doc, i, layers, hmax) {
    const g = doc.grids;
    if (!g.land[i]) {
      if (!layers.water) return [40, 46, 52];
      const d = Math.max(0, Math.min(1, -g.height[i] / 0.46));
      return [86 * (1 - d) + 26 * d, 142 * (1 - d) + 50 * d, 186 * (1 - d) + 92 * d];
    }
    if (g.lake[i] && layers.water) return BIOME_RGB.o;
    if (layers.biome) {
      const c = BIOME_RGB[String.fromCharCode(g.biome[i])];
      if (c) return c;
    }
    if (layers.height) {
      const t = Math.max(0, Math.min(1, g.height[i] / hmax)) * (HYPSO.length - 1);
      const k = Math.min(HYPSO.length - 2, Math.floor(t)), f = t - k;
      return [0, 1, 2].map((c) => HYPSO[k][c] * (1 - f) + HYPSO[k + 1][c] * f);
    }
    return [150, 150, 140];
  }

  const GEOGRAPHY_DARKEST_SHADE = 0.4, GEOGRAPHY_SHADE_RANGE = 0.92;
  const GEOGRAPHY_COAST_RIM = 0.55, GEOGRAPHY_TIER_SHADOW = 0.6, GEOGRAPHY_TIER_LIGHT = 1.16;
  // the coast gets a dark rim; a tier step reads as a terrace lit from the north-west
  function geography_rim_factor(g, i, j, has_tiers, faces_the_light) {
    if (j < 0 || !g.land[i]) return 1;
    if (!g.land[j]) return GEOGRAPHY_COAST_RIM;
    if (!has_tiers || g.tiers[j] <= g.tiers[i]) return 1;
    const rim = faces_the_light ? GEOGRAPHY_TIER_LIGHT : GEOGRAPHY_TIER_SHADOW;
    return rim;
  }

  // Paint cells [x0, x1) x [y0, y1) into an RGBA buffer of (W*S) x (H*S) pixels.
  // Colour is per cell (crisp, like the server render); shading is interpolated
  // between cell centres; the coast and tier steps get a dark rim.
  function renderBase(doc, layers, S, data, rect, shade) {
    const W = doc.W, H = doc.H, g = doc.grids;
    const [x0, y0, x1, y1] = rect || [0, 0, W, H];
    shade = shade || shadeGrid(g.height, W, H, 9);
    let hmax = 1e-6;
    for (let i = 0; i < W * H; i++) if (g.land[i] && g.height[i] > hmax) hmax = g.height[i];
    const PW = W * S;
    const tiers = g.tiers, landG = g.land;
    const rims = layers.height && tiers;
    // per sub-pixel offset: which neighbour column/row and the blend weight
    const off = new Float32Array(S), side = new Int8Array(S);
    for (let p = 0; p < S; p++) {
      const f = (p + 0.5) / S - 0.5; // -0.5 .. 0.5 from the cell centre
      side[p] = f < 0 ? -1 : 1;
      off[p] = Math.abs(f);
    }
    for (let cy = Math.max(0, y0); cy < Math.min(H, y1); cy++) {
      for (let cx = Math.max(0, x0); cx < Math.min(W, x1); cx++) {
        const i = cy * W + cx;
        const col = cellColor(doc, i, layers, hmax);
        const land = landG[i];
        const wet = !land || (g.lake[i] && layers.water);
        const iL = cx > 0 ? i - 1 : -1, iR = cx < W - 1 ? i + 1 : -1;
        const iU = cy > 0 ? i - W : -1, iD = cy < H - 1 ? i + W : -1;
        const kL = geography_rim_factor(g, i, iL, rims, false), kR = geography_rim_factor(g, i, iR, rims, true);
        const kU = geography_rim_factor(g, i, iU, rims, false), kD = geography_rim_factor(g, i, iD, rims, true);
        const s0 = shade[i];
        const sL = iL >= 0 ? shade[iL] : s0, sR = iR >= 0 ? shade[iR] : s0;
        const sU = iU >= 0 ? shade[iU] : s0, sD = iD >= 0 ? shade[iD] : s0;
        for (let py = 0; py < S; py++) {
          const ty = off[py], sy = side[py] < 0 ? sU : sD;
          const jy = side[py] < 0 ? (iU >= 0 ? iU : i) : (iD >= 0 ? iD : i);
          const rowK = (py === 0 ? kU : 1) * (py === S - 1 ? kD : 1);
          let o = ((cy * S + py) * PW + cx * S) * 4;
          for (let px = 0; px < S; px++) {
            const tx = off[px];
            const sx = side[px] < 0 ? sL : sR;
            const jx = side[px] < 0 ? (iL >= 0 ? iL : i) : (iR >= 0 ? iR : i);
            const diag = shade[jy + (jx - i)] !== undefined ? shade[jy + (jx - i)] : s0;
            const sh = (s0 * (1 - tx) + sx * tx) * (1 - ty) + (sy * (1 - tx) + diag * tx) * ty;
            let k = layers.height ? (wet ? 0.92 + 0.08 * sh : GEOGRAPHY_DARKEST_SHADE + GEOGRAPHY_SHADE_RANGE * sh) : (wet ? 1 : 0.95);
            k *= rowK * (px === 0 ? kL : 1) * (px === S - 1 ? kR : 1);
            data[o] = Math.min(255, col[0] * k);
            data[o + 1] = Math.min(255, col[1] * k);
            data[o + 2] = Math.min(255, col[2] * k);
            data[o + 3] = 255;
            o += 4;
          }
        }
      }
    }
  }

  // ------------------------------------------------------------ places and routes
  function asciiId(s) {
    const map = { "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue" };
    return String(s).replace(/[äöüßÄÖÜ]/g, (c) => map[c]).replace(/[^\p{L}\p{N}]/gu, "");
  }
  function routeByName(doc, name) { return doc.routes.find((r) => r.name === name) || null; }
  function route_or_chain_map_by_name(doc, name) {
    const chain_map = doc.routes.flatMap((route) => route.chain || []).find((candidate) => candidate.name === name);
    const route_or_chain_map = chain_map || routeByName(doc, name);
    return route_or_chain_map;
  }
  function first_map_of_route(route) {
    const first_map = route.chain && route.chain.length > 1 ? route.chain[0] : route;
    return first_map;
  }
  function nodeRect(doc, name) {
    const r = routeByName(doc, name);
    if (r) return { kind: "route", rect: r.rect, route: r };
    const c = doc.cities.find((c) => c.name === name);
    return c ? { kind: "city", rect: c.rect, city: c } : null;
  }

  function covers_grid_cell(place, grid_column, grid_row) {
    const covers = (place.grid_cells || []).some((cell) => cell[0] === grid_column && cell[1] === grid_row);
    return covers;
  }
  function place_on_map_grid(doc, x, y) {
    const region_cells_per_grid_cell = (doc.map_grid || {}).cell;
    if (!region_cells_per_grid_cell) return null;
    const grid_column = Math.floor(x / region_cells_per_grid_cell), grid_row = Math.floor(y / region_cells_per_grid_cell);
    const town = doc.cities.find((c) => covers_grid_cell(c, grid_column, grid_row));
    if (town) return { kind: "city", id: town.id, obj: town };
    const route = doc.routes.find((r) => covers_grid_cell(r, grid_column, grid_row));
    if (route) return { kind: "route", id: route.name, obj: route };
    return null;
  }
  // what is under cell (x, y): a landmark marker, a town or route on the Map grid, else the smallest route rect
  function placeAt(doc, x, y, opts) {
    opts = opts || {};
    if (!opts.noLandmarks) {
      let best = null, bd = 1.6;
      for (const l of doc.landmarks) {
        const d = Math.hypot(l.x + 0.5 - x, l.y + 0.5 - y);
        if (d < bd) { bd = d; best = l; }
      }
      if (best) return { kind: "landmark", id: best.id, obj: best };
    }
    const place_on_the_grid = place_on_map_grid(doc, x, y);
    if (place_on_the_grid) return place_on_the_grid;
    for (const c of doc.cities) {
      if ((c.grid_cells || []).length) continue;
      const [x0, y0, x1, y1] = c.rect;
      if (x >= x0 && x < x1 && y >= y0 && y < y1) return { kind: "city", id: c.id, obj: c };
    }
    let best = null, area = Infinity;
    for (const r of doc.routes) {
      if ((r.grid_cells || []).length) continue;
      const [x0, y0, x1, y1] = r.rect;
      if (x >= x0 && x < x1 && y >= y0 && y < y1 && (x1 - x0) * (y1 - y0) < area) { best = r; area = (x1 - x0) * (y1 - y0); }
    }
    return best ? { kind: "route", id: best.name, obj: best } : null;
  }
  function refOf(place) {
    if (!place) return null;
    if (place.kind === "city") return { city: place.id };
    if (place.kind === "route") return { route: place.id };
    return { landmark: place.id };
  }

  function renameRoute(doc, oldName, newName) {
    newName = String(newName || "").trim().slice(0, 40);
    if (!newName || newName === oldName) return false;
    if (doc.routes.some((r) => r.name === newName) || doc.cities.some((c) => c.name === newName)) return false;
    const r = routeByName(doc, oldName);
    if (!r) return false;
    r.name = newName;
    r.id = asciiId(newName) || r.id;
    r.map = "MAP_" + r.id.toUpperCase();
    r.manual = Object.assign({}, r.manual, { name: newName });
    r.pinned = true;
    for (const o of doc.routes) for (const e of o.exits) if (e.to === oldName) { e.to = newName; e.to_map = r.map; }
    for (const l of doc.links) l.routes = (l.routes || []).map((n) => (n === oldName ? newName : n));
    for (const b of doc.story || []) if (b.at === oldName) b.at = newName;
    return true;
  }
  function renamePlace(doc, kind, id, newName) {
    newName = String(newName || "").trim().slice(0, 40);
    if (!newName) return false;
    const list = kind === "city" ? doc.cities : doc.landmarks;
    const o = list.find((p) => p.id === id);
    if (!o || o.name === newName) return false;
    if (kind === "city" && (doc.routes.some((r) => r.name === newName) || doc.cities.some((c) => c.name === newName))) return false;
    const old = o.name;
    o.name = newName;
    if (kind === "city") for (const r of doc.routes) for (const e of r.exits) if (e.to === old) e.to = newName;
    for (const b of doc.story || []) if (b.at === old) b.at = newName;
    return true;
  }


  // landmarks that travel together: Romowe carries its pillar and its gate
  const CARRY = { romowe: ["sky_pillar"], gold_feather_tower: ["brandwald"] };
  function moveLandmark(doc, id, dx, dy) {
    const l = doc.landmarks.find((l) => l.id === id);
    if (!l || (!dx && !dy)) return false;
    const ndx = Math.max(-l.x, Math.min(doc.W - 1 - l.x, dx));
    const ndy = Math.max(-l.y, Math.min(doc.H - 1 - l.y, dy));
    const ids = [id].concat(CARRY[id] || []);
    for (const lid of ids) {
      const m = doc.landmarks.find((q) => q.id === lid);
      if (!m) continue;
      m.x = m.x + ndx;
      m.y = m.y + ndy;
      m.footprint = [m.footprint[0] + ndx, m.footprint[1] + ndy, m.footprint[2] + ndx, m.footprint[3] + ndy];
    }
    if (id === "romowe" && doc.meta && doc.meta.den_gate) doc.meta.den_gate = [doc.meta.den_gate[0] + ndx, doc.meta.den_gate[1] + ndy];
    if (id === "glutberg" && doc.meta && doc.meta.volcano) doc.meta.volcano = [doc.meta.volcano[0] + ndx, doc.meta.volcano[1] + ndy];
    return !!(ndx || ndy);
  }

  function sizeOk(wc, hc) { return wc <= MAX_W && hc <= MAX_H && (wc * CELL + 15) * (hc * CELL + 14) <= MAP_BUDGET; }
  // problems a hand edit can leave behind
  function routeWarnings(doc, r) {
    const out = [];
    const wc = r.rect[2] - r.rect[0], hc = r.rect[3] - r.rect[1];
    const is_chain_of_maps = (r.grid_cells || []).length > 1;
    if (!is_chain_of_maps && !sizeOk(wc, hc)) out.push(`${wc * CELL} x ${hc * CELL} metatiles is over the GBA map budget`);
    for (const e of r.exits) {
      const nb = nodeRect(doc, e.to);
      if (!nb) { out.push(`exit ${e.side} goes to ${e.to || "nothing"}, which is gone`); continue; }
      const [dx, dy] = DELTA[e.side];
      const x = e.cell[0] + dx, y = e.cell[1] + dy;
      const [x0, y0, x1, y1] = nb.rect;
      if (!(x >= x0 && x < x1 && y >= y0 && y < y1)) out.push(`exit ${e.side} to ${e.to} isn't flush with it`);
    }
    for (const o of doc.routes) {
      if (o === r) continue;
      const a = r.rect, b = o.rect;
      const both_on_the_map_grid = !!(r.grid_cells && r.grid_cells.length && o.grid_cells && o.grid_cells.length);
      if (!both_on_the_map_grid && a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3]) out.push(`overlaps ${o.name}`);
    }
    return out;
  }
  // set (or clear, with value undefined) a spec field by hand
  function setSpecField(r, k, v) {
    r.spec_overrides = Object.assign({}, r.spec_overrides);
    if (v === undefined) delete r.spec_overrides[k]; else r.spec_overrides[k] = v;
    if (!Object.keys(r.spec_overrides).length) delete r.spec_overrides;
    r.pinned = true;
  }

  // ------------------------------------------------------------ story + route design B
  // Story (by hand, per game): doc.story = [{name, at, unlocks}], in play order; `at` is
  // a town or route name, `unlocks` the field move it hands out ("" = none).
  // Route design B: r.gate = {needs, after}: the move you need to get onto the route
  // (sea routes need surf unless set) and the beat after which it opens (an NPC moves...).
  const HMS = ["cut", "surf", "strength", "rock_smash", "waterfall", "dive"];
  function routeNeeds(r) {
    const g = r.gate || {};
    return g.needs != null ? g.needs : r.mode === "sea" ? "surf" : "";
  }
  function routeOpenAt(r, have, step) {
    const n = routeNeeds(r), after = (r.gate || {}).after;
    return (!n || have.has(n)) && !(after != null && after > step);
  }
  // town and route names you can walk to once beats 0..step are done (step -1 = new game)
  function reachableAt(doc, step) {
    const story = doc.story || [];
    const have = new Set(story.slice(0, step + 1).map((b) => b.unlocks).filter(Boolean));
    const open = new Map(doc.routes.map((r) => [r.name, routeOpenAt(r, have, step)]));
    const adj = new Map();
    const link = (a, b) => {
      if (!adj.has(a)) adj.set(a, []);
      if (!adj.has(b)) adj.set(b, []);
      adj.get(a).push(b); adj.get(b).push(a);
    };
    for (const r of doc.routes) for (const e of r.exits) if (e.to) link(r.name, e.to);
    const start = story.length ? story[0].at : (doc.cities[0] || {}).name;
    const seen = new Set();
    if (!start || open.get(start) === false) return seen;
    const todo = [start];
    seen.add(start);
    while (todo.length) {
      for (const n of adj.get(todo.pop()) || []) {
        if (seen.has(n) || open.get(n) === false) continue;
        seen.add(n);
        todo.push(n);
      }
    }
    return seen;
  }
  // swap two story beats, keeping every route gate pointed at the beat it opened after
  function moveBeat(doc, i, j) {
    const story = doc.story || [];
    if (j < 0 || j >= story.length || i < 0 || i >= story.length) return false;
    [story[i], story[j]] = [story[j], story[i]];
    for (const r of doc.routes || []) {
      const after = (r.gate || {}).after;
      if (after === i) r.gate.after = j;
      else if (after === j) r.gate.after = i;
    }
    return true;
  }
  // remove a story beat, keeping every route gate pointed at the right beat
  function removeBeat(doc, i) {
    const story = doc.story || [];
    if (i < 0 || i >= story.length) return false;
    story.splice(i, 1);
    for (const r of doc.routes || []) {
      const after = (r.gate || {}).after;
      if (after == null) continue;
      if (after === i) delete r.gate.after;
      else if (after > i) r.gate.after = after - 1;
      if (r.gate && !Object.keys(r.gate).length) delete r.gate;
    }
    return true;
  }
  // beats whose place you can't reach with what the beats before them gave you
  function storyWarnings(doc) {
    const out = [];
    (doc.story || []).forEach((b, i) => {
      if (!b.at) out.push(`beat ${i + 1} (${b.name || "?"}) has no place`);
      else if (i > 0 && !reachableAt(doc, i - 1).has(b.at)) out.push(`beat ${i + 1} (${b.name || "?"}): ${b.at} can't be reached yet`);
    });
    return out;
  }

  // the bench's saved-params blob for a full route spec (what region.js does)
  function benchParams(spec) {
    const p = {
      name: spec.name, seed: spec.seed, width: spec.size[0], height: spec.size[1],
      special_areas: (spec.special_areas || []).slice(0, 6),
      exits: (spec.exits || []).slice(0, 4).map((e) => ({ side: e.side, at: e.at, width: e.width, to: e.to })),
    };
    if (p.exits.length < 2) {
      const g = (spec.region || {}).goal;
      p.exits.push({ side: g ? g.side : "north", at: 0.5, width: 2, to: "" });
    }
    for (const [k, v] of Object.entries(spec)) {
      if (k in p || k === "size" || k === "region" || k === "species_file" || k === "endings") continue;
      if (typeof v === "number" || typeof v === "string" || typeof v === "boolean") p[k] = v;
    }
    return p;
  }


  // ------------------------------------------------------------ town-map style (Sinnoh-like) base raster
  const TOWN_MAP_RGB = {
    sea: [104, 170, 236], deep_sea: [92, 158, 228], halo: [178, 224, 250], outer_halo: [146, 200, 246], lake: [146, 206, 248], river: [120, 186, 242],
    lowland: [168, 216, 128], midland: [146, 202, 108], upland: [122, 180, 94], highland: [170, 150, 108],
    peak: [200, 186, 160], snow: [240, 242, 246], forest: [140, 200, 110], deep_forest: [120, 184, 100], sand: [228, 218, 156],
    marsh: [146, 196, 146], volcanic: [176, 140, 120],
  };
  const TOWN_MAP_BIOME = { f: "forest", F: "deep_forest", b: "sand", d: "sand", w: "marsh", v: "volcanic", S: "snow" };
  const HEIGHT_BANDS = [0.28, 0.52, 0.74, 0.9];
  const BAND_RGB = ["lowland", "midland", "upland", "highland", "peak"];
  function sea_distance_to_land(doc, limit) {
    const W = doc.W, H = doc.H, g = doc.grids;
    const distance = new Uint8Array(W * H).fill(limit + 1);
    let frontier = [];
    for (let i = 0; i < W * H; i++) if (g.land[i]) { distance[i] = 0; frontier.push(i); }
    for (let step = 1; step <= limit && frontier.length; step++) {
      const next = [];
      for (const i of frontier) {
        const x = i % W, y = (i / W) | 0;
        for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
          const nx = x + dx, ny = y + dy;
          if (nx < 0 || ny < 0 || nx >= W || ny >= H) continue;
          const j = ny * W + nx;
          if (distance[j] <= step) continue;
          distance[j] = step;
          next.push(j);
        }
      }
      frontier = next;
    }
    return distance;
  }
  function height_band(height, hmax) {
    const t = height / hmax;
    let band = 0;
    while (band < HEIGHT_BANDS.length && t >= HEIGHT_BANDS[band]) band++;
    return band;
  }
  function town_map_sea_color(distance_to_land, layers, height) {
    if (!layers.water) return TOWN_MAP_RGB.sea;
    if (distance_to_land <= 1) return TOWN_MAP_RGB.halo;
    if (distance_to_land <= 2) return TOWN_MAP_RGB.outer_halo;
    return height < -0.3 ? TOWN_MAP_RGB.deep_sea : TOWN_MAP_RGB.sea;
  }
  function town_map_cell_color(doc, i, layers, hmax, sea_distance) {
    const g = doc.grids;
    if (!g.land[i]) return town_map_sea_color(sea_distance[i], layers, g.height[i]);
    if (g.lake[i] && layers.water) return TOWN_MAP_RGB.lake;
    const tint = layers.biome && TOWN_MAP_BIOME[String.fromCharCode(g.biome[i])];
    const band = layers.height ? height_band(g.height[i], hmax) : 0;
    if (tint && (band < 3 || tint === "snow")) return TOWN_MAP_RGB[tint];
    return TOWN_MAP_RGB[BAND_RGB[band]];
  }
  function relief_factor(shade_value) {
    if (shade_value < 0.3) return 0.74;
    if (shade_value < 0.45) return 0.84;
    if (shade_value < 0.6) return 0.93;
    if (shade_value > 0.86) return 1.09;
    if (shade_value > 0.74) return 1.04;
    return 1;
  }
  function sample_grid(grid, W, H, fx, fy) {
    const x = Math.max(0, Math.min(W - 1, fx)), y = Math.max(0, Math.min(H - 1, fy));
    const x0 = Math.floor(x), y0 = Math.floor(y), x1 = Math.min(W - 1, x0 + 1), y1 = Math.min(H - 1, y0 + 1);
    const tx = x - x0, ty = y - y0;
    const top = grid[y0 * W + x0] * (1 - tx) + grid[y0 * W + x1] * tx;
    const bottom = grid[y1 * W + x0] * (1 - tx) + grid[y1 * W + x1] * tx;
    return top * (1 - ty) + bottom * ty;
  }
  // a cell's edge gets a rim where it meets the sea or another terrace: the coast line, and terrace steps
  // lit from the north-west (a step up to the north or west casts a shadow, a step up to the south or east catches light)
  const TERRACE_SHADOW = 0.72, TERRACE_LIGHT = 1.12, MINOR_TERRACE_SHADOW = 0.86, MINOR_TERRACE_LIGHT = 1.05, COAST_RIM = 0.78;
  const MINOR_TERRACES_PER_BAND = 2;
  function terrace_level(height, hmax) {
    const t = height / hmax;
    let band = height_band(height, hmax);
    const band_floor = band === 0 ? 0 : HEIGHT_BANDS[band - 1];
    const band_top = band < HEIGHT_BANDS.length ? HEIGHT_BANDS[band] : 1;
    const minor_step = Math.min(MINOR_TERRACES_PER_BAND - 1, Math.floor(((t - band_floor) / Math.max(1e-6, band_top - band_floor)) * MINOR_TERRACES_PER_BAND));
    const level = band * MINOR_TERRACES_PER_BAND + Math.max(0, minor_step);
    return level;
  }
  function rim_factor(doc, i, j, layers, hmax, faces_the_light) {
    const g = doc.grids;
    if (j < 0 || !g.land[i]) return 1;
    if (!g.land[j]) return COAST_RIM;
    if (!layers.height || (g.lake[j] && layers.water)) return 1;
    const own_level = terrace_level(g.height[i], hmax), neighbour_level = terrace_level(g.height[j], hmax);
    if (neighbour_level <= own_level) return 1;
    const is_band_step = height_band(g.height[j], hmax) > height_band(g.height[i], hmax);
    if (faces_the_light) return is_band_step ? TERRACE_LIGHT : MINOR_TERRACE_LIGHT;
    const shadow = is_band_step ? TERRACE_SHADOW : MINOR_TERRACE_SHADOW;
    return shadow;
  }

  // ------------------------------------------------------------ town-map rivers: water bands on the grid, width from flow
  const RIVER_NARROWEST_HALF_WIDTH = 0.17, RIVER_WIDEST_HALF_WIDTH = 0.62, RIVER_BANK_WIDTH = 0.11;
  function river_flow_range(doc) {
    const g = doc.grids;
    let smallest_flow = Infinity, largest_flow = 0;
    for (let i = 0; i < g.river.length; i++) {
      if (!g.river[i]) continue;
      smallest_flow = Math.min(smallest_flow, g.flow[i]);
      largest_flow = Math.max(largest_flow, g.flow[i]);
    }
    const flow_range = { smallest_flow: Math.max(1e-6, smallest_flow), largest_flow: Math.max(smallest_flow * 1.0001, largest_flow) };
    return flow_range;
  }
  function river_half_width(flow, flow_range) {
    const share = Math.log(Math.max(flow, flow_range.smallest_flow) / flow_range.smallest_flow) / Math.log(flow_range.largest_flow / flow_range.smallest_flow);
    const half_width = RIVER_NARROWEST_HALF_WIDTH + (RIVER_WIDEST_HALF_WIDTH - RIVER_NARROWEST_HALF_WIDTH) * Math.sqrt(Math.max(0, Math.min(1, share)));
    return half_width;
  }
  function river_segment_of_cell(doc, i, flow_range) {
    const g = doc.grids, W = doc.W;
    const x = i % W, y = (i / W) | 0;
    const half_width = river_half_width(g.flow[i], flow_range);
    const receiver = g.recv ? g.recv[i] : -1;
    const runs_on = receiver >= 0 && (g.river[receiver] || g.lake[receiver] || !g.land[receiver]) && Math.abs((receiver % W) - x) <= 1;
    const end = runs_on ? [receiver % W + 0.5, ((receiver / W) | 0) + 0.5] : [x + 0.5, y + 0.5];
    const segment = { x0: x + 0.5, y0: y + 0.5, x1: end[0], y1: end[1], half_width };
    return segment;
  }
  // per cell, the river segments that can reach into it (its own, its neighbours')
  function river_segments_by_cell(doc) {
    const W = doc.W, H = doc.H, g = doc.grids;
    const by_cell = new Array(W * H);
    if (!g.river || !g.flow) return by_cell;
    const flow_range = river_flow_range(doc);
    for (let i = 0; i < W * H; i++) {
      if (!g.river[i]) continue;
      const segment = river_segment_of_cell(doc, i, flow_range);
      const reach = segment.half_width + RIVER_BANK_WIDTH;
      const column_from = Math.max(0, Math.floor(Math.min(segment.x0, segment.x1) - reach)), column_to = Math.min(W - 1, Math.floor(Math.max(segment.x0, segment.x1) + reach));
      const row_from = Math.max(0, Math.floor(Math.min(segment.y0, segment.y1) - reach)), row_to = Math.min(H - 1, Math.floor(Math.max(segment.y0, segment.y1) + reach));
      for (let row = row_from; row <= row_to; row++) {
        for (let column = column_from; column <= column_to; column++) (by_cell[row * W + column] = by_cell[row * W + column] || []).push(segment);
      }
    }
    return by_cell;
  }
  function distance_outside_river(segments, px, py) {
    let nearest = Infinity;
    for (const segment of segments) {
      const dx = segment.x1 - segment.x0, dy = segment.y1 - segment.y0;
      const length_squared = dx * dx + dy * dy;
      const t = length_squared > 0 ? Math.max(0, Math.min(1, ((px - segment.x0) * dx + (py - segment.y0) * dy) / length_squared)) : 0;
      const distance = Math.hypot(px - (segment.x0 + t * dx), py - (segment.y0 + t * dy)) - segment.half_width;
      if (distance < nearest) nearest = distance;
    }
    return nearest;
  }

  function renderTownMap(doc, layers, S, data, rect, shade, sea_distance, river_segments) {
    const W = doc.W, H = doc.H, g = doc.grids;
    const [x0, y0, x1, y1] = rect || [0, 0, W, H];
    sea_distance = sea_distance || sea_distance_to_land(doc, 3);
    shade = shade || shadeGrid(g.height, W, H, 9);
    river_segments = layers.water ? river_segments || river_segments_by_cell(doc) : [];
    let hmax = 1e-6;
    for (let i = 0; i < W * H; i++) if (g.land[i] && g.height[i] > hmax) hmax = g.height[i];
    const PW = W * S;
    for (let cy = Math.max(0, y0); cy < Math.min(H, y1); cy++) {
      for (let cx = Math.max(0, x0); cx < Math.min(W, x1); cx++) {
        const i = cy * W + cx;
        const col = town_map_cell_color(doc, i, layers, hmax, sea_distance);
        const is_dry = g.land[i] && !(g.lake[i] && layers.water);
        const rims = [[cx > 0 ? i - 1 : -1, false], [cx < W - 1 ? i + 1 : -1, true], [cy > 0 ? i - W : -1, false], [cy < H - 1 ? i + W : -1, true]]
          .map(([j, faces_the_light]) => rim_factor(doc, i, j, layers, hmax, faces_the_light));
        const segments = is_dry ? river_segments[i] : null;
        for (let py = 0; py < S; py++) {
          let o = ((cy * S + py) * PW + cx * S) * 4;
          for (let px = 0; px < S; px++) {
            let pixel = col;
            let k = is_dry && layers.height ? relief_factor(sample_grid(shade, W, H, cx + (px + 0.5) / S - 0.5, cy + (py + 0.5) / S - 0.5)) : 1;
            if (px === 0) k *= rims[0];
            if (px === S - 1) k *= rims[1];
            if (py === 0) k *= rims[2];
            if (py === S - 1) k *= rims[3];
            if (segments) {
              const outside = distance_outside_river(segments, cx + (px + 0.5) / S, cy + (py + 0.5) / S);
              if (outside <= 0) { pixel = TOWN_MAP_RGB.river; k = 1; }
              else if (outside <= RIVER_BANK_WIDTH) k = Math.min(k, COAST_RIM);
            }
            data[o] = Math.min(255, pixel[0] * k);
            data[o + 1] = Math.min(255, pixel[1] * k);
            data[o + 2] = Math.min(255, pixel[2] * k);
            data[o + 3] = 255;
            o += 4;
          }
        }
      }
    }
  }

  const api = {
    STAGES, STALE_BY, BIOME_NAMES, PAINT_BIOMES, BIOME_RGB, ROLE_RGB, ROLES, SPEC_BIOMES, SIDES, DELTA, OPP, CELL,
    decodeGrid, encodeGrid, decodeDoc, encodeDoc, b64ToBytes, bytesToB64,
    History, snapshot, restore, touch, markStale, stagesFor,
    brushCells, lineCells, bbox, brushRaise, brushFlatten, brushSmooth, paintBiome, paintWater,
    shadeGrid, cellColor, renderBase,
    asciiId, routeByName, route_or_chain_map_by_name, first_map_of_route, nodeRect, placeAt, refOf, renameRoute, renamePlace, moveLandmark,
    sizeOk, routeWarnings,
    setSpecField, benchParams, HMS, routeNeeds, reachableAt, storyWarnings, moveBeat, removeBeat,
    TOWN_MAP_RGB, sea_distance_to_land, town_map_cell_color, renderTownMap, river_segments_by_cell, river_half_width, terrace_level,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ED = api;
})(typeof window !== "undefined" ? window : globalThis);
