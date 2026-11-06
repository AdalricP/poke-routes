(function () {
  const agent_dock = document.getElementById("agentDock");
  if (!agent_dock || window.__agentTab) return;
  window.__agentTab = true;

  const polling_interval_while_busy_in_milliseconds = 1500;
  const polling_interval_while_idle_in_milliseconds = 4000;
  const polling_interval_while_hidden_in_milliseconds = 30000;

  function make_element(tag_name, class_name, text_content) {
    const element = document.createElement(tag_name);
    if (class_name) element.className = class_name;
    if (text_content != null) element.textContent = text_content;
    return element;
  }

  const agent_panel = make_element("div", "imp-docked agent");
  agent_panel.setAttribute("aria-label", "Agent");
  agent_panel.innerHTML = `
      <section>
        <h2><label for="agText">What should the agent do for you?</label></h2>
        <div class="imp-sec">
          <textarea id="agText" placeholder="e.g. make this route feel like a misty lakeside, or give me three desert routes for a canyon region"></textarea>
          <div class="imp-row">
            <select id="agTarget" aria-label="Where" hidden></select>
            <label class="ag-context"><input type="checkbox" id="agSendContext" checked> include the open route</label>
            <button type="button" class="btn primary" id="agSubmit">Send</button>
          </div>
          <p class="imp-note">Makes and tunes routes and regions, finds or applies tilesets, or changes the generator. Runs Claude Code in its own git worktree; whatever it keeps ends up as one commit you can undo. Shift+Enter for a new line.</p>
          <p class="imp-err" id="agErr"></p>
        </div>
      </section>
      <section>
        <h2>Tasks</h2>
        <div class="imp-sec"><ul class="imp-hist" id="agHist"><li class="muted">nothing yet</li></ul></div>
      </section>
      <section>
        <h2>What it's doing</h2>
        <div class="imp-sec" id="agLogBox"><p class="imp-cur" id="agCur"></p><div id="agSteer" hidden></div><pre class="imp-log" id="agLog"></pre></div>
      </section>`;
  agent_dock.append(agent_panel);

  function find_in_panel(element_id) {
    const found_element = agent_panel.querySelector("#" + element_id);
    return found_element;
  }

  const agent_tab_state = { jobs: [], shown_job_id: null, log_job_id: null, next_log_line: 0, refresh_timer: null, server_features: null };

  async function call_improve_api(api_path, request_body) {
    const fetch_options = request_body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request_body),
    };
    const http_response = await fetch(api_path, fetch_options);
    const response_json = await http_response.json().catch(() => ({ error: `HTTP ${http_response.status}` }));
    if (!http_response.ok || response_json.error) throw new Error(response_json.error || `HTTP ${http_response.status}`);
    return response_json;
  }

  function show_error(error_text) {
    find_in_panel("agErr").textContent = error_text || "";
  }

  async function load_server_features() {
    try {
      agent_tab_state.server_features = await call_improve_api("/api/improve/features");
    } catch (feature_load_error) {
      agent_tab_state.server_features = null;
      return;
    }
    render_target_choices(agent_tab_state.server_features.targets || {});
    if (!agent_tab_state.server_features.agent) {
      show_error("The Agent runs Claude Code, and no `claude` command was found. Install Claude Code, run `claude` once to log in, then restart webui/server.py.");
      find_in_panel("agSubmit").disabled = true;
    }
  }

  function render_target_choices(targets_by_name) {
    const target_select = find_in_panel("agTarget");
    target_select.innerHTML = "";
    for (const [target_name, target_label] of Object.entries(targets_by_name)) {
      target_select.append(Object.assign(make_element("option", "", target_label), { value: target_name }));
    }
    target_select.hidden = Object.keys(targets_by_name).length < 2;
  }

  function collect_editor_context() {
    const bench = window.Bench;
    if (!bench || !find_in_panel("agSendContext").checked) return null;
    const generated_route = bench.state && bench.state.result;
    const editor_context = {
      params: bench.params(),
      tiler: generated_route ? generated_route.tiler : null,
      route: generated_route ? { name: generated_route.name, seed: generated_route.seed, width: generated_route.width,
        height: generated_route.height, biome: generated_route.biome, report: generated_route.report || null } : null,
      region: bench.state && bench.state.region ? bench.state.region : null,
    };
    return editor_context;
  }

  async function open_offered_spec_in_editor(job_id, offered_spec_path, open_button) {
    open_button.disabled = true;
    try {
      const job_details = await call_improve_api(`/api/improve/${job_id}?since=0`);
      const offered_spec = (job_details.editor_specs || []).find((candidate_spec) => candidate_spec.path === offered_spec_path);
      if (!offered_spec) throw new Error(`${offered_spec_path} is no longer offered`);
      await window.Bench.loadSpecText(offered_spec.text, offered_spec.path, null);
    } catch (open_error) {
      show_error(`Could not open ${offered_spec_path}: ${open_error.message}`);
    } finally {
      open_button.disabled = false;
    }
  }

  function render_offered_specs(job, history_item) {
    if (!job.editor_specs || !job.editor_specs.length || !window.Bench) return;
    const offered_specs_list = make_element("div", "ag-specs");
    for (const offered_spec of job.editor_specs) {
      const open_button = make_element("button", "btn", "Open in editor");
      open_button.type = "button";
      open_button.title = offered_spec.path;
      open_button.onclick = () => open_offered_spec_in_editor(job.id, offered_spec.path, open_button);
      const spec_row = make_element("div", "ag-spec");
      spec_row.append(open_button, make_element("code", "", offered_spec.path.replace(/^specs\//, "")));
      if (offered_spec.note) spec_row.append(make_element("span", "muted", offered_spec.note));
      offered_specs_list.append(spec_row);
    }
    history_item.append(offered_specs_list);
  }

  function make_job_action_button(button_label, run_action) {
    const action_button = make_element("button", "undo", button_label);
    action_button.type = "button";
    action_button.onclick = async (click_event) => {
      click_event.stopPropagation();
      action_button.disabled = true;
      try { await run_action(); } catch (action_error) { show_error(action_error.message); }
      action_button.disabled = false;
      refresh_agent_tab();
    };
    return action_button;
  }

  async function undo_job(job) {
    const revert_confirmed = await window.Shell.ask({ title: "Undo this task?", message: `Revert "${job.subject || job.request}"?`, ok_label: "Revert" });
    if (!revert_confirmed) return;
    await call_improve_api(`/api/improve/${job.id}/undo`, {});
  }

  async function follow_up_on_job(job) {
    const follow_up_text = await window.Shell.ask({ title: "Follow up", message: "What should change about this result?", input: "", ok_label: "Send" });
    if (!follow_up_text || !follow_up_text.trim()) return;
    const follow_up_job = await call_improve_api(`/api/improve/${job.id}/fix`, { request: follow_up_text.trim() });
    agent_tab_state.shown_job_id = follow_up_job.id;
  }

  function render_job_actions(job, job_meta_row) {
    switch (job.status) {
      case "queued":
      case "running":
        job_meta_row.append(make_job_action_button("Cancel", () => call_improve_api(`/api/improve/${job.id}/cancel`, {})));
        break;
      case "done":
        if (job.commit) job_meta_row.append(make_job_action_button("Undo", () => undo_job(job)));
        job_meta_row.append(make_job_action_button("Follow up", () => follow_up_on_job(job)));
        break;
      case "no-change":
        job_meta_row.append(make_job_action_button("Follow up", () => follow_up_on_job(job)));
        break;
      case "failed":
      case "timeout":
      case "conflict":
      case "cancelled":
        job_meta_row.append(make_job_action_button("Retry", () => call_improve_api(`/api/improve/${job.id}/retry`, {})));
        break;
    }
  }

  function render_one_history_item(job) {
    const history_item = make_element("li", agent_tab_state.shown_job_id === job.id ? "on" : "");
    const request_button = make_element("button", "req", job.request);
    request_button.type = "button";
    request_button.onclick = () => { agent_tab_state.shown_job_id = job.id; render_history(); poll_shown_job_log(); };
    const job_meta_row = make_element("div", "meta");
    job_meta_row.append(make_element("span", "st " + job.status, job.status));
    if (job.commit) job_meta_row.append(make_element("code", "", job.commit.slice(0, 8)));
    render_job_actions(job, job_meta_row);
    history_item.append(request_button, job_meta_row);
    if (job.error) history_item.append(make_element("p", "sum", job.error));
    else if (job.summary && job.status !== "running") history_item.append(make_element("p", "sum", job.summary.split("\n").slice(-4).join(" ").slice(0, 400)));
    if (job.restart_needed) history_item.append(make_element("p", "sum", "Changed the server: restart webui/server.py to load it."));
    render_offered_specs(job, history_item);
    return history_item;
  }

  function render_history() {
    const history_list = find_in_panel("agHist");
    history_list.innerHTML = "";
    if (!agent_tab_state.jobs.length) { history_list.append(make_element("li", "muted", "nothing yet")); return; }
    for (const job of agent_tab_state.jobs) history_list.append(render_one_history_item(job));
  }

  function render_steer_box(job) {
    const steer_box = find_in_panel("agSteer");
    const job_is_running = job.status === "running";
    if (!job_is_running) { steer_box.hidden = true; steer_box.innerHTML = ""; delete steer_box.dataset.jobId; return; }
    if (steer_box.dataset.jobId === job.id) return;
    steer_box.dataset.jobId = job.id;
    steer_box.hidden = false;
    steer_box.innerHTML = "";
    const steer_input = make_element("input", "ag-steer-input");
    steer_input.placeholder = "Tell the running agent something (Enter to send)";
    steer_input.addEventListener("keydown", async (key_event) => {
      key_event.stopPropagation();
      if (key_event.key !== "Enter" || !steer_input.value.trim()) return;
      try {
        await call_improve_api(`/api/improve/${job.id}/steer`, { text: steer_input.value.trim() });
        steer_input.value = "";
      } catch (steer_error) { show_error(steer_error.message); }
    });
    steer_box.append(steer_input);
  }

  function log_line_class(log_line) {
    if (/^\[(error|timeout|warn|cancelled)\]|^ {2}!/.test(log_line)) return "e";
    if (/^[>[]/.test(log_line)) return "";
    return "t";
  }

  async function poll_shown_job_log() {
    const shown_job_id = agent_tab_state.shown_job_id;
    const log_element = find_in_panel("agLog");
    const log_box = find_in_panel("agLogBox");
    if (!shown_job_id) { find_in_panel("agCur").textContent = ""; return; }
    if (agent_tab_state.log_job_id !== shown_job_id) {
      agent_tab_state.log_job_id = shown_job_id;
      agent_tab_state.next_log_line = 0;
      log_element.textContent = "";
    }
    let job_details;
    try { job_details = await call_improve_api(`/api/improve/${shown_job_id}?since=${agent_tab_state.next_log_line}`); } catch (poll_error) { return; }
    if (agent_tab_state.log_job_id !== shown_job_id) return;
    find_in_panel("agCur").textContent = `${job_details.status}: ${job_details.request.slice(0, 90)}`;
    render_steer_box(job_details);
    const log_is_scrolled_to_bottom = log_box.scrollHeight - log_box.scrollTop - log_box.clientHeight < 30;
    for (const log_line of job_details.log || []) log_element.append(make_element("span", log_line_class(log_line), log_line), "\n");
    agent_tab_state.next_log_line = job_details.log_next;
    if (log_is_scrolled_to_bottom) log_box.scrollTop = log_box.scrollHeight;
  }

  function is_agent_job(job) {
    const job_came_from_agent_tab = job.mode === "agent";
    return job_came_from_agent_tab;
  }

  function is_job_in_progress(job) {
    const job_is_in_progress = job.status === "queued" || job.status === "running" || job.status === "merging";
    return job_is_in_progress;
  }

  async function refresh_agent_tab() {
    clearTimeout(agent_tab_state.refresh_timer);
    try {
      const improve_history = await call_improve_api("/api/improve");
      agent_tab_state.jobs = improve_history.jobs.filter(is_agent_job);
      if (!agent_tab_state.shown_job_id && agent_tab_state.jobs.length) agent_tab_state.shown_job_id = agent_tab_state.jobs[0].id;
      if (agent_dock.offsetParent) { render_history(); poll_shown_job_log(); }
    } catch (refresh_error) { /* server busy or restarting; the next tick tries again */ }
    const agent_tab_is_visible = !!agent_dock.offsetParent;
    const some_job_is_in_progress = agent_tab_state.jobs.some(is_job_in_progress);
    const next_refresh_in_milliseconds = !agent_tab_is_visible ? polling_interval_while_hidden_in_milliseconds
      : some_job_is_in_progress ? polling_interval_while_busy_in_milliseconds : polling_interval_while_idle_in_milliseconds;
    agent_tab_state.refresh_timer = setTimeout(refresh_agent_tab, next_refresh_in_milliseconds);
  }

  async function submit_agent_task() {
    const task_text = find_in_panel("agText").value.trim();
    show_error("");
    if (!task_text) { find_in_panel("agText").focus(); return; }
    const submit_button = find_in_panel("agSubmit");
    submit_button.disabled = true;
    try {
      const target_select = find_in_panel("agTarget");
      const submitted_job = await call_improve_api("/api/improve", {
        request: task_text, target: target_select.value || "poke-routes", mode: "agent", editor_context: collect_editor_context(),
      });
      find_in_panel("agText").value = "";
      agent_tab_state.shown_job_id = submitted_job.id;
      await refresh_agent_tab();
    } catch (submit_error) {
      show_error(submit_error.message);
    } finally {
      submit_button.disabled = !(agent_tab_state.server_features && agent_tab_state.server_features.agent);
    }
  }

  find_in_panel("agSubmit").onclick = submit_agent_task;
  find_in_panel("agText").addEventListener("keydown", (key_event) => {
    key_event.stopPropagation();
    if (key_event.key === "Enter" && !key_event.shiftKey && !key_event.isComposing) { key_event.preventDefault(); submit_agent_task(); }
  });
  if (window.Shell && window.Shell.on_panel_shown) window.Shell.on_panel_shown((panel_id) => { if (panel_id === "agent") refresh_agent_tab(); });
  load_server_features().then(refresh_agent_tab);
})();
