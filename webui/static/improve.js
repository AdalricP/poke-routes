// Improvements box: "How should this be different?" -> a headless Claude Code run
// implements it as one git commit (webui/improve.py). Include on any page with
// <script src="improve.js" defer></script>; it adds its own button, panel and CSS.
(function () {
  if (window.__improveBox) return;
  window.__improveBox = true;

  const css = document.createElement("link");
  css.rel = "stylesheet";
  css.href = "improve.css";
  document.head.append(css);

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
  const dot = btn.lastChild;

  const panel = el("aside", "imp-panel");
  panel.hidden = true;
  panel.setAttribute("aria-label", "Improvements");
  panel.innerHTML = `
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
          <button type="button" class="primary" id="impSubmit">Submit</button>
        </div>
        <p class="imp-note">Runs Claude Code unattended in that repo and ends in one commit you can undo. Cmd/Ctrl + Enter submits.</p>
        <p class="imp-err" id="impErr"></p>
      </section>
      <section>
        <h2>Run log</h2>
        <p class="imp-cur" id="impCur"></p>
        <pre class="imp-log" id="impLog"></pre>
      </section>
      <section>
        <h2>History</h2>
        <ul class="imp-hist" id="impHist"><li class="muted">nothing yet</li></ul>
      </section>
    </div>`;

  document.body.append(btn, panel);
  const $ = (id) => panel.querySelector("#" + id);

  const state = { jobs: [], running: null, shown: null, logNext: 0, logFor: null, timer: null, prev: {} };

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
      const li = el("li");
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
  }

  async function pollLog() {
    const id = state.shown || state.running;
    const log = $("impLog");
    if (!id) { $("impCur").textContent = ""; return; }
    if (state.logFor !== id) { state.logFor = id; state.logNext = 0; log.textContent = ""; }
    let j;
    try { j = await api(`/api/improve/${id}?since=${state.logNext}`); } catch (e) { return; }
    if (state.logFor !== id) return;
    $("impCur").textContent = `${j.status}: ${j.request.slice(0, 90)}`;
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 30;
    for (const line of j.log || []) {
      const cls = /^\[(error|timeout|warn|cancelled)\]|^ {2}!/.test(line) ? "e" : /^[>[]/.test(line) ? "" : "t";
      log.append(Object.assign(el("span", cls, line), {}), "\n");
    }
    state.logNext = j.log_next;
    if (atBottom) log.scrollTop = log.scrollHeight;
  }

  async function refresh() {
    let h;
    try { h = await api("/api/improve"); } catch (e) { return; }
    // a poke-routes improvement that just landed: offer a reload so the page shows it
    for (const j of h.jobs) {
      const was = state.prev[j.id];
      if (was && was !== j.status && j.status === "done" && j.target === "poke-routes") {
        $("impCur").textContent = "Done. Reload the page to see the change.";
        if (!panel.querySelector(".imp-reload")) {
          const r = el("button", "small imp-reload", "Reload page");
          r.type = "button";
          r.onclick = () => location.reload();
          panel.querySelector("#impCur").after(r);
        }
      }
      state.prev[j.id] = j.status;
    }
    state.jobs = h.jobs;
    state.running = h.running;
    dot.hidden = !h.running && !h.queued;
    if (!panel.hidden) { renderHist(); pollLog(); }
    schedule();
  }

  function schedule() {
    clearTimeout(state.timer);
    const busy = state.running || state.jobs.some((j) => j.status === "queued");
    state.timer = setTimeout(refresh, !panel.hidden && busy ? 1500 : busy ? 5000 : panel.hidden ? 30000 : 4000);
  }

  async function submit() {
    const text = $("impText").value.trim();
    $("impErr").textContent = "";
    if (!text) { $("impText").focus(); return; }
    const b = $("impSubmit");
    b.disabled = true;
    try {
      const j = await api("/api/improve", { request: text, target: $("impTarget").value });
      $("impText").value = "";
      state.shown = j.id;
      state.logFor = null;
      await refresh();
    } catch (e) {
      $("impErr").textContent = e.message;
    } finally {
      b.disabled = false;
    }
  }

  btn.onclick = () => {
    panel.hidden = !panel.hidden;
    if (!panel.hidden) { $("impText").focus(); refresh(); }
  };
  panel.querySelector(".imp-close").onclick = () => { panel.hidden = true; };
  $("impSubmit").onclick = submit;
  $("impText").addEventListener("keydown", (e) => {
    e.stopPropagation(); // keep page shortcuts (Ctrl+Enter = Generate) out of the textarea
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submit(); }
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !panel.hidden) panel.hidden = true; });
  refresh();
})();
