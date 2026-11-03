// Commits tab: a local, no-token stand-in for github.com/AdalricP/See-Thy-Commits' commit
// graph overlay. Mounts into #commitsDock (editor shell's bottom dock, next to Improve) and
// shows this repo's recent history as a small interactive graph: GET /api/commits
// (webui/shell_api.py runs `git log`), one dot per commit, click one to see the full message.
(function () {
  const dock = document.getElementById("commitsDock");
  if (!dock || window.__commitsTab) return;
  window.__commitsTab = true;

  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };

  const panel = el("div", "imp-docked commits");
  panel.setAttribute("aria-label", "Commits");
  panel.innerHTML = `
      <section>
        <h2 id="cmBranch">Commits</h2>
        <div class="imp-sec" id="cmBox"><ul class="commit-list" id="cmList"><li class="muted">loading…</li></ul></div>
      </section>`;
  dock.append(panel);
  const $ = (id) => panel.querySelector("#" + id);

  const state = { commits: [], shown: null, timer: null };

  // classic simplified git-graph lane assignment: each lane tracks the sha it's waiting for
  function assignLanes(commits) {
    const lanes = [];
    return commits.map((c) => {
      let lane = lanes.indexOf(c.sha);
      if (lane === -1) lane = lanes.indexOf(null);
      if (lane === -1) { lane = lanes.length; lanes.push(null); }
      lanes[lane] = c.parents[0] || null;
      for (let i = 1; i < c.parents.length; i++) {
        let l2 = lanes.indexOf(null);
        if (l2 === -1) l2 = lanes.length;
        lanes[l2] = c.parents[i];
      }
      return { c, lane, lanes: lanes.length };
    });
  }

  const ago = (t) => {
    const s = Math.round(Date.now() / 1000 - t);
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    if (s < 86400 * 30) return `${Math.round(s / 86400)} d ago`;
    return new Date(t * 1000).toLocaleDateString();
  };

  function graphSvg(lane, laneCount, isMerge) {
    const w = Math.max(16, (laneCount + 1) * 14);
    const cx = 7 + lane * 14;
    const parts = [`<line x1="${cx}" y1="0" x2="${cx}" y2="11" stroke="#555"/>`,
      `<line x1="${cx}" y1="13" x2="${cx}" y2="24" stroke="#555"/>`,
      `<circle cx="${cx}" cy="12" r="4" fill="${isMerge ? "#e3a23d" : "#6f9a5f"}"/>`];
    return `<svg width="${w}" height="24" class="commit-dot">${parts.join("")}</svg>`;
  }

  function render() {
    const ul = $("cmList");
    ul.innerHTML = "";
    if (!state.commits.length) { ul.append(el("li", "muted", "no commits found")); return; }
    const rows = assignLanes(state.commits);
    const maxLanes = Math.max(1, ...rows.map((r) => r.lanes));
    for (const { c, lane } of rows) {
      const li = el("li", state.shown === c.sha ? "on" : "");
      li.innerHTML = graphSvg(lane, maxLanes, c.parents.length > 1);
      const body = el("div", "commit-body");
      const subj = el("button", "req", c.subject);
      subj.type = "button";
      subj.onclick = () => { state.shown = state.shown === c.sha ? null : c.sha; render(); };
      const meta = el("div", "meta");
      meta.append(el("code", "", c.short), el("span", "", c.author), el("span", "", ago(c.time)));
      body.append(subj, meta);
      if (state.shown === c.sha) {
        const detail = el("p", "sum", `${c.sha}${c.parents.length > 1 ? ` (merge of ${c.parents.map((p) => p.slice(0, 8)).join(", ")})` : ""}`);
        const copy = el("button", "undo", "Copy hash");
        copy.type = "button";
        copy.onclick = () => navigator.clipboard.writeText(c.sha).catch(() => {});
        body.append(detail, copy);
      }
      li.append(body);
      ul.append(li);
    }
  }

  async function refresh() {
    clearTimeout(state.timer);
    try {
      const r = await fetch("/api/commits");
      const j = await r.json();
      if (j.error) throw new Error(j.error);
      state.commits = j.commits || [];
      $("cmBranch").textContent = j.branch ? `Commits — ${j.branch}` : "Commits";
      if (dock.offsetParent) render();
    } catch (e) {
      if (dock.offsetParent) $("cmList").innerHTML = `<li class="muted">${e.message}</li>`;
    }
    state.timer = setTimeout(refresh, dock.offsetParent ? 15000 : 60000);
  }

  if (window.Shell && window.Shell.onBottom) window.Shell.onBottom((tab) => { if (tab === "commits") render(); });
  refresh();
})();
