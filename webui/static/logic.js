// Pure helpers shared by bench.js and the node tests (no DOM in here).
(function (root) {
  "use strict";

  const ZOOMS = [0.25, 0.5, 1, 2, 3, 4, 5, 6, 8];
  const PATH_KINDS = ["main", "branch", "loop", "dead_end", "hidden", "trail"];
  const PATH_COLORS = {
    main: "#e8b84a", branch: "#5fb4e6", loop: "#e77fb3", dead_end: "#a98be0",
    hidden: "#e5544b", trail: "#f3efe4",
  };
  const CLASS_COLORS = {
    g: "#78be5a", p: "#d6b478", t: "#28823a", w: "#4678dc", f: "#14461e",
    r: "#825a3c", s: "#e67828", e: "#e6a028", x: "#e6a028", a: "#a0d2ff", o: "#c800c8", q: "#aa783c",
    j: "#e6b428", d: "#f0dc8c", b: "#1e3caa", h: "#82bee6", y: "#969670", v: "#beb9a0", k: "#5a321e",
    z: "#e6e6e6", l: "#f03c00", n: "#141414", u: "#6e645a", T: "#a0782a", F: "#b4641e", R: "#8c2323", c: "#966946",
  };
  const CLASS_NAMES = {
    g: "grass", p: "dirt road", t: "tall grass", w: "water", f: "trees",
    r: "rock", s: "ledge (south)", e: "ledge (east)", x: "ledge (west)", a: "waterfall", o: "object", q: "bridge (wood)",
    j: "ledge (north)", d: "sand", b: "sea", h: "shallow water", y: "ash grass", v: "ash ground", k: "cliff",
    z: "stairs", l: "lava / hot springs", n: "cave entrance", u: "boulder",
    T: "tall grass (autumn)", F: "forest (orange)", R: "forest (red)", c: "fence",
  };

  // Largest zoom level whose map fits the viewport (falls back to the smallest).
  function fitZoom(mapW, mapH, viewW, viewH, pad) {
    pad = pad == null ? 24 : pad;
    let best = ZOOMS[0];
    for (const z of ZOOMS) if (mapW * z <= viewW - pad * 2 && mapH * z <= viewH - pad * 2) best = z;
    return best;
  }

  // Step through ZOOMS; dir > 0 zooms in.
  function stepZoom(z, dir) {
    let i = ZOOMS.indexOf(z);
    if (i < 0) i = ZOOMS.findIndex((v) => v >= z);
    if (i < 0) i = ZOOMS.length - 1;
    return ZOOMS[Math.max(0, Math.min(ZOOMS.length - 1, i + (dir > 0 ? 1 : -1)))];
  }

  // Keep the map point under (sx, sy) fixed while changing zoom.
  function zoomAt(view, newZoom, sx, sy) {
    const mx = (sx - view.ox) / view.zoom;
    const my = (sy - view.oy) / view.zoom;
    return { zoom: newZoom, ox: Math.round(sx - mx * newZoom), oy: Math.round(sy - my * newZoom) };
  }

  // Screen point -> map cell {x, y} (may be outside the map).
  function cellAt(view, sx, sy, cell) {
    cell = cell || 16;
    return {
      x: Math.floor((sx - view.ox) / (view.zoom * cell)),
      y: Math.floor((sy - view.oy) / (view.zoom * cell)),
    };
  }

  // Zones / events present at a stage (generation appends them in order).
  function zonesAt(result, stageIdx) {
    const st = result.stages[stageIdx];
    return result.zones.slice(0, st ? st.zones : result.zones.length);
  }
  function eventsAt(result, stageIdx) {
    const st = result.stages[stageIdx];
    return result.events.slice(0, st ? st.events : result.events.length);
  }

  // Special area whose bbox ([x0, y0, x1, y1), exclusive) contains the cell.
  function specialAt(zones, x, y) {
    for (let i = zones.length - 1; i >= 0; i--) {
      const z = zones[i];
      if (z.kind !== "special" || !z.bbox) continue;
      const [x0, y0, x1, y1] = z.bbox;
      if (x >= x0 && x < x1 && y >= y0 && y < y1) return z;
    }
    return null;
  }

  // Path cells split into polylines wherever consecutive cells are not 4-neighbours
  // (zones are concatenated segments, so a straight polyline would draw jumps).
  function polylines(cells) {
    const out = [];
    let cur = [];
    for (let i = 0; i < cells.length; i++) {
      const c = cells[i];
      const p = cur[cur.length - 1];
      if (p && Math.abs(p[0] - c[0]) + Math.abs(p[1] - c[1]) > 1) {
        if (cur.length > 1) out.push(cur);
        cur = [];
      }
      if (!p || p[0] !== c[0] || p[1] !== c[1]) cur.push(c);
    }
    if (cur.length > 1) out.push(cur);
    return out;
  }

  // Wild table rows with percentages from weights.
  function encounterRows(table) {
    const rows = (table || []).slice();
    const tot = rows.reduce((s, r) => s + (r.weight || 0), 0) || 1;
    return rows
      .map((r) => ({ ...r, pct: (100 * (r.weight || 0)) / tot }))
      .sort((a, b) => b.pct - a.pct || a.name.localeCompare(b.name));
  }

  // Emerald slot table (12 land slots) folded by species.
  function slotRows(entry, rates) {
    if (!entry || !entry.mons) return [];
    const by = new Map();
    entry.mons.forEach((m, i) => {
      const k = m.species;
      const r = by.get(k) || { species: k, pct: 0, min: m.min_level, max: m.max_level };
      r.pct += rates[i] || 0;
      r.min = Math.min(r.min, m.min_level);
      r.max = Math.max(r.max, m.max_level);
      by.set(k, r);
    });
    return [...by.values()].sort((a, b) => b.pct - a.pct);
  }

  function prettySpecies(constName) {
    return String(constName || "").replace(/^SPECIES_/, "").toLowerCase()
      .split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
  }

  function gbaSizeOk(w, h) { return (w + 15) * (h + 14) <= 10240; }

  const api = {
    ZOOMS, PATH_KINDS, PATH_COLORS, CLASS_COLORS, CLASS_NAMES,
    fitZoom, stepZoom, zoomAt, cellAt, zonesAt, eventsAt, specialAt, polylines,
    encounterRows, slotRows, prettySpecies, gbaSizeOk,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PR = api;
})(typeof window !== "undefined" ? window : globalThis);
