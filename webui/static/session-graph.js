(function () {
  const graph_dock = document.getElementById("sessionGraphDock");
  if (!graph_dock || window.__sessionGraph) return;
  window.__sessionGraph = true;

  const row_height_in_pixels = 26;
  const lane_width_in_pixels = 16;
  const node_radius_in_pixels = 4.5;
  const lane_colors = ["#4d90d6", "#e3a23d", "#6fcf6a", "#e77fb3", "#a98be0", "#5fb4e6", "#e5544b", "#c9c06a"];
  const refresh_interval_while_visible_in_milliseconds = 4000;
  const refresh_interval_while_hidden_in_milliseconds = 30000;

  function make_element(tag_name, class_name, text_content) {
    const element = document.createElement(tag_name);
    if (class_name) element.className = class_name;
    if (text_content != null) element.textContent = text_content;
    return element;
  }

  const graph_panel = make_element("div", "session-graph");
  graph_panel.innerHTML = `
      <div class="sg-bar">
        <span class="sg-title" id="sgTitle">Session</span>
        <select id="sgWhich" aria-label="Which session"></select>
        <span class="grow"></span>
        <span class="sg-note" id="sgNote"></span>
        <button type="button" class="btn primary" id="sgFinish" title="Squash everything since this session's root into one commit on main; the full history stays under refs/sessions/">Finish session…</button>
      </div>
      <div class="sg-scroll" id="sgScroll"><div class="sg-rows" id="sgRows"></div></div>`;
  graph_dock.append(graph_panel);

  function find_in_panel(element_id) {
    const found_element = graph_panel.querySelector("#" + element_id);
    return found_element;
  }

  const graph_state = { graph: null, viewed_session: "", refresh_timer: null };

  function seconds_ago_text(unix_time) {
    const seconds_ago = Math.round(Date.now() / 1000 - unix_time);
    if (seconds_ago < 60) return "just now";
    if (seconds_ago < 3600) return `${Math.round(seconds_ago / 60)} min ago`;
    if (seconds_ago < 86400) return `${Math.round(seconds_ago / 3600)} h ago`;
    const days_ago_text = `${Math.round(seconds_ago / 86400)} d ago`;
    return days_ago_text;
  }

  function lane_color(lane_index) {
    const color_for_lane = lane_colors[lane_index % lane_colors.length];
    return color_for_lane;
  }

  function working_job_nodes(active_jobs) {
    const job_nodes = active_jobs.filter((active_job) => active_job.tip && active_job.status !== "queued").map((active_job) => ({
      sha: "job-" + active_job.id, short: active_job.slot != null ? `W${active_job.slot + 1}` : "job",
      parents: [active_job.tip], subject: `${active_job.status}${active_job.mode === "agent" ? " (agent)" : ""}: ${active_job.request}`,
      refs: [], time: Date.now() / 1000, working: true,
    }));
    return job_nodes;
  }

  function first_free_lane(awaited_sha_by_lane) {
    const free_lane = awaited_sha_by_lane.indexOf(null);
    return free_lane === -1 ? awaited_sha_by_lane.length : free_lane;
  }

  function lay_out_one_commit(graph_commit, awaited_sha_by_lane) {
    let commit_lane = awaited_sha_by_lane.indexOf(graph_commit.sha);
    if (commit_lane === -1) commit_lane = first_free_lane(awaited_sha_by_lane);
    awaited_sha_by_lane.forEach((awaited_sha, lane_index) => { if (awaited_sha === graph_commit.sha && lane_index !== commit_lane) awaited_sha_by_lane[lane_index] = null; });
    const visible_parents = graph_commit.boundary ? [] : graph_commit.parents;
    awaited_sha_by_lane[commit_lane] = visible_parents[0] || null;
    const parent_lanes = visible_parents.length ? [commit_lane] : [];
    for (const extra_parent of visible_parents.slice(1)) {
      const lane_already_awaiting_parent = awaited_sha_by_lane.indexOf(extra_parent);
      if (lane_already_awaiting_parent !== -1) { parent_lanes.push(lane_already_awaiting_parent); continue; }
      const new_lane = first_free_lane(awaited_sha_by_lane);
      awaited_sha_by_lane[new_lane] = extra_parent;
      parent_lanes.push(new_lane);
    }
    while (awaited_sha_by_lane.length && awaited_sha_by_lane[awaited_sha_by_lane.length - 1] === null) awaited_sha_by_lane.pop();
    const laid_out_row = { graph_commit, commit_lane, parent_lanes, lanes_after: awaited_sha_by_lane.slice() };
    return laid_out_row;
  }

  function lay_out_lanes(graph_commits) {
    const awaited_sha_by_lane = [];
    const laid_out_rows = graph_commits.map((graph_commit) => lay_out_one_commit(graph_commit, awaited_sha_by_lane));
    return laid_out_rows;
  }

  function lane_x(lane_index) {
    const center_x = lane_width_in_pixels / 2 + lane_index * lane_width_in_pixels;
    return center_x;
  }

  function edge_path(start_x, start_y, end_x, end_y) {
    if (start_x === end_x) return `M${start_x} ${start_y}L${end_x} ${end_y}`;
    const curved_path = `M${start_x} ${start_y}C${start_x} ${start_y + (end_y - start_y) * 0.6} ${end_x} ${start_y + (end_y - start_y) * 0.4} ${end_x} ${end_y}`;
    return curved_path;
  }

  function edges_below_row(laid_out_row, next_row, row_center_y) {
    const edge_parts = [];
    laid_out_row.lanes_after.forEach((awaited_sha, lane_index) => {
      if (!awaited_sha || !next_row) return;
      const starts_at_this_node = laid_out_row.parent_lanes.includes(lane_index);
      const start_x = starts_at_this_node ? lane_x(laid_out_row.commit_lane) : lane_x(lane_index);
      const end_x = next_row.graph_commit.sha === awaited_sha ? lane_x(next_row.commit_lane) : lane_x(lane_index);
      const dash = laid_out_row.graph_commit.working && starts_at_this_node ? ' stroke-dasharray="3 3"' : "";
      edge_parts.push(`<path d="${edge_path(start_x, row_center_y, end_x, row_center_y + row_height_in_pixels)}" stroke="${lane_color(lane_index)}" stroke-width="2" fill="none"${dash}/>`);
    });
    return edge_parts;
  }

  function node_for_row(laid_out_row, row_center_y) {
    const node_x = lane_x(laid_out_row.commit_lane);
    const graph_commit = laid_out_row.graph_commit;
    const node_color = lane_color(laid_out_row.commit_lane);
    if (graph_commit.working) return `<circle class="sg-working" cx="${node_x}" cy="${row_center_y}" r="${node_radius_in_pixels}" fill="none" stroke="${node_color}" stroke-width="2"/>`;
    if (graph_commit.boundary) return `<circle cx="${node_x}" cy="${row_center_y}" r="${node_radius_in_pixels + 2}" fill="#202020" stroke="#ffde59" stroke-width="2.5"/>`;
    if (graph_commit.parents.length > 1) return `<circle cx="${node_x}" cy="${row_center_y}" r="${node_radius_in_pixels}" fill="#202020" stroke="${node_color}" stroke-width="2.5"/>`;
    const plain_node = `<circle cx="${node_x}" cy="${row_center_y}" r="${node_radius_in_pixels}" fill="${node_color}"/>`;
    return plain_node;
  }

  function graph_svg_for_rows(laid_out_rows) {
    const lane_count = Math.max(1, ...laid_out_rows.map((laid_out_row) => Math.max(laid_out_row.commit_lane + 1, laid_out_row.lanes_after.length)));
    const svg_width = lane_count * lane_width_in_pixels;
    const svg_height = laid_out_rows.length * row_height_in_pixels;
    const row_center = (row_index) => row_index * row_height_in_pixels + row_height_in_pixels / 2;
    const edge_parts = laid_out_rows.flatMap((laid_out_row, row_index) => edges_below_row(laid_out_row, laid_out_rows[row_index + 1], row_center(row_index)));
    const node_parts = laid_out_rows.map((laid_out_row, row_index) => node_for_row(laid_out_row, row_center(row_index)));
    const graph_svg = `<svg class="sg-svg" width="${svg_width}" height="${svg_height}" viewBox="0 0 ${svg_width} ${svg_height}">${edge_parts.join("")}${node_parts.join("")}</svg>`;
    return { graph_svg, svg_width };
  }

  function reference_badges(graph_commit) {
    const shown_references = graph_commit.refs.map((reference) => reference.replace(/^HEAD -> /, "")).filter((reference) => reference && !reference.startsWith("refs/") && reference !== "HEAD");
    const badges = shown_references.map((reference) => make_element("span", "sg-ref" + (reference === "main" ? " main" : ""), reference.replace(/^improve\/job-/, "job ")));
    return badges;
  }

  function render_one_row(laid_out_row, svg_width) {
    const graph_commit = laid_out_row.graph_commit;
    const row_element = make_element("div", "sg-row" + (graph_commit.working ? " working" : "") + (graph_commit.boundary ? " root" : ""));
    row_element.style.paddingLeft = svg_width + 8 + "px";
    row_element.style.height = row_height_in_pixels + "px";
    row_element.title = graph_commit.working ? graph_commit.subject : `${graph_commit.sha}\n${graph_commit.author}\n${graph_commit.subject}`;
    const subject_text = graph_commit.boundary ? `session root · ${graph_commit.subject}` : graph_commit.subject;
    row_element.append(make_element("code", "sg-sha", graph_commit.short), ...reference_badges(graph_commit),
      make_element("span", "sg-subject", subject_text), make_element("span", "sg-time", graph_commit.working ? "" : seconds_ago_text(graph_commit.time)));
    return row_element;
  }

  function render_session_choices(graph) {
    const session_select = find_in_panel("sgWhich");
    session_select.innerHTML = "";
    session_select.append(Object.assign(make_element("option", "", "Current session (live)"), { value: "" }));
    for (const past_session of graph.past_sessions.slice().reverse()) {
      session_select.append(Object.assign(make_element("option", "", `Session ${past_session.number} · ${past_session.merged_jobs} merged`), { value: String(past_session.number) }));
    }
    session_select.value = graph_state.viewed_session;
  }

  function describe_graph_title(graph) {
    const merged_job_count = graph.commits.filter((graph_commit) => graph_commit.parents.length > 1 && !graph_commit.boundary).length;
    if (!graph.live) return `Session ${graph.session.number} · finished ${seconds_ago_text(graph.session.finished)} · ${merged_job_count} merged`;
    const working_count = graph.active_jobs.filter((active_job) => active_job.status !== "queued").length;
    const live_title = `Session ${graph.session.number} · started ${seconds_ago_text(graph.session.started)} · ${merged_job_count} merged · ${working_count} working`;
    return live_title;
  }

  function render_graph() {
    const graph = graph_state.graph;
    if (!graph) return;
    render_session_choices(graph);
    const graph_commits = graph.live ? working_job_nodes(graph.active_jobs).concat(graph.commits) : graph.commits;
    const laid_out_rows = lay_out_lanes(graph_commits);
    const { graph_svg, svg_width } = graph_svg_for_rows(laid_out_rows);
    const rows_container = find_in_panel("sgRows");
    rows_container.innerHTML = graph_svg;
    rows_container.style.height = laid_out_rows.length * row_height_in_pixels + "px";
    for (const laid_out_row of laid_out_rows) rows_container.append(render_one_row(laid_out_row, svg_width));
    find_in_panel("sgTitle").textContent = describe_graph_title(graph);
    const some_job_is_running = graph.live && graph.active_jobs.some((active_job) => active_job.status !== "queued");
    const finish_button = find_in_panel("sgFinish");
    finish_button.hidden = !graph.live;
    finish_button.disabled = some_job_is_running || graph.main === graph.session.root;
    find_in_panel("sgNote").textContent = some_job_is_running ? "finishing waits for running jobs" : "";
  }

  async function refresh_graph() {
    clearTimeout(graph_state.refresh_timer);
    const graph_is_visible = !!graph_dock.offsetParent;
    if (graph_is_visible) {
      try {
        const session_query = graph_state.viewed_session ? `?number=${graph_state.viewed_session}` : "";
        const graph_response = await (await fetch("/api/improve/session" + session_query)).json();
        if (graph_response.error) throw new Error(graph_response.error);
        graph_state.graph = graph_response;
        render_graph();
      } catch (graph_error) {
        find_in_panel("sgTitle").textContent = "Session graph: " + graph_error.message;
      }
    }
    const next_refresh_in_milliseconds = graph_is_visible ? refresh_interval_while_visible_in_milliseconds : refresh_interval_while_hidden_in_milliseconds;
    graph_state.refresh_timer = setTimeout(refresh_graph, next_refresh_in_milliseconds);
  }

  async function finish_current_session() {
    const session_message = await window.Shell.ask({ title: "Finish session",
      message: "Everything since the session root becomes this one commit on main (the full history is kept). Name it:",
      input: `session ${graph_state.graph.session.number}`, ok_label: "Finish session" });
    if (session_message == null) return;
    find_in_panel("sgFinish").disabled = true;
    try {
      const finish_response = await fetch("/api/improve/session/finish", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: session_message }),
      });
      const finish_result = await finish_response.json();
      if (!finish_response.ok || finish_result.error) throw new Error(finish_result.error || `HTTP ${finish_response.status}`);
      if (window.Shell) window.Shell.log("info", `Session ${finish_result.finished.number} is now one commit, ${finish_result.finished.commit.slice(0, 10)}; full history at ${finish_result.finished.archive}`);
    } catch (finish_error) {
      if (window.Shell) window.Shell.log("error", "Finish session failed: " + finish_error.message);
    }
    refresh_graph();
  }

  function show_improve_view(improve_view_name) {
    document.querySelectorAll("[data-improve-view]").forEach((view_button) => view_button.classList.toggle("on", view_button.dataset.improveView === improve_view_name));
    document.getElementById("improveDock").hidden = improve_view_name !== "queue";
    graph_dock.hidden = improve_view_name !== "graph";
    if (improve_view_name === "graph") refresh_graph();
  }

  find_in_panel("sgFinish").onclick = finish_current_session;
  find_in_panel("sgWhich").onchange = () => { graph_state.viewed_session = find_in_panel("sgWhich").value; refresh_graph(); };
  document.querySelectorAll("[data-improve-view]").forEach((view_button) => { view_button.onclick = () => show_improve_view(view_button.dataset.improveView); });
  if (window.Shell && window.Shell.on_panel_shown) window.Shell.on_panel_shown((panel_id) => { if (panel_id === "improve") refresh_graph(); });
  refresh_graph();
})();
