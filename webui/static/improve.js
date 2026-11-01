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
            <button type="button" class="btn" id="impShotBtn" title="Screenshot this tab and mark it up with a red pen">Screenshot</button>
            <button type="button" class="btn primary" id="impSubmit">Submit</button>
          </div>
          <div class="imp-shot" id="impShot" hidden><img id="impShotImg" alt="attached screenshot"><button type="button" class="btn" id="impShotDrop">Remove</button></div>
          <p class="imp-note">One commit per job, undoable. Shift+Enter for a new line.</p>
          <p class="imp-err" id="impErr"></p>
        </div>
      </section>
      <section>
        <h2>Queue &amp; history</h2>
        <div class="imp-sec"><ul class="imp-hist" id="impHist"><li class="muted">nothing yet</li></ul></div>
      </section>
      <section>
        <h2>Run log</h2>
        <div class="imp-sec" id="impLogBox"><p class="imp-cur" id="impCur"></p><pre class="imp-log" id="impLog"></pre></div>
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
          <button type="button" id="impShotBtn" title="Screenshot this tab and mark it up with a red pen">Screenshot</button>
          <button type="button" class="primary" id="impSubmit">Submit</button>
        </div>
        <div class="imp-shot" id="impShot" hidden><img id="impShotImg" alt="attached screenshot"><button type="button" id="impShotDrop">Remove</button></div>
        <p class="imp-note">One commit per job, undoable. Shift+Enter for a new line.</p>
        <p class="imp-err" id="impErr"></p>
      </section>
      <section>
        <h2>Run log</h2>
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

  const state = { jobs: [], running: null, shown: null, logNext: 0, logFor: null, timer: null, prev: {}, screenshot: "" };
  const maximum_screenshot_width_in_pixels = 1920;
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
      const li = el("li", (state.shown || state.running) === j.id ? "on" : "");
      const req = el("button", "req", j.request);
      req.type = "button";
      req.title = "Show this run's log";
      req.onclick = () => showLog(j.id);
      const meta = el("div", "meta");
      meta.append(el("span", "st " + j.status, j.status), el("span", "", j.kind === "undo" ? "undo" : j.target_label));
      if (j.commit) meta.append(el("code", "", j.commit.slice(0, 8)));
      meta.append(el("span", "", ago(j.finished || j.started || j.created)));
      if (j.kind === "improve" && j.status === "done" && j.commit) {
        const u = el("button", "undo", "Undo");
        u.type = "button";
        u.title = j.target === "rom" ? "git revert on arklarna, rebuild, recopy the release ROM" : "git revert this commit";
        u.onclick = () => undo(j, u);
        meta.append(u);
      }
      if (j.status === "queued" || j.status === "running") {
        const c = el("button", "undo", "Cancel");
        c.type = "button";
        c.onclick = () => api(`/api/improve/${j.id}/cancel`, {}).then(refresh).catch((e) => ($("impErr").textContent = e.message));
        meta.append(c);
      }
      li.append(req, meta);
      if (j.error) li.append(el("p", "sum", j.error));
      else if (j.summary && j.status !== "running") li.append(el("p", "sum", j.summary.split("\n").slice(-3).join(" ").slice(0, 280)));
      if (j.restart_needed) li.append(el("p", "sum", "Changed the server: restart webui/server.py to load it."));
      ul.append(li);
    }
  }

  const clock = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const until = (t) => {
    const m = Math.max(0, Math.round((t - Date.now() / 1000) / 60));
    return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
  };

  // Claude Code's 5-hour window, as last reported by an improve run (no run yet = nothing to show)
  function renderUsage(u) {
    const box = $("impUsage");
    box.innerHTML = "";
    if (!u) { box.append(el("p", "imp-note", "No reading yet.")); return; }
    const reset = u.resets_at && u.resets_at < Date.now() / 1000;
    const left = Math.max(0, Math.round(100 - u.utilization * 100));
    box.append(el("p", "big", reset ? "window reset" : `${left}% left`));
    const bar = el("div", "bar");
    bar.append(Object.assign(el("span"), { style: `width:${reset ? 100 : left}%` }));
    box.append(bar);
    if (u.resets_at) box.append(el("p", "imp-note", reset ? `reset at ${clock(u.resets_at)}`
      : `resets ${clock(u.resets_at)} (in ${until(u.resets_at)})`));
    box.append(el("p", "imp-note", `as of ${ago(u.seen)}${u.status && u.status !== "allowed" ? ", status " + u.status : ""}`));
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

  function showLog(id) {
    state.shown = id;
    state.logFor = null;
    pollLog();
    renderHist();
  }

  async function pollLog() {
    const id = state.shown || state.running;
    const log = $("impLog");
    const scroller = dock ? $("impLogBox") : log;
    if (!id) { $("impCur").textContent = ""; return; }
    if (state.logFor !== id) { state.logFor = id; state.logNext = 0; log.textContent = ""; }
    let j;
    try { j = await api(`/api/improve/${id}?since=${state.logNext}`); } catch (e) { return; }
    if (state.logFor !== id) return;
    $("impCur").textContent = `${j.status}: ${j.request.slice(0, 90)}`;
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
        const bad = ["failed", "timeout", "cancelled"].includes(j.status);
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
    state.running = h.running;
    renderUsage(h.usage);
    dot.hidden = !h.running && !h.queued;
    if (isOpen()) { renderHist(); pollLog(); }
    schedule();
  }

  function schedule() {
    clearTimeout(state.timer);
    const busy = state.running || state.jobs.some((j) => j.status === "queued");
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
      const j = await api("/api/improve", { request: text, target: $("impTarget").value, screenshot: state.screenshot });
      $("impText").value = "";
      set_attached_screenshot("");
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

  function open_red_pen_editor(captured_canvas) {
    return new Promise((resolve) => {
      const overlay = el("div", "imp-draw");
      const toolbar = el("div", "imp-draw-bar");
      const hint = el("span", "imp-draw-hint", "Draw with the red pen to point at the issue.");
      const clear_button = el("button", "", "Clear");
      const cancel_button = el("button", "", "Cancel");
      const attach_button = el("button", "attach", "Attach");
      const drawing_canvas = document.createElement("canvas");
      drawing_canvas.width = captured_canvas.width;
      drawing_canvas.height = captured_canvas.height;
      const drawing_context = drawing_canvas.getContext("2d");
      const redraw_captured_image = () => drawing_context.drawImage(captured_canvas, 0, 0);
      const close_editor_with = (screenshot_data_url) => { overlay.remove(); resolve(screenshot_data_url); };

      [clear_button, cancel_button, attach_button].forEach((button) => { button.type = "button"; });
      clear_button.onclick = redraw_captured_image;
      cancel_button.onclick = () => close_editor_with("");
      attach_button.onclick = () => close_editor_with(drawing_canvas.toDataURL("image/png"));
      overlay.addEventListener("keydown", (event) => { event.stopPropagation(); if (event.key === "Escape") close_editor_with(""); });

      redraw_captured_image();
      enable_red_pen_strokes_on_canvas(drawing_canvas, drawing_context);
      toolbar.append(hint, clear_button, cancel_button, attach_button);
      overlay.append(toolbar, drawing_canvas);
      overlay.tabIndex = -1;
      document.body.append(overlay);
      overlay.focus();
    });
  }

  function enable_red_pen_strokes_on_canvas(drawing_canvas, drawing_context) {
    drawing_context.strokeStyle = red_pen_colour;
    drawing_context.lineWidth = Math.max(3, drawing_canvas.width * red_pen_width_as_fraction_of_image_width);
    drawing_context.lineCap = "round";
    drawing_context.lineJoin = "round";
    const pointer_position_in_canvas_pixels = (event) => {
      const canvas_rectangle = drawing_canvas.getBoundingClientRect();
      const canvas_pixels_per_css_pixel = drawing_canvas.width / canvas_rectangle.width;
      return [(event.clientX - canvas_rectangle.left) * canvas_pixels_per_css_pixel, (event.clientY - canvas_rectangle.top) * canvas_pixels_per_css_pixel];
    };
    drawing_canvas.onpointerdown = (event) => {
      drawing_canvas.setPointerCapture(event.pointerId);
      drawing_context.beginPath();
      drawing_context.moveTo(...pointer_position_in_canvas_pixels(event));
      drawing_context.lineTo(...pointer_position_in_canvas_pixels(event));
      drawing_context.stroke();
    };
    drawing_canvas.onpointermove = (event) => {
      if (!drawing_canvas.hasPointerCapture(event.pointerId)) return;
      drawing_context.lineTo(...pointer_position_in_canvas_pixels(event));
      drawing_context.stroke();
    };
  }

  function set_attached_screenshot(screenshot_data_url) {
    state.screenshot = screenshot_data_url;
    $("impShot").hidden = !screenshot_data_url;
    $("impShotImg").src = screenshot_data_url;
  }

  async function take_and_mark_up_screenshot() {
    $("impErr").textContent = "";
    let captured_canvas;
    try { captured_canvas = await capture_current_tab_as_canvas(); } catch (capture_error) {
      $("impErr").textContent = `Screenshot failed: ${capture_error.message}`;
      return;
    }
    const marked_up_screenshot = await open_red_pen_editor(captured_canvas);
    if (marked_up_screenshot) set_attached_screenshot(marked_up_screenshot);
  }

  $("impShotBtn").onclick = take_and_mark_up_screenshot;
  $("impShotDrop").onclick = () => set_attached_screenshot("");
  $("impSubmit").onclick = submit;
  $("impText").addEventListener("keydown", (e) => {
    e.stopPropagation(); // keep page shortcuts (Ctrl+Enter = Generate) out of the textarea
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
  });
  refresh();
})();
