// Agent tab: ask for a task to be done *for* you in the tool ("get this tileset",
// "apply that tileset to Route 3") instead of a change to the software. Same queue
// as the Improve box (POST /api/improve with mode "agent", webui/improve.py), so it
// ends in one undoable commit too; only the prompt's opening differs. Mounts into
// #agentDock in the editor shell's bottom dock; reuses the Improve tab's .imp-docked styles.
(function () {
  const dock = document.getElementById("agentDock");
  if (!dock || window.__agentTab) return;
  window.__agentTab = true;

  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };

  const panel = el("div", "imp-docked agent");
  panel.setAttribute("aria-label", "Agent");
  panel.innerHTML = `
      <section>
        <h2><label for="agText">What should the agent do for you?</label></h2>
        <div class="imp-sec">
          <textarea id="agText" placeholder="e.g. find a DS-style autumn forest tileset and use it for the routes"></textarea>
          <div class="imp-row">
            <select id="agTarget" aria-label="Where">
              <option value="poke-routes">Generator &amp; web UI</option>
              <option value="rom">ROM (Arklarna)</option>
            </select>
            <button type="button" class="btn primary" id="agSubmit">Send</button>
          </div>
          <p class="imp-note">Good for tilesets: getting one (with credits), switching to one, applying one to a route. Runs in the Improve queue, one undoable commit. Shift+Enter for a new line.</p>
          <p class="imp-err" id="agErr"></p>
        </div>
      </section>
      <section>
        <h2>Tasks</h2>
        <div class="imp-sec"><ul class="imp-hist" id="agHist"><li class="muted">nothing yet</li></ul></div>
      </section>
      <section>
        <h2>What it's doing</h2>
        <div class="imp-sec" id="agLogBox"><p class="imp-cur" id="agCur"></p><pre class="imp-log" id="agLog"></pre></div>
      </section>`;
  dock.append(panel);
  const $ = (id) => panel.querySelector("#" + id);
  const state = { jobs: [], shown: null, logFor: null, logNext: 0, timer: null };

  async function api(path, body) {
    const opts = body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    };
    const r = await fetch(path, opts);
    const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
    if (!r.ok || j.error) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  }

  function renderHist() {
    const ul = $("agHist");
    ul.innerHTML = "";
    if (!state.jobs.length) { ul.append(el("li", "muted", "nothing yet")); return; }
    for (const j of state.jobs) {
      const li = el("li", state.shown === j.id ? "on" : "");
      const req = el("button", "req", j.request);
      req.type = "button";
      req.onclick = () => { state.shown = j.id; renderHist(); pollLog(); };
      const meta = el("div", "meta");
      meta.append(el("span", "st " + j.status, j.status), el("span", "", j.target_label));
      if (j.commit) meta.append(el("code", "", j.commit.slice(0, 8)));
      if (j.status === "done" && j.commit) {
        const u = el("button", "undo", "Undo");
        u.type = "button";
        u.onclick = async () => {
          if (!confirm(`Revert "${j.subject || j.request}"?`)) return;
          u.disabled = true;
          try { await api(`/api/improve/${j.id}/undo`, {}); } catch (e) { $("agErr").textContent = e.message; u.disabled = false; }
          refresh();
        };
        meta.append(u);
      }
      li.append(req, meta);
      if (j.error) li.append(el("p", "sum", j.error));
      else if (j.summary && j.status !== "running") li.append(el("p", "sum", j.summary.split("\n").slice(-3).join(" ").slice(0, 280)));
      if (j.restart_needed) li.append(el("p", "sum", "Changed the server: restart webui/server.py to load it."));
      ul.append(li);
    }
  }

  async function pollLog() {
    const id = state.shown;
    const log = $("agLog"), box = $("agLogBox");
    if (!id) { $("agCur").textContent = ""; return; }
    if (state.logFor !== id) { state.logFor = id; state.logNext = 0; log.textContent = ""; }
    let j;
    try { j = await api(`/api/improve/${id}?since=${state.logNext}`); } catch { return; }
    if (state.logFor !== id) return;
    $("agCur").textContent = `${j.status}: ${j.request.slice(0, 90)}`;
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 30;
    for (const line of j.log || []) {
      const cls = /^\[(error|timeout|warn|cancelled)\]|^ {2}!/.test(line) ? "e" : /^[>[]/.test(line) ? "" : "t";
      log.append(el("span", cls, line), "\n");
    }
    state.logNext = j.log_next;
    if (atBottom) box.scrollTop = box.scrollHeight;
  }

  async function refresh() {
    clearTimeout(state.timer);
    try {
      const h = await api("/api/improve");
      state.jobs = h.jobs.filter((j) => j.mode === "agent");
      if (!state.shown && state.jobs.length) state.shown = state.jobs[0].id;
      if (dock.offsetParent) { renderHist(); pollLog(); }
    } catch { /* server busy or restarting; try again on the next tick */ }
    const busy = state.jobs.some((j) => j.status === "queued" || j.status === "running");
    state.timer = setTimeout(refresh, dock.offsetParent ? (busy ? 1500 : 4000) : 30000);
  }

  async function submit() {
    const text = $("agText").value.trim();
    $("agErr").textContent = "";
    if (!text) { $("agText").focus(); return; }
    const b = $("agSubmit");
    b.disabled = true;
    try {
      const j = await api("/api/improve", { request: text, target: $("agTarget").value, mode: "agent" });
      $("agText").value = "";
      state.shown = j.id;
      await refresh();
    } catch (e) {
      $("agErr").textContent = e.message;
    } finally {
      b.disabled = false;
    }
  }

  $("agSubmit").onclick = submit;
  $("agText").addEventListener("keydown", (e) => {
    e.stopPropagation(); // keep page shortcuts out of the textarea
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
  });
  if (window.Shell && window.Shell.onBottom) window.Shell.onBottom((tab) => { if (tab === "agent") refresh(); });
  refresh();
})();
