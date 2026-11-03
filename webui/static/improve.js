// Improvements box: "How should this be different?" -> a headless Claude Code run
// implements it as one git commit (webui/improve.py). Include on any page with
// <script src="improve.js" defer></script>. If the page has an #improveDock element
// (the editor shell's bottom "Improve" tab) it renders docked there; otherwise it adds
// its own floating button, panel and CSS.
(function () {
  if (window.__improveBox) return;
  window.__improveBox = true;
  const dock = document.getElementById("improveDock");

  if (!dock) {
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = "improve.css";
    document.head.append(css);
  }
  const draw_css = document.createElement("link");
  draw_css.rel = "stylesheet";
  draw_css.href = "improve-draw.css";
  document.head.append(draw_css);

  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };

  const btn = el("button", "imp-btn");
  btn.type = "button";
  btn.title = "Ask for a change to this software";
  btn.append("Improve", Object.assign(el("span", "imp-dot"), { hidden: true }));
  const dot = dock ? (document.getElementById("improveDot") || el("span")) : btn.lastChild;

  const panel = el(dock ? "div" : "aside", dock ? "imp-docked" : "imp-panel");
  panel.hidden = !dock;
  panel.setAttribute("aria-label", "Improvements");
  panel.innerHTML = dock ? `
      <section>
        <h2><label for="impText">How should this be different?</label></h2>
        <div class="imp-sec">
          <textarea id="impText" placeholder="e.g. show the walkable percentage in the stats panel"></textarea>
          <div class="imp-row">
            <select id="impTarget" aria-label="Target">
              <option value="poke-routes">Generator &amp; web UI</option>
              <option value="rom">ROM (Arklarna)</option>
            </select>
            <button type="button" class="btn" id="impShotBtn" title="Drag a box anywhere on the page to screenshot it">Screenshot</button>
            <button type="button" class="btn" id="impPenBtn" title="Draw on the page in red, then attach the area around it or the whole page">Mark up</button>
            <button type="button" class="btn primary" id="impSubmit">Submit</button>
          </div>
          <div class="imp-shot" id="impShot" hidden></div>
          <p class="imp-note">One commit per job, undoable. Shift+Enter for a new line.</p>
          <p class="imp-err" id="impErr"></p>
        </div>
      </section>
      <section>
        <h2 class="imp-wh">Workers <span class="imp-wctl" id="impWctl"><button type="button" data-d="-1" title="Fewer workers">&minus;</button><b id="impWn">-</b><button type="button" data-d="1" title="More workers">+</button></span></h2>
        <div class="imp-sec"><ul class="imp-slots" id="impSlots"></ul><p class="imp-note" id="impOwner"></p></div>
      </section>
      <section>
        <h2>Current job</h2>
        <div class="imp-sec" id="impLogBox"><p class="imp-cur" id="impCur"></p><pre class="imp-log" id="impLog"></pre></div>
      </section>
      <section>
        <h2>Queue &amp; history</h2>
        <div class="imp-sec"><ul class="imp-hist" id="impHist"><li class="muted">nothing yet</li></ul></div>
      </section>
      <section>
        <h2>Usage</h2>
        <div class="imp-sec imp-usage" id="impUsage"></div>
      </section>` : `
    <header class="imp-head"><h2>Improve</h2><button type="button" class="imp-close" aria-label="Close">&times;</button></header>
    <div class="imp-body">
      <section>
        <label for="impText" class="imp-note" style="display:block;margin:0 0 6px">How should this be different?</label>
        <textarea id="impText" placeholder="e.g. show the walkable percentage in the stats panel"></textarea>
        <div class="imp-row">
          <select id="impTarget" aria-label="Target">
            <option value="poke-routes">Generator &amp; web UI</option>
            <option value="rom">ROM (Arklarna)</option>
          </select>
          <button type="button" id="impShotBtn" title="Drag a box anywhere on the page to screenshot it">Screenshot</button>
          <button type="button" id="impPenBtn" title="Draw on the page in red, then attach the area around it or the whole page">Mark up</button>
          <button type="button" class="primary" id="impSubmit">Submit</button>
        </div>
        <div class="imp-shot" id="impShot" hidden></div>
        <p class="imp-note">One commit per job, undoable. Shift+Enter for a new line.</p>
        <p class="imp-err" id="impErr"></p>
      </section>
      <section>
        <h2 class="imp-wh">Workers <span class="imp-wctl" id="impWctl"><button type="button" data-d="-1" title="Fewer workers">&minus;</button><b id="impWn">-</b><button type="button" data-d="1" title="More workers">+</button></span></h2>
        <ul class="imp-slots" id="impSlots"></ul>
        <p class="imp-note" id="impOwner"></p>
        <p class="imp-cur" id="impCur"></p>
        <pre class="imp-log" id="impLog"></pre>
      </section>
      <section>
        <h2>Usage</h2>
        <div class="imp-usage" id="impUsage"></div>
      </section>
      <section>
        <h2>History</h2>
        <ul class="imp-hist" id="impHist"><li class="muted">nothing yet</li></ul>
      </section>
    </div>`;

  if (dock) dock.append(panel); else document.body.append(btn, panel);
  // docked: "open" means the Improve tab is showing
  const isOpen = () => (dock ? !!dock.offsetParent : !panel.hidden);
  const $ = (id) => panel.querySelector("#" + id);

  const state = { jobs: [], running: [], shown: null, logNext: 0, logFor: null, timer: null, prev: {}, h: null, screenshots: [] };
  const ACTIVE = ["queued", "running", "merging"];
  const BAD = ["failed", "timeout", "cancelled", "conflict"];
  const maximum_screenshot_width_in_pixels = 1920;
  const maximum_screenshots_per_request = 6;     // same cap as improve.py
  const red_pen_colour = "#e3201b";
  const red_pen_width_as_fraction_of_image_width = 1 / 320;

  async function api(path, body) {
    const opts = body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    };
    const r = await fetch(path, opts);
    const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
    if (!r.ok || j.error) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  }

  const ago = (t) => {
    if (!t) return "";
    const s = Math.round(Date.now() / 1000 - t);
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    return new Date(t * 1000).toLocaleDateString();
  };

  function renderHist() {
    const ul = $("impHist");
    ul.innerHTML = "";
    const jobs = state.jobs;
    if (!jobs.length) { ul.append(el("li", "muted", "nothing yet")); return; }
    for (const j of jobs) {
      const li = el("li", current() === j.id ? "on" : "");
      const req = el("button", "req", j.request);
      req.type = "button";
      req.title = "Show this run's log";
      req.onclick = () => showLog(j.id);
      const meta = el("div", "meta");
      meta.append(el("span", "st " + j.status, j.status), el("span", "", j.kind === "undo" ? "undo" : (j.fix_of ? "fix, " : "") + (j.mode === "agent" ? "agent, " : "") + j.target_label));
      if (j.commit) meta.append(el("code", "", j.commit.slice(0, 8)));
      if (j.attempt > 1) meta.append(el("span", "", "re-run"));
      if (j.held_by) meta.append(el("span", "", "held by " + j.held_by));
      meta.append(el("span", "", ago(j.finished || j.started || j.created)));
      if (j.kind === "improve" && j.status === "done" && j.commit && !(state.h && state.h.readonly && !state.h.owner.port)) {
        const u = el("button", "undo", "Undo");
        u.type = "button";
        u.title = j.target === "rom" ? "git revert on arklarna, rebuild, recopy the release ROM" : "git revert this commit";
        u.onclick = () => undo(j, u);
        meta.append(u);
        const f = el("button", "undo", "Fix");
        f.type = "button";
        f.title = "Tell it what's wrong; runs as a new job on top of this one";
        f.onclick = () => toggleFixForm(j, li);
        meta.append(f);
      }
      if ((j.status === "queued" || j.status === "running") && !j.held_by) {
        const c = el("button", "undo", "Cancel");
        c.type = "button";
        c.onclick = () => api(`/api/improve/${j.id}/cancel`, {}).then(refresh).catch((e) => ($("impErr").textContent = e.message));
        meta.append(c);
      }
      li.append(req, meta);
      if (j.error) li.append(el("p", "sum", j.error));
      else if (j.notes && j.notes.length && ACTIVE.includes(j.status)) li.append(el("p", "sum", j.notes[j.notes.length - 1]));
      if (j.root_note) li.append(el("p", "sum", j.root_note));
      else if (j.summary && j.status !== "running") li.append(el("p", "sum", j.summary.split("\n").slice(-3).join(" ").slice(0, 280)));
      if (j.restart_needed) li.append(el("p", "sum", "Changed the server: restart webui/server.py to load it."));
      ul.append(li);
    }
  }

  const current = () => state.shown || state.running[0] || null;
  const byId = (id) => state.jobs.find((j) => j.id === id);

  // one row per worker slot: what it runs (click shows that job's log)
  function renderSlots(h) {
    const ul = $("impSlots");
    ul.innerHTML = "";
    $("impWn").textContent = h.workers;
    for (const b of $("impWctl").querySelectorAll("button")) {
      const n = h.workers + Number(b.dataset.d);
      b.disabled = n < 1 || n > h.max_workers;
    }
    const busy = new Set();
    for (const s of h.slots) {
      const j = s.job && byId(s.job);
      if (j) busy.add(j.id);
      const li = el("li", (j && current() === j.id ? "on " : "") + (s.retiring ? "retiring" : ""));
      li.append(el("span", "w", `W${s.slot + 1}`));
      if (j) {
        li.append(el("span", "st " + j.status, j.status));
        const r = el("button", "req", j.request);
        r.type = "button";
        r.title = `Show this job's log (verifies on port ${s.port})`;
        r.onclick = () => showLog(j.id);
        li.append(r);
      } else li.append(el("span", "idle", s.retiring ? "stopping" : "idle"));
      ul.append(li);
    }
    // jobs run by an older server (before the queue lock) have no slot here
    for (const j of state.jobs.filter((x) => (x.status === "running" || x.status === "merging") && !busy.has(x.id))) {
      const li = el("li", current() === j.id ? "on" : "");
      li.append(el("span", "w", "old"), el("span", "st " + j.status, j.status));
      const r = el("button", "req", j.request);
      r.type = "button";
      r.onclick = () => showLog(j.id);
      li.append(r);
      ul.append(li);
    }
    const o = h.owner || {};
    const bits = [];
    if (h.readonly) bits.push(o.port ? `Read-only here: the queue is run by the server on port ${o.port} (pid ${o.pid}); submit, undo and cancel are passed on to it.`
      : "No server owns the queue right now.");
    if (h.queued) bits.push(`${h.queued} queued`);
    if (h.mem && h.mem.waiting) bits.push(`waiting for memory (${h.mem.avail_mb} MB free, needs ${h.mem.min_mb})`);
    if (h.legacy && h.legacy.length) bits.push(`an older server (pid ${h.legacy.join(", ")}) is still running its own jobs`);
    if (h.root_stale) bits.push("main moved but the repo checkout has uncommitted edits; it catches up later");
    $("impOwner").textContent = bits.join(" · ");
  }

  async function setWorkers(d) {
    if (!state.h) return;
    try {
      await api("/api/improve/workers", { workers: state.h.workers + d });
      await refresh();
    } catch (e) { $("impErr").textContent = e.message; }
  }

  const clock = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const until = (t) => {
    const m = Math.max(0, Math.round((t - Date.now() / 1000) / 60));
    return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
  };

  // Claude Code's 5-hour window: from the last improve run, or a tiny check the server makes (webui/usage.py)
  function renderUsage(u) {
    const box = $("impUsage");
    if (!u && box.childElementCount) return;
    box.innerHTML = "";
    if (!u) { box.append(el("p", "imp-note", "Checking…")); return; }
    if (u.utilization == null) { box.append(el("p", "imp-note", `No reading: ${u.error || "unknown"}.`)); return; }
    const reset = u.resets_at && u.resets_at < Date.now() / 1000;
    const left = Math.max(0, Math.round(100 - u.utilization * 100));
    box.classList.toggle("low", !reset && left < 40);
    box.append(el("p", "big", reset ? "window reset" : `${left}% left`));
    const bar = el("div", "bar");
    bar.append(Object.assign(el("span"), { style: `width:${reset ? 100 : left}%` }));
    box.append(bar);
    if (u.resets_at) box.append(el("p", "imp-note", reset ? `reset at ${clock(u.resets_at)}`
      : `resets ${clock(u.resets_at)} (in ${until(u.resets_at)})`));
    box.append(el("p", "imp-note", `as of ${ago(u.seen)}, ${u.source === "check" ? "usage check" : "last run"}${u.status && u.status !== "allowed" ? ", status " + u.status : ""}`));
    if (u.error) box.append(el("p", "imp-note", `couldn't refresh: ${u.error}`));
  }

  async function pollUsage() {
    if (document.hidden || state.usageBusy) return;
    state.usageBusy = true;
    try { renderUsage(await (await fetch("/api/improve/usage")).json()); }
    catch (e) { renderUsage({ error: "the page server didn't answer" }); }
    state.usageBusy = false;
  }

  async function undo(j, b) {
    if (!confirm(`Revert "${j.subject || j.request}"?`)) return;
    b.disabled = true;
    try {
      const u = await api(`/api/improve/${j.id}/undo`, {});
      showLog(u.id);
      await refresh();
    } catch (e) {
      $("impErr").textContent = e.message;
      b.disabled = false;
    }
  }

  // inline "what's wrong?" box under a done job; submits as a new job on top of it
  function toggleFixForm(j, li) {
    const existing = li.querySelector(".imp-fix-form");
    if (existing) { existing.remove(); return; }
    const form = el("div", "imp-fix-form");
    const ta = document.createElement("textarea");
    ta.placeholder = "What's wrong, or what to change about this one?";
    ta.rows = 2;
    const row = el("div", "imp-row");
    const send = el("button", "primary", "Send fix");
    send.type = "button";
    const err = el("p", "imp-err");
    send.onclick = async () => {
      const text = ta.value.trim();
      if (!text) { ta.focus(); return; }
      send.disabled = true;
      try {
        const nj = await api(`/api/improve/${j.id}/fix`, { request: text });
        form.remove();
        state.shown = nj.id;
        state.logFor = null;
        await refresh();
      } catch (e) {
        err.textContent = e.message;
        send.disabled = false;
      }
    };
    ta.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send.click(); }
    });
    row.append(send);
    form.append(ta, row, err);
    li.append(form);
    ta.focus();
  }

  function showLog(id) {
    state.shown = id;
    state.logFor = null;
    pollLog();
    renderHist();
    if (state.h) renderSlots(state.h);
  }

  async function pollLog() {
    const id = current();
    const log = $("impLog");
    const scroller = dock ? $("impLogBox") : log;
    if (!id) { $("impCur").textContent = ""; return; }
    if (state.logFor !== id) { state.logFor = id; state.logNext = 0; log.textContent = ""; }
    let j;
    try { j = await api(`/api/improve/${id}?since=${state.logNext}`); } catch (e) { return; }
    if (state.logFor !== id) return;
    $("impCur").textContent = `${j.status}${j.attempt > 1 ? " (re-run)" : ""}: ${j.request.slice(0, 90)}`;
    const atBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 30;
    for (const line of j.log || []) {
      const cls = /^\[(error|timeout|warn|cancelled)\]|^ {2}!/.test(line) ? "e" : /^[>[]/.test(line) ? "" : "t";
      log.append(Object.assign(el("span", cls, line), {}), "\n");
    }
    state.logNext = j.log_next;
    if (atBottom) scroller.scrollTop = scroller.scrollHeight;
  }

  async function refresh() {
    let h;
    try { h = await api("/api/improve"); } catch (e) { return; }
    // a poke-routes improvement that just landed: offer a reload so the page shows it
    for (const j of h.jobs) {
      const was = state.prev[j.id];
      if (was && was !== j.status && window.Shell && window.Shell.log) {
        const bad = BAD.includes(j.status);
        window.Shell.log(bad ? "warn" : "info", `Improve ${j.kind === "undo" ? "undo " : ""}${j.status}: ${j.request.slice(0, 120)}`,
          [j.subject, j.commit && "commit " + j.commit.slice(0, 10), j.error, j.summary].filter(Boolean).join("\n"));
      }
      if (was && was !== j.status && j.status === "done" && j.target === "poke-routes") {
        $("impCur").textContent = "Done. Reload the page to see the change.";
        if (!panel.querySelector(".imp-reload")) {
          const r = el("button", dock ? "btn imp-reload" : "small imp-reload", "Reload page");
          r.type = "button";
          r.onclick = () => location.reload();
          panel.querySelector("#impCur").after(r);
        }
      }
      state.prev[j.id] = j.status;
    }
    state.jobs = h.jobs;
    state.running = h.running || [];
    state.h = h;
    dot.hidden = !state.running.length && !h.queued;
    if (isOpen()) { renderSlots(h); renderHist(); pollLog(); pollUsage(); } else renderUsage(h.usage);
    schedule();
  }

  function schedule() {
    clearTimeout(state.timer);
    const busy = state.running.length || state.jobs.some((j) => j.status === "queued");
    const open = isOpen();
    state.timer = setTimeout(refresh, open && busy ? 1500 : busy ? 5000 : open ? 4000 : 30000);
  }

  async function submit() {
    const text = $("impText").value.trim();
    $("impErr").textContent = "";
    if (!text) { $("impText").focus(); return; }
    const b = $("impSubmit");
    b.disabled = true;
    try {
      // first image in the old "screenshot" field too, so a server not yet restarted still gets that one
      const [first_screenshot = "", ...other_screenshots] = state.screenshots;
      const j = await api("/api/improve", { request: text, target: $("impTarget").value, screenshot: first_screenshot, screenshots: other_screenshots });
      $("impText").value = "";
      set_attached_screenshots([]);
      state.shown = j.id;
      state.logFor = null;
      await refresh();
    } catch (e) {
      $("impErr").textContent = e.message;
    } finally {
      b.disabled = false;
    }
  }

  if (dock) {
    // refresh as soon as the tab is shown
    if (window.Shell && window.Shell.onBottom) window.Shell.onBottom((tab) => { if (tab === "improve") refresh(); });
  } else {
    btn.onclick = () => {
      panel.hidden = !panel.hidden;
      if (!panel.hidden) { $("impText").focus(); refresh(); }
    };
    panel.querySelector(".imp-close").onclick = () => { panel.hidden = true; };
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !panel.hidden) panel.hidden = true; });
  }
  async function capture_current_tab_as_canvas() {
    const stream = await navigator.mediaDevices.getDisplayMedia({ video: { displaySurface: "browser" }, audio: false, preferCurrentTab: true });
    const video = Object.assign(document.createElement("video"), { srcObject: stream, muted: true });
    await video.play();
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const canvas = scale_video_frame_into_canvas(video);
    stream.getTracks().forEach((track) => track.stop());
    return canvas;
  }

  function scale_video_frame_into_canvas(video) {
    const scale_factor = Math.min(1, maximum_screenshot_width_in_pixels / video.videoWidth);
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(video.videoWidth * scale_factor);
    canvas.height = Math.round(video.videoHeight * scale_factor);
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas;
  }

  // The captured frame is shown full-page over the live UI, so boxing or drawing on it
  // feels like doing it on the page itself. Resolves to a PNG data URL, or "" if cancelled.
  function open_capture_overlay(captured_canvas, mode) {
    return new Promise((resolve) => {
      const overlay = el("div", "imp-draw");
      const toolbar = el("div", "imp-draw-bar");
      const drawing_canvas = document.createElement("canvas");
      drawing_canvas.width = captured_canvas.width;
      drawing_canvas.height = captured_canvas.height;
      const drawing_context = drawing_canvas.getContext("2d");
      drawing_context.drawImage(captured_canvas, 0, 0);
      const close_overlay_with = (screenshot_data_url) => { overlay.remove(); resolve(screenshot_data_url); };
      const make_button = (label, cls, onclick) => Object.assign(el("button", cls, label), { type: "button", onclick });

      if (mode === "box") {
        toolbar.append(el("span", "imp-draw-hint", "Drag a box around what to screenshot."), make_button("Cancel", "", () => close_overlay_with("")));
        enable_box_selection_on_canvas(drawing_canvas, drawing_context, captured_canvas, (box) => close_overlay_with(crop_canvas_to_data_url(captured_canvas, box)));
      } else {
        const stroke_bounds = enable_red_pen_strokes_on_canvas(drawing_canvas, drawing_context);
        const crop_around_strokes = () => {
          if (!stroke_bounds.drawn) return;
          const padding = Math.max(drawing_canvas.width / 16, (stroke_bounds.x1 - stroke_bounds.x0 + stroke_bounds.y1 - stroke_bounds.y0) / 4);
          close_overlay_with(crop_canvas_to_data_url(drawing_canvas, [stroke_bounds.x0 - padding, stroke_bounds.y0 - padding, stroke_bounds.x1 + padding, stroke_bounds.y1 + padding]));
        };
        const clear_strokes = () => { drawing_context.drawImage(captured_canvas, 0, 0); Object.assign(stroke_bounds, { drawn: false, x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity }); };
        toolbar.append(el("span", "imp-draw-hint", "Draw in red where the issue is."),
          make_button("Clear", "", clear_strokes), make_button("Cancel", "", () => close_overlay_with("")),
          make_button("Attach whole page", "", () => close_overlay_with(drawing_canvas.toDataURL("image/png"))),
          make_button("Attach area around drawing", "attach", crop_around_strokes));
      }
      overlay.addEventListener("keydown", (event) => { event.stopPropagation(); if (event.key === "Escape") close_overlay_with(""); });
      overlay.append(drawing_canvas, toolbar);
      overlay.tabIndex = -1;
      document.body.append(overlay);
      overlay.focus();
    });
  }

  const pointer_position_in_canvas_pixels = (drawing_canvas, event) => {
    const canvas_rectangle = drawing_canvas.getBoundingClientRect();
    return [(event.clientX - canvas_rectangle.left) * drawing_canvas.width / canvas_rectangle.width,
      (event.clientY - canvas_rectangle.top) * drawing_canvas.height / canvas_rectangle.height];
  };

  // box: [x0, y0, x1, y1] in canvas pixels, any corner order, clipped to the canvas
  function crop_canvas_to_data_url(source_canvas, box) {
    const x0 = Math.max(0, Math.round(Math.min(box[0], box[2]))), y0 = Math.max(0, Math.round(Math.min(box[1], box[3])));
    const x1 = Math.min(source_canvas.width, Math.round(Math.max(box[0], box[2]))), y1 = Math.min(source_canvas.height, Math.round(Math.max(box[1], box[3])));
    const cropped_canvas = document.createElement("canvas");
    cropped_canvas.width = Math.max(1, x1 - x0);
    cropped_canvas.height = Math.max(1, y1 - y0);
    cropped_canvas.getContext("2d").drawImage(source_canvas, x0, y0, cropped_canvas.width, cropped_canvas.height, 0, 0, cropped_canvas.width, cropped_canvas.height);
    return cropped_canvas.toDataURL("image/png");
  }

  function enable_box_selection_on_canvas(drawing_canvas, drawing_context, captured_canvas, on_box_chosen) {
    const minimum_box_size_in_canvas_pixels = 8;
    let box_start = null;
    drawing_canvas.onpointerdown = (event) => {
      drawing_canvas.setPointerCapture(event.pointerId);
      box_start = pointer_position_in_canvas_pixels(drawing_canvas, event);
    };
    drawing_canvas.onpointermove = (event) => {
      if (!box_start) return;
      const [x, y] = pointer_position_in_canvas_pixels(drawing_canvas, event);
      drawing_context.drawImage(captured_canvas, 0, 0);
      drawing_context.fillStyle = "rgba(0, 0, 0, 0.45)";
      drawing_context.fillRect(0, 0, drawing_canvas.width, drawing_canvas.height);
      const [bx, by, bw, bh] = [Math.min(x, box_start[0]), Math.min(y, box_start[1]), Math.abs(x - box_start[0]), Math.abs(y - box_start[1])];
      drawing_context.drawImage(captured_canvas, bx, by, bw, bh, bx, by, bw, bh);
      drawing_context.strokeStyle = "#ffffff";
      drawing_context.lineWidth = Math.max(1, drawing_canvas.width / 960);
      drawing_context.strokeRect(bx, by, bw, bh);
    };
    drawing_canvas.onpointerup = (event) => {
      if (!box_start) return;
      const [x, y] = pointer_position_in_canvas_pixels(drawing_canvas, event);
      const start = box_start;
      box_start = null;
      if (Math.abs(x - start[0]) < minimum_box_size_in_canvas_pixels || Math.abs(y - start[1]) < minimum_box_size_in_canvas_pixels) {
        drawing_context.drawImage(captured_canvas, 0, 0);
        return;
      }
      on_box_chosen([start[0], start[1], x, y]);
    };
  }

  // Returns the live bounding box of everything drawn, in canvas pixels.
  function enable_red_pen_strokes_on_canvas(drawing_canvas, drawing_context) {
    const stroke_bounds = { drawn: false, x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity };
    drawing_context.strokeStyle = red_pen_colour;
    drawing_context.lineWidth = Math.max(3, drawing_canvas.width * red_pen_width_as_fraction_of_image_width);
    drawing_context.lineCap = "round";
    drawing_context.lineJoin = "round";
    const draw_to = (event) => {
      const [x, y] = pointer_position_in_canvas_pixels(drawing_canvas, event);
      drawing_context.lineTo(x, y);
      drawing_context.stroke();
      Object.assign(stroke_bounds, { drawn: true, x0: Math.min(stroke_bounds.x0, x), y0: Math.min(stroke_bounds.y0, y),
        x1: Math.max(stroke_bounds.x1, x), y1: Math.max(stroke_bounds.y1, y) });
    };
    drawing_canvas.onpointerdown = (event) => {
      drawing_canvas.setPointerCapture(event.pointerId);
      drawing_context.beginPath();
      drawing_context.moveTo(...pointer_position_in_canvas_pixels(drawing_canvas, event));
      draw_to(event);
    };
    drawing_canvas.onpointermove = (event) => {
      if (drawing_canvas.hasPointerCapture(event.pointerId)) draw_to(event);
    };
    return stroke_bounds;
  }

  // a strip of thumbnails, each with an × to drop it
  function set_attached_screenshots(screenshot_data_urls) {
    state.screenshots = screenshot_data_urls;
    const strip = $("impShot");
    strip.innerHTML = "";
    strip.hidden = !screenshot_data_urls.length;
    screenshot_data_urls.forEach((screenshot_data_url, index) => {
      const thumbnail = el("span", "imp-shot-item");
      const remove_button = Object.assign(el("button", "imp-shot-drop", "\u00d7"), { type: "button", title: "Remove this screenshot",
        onclick: () => set_attached_screenshots(state.screenshots.filter((_, i) => i !== index)) });
      thumbnail.append(Object.assign(el("img"), { src: screenshot_data_url, alt: `attached screenshot ${index + 1}` }), remove_button);
      strip.append(thumbnail);
    });
    $("impShotBtn").disabled = $("impPenBtn").disabled = screenshot_data_urls.length >= maximum_screenshots_per_request;
  }

  // mode: "box" (drag a box to screenshot) or "pen" (draw in red, attach around it or the whole page)
  async function capture_and_attach_screenshot(mode) {
    $("impErr").textContent = "";
    let captured_canvas;
    try { captured_canvas = await capture_current_tab_as_canvas(); } catch (capture_error) {
      $("impErr").textContent = `Screenshot failed: ${capture_error.message}`;
      return;
    }
    const screenshot_data_url = await open_capture_overlay(captured_canvas, mode);
    if (screenshot_data_url) set_attached_screenshots([...state.screenshots, screenshot_data_url].slice(0, maximum_screenshots_per_request));
  }

  $("impShotBtn").onclick = () => capture_and_attach_screenshot("box");
  $("impPenBtn").onclick = () => capture_and_attach_screenshot("pen");
  $("impSubmit").onclick = submit;
  for (const b of $("impWctl").querySelectorAll("button")) b.onclick = () => setWorkers(Number(b.dataset.d));
  $("impText").addEventListener("keydown", (e) => {
    e.stopPropagation(); // keep page shortcuts out of the textarea
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
  });
  refresh();
})();
