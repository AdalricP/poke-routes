/* Bench "Paint" mode: fix a generated route by hand, cell by cell. Painted cells
   are semantic classes (tree, grass, path, ...); the server re-tiles them with the
   same tiler (POST /api/retile), so the export zip carries the painted map.
   Paint is kept per route name + size in localStorage and put back on every
   regenerate (new seeds included) until cleared. Needs window.PRBench (app.js). */
(function () {
  "use strict";
  const B = window.PRBench, PR = window.PR;
  if (!B || !PR) return;
  const KEY = "pokeroutes-paint-v1";
  const CLASSES = [["f", "tree"], ["g", "grass"], ["p", "path"], ["t", "tall grass"], ["w", "water"],
    ["r", "rock"], ["s", "ledge S"], ["e", "ledge E"], ["x", "ledge W"], ["a", "waterfall"]];
  const st = { on: false, cls: "f", size: 1, painting: false, lastId: null, pending: false };
  const wrap = document.getElementById("mapwrap");

  // ------------------------------------------------------------ storage (pure-ish)
  function loadAll() { try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { return {}; } }
  function saveAll(all) { try { localStorage.setItem(KEY, JSON.stringify(all)); } catch (e) { /* storage full or blocked */ } }
  function keyOf(r) { return `${r.name}|${r.width}x${r.height}`; }
  function paintOf(r) { return (r && loadAll()[keyOf(r)]) || {}; }
  function setPaint(r, cells) {
    const all = loadAll();
    if (Object.keys(cells).length) all[keyOf(r)] = cells; else delete all[keyOf(r)];
    saveAll(all);
  }
  function cellList(cells) { return Object.entries(cells).map(([k, c]) => { const [x, y] = k.split(",").map(Number); return [x, y, c]; }); }

  // ------------------------------------------------------------ UI
  const bar = document.querySelector("#mapwrap .toolbar");
  const seg = document.createElement("div");
  seg.className = "seg";
  seg.setAttribute("role", "group");
  seg.setAttribute("aria-label", "Paint");
  seg.innerHTML = '<button type="button" class="tog" id="paintTog" title="paint classes on the finished route (P)">Paint</button>';
  bar.appendChild(seg);
  const pal = document.createElement("div");
  pal.className = "seg paintpal";
  pal.style.display = "none";
  pal.innerHTML = CLASSES.map(([c, n]) =>
    `<button type="button" data-pc="${c}" title="${n}"><i style="display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px;background:${PR.CLASS_COLORS[c] || "#888"}"></i>${n}</button>`).join("") +
    '<button type="button" data-ps="1" title="brush 1 cell">1</button><button type="button" data-ps="3" title="brush 3x3">3</button>' +
    '<button type="button" id="paintClear" title="forget every painted cell on this route">clear</button>' +
    '<span id="paintN" style="min-width:0;padding:0 6px"></span>';
  bar.appendChild(pal);
  const tog = document.getElementById("paintTog");

  function refreshUI() {
    tog.classList.toggle("on", st.on);
    pal.style.display = st.on ? "" : "none";
    pal.querySelectorAll("[data-pc]").forEach((b) => b.classList.toggle("on", b.dataset.pc === st.cls));
    pal.querySelectorAll("[data-ps]").forEach((b) => b.classList.toggle("on", +b.dataset.ps === st.size));
    const n = Object.keys(paintOf(B.state.result)).length;
    document.getElementById("paintN").textContent = n ? `${n} painted` : "";
    wrap.style.cursor = st.on ? "crosshair" : "";
  }
  function setOn(on) {
    const r = B.state.result;
    st.on = on && !!r;
    if (st.on) { B.stopPlay(); B.setStage(r.stages.length - 1, false); }
    refreshUI();
  }
  tog.onclick = () => setOn(!st.on);
  pal.addEventListener("click", (ev) => {
    const b = ev.target.closest("button");
    if (!b) return;
    if (b.dataset.pc) st.cls = b.dataset.pc;
    if (b.dataset.ps) st.size = +b.dataset.ps;
    if (b.id === "paintClear") clearPaint();
    refreshUI();
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.target && ev.target.closest && ev.target.closest("input, select, textarea")) return;
    if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
    if (ev.key === "p" || ev.key === "P") setOn(!st.on);
  });

  // ------------------------------------------------------------ painting
  function cellAt(ev) {
    const box = wrap.getBoundingClientRect();
    return PR.cellAt(B.state.view, ev.clientX - box.left, ev.clientY - box.top, B.CELL);
  }
  // cells on the straight line from the last dab, so quick strokes leave no gaps
  function line(a, b) {
    const out = [];
    const n = Math.max(Math.abs(b.x - a.x), Math.abs(b.y - a.y));
    for (let k = 0; k <= n; k++) out.push({ x: Math.round(a.x + ((b.x - a.x) * k) / (n || 1)), y: Math.round(a.y + ((b.y - a.y) * k) / (n || 1)) });
    return out;
  }
  function dab(ev) {
    const c = cellAt(ev);
    const pts = st.last ? line(st.last, c).slice(1) : [c];
    st.last = c;
    for (const p of pts) paintCell(p);
  }
  function paintCell(c) {
    const r = B.state.result;
    const cells = paintOf(r);
    const last = r.stages[r.stages.length - 1];
    const h = st.size === 3 ? 1 : 0;
    let changed = false;
    for (let y = c.y - h; y <= c.y + h; y++) {
      for (let x = c.x - h; x <= c.x + h; x++) {
        if (x < 0 || y < 0 || x >= r.width || y >= r.height) continue;
        if (cells[`${x},${y}`] === st.cls) continue;
        cells[`${x},${y}`] = st.cls;
        const row = last.cls[y];
        last.cls[y] = row.slice(0, x) + st.cls + row.slice(x + 1);
        // show the class colour right away; the tiles follow from the server
        const g = B.state.tileCanvases[r.stages.length - 1].getContext("2d");
        g.fillStyle = PR.CLASS_COLORS[st.cls] || "#888";
        g.globalAlpha = 0.75;
        g.fillRect(x * B.CELL, y * B.CELL, B.CELL, B.CELL);
        g.globalAlpha = 1;
        changed = true;
      }
    }
    if (changed) { setPaint(r, cells); B.draw(); }
  }
  wrap.addEventListener("pointerdown", (ev) => {
    if (!st.on || !B.state.result || ev.target.closest(".toolbar") || ev.button !== 0) return;
    ev.stopPropagation();
    st.painting = true;
    st.last = null;
    wrap.setPointerCapture(ev.pointerId);
    dab(ev);
  }, true);
  wrap.addEventListener("pointermove", (ev) => {
    if (!st.on || !st.painting) return;
    ev.stopPropagation();
    dab(ev);
  }, true);
  const end = (ev) => {
    if (!st.painting) return;
    ev.stopPropagation();
    st.painting = false;
    retile();
  };
  wrap.addEventListener("pointerup", end, true);
  wrap.addEventListener("pointercancel", end, true);

  // ------------------------------------------------------------ server
  async function retile() {
    const r = B.state.result;
    if (!r) return;
    const cells = cellList(paintOf(r));
    if (!cells.length) return;
    if (st.pending) { st.again = true; return; }
    st.pending = true;
    try {
      const res = await B.fetchJSON("/api/retile", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: r.id, cells }),
      });
      if (B.state.result !== r) return; // a new route arrived meanwhile
      const last = r.stages[r.stages.length - 1];
      last.cls = res.cls;
      last.tiles = res.tiles;
      B.buildCanvases();
      B.draw();
    } catch (e) {
      const bt = document.getElementById("busyText");
      if (bt) { document.getElementById("busy").hidden = false; bt.textContent = "paint: " + e.message; setTimeout(() => { document.getElementById("busy").hidden = true; }, 5000); }
    } finally {
      st.pending = false;
      refreshUI();
      if (st.again) { st.again = false; retile(); }
    }
  }
  async function clearPaint() {
    const r = B.state.result;
    if (!r) return;
    setPaint(r, {});
    refreshUI();
    // regenerate to get the unpainted route back
    document.getElementById("generate").click();
  }
  // every new route: put the saved paint back
  setInterval(() => {
    const r = B.state.result;
    if (!r || r.id === st.lastId) return;
    st.lastId = r.id;
    refreshUI();
    if (Object.keys(paintOf(r)).length) retile();
  }, 300);
  refreshUI();
})();
