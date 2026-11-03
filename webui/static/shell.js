/* Editor shell: menu bar, docked panels + splitters, console, status bar, dialogs and
   keyboard shortcut dispatch. The route bench (bench.js) supplies the menu model and
   the actions; the pure parts live in shell-logic.js (window.SL). */
(function () {
  "use strict";
  const SL = window.SL;
  const $ = (id) => document.getElementById(id);
  const mac = SL.isMacPlatform(navigator);
  const store = (() => { try { return window.localStorage; } catch (e) { return null; } })();

  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };

  // ------------------------------------------------------------ layout
  let layout = SL.loadLayout(store);
  const resizeCbs = [];
  const centerCbs = [];
  const bottomCbs = [];
  const docks = { left: $("dockLeft"), center: $("dockCenter"), right: $("dockRight"), bottom: $("dockBottom") };
  const parts = { menu: $("menubar"), toolbar: $("toolbar"), status: $("statusbar"),
    splitLeft: $("splitLeft"), splitRight: $("splitRight"), splitBottom: $("splitBottom") };

  function place(node, r) {
    node.style.left = r.x + "px";
    node.style.top = r.y + "px";
    node.style.width = r.w + "px";
    node.style.height = r.h + "px";
  }

  let lastRects = null;
  function applyLayout() {
    const rects = SL.computeRects(layout, window.innerWidth, window.innerHeight);
    lastRects = rects;
    place(parts.menu, rects.menu);
    place(parts.toolbar, rects.toolbar);
    place(parts.status, rects.status);
    for (const k of ["left", "center", "right", "bottom"]) place(docks[k], rects[k]);
    place(parts.splitLeft, rects.splitLeft);
    place(parts.splitRight, rects.splitRight);
    place(parts.splitBottom, rects.splitBottom);
    for (const k of ["left", "right", "bottom"]) {
      docks[k].classList.toggle("collapsed", rects.collapsed[k]);
      docks[k].classList.toggle("floating", rects.floating[k]);
    }
    parts.splitLeft.classList.toggle("off", rects.collapsed.left || rects.floating.left);
    parts.splitRight.classList.toggle("off", rects.collapsed.right || rects.floating.right);
    parts.splitBottom.classList.toggle("off", rects.collapsed.bottom || rects.floating.bottom);
    // tabs
    document.querySelectorAll("[data-center]").forEach((b) => {
      const on = b.dataset.center === layout.center;
      b.classList.toggle("on", on);
      b.setAttribute("aria-selected", on);
    });
    document.querySelectorAll("[data-bottom]").forEach((b) => {
      const on = b.dataset.bottom === layout.bottom.tab;
      b.classList.toggle("on", on);
      b.setAttribute("aria-selected", on);
    });
    $("paneConsole").hidden = layout.bottom.tab !== "console";
    $("paneTimeline").hidden = layout.bottom.tab !== "timeline";
    $("paneImprove").hidden = layout.bottom.tab !== "improve";
    $("paneCommits").hidden = layout.bottom.tab !== "commits";
    $("paneAgent").hidden = layout.bottom.tab !== "agent";
    resizeCbs.forEach((f) => f(rects));
  }

  function saveLayout() { SL.saveLayout(store, layout); }

  function setCenter(tab) {
    if (!SL.CENTER_TABS.includes(tab)) return;
    const changed = layout.center !== tab;
    layout.center = tab;
    applyLayout();
    saveLayout();
    if (changed) centerCbs.forEach((f) => f(tab));
  }
  function setBottom(tab, reveal) {
    if (!SL.BOTTOM_TABS.includes(tab)) return;
    layout.bottom.tab = tab;
    if (reveal !== false) layout.bottom.open = true;
    applyLayout();
    saveLayout();
    if (tab === "console") seenProblems = problems();
    updateConsoleBadge();
    bottomCbs.forEach((f) => f(tab));
  }
  function toggleDock(which, open) {
    const d = layout[which];
    d.open = open == null ? !d.open : !!open;
    if (!d.open) d.float = null; // a closed window goes back to its strip in the dock
    applyLayout();
    saveLayout();
  }
  function resetLayout() {
    layout = SL.defaultLayout();
    applyFolds();
    applyLayout();
    saveLayout();
    log("info", "Layout reset to the default.");
  }

  // splitters
  for (const sp of [parts.splitLeft, parts.splitRight, parts.splitBottom]) {
    const which = sp.dataset.split;
    sp.addEventListener("pointerdown", (ev) => {
      if (ev.button !== 0) return;
      ev.preventDefault();
      const r = lastRects[which];
      const start = which === "bottom" ? r.h : r.w;
      const x0 = ev.clientX, y0 = ev.clientY;
      sp.setPointerCapture(ev.pointerId);
      sp.classList.add("drag");
      document.body.classList.add("resizing", which === "bottom" ? "h" : "v");
      const move = (e) => {
        layout[which].size = SL.dragSize(which, start, e.clientX - x0, e.clientY - y0);
        applyLayout();
      };
      const up = () => {
        sp.removeEventListener("pointermove", move);
        sp.removeEventListener("pointerup", up);
        sp.removeEventListener("pointercancel", up);
        sp.classList.remove("drag");
        document.body.classList.remove("resizing", "h", "v");
        // store what is actually shown (computeRects clamps)
        const rr = lastRects[which];
        layout[which].size = which === "bottom" ? rr.h : rr.w;
        saveLayout();
      };
      sp.addEventListener("pointermove", move);
      sp.addEventListener("pointerup", up);
      sp.addEventListener("pointercancel", up);
    });
    sp.addEventListener("keydown", (ev) => {
      const step = ev.shiftKey ? 64 : 16;
      const r = lastRects[which];
      const cur = which === "bottom" ? r.h : r.w;
      let d = 0;
      if (which === "bottom") { if (ev.key === "ArrowUp") d = step; else if (ev.key === "ArrowDown") d = -step; }
      else if (ev.key === "ArrowLeft") d = which === "left" ? -step : step;
      else if (ev.key === "ArrowRight") d = which === "left" ? step : -step;
      if (!d) return;
      ev.preventDefault();
      layout[which].size = cur + d;
      applyLayout();
      saveLayout();
    });
    sp.addEventListener("dblclick", () => toggleDock(which, false));
  }
  // floating windows: drag a dock's tab strip to tear it off and move it (Unity-style);
  // drop it on its home edge, double-click the strip or press its arrow to dock it again
  function redock(which) {
    layout[which].float = null;
    layout[which].open = true;
    applyLayout();
    saveLayout();
  }
  let floatZ = 20;
  const dropHint = el("div", "dock-drop");
  dropHint.hidden = true;
  $("shell").append(dropHint);
  function homeRect(which) {
    const docked = Object.assign({}, layout, { [which]: Object.assign({}, layout[which], { float: null, open: true }) });
    return SL.computeRects(docked, window.innerWidth, window.innerHeight)[which];
  }
  for (const which of ["left", "right", "bottom"]) {
    const dock = docks[which];
    const head = dock.querySelector(".dock-head");
    head.addEventListener("pointerdown", (ev) => {
      if (ev.button !== 0 || ev.target.closest(".dock-collapse, input, select")) return;
      const x0 = ev.clientX, y0 = ev.clientY;
      let grab = null;
      dock.style.zIndex = ++floatZ; // the window you grab comes to the front
      const move = (e) => {
        if (!grab) {
          if (Math.hypot(e.clientX - x0, e.clientY - y0) < 6) return;
          const r = lastRects[which];
          const size = layout[which].float ? r : homeRect(which);
          const w = layout[which].float ? r.w : Math.min(size.w, 560), h = layout[which].float ? r.h : Math.min(size.h, 420);
          grab = { dx: Math.min(x0 - r.x, w - 40), dy: y0 - r.y, w, h };
          document.body.classList.add("moving");
        }
        layout[which].open = true;
        layout[which].float = { x: e.clientX - grab.dx, y: e.clientY - grab.dy, w: grab.w, h: grab.h };
        const snap = SL.nearHomeEdge(which, e.clientX, e.clientY, window.innerWidth, window.innerHeight);
        dropHint.hidden = !snap;
        if (snap) place(dropHint, homeRect(which));
        applyLayout();
      };
      const up = (e) => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        window.removeEventListener("pointercancel", up);
        if (!grab) return;
        document.body.classList.remove("moving");
        dropHint.hidden = true;
        // the release would otherwise click the tab under the pointer
        const swallow = (c) => { c.stopPropagation(); c.preventDefault(); };
        window.addEventListener("click", swallow, true);
        setTimeout(() => window.removeEventListener("click", swallow, true), 0);
        if (SL.nearHomeEdge(which, e.clientX, e.clientY, window.innerWidth, window.innerHeight)) { redock(which); return; }
        const rr = lastRects[which];
        layout[which].float = { x: rr.x, y: rr.y, w: rr.w, h: rr.h };
        saveLayout();
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
      window.addEventListener("pointercancel", up);
    });
    head.addEventListener("dblclick", (ev) => {
      if (layout[which].float && !ev.target.closest(".dock-collapse")) redock(which);
    });
    // floating windows resize from their corner grip (CSS resize); keep the new size
    new ResizeObserver(() => {
      const f = layout[which].float;
      if (!f || (dock.offsetWidth === f.w && dock.offsetHeight === f.h)) return;
      f.w = dock.offsetWidth;
      f.h = dock.offsetHeight;
      applyLayout();
      saveLayout();
    }).observe(dock);
  }

  // pinned panels: another module (e.g. improve.js) offers a panel via registerPanel();
  // dragging its header onto Hierarchy or Inspector parks it there, beneath that dock's
  // own content, until it's dragged back out or its "Unpin" button is clicked
  const pinHosts = { left: $("dockLeftExtra"), right: $("dockRightExtra") };
  const panelRegistry = new Map();
  function pinPanel(side, key) {
    const entry = panelRegistry.get(key);
    if (!entry) return;
    for (const s of ["left", "right"]) if (layout.pins[s] === key && s !== side) unpinPanel(s);
    if (layout.pins[side] && layout.pins[side] !== key) unpinPanel(side);
    const host = pinHosts[side];
    host.innerHTML = "";
    const head = el("div", "dock-extra-head");
    head.append(el("span", "", entry.label));
    const un = el("button", "dock-extra-unpin", "Unpin");
    un.type = "button";
    un.title = "Send back to where it came from";
    un.onclick = () => unpinPanel(side);
    head.append(un);
    host.append(head, entry.node);
    host.hidden = false;
    layout.pins[side] = key;
    saveLayout();
  }
  function unpinPanel(side) {
    const key = layout.pins[side];
    const entry = key && panelRegistry.get(key);
    if (entry) entry.parent.insertBefore(entry.node, entry.parent.contains(entry.next) ? entry.next : null);
    pinHosts[side].innerHTML = "";
    pinHosts[side].hidden = true;
    layout.pins[side] = null;
    saveLayout();
  }
  function registerPanel(key, node, label) {
    panelRegistry.set(key, { node, label, parent: node.parentNode, next: node.nextSibling });
    for (const side of ["left", "right"]) if (layout.pins[side] === key) pinPanel(side, key);
  }
  // for a panel's owner to call before moving its node itself (e.g. reordering it among
  // siblings): a no-op unless that panel is currently pinned somewhere
  function unpinByKey(key) {
    for (const side of ["left", "right"]) if (layout.pins[side] === key) unpinPanel(side);
  }
  for (const side of ["left", "right"]) {
    const zone = docks[side];
    zone.addEventListener("dragover", (ev) => {
      if (!ev.dataTransfer.types.includes("application/x-pin-panel")) return;
      ev.preventDefault();
      zone.classList.add("drop-target");
    });
    zone.addEventListener("dragleave", () => zone.classList.remove("drop-target"));
    zone.addEventListener("drop", (ev) => {
      if (!ev.dataTransfer.types.includes("application/x-pin-panel")) return;
      ev.preventDefault();
      zone.classList.remove("drop-target");
      const key = ev.dataTransfer.getData("application/x-pin-panel");
      if (key) pinPanel(side, key);
    });
  }
  window.addEventListener("dragend", () => { docks.left.classList.remove("drop-target"); docks.right.classList.remove("drop-target"); });

  document.querySelectorAll("[data-collapse]").forEach((b) => {
    b.onclick = () => (layout[b.dataset.collapse].float ? redock(b.dataset.collapse) : toggleDock(b.dataset.collapse));
  });
  document.querySelectorAll("[data-expand]").forEach((b) => { b.onclick = () => toggleDock(b.dataset.expand, true); });
  document.querySelectorAll("[data-center]").forEach((b) => { b.onclick = () => setCenter(b.dataset.center); });
  document.querySelectorAll("[data-bottom]").forEach((b) => { b.onclick = () => setBottom(b.dataset.bottom); });
  window.addEventListener("resize", applyLayout);

  // inspector foldouts remember their state
  function applyFolds() {
    document.querySelectorAll("details[data-fold]").forEach((d) => {
      const c = layout.inspector[d.dataset.fold];
      if (c != null) d.open = !c;
    });
  }
  document.querySelectorAll("details[data-fold]").forEach((d) => {
    d.addEventListener("toggle", () => {
      layout.inspector[d.dataset.fold] = !d.open;
      saveLayout();
    });
  });

  // ------------------------------------------------------------ console + status
  const con = { items: [], filter: { info: true, warn: true, error: true }, sel: null };
  let seenProblems = 0;
  const problems = () => con.items.filter((i) => i.level !== "info").reduce((s, i) => s + i.n, 0);
  const pad2 = (n) => String(n).padStart(2, "0");

  function log(level, text, detail) {
    level = level === "warn" || level === "error" ? level : "info";
    const last = con.items[con.items.length - 1];
    if (last && last.level === level && last.text === text && (last.detail || "") === (detail || "")) {
      last.n++;
      last.t = new Date();
    } else {
      con.items.push({ level, text: String(text), detail: detail || "", t: new Date(), n: 1 });
      if (con.items.length > 500) con.items.shift();
    }
    status(text, level);
    renderConsole();
    if (layout.bottom.tab === "console" && layout.bottom.open) seenProblems = problems();
    updateConsoleBadge();
  }

  function updateConsoleBadge() {
    const n = problems() - seenProblems;
    const b = $("consoleCount");
    b.hidden = n <= 0;
    b.textContent = n;
    b.classList.toggle("err", con.items.some((i) => i.level === "error"));
  }

  function renderConsole() {
    const ol = $("conList");
    const atBottom = ol.scrollHeight - ol.scrollTop - ol.clientHeight < 24;
    ol.innerHTML = "";
    const counts = { info: 0, warn: 0, error: 0 };
    con.items.forEach((it) => {
      counts[it.level] += it.n;
      if (!con.filter[it.level]) return;
      const li = el("li", it.level + (it === con.sel ? " sel" : ""));
      const t = it.t;
      li.append(el("span", "lv"), el("span", "tm", `${pad2(t.getHours())}:${pad2(t.getMinutes())}:${pad2(t.getSeconds())}`),
        el("span", "tx", it.text), el("span", "rep", it.n > 1 ? String(it.n) : ""));
      li.title = it.text;
      li.onclick = () => { con.sel = it; renderConsole(); };
      ol.append(li);
    });
    $("nInfo").textContent = counts.info;
    $("nWarn").textContent = counts.warn;
    $("nError").textContent = counts.error;
    const s = con.sel;
    $("conDetail").textContent = s ? (s.detail ? `${s.text}\n\n${s.detail}` : s.text) : "Select a message to see its details.";
    if (atBottom) ol.scrollTop = ol.scrollHeight;
  }
  $("conClear").onclick = () => { con.items = []; con.sel = null; seenProblems = 0; renderConsole(); updateConsoleBadge(); };
  document.querySelectorAll("[data-level]").forEach((b) => {
    b.onclick = () => {
      const k = b.dataset.level;
      con.filter[k] = !con.filter[k];
      b.classList.toggle("on", con.filter[k]);
      renderConsole();
    };
  });

  function status(text, level) {
    const m = $("stMsg");
    m.textContent = String(text).split("\n")[0];
    m.className = "st-msg" + (level && level !== "info" ? " " + level : "");
  }

  // ------------------------------------------------------------ menus
  let menus = [];
  let extraKeys = [];
  const menuState = { open: -1, pops: [], hot: [] };
  const layer = $("menuLayer");

  const itemsOf = (it) => (typeof it.items === "function" ? it.items() : it.items) || [];
  const enabled = (it) => (typeof it.enabled === "function" ? !!it.enabled() : it.enabled !== false);

  function renderMenubar() {
    const nav = $("menus");
    nav.innerHTML = "";
    menus.forEach((m, i) => {
      const b = el("button", "menu-top", m.label);
      b.type = "button";
      b.setAttribute("role", "menuitem");
      b.setAttribute("aria-haspopup", "true");
      b.addEventListener("pointerdown", (ev) => {
        if (ev.button !== 0) return;
        ev.preventDefault();
        if (menuState.open === i) closeMenus(); else openMenu(i);
      });
      b.addEventListener("pointerenter", () => { if (menuState.open >= 0 && menuState.open !== i) openMenu(i); });
      b.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " " || ev.key === "ArrowDown") { ev.preventDefault(); openMenu(i, true); }
      });
      nav.append(b);
    });
  }

  function topButtons() { return [...$("menus").children]; }

  function openMenu(i, keyboard) {
    closeMenus(true);
    menuState.open = i;
    topButtons().forEach((b, k) => b.classList.toggle("open", k === i));
    const b = topButtons()[i];
    const r = b.getBoundingClientRect();
    showPop(itemsOf(menus[i]), r.left, r.bottom, 0);
    if (keyboard) setHot(0, firstEnabled(0, 1));
  }

  function closeMenus(keepTop) {
    menuState.pops.forEach((p) => p.el.remove());
    menuState.pops = [];
    if (!keepTop) {
      menuState.open = -1;
      topButtons().forEach((b) => b.classList.remove("open"));
    }
  }

  function closeFrom(level) {
    while (menuState.pops.length > level) menuState.pops.pop().el.remove();
  }

  function showPop(items, x, y, level) {
    closeFrom(level);
    const pop = el("div", "menu-pop");
    pop.setAttribute("role", "menu");
    const rows = [];
    items.forEach((it) => {
      if (!it) return;
      if (it.sep) { pop.append(el("div", "menu-sep")); return; }
      if (it.head) { pop.append(el("div", "menu-head", it.head)); return; }
      const row = el("button", "menu-item");
      row.type = "button";
      row.setAttribute("role", it.radio ? "menuitemradio" : it.checked ? "menuitemcheckbox" : "menuitem");
      const ck = typeof it.checked === "function" ? it.checked() : null;
      const mk = el("span", "mk", ck ? (it.radio ? "•" : "✓") : "");
      if (ck != null) row.setAttribute("aria-checked", !!ck);
      row.append(mk, el("span", "lb", it.label));
      const sub = !!it.items;
      if (sub) row.append(el("span", "arrow", "▸"));
      else if (it.shortcut) row.append(el("span", "sc", SL.formatShortcut(it.shortcut, mac)));
      row.disabled = !enabled(it);
      const idx = rows.length;
      rows.push({ it, row });
      row.addEventListener("pointerenter", () => {
        setHot(level, idx);
        if (sub && !row.disabled) openSub(level, idx);
        else closeFrom(level + 1);
      });
      row.addEventListener("click", (ev) => {
        ev.preventDefault();
        if (row.disabled) return;
        if (sub) { openSub(level, idx); return; }
        activate(it);
      });
      pop.append(row);
    });
    layer.append(pop);
    // keep on screen
    const w = pop.offsetWidth, h = pop.offsetHeight;
    const px = Math.max(0, Math.min(x, window.innerWidth - w - 2));
    const py = Math.max(0, Math.min(y, window.innerHeight - h - 2));
    pop.style.left = px + "px";
    pop.style.top = py + "px";
    menuState.pops[level] = { el: pop, rows, hot: -1 };
    return pop;
  }

  function openSub(level, idx) {
    const p = menuState.pops[level];
    const { it, row } = p.rows[idx];
    if (menuState.pops[level + 1] && menuState.pops[level + 1].from === idx) return;
    const r = row.getBoundingClientRect();
    let x = r.right - 2;
    const pop = showPop(itemsOf(it), x, r.top - 5, level + 1);
    if (x + pop.offsetWidth > window.innerWidth) pop.style.left = Math.max(0, r.left - pop.offsetWidth + 2) + "px";
    menuState.pops[level + 1].from = idx;
  }

  function setHot(level, idx) {
    const p = menuState.pops[level];
    if (!p) return;
    p.hot = idx;
    p.rows.forEach((r, k) => r.row.classList.toggle("hot", k === idx));
    if (p.rows[idx]) p.rows[idx].row.focus({ preventScroll: true });
  }

  function firstEnabled(level, dir, from) {
    const p = menuState.pops[level];
    if (!p || !p.rows.length) return -1;
    let i = from == null ? (dir > 0 ? -1 : p.rows.length) : from;
    for (let n = 0; n < p.rows.length; n++) {
      i = (i + dir + p.rows.length) % p.rows.length;
      if (!p.rows[i].row.disabled) return i;
    }
    return -1;
  }

  function activate(it) {
    closeMenus();
    try { it.run && it.run(); } catch (e) { log("error", `${it.label}: ${e.message}`, e.stack); }
  }

  function menuKeys(ev) {
    const level = menuState.pops.length - 1;
    const p = menuState.pops[level];
    if (!p) return false;
    const k = ev.key;
    if (k === "Escape") {
      if (level > 0) closeFrom(level); else { const i = menuState.open; closeMenus(); topButtons()[i] && topButtons()[i].focus(); }
    } else if (k === "ArrowDown") setHot(level, firstEnabled(level, 1, p.hot));
    else if (k === "ArrowUp") setHot(level, firstEnabled(level, -1, p.hot));
    else if (k === "ArrowRight") {
      const r = p.rows[p.hot];
      if (r && r.it.items && !r.row.disabled) { openSub(level, p.hot); setHot(level + 1, firstEnabled(level + 1, 1)); }
      else openMenu((menuState.open + 1) % menus.length, true);
    } else if (k === "ArrowLeft") {
      if (level > 0) { closeFrom(level); setHot(level - 1, menuState.pops[level - 1].hot); }
      else openMenu((menuState.open - 1 + menus.length) % menus.length, true);
    } else if (k === "Enter" || k === " ") {
      const r = p.rows[p.hot];
      if (r && !r.row.disabled) {
        if (r.it.items) { openSub(level, p.hot); setHot(level + 1, firstEnabled(level + 1, 1)); }
        else activate(r.it);
      }
    } else if (k === "Tab") {
      closeMenus();
      return false;
    } else return false;
    ev.preventDefault();
    return true;
  }

  document.addEventListener("pointerdown", (ev) => {
    if (menuState.open < 0) return;
    if (ev.target.closest(".menu-pop") || ev.target.closest(".menu-top")) return;
    closeMenus();
  }, true);
  window.addEventListener("blur", () => closeMenus());

  // ------------------------------------------------------------ keyboard
  document.addEventListener("keydown", (ev) => {
    if (menuState.open >= 0 && menuKeys(ev)) return;
    if (ev.defaultPrevented || ev.isComposing) return;
    if (document.querySelector("dialog[open]")) return; // dialogs keep their keys (Esc closes)
    const kind = SL.focusKind(document.activeElement);
    const it = SL.findShortcut(menus, ev, kind, mac, extraKeys);
    if (!it) return;
    ev.preventDefault();
    try { it.run && it.run(ev); } catch (e) { log("error", `${it.label || it.id}: ${e.message}`, e.stack); }
  });

  // ------------------------------------------------------------ dialogs
  function openDialog(id) {
    closeMenus();
    const d = $(id);
    if (!d.open) d.showModal();
  }

  function renderKeys() {
    const box = $("keysBody");
    box.innerHTML = "";
    const g = el("div", "keys");
    const groups = {};
    for (const it of SL.flattenMenus(menus).concat(extraKeys)) {
      if (!it.shortcut) continue;
      const grp = it.group || it.path[0];
      (groups[grp] = groups[grp] || []).push(it);
    }
    for (const [name, items] of Object.entries(groups)) {
      g.append(el("h4", "", name));
      const seen = new Set();
      for (const it of items) {
        const label = it.keyLabel || it.label;
        const k = label + it.shortcut;
        if (seen.has(k)) continue;
        seen.add(k);
        const row = el("div");
        row.append(el("span", "", label), el("kbd", "", SL.formatShortcut(it.shortcut, mac)));
        g.append(row);
      }
    }
    box.append(g);
  }

  // ------------------------------------------------------------ public
  window.Shell = {
    mac, store, el,
    get layout() { return layout; },
    applyLayout, saveLayout, setCenter, setBottom, toggleDock, resetLayout,
    onResize: (f) => resizeCbs.push(f),
    onCenter: (f) => centerCbs.push(f),
    onBottom: (f) => bottomCbs.push(f),
    log, status,
    registerPanel,
    unpinPanel: unpinByKey,
    setMenus(m, extra) { menus = m; extraKeys = extra || []; renderMenubar(); },
    closeMenus,
    openDialog,
    showShortcuts() { renderKeys(); openDialog("dlgKeys"); },
    fmt: (s) => SL.formatShortcut(s, mac),
  };

  applyFolds();
  applyLayout();
})();
