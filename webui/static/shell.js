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

  // ------------------------------------------------------------ docking layout
  const DOCK = window.DOCK_LAYOUT;
  let layout = DOCK.load_layout(store);
  const resizeCbs = [];
  const centerCbs = [];
  const panel_shown_callbacks = [];
  const dock_area = $("dockArea");
  const dock_parking = $("dockParking");
  const parked_tabs = $("dockTabs");
  const drop_highlight = $("dockDropHighlight");
  const tab_ghost = $("dockTabGhost");
  const panel_elements = Object.fromEntries([...document.querySelectorAll("[data-panel]")].map((panel_element) => [panel_element.dataset.panel, panel_element]));
  const tab_buttons = Object.fromEntries([...document.querySelectorAll("[data-panel-tab]")].map((tab_button) => [tab_button.dataset.panelTab, tab_button]));
  const panel_titles = Object.fromEntries(Object.entries(tab_buttons).map(([panel_id, tab_button]) => [panel_id, tab_button.firstChild.textContent.trim()]));
  const unavailable_panels = new Set(["improve"]);
  const tab_drag_start_distance_in_pixels = 5;
  const smallest_panel_side_in_pixels = 90;
  const middle_mouse_button = 1;
  let rendered_group_geometry_sources = [];
  let previously_visible_panels = [];
  let previously_shown_views_signature = "";

  function effective_dock_tree() {
    const tree = DOCK.tree_without_panels(layout.dock, [...unavailable_panels]);
    return tree;
  }

  function saveLayout() { DOCK.save_layout(store, layout); }

  function is_panel_open(panel_id) {
    const tree = effective_dock_tree();
    const is_open = !!tree && DOCK.panels_in_tree(tree).includes(panel_id);
    return is_open;
  }

  function is_panel_visible(panel_id) {
    const tree = effective_dock_tree();
    const is_visible = !!tree && DOCK.visible_panels(tree).includes(panel_id);
    return is_visible;
  }

  function shown_views() {
    const tree = effective_dock_tree();
    const view_panel_ids = tree ? DOCK.visible_view_panels(tree) : [];
    return view_panel_ids;
  }

  function add_close_button_to_tab(panel_id) {
    const close_button = el("span", "dock-tab-close");
    close_button.setAttribute("role", "button");
    close_button.setAttribute("aria-label", `Close ${panel_titles[panel_id]}`);
    close_button.addEventListener("pointerdown", (pointer_event) => pointer_event.stopPropagation());
    close_button.addEventListener("click", (click_event) => { click_event.stopPropagation(); close_panel(panel_id); });
    tab_buttons[panel_id].append(close_button);
  }

  function tab_button_for_group(panel_id, group) {
    const tab_button = tab_buttons[panel_id];
    const is_active_tab = group.active === panel_id;
    tab_button.classList.toggle("on", is_active_tab);
    tab_button.setAttribute("aria-selected", is_active_tab);
    return tab_button;
  }

  function element_of_group(group, group_path) {
    const group_element = el("div", "dock-group");
    const tab_bar = el("div", "dock-group-tabs");
    const group_body = el("div", "dock-group-body");
    tab_bar.setAttribute("role", "tablist");
    for (const panel_id of group.tabs) tab_bar.append(tab_button_for_group(panel_id, group));
    group_body.append(panel_elements[group.active]);
    group_element.append(tab_bar, group_body);
    rendered_group_geometry_sources.push({ group_path, group_element, tab_bar, group_body, tabs: group.tabs });
    return group_element;
  }

  function divider_element(split, split_path, divider_index) {
    const divider = el("div", `dock-divider ${split.direction}`);
    divider.setAttribute("role", "separator");
    divider.setAttribute("aria-orientation", split.direction === "row" ? "vertical" : "horizontal");
    divider.addEventListener("pointerdown", (pointer_event) => start_divider_drag(pointer_event, divider, split_path, divider_index));
    return divider;
  }

  function append_split_child(split_element, split, split_path, child_index) {
    if (child_index > 0) split_element.append(divider_element(split, split_path, child_index - 1));
    const child_element = element_of_dock_node(split.children[child_index], split_path.concat([child_index]));
    child_element.style.flex = `${split.sizes[child_index]} 1 0px`;
    split_element.append(child_element);
  }

  function element_of_split(split, split_path) {
    const split_element = el("div", `dock-split ${split.direction}`);
    split.children.forEach((child_node, child_index) => append_split_child(split_element, split, split_path, child_index));
    return split_element;
  }

  function element_of_dock_node(dock_node, node_path) {
    const node_element = dock_node.kind === "group" ? element_of_group(dock_node, node_path) : element_of_split(dock_node, node_path);
    return node_element;
  }

  function scroll_positions_of_panels() {
    const scrollable_elements = [...dock_area.querySelectorAll(".dock-panel, .panel-scroll, .con-list, .imp-sec")];
    const scroll_positions = scrollable_elements.filter((scroll_element) => scroll_element.scrollTop > 0).map((scroll_element) => [scroll_element, scroll_element.scrollTop]);
    return scroll_positions;
  }

  function park_hidden_panels(tree) {
    const shown_panel_ids = tree ? DOCK.visible_panels(tree) : [];
    const open_panel_ids = tree ? DOCK.panels_in_tree(tree) : [];
    for (const [panel_id, panel_element] of Object.entries(panel_elements)) if (!shown_panel_ids.includes(panel_id)) dock_parking.append(panel_element);
    for (const [panel_id, tab_button] of Object.entries(tab_buttons)) if (!open_panel_ids.includes(panel_id)) parked_tabs.append(tab_button);
  }

  function render_dock_layout() {
    const tree = effective_dock_tree();
    const scroll_positions = scroll_positions_of_panels();
    rendered_group_geometry_sources = [];
    park_hidden_panels(tree);
    dock_area.replaceChildren(tree ? element_of_dock_node(tree, []) : el("div", "dock-empty"));
    for (const [scroll_element, scroll_top] of scroll_positions) scroll_element.scrollTop = scroll_top;
    announce_layout_changes(tree);
  }

  function focus_first_shown_view_if_focus_hidden(view_panel_ids) {
    if (!view_panel_ids.length || view_panel_ids.includes(layout.center)) return false;
    layout.center = view_panel_ids[0];
    return true;
  }

  function mark_focused_view() {
    const view_panel_ids = shown_views();
    for (const view_panel_id of DOCK.VIEW_PANEL_IDS) {
      const is_focused_view = view_panel_id === layout.center && view_panel_ids.includes(view_panel_id);
      panel_elements[view_panel_id].classList.toggle("active-pane", is_focused_view && view_panel_ids.length > 1);
      tab_buttons[view_panel_id].classList.toggle("focused", is_focused_view);
    }
  }

  function announce_panel_shown(panel_id) {
    if (panel_id === "console") seenProblems = problems();
    updateConsoleBadge();
    panel_shown_callbacks.forEach((panel_shown_callback) => panel_shown_callback(panel_id));
  }

  function announce_layout_changes(tree) {
    const visible_panel_ids = tree ? DOCK.visible_panels(tree) : [];
    const newly_visible_panel_ids = visible_panel_ids.filter((panel_id) => !previously_visible_panels.includes(panel_id));
    previously_visible_panels = visible_panel_ids;
    const view_panel_ids = shown_views();
    const has_focus_moved = focus_first_shown_view_if_focus_hidden(view_panel_ids);
    const shown_views_signature = view_panel_ids.join(" ");
    const have_shown_views_changed = shown_views_signature !== previously_shown_views_signature;
    previously_shown_views_signature = shown_views_signature;
    mark_focused_view();
    newly_visible_panel_ids.forEach(announce_panel_shown);
    if (has_focus_moved || have_shown_views_changed) centerCbs.forEach((center_callback) => center_callback(layout.center));
    resizeCbs.forEach((resize_callback) => resize_callback());
  }

  function apply_dock_tree(new_tree) {
    layout.dock = new_tree;
    const open_panel_ids = new_tree ? DOCK.panels_in_tree(new_tree) : [];
    layout.closed_panels = layout.closed_panels.filter((panel_id) => !open_panel_ids.includes(panel_id));
    render_dock_layout();
    saveLayout();
  }

  function focus_view(view_panel_id) {
    if (!DOCK.VIEW_PANEL_IDS.includes(view_panel_id) || layout.center === view_panel_id) return;
    layout.center = view_panel_id;
    mark_focused_view();
    saveLayout();
    centerCbs.forEach((center_callback) => center_callback(view_panel_id));
  }

  function show_panel(panel_id) {
    if (!DOCK.PANEL_IDS.includes(panel_id) || unavailable_panels.has(panel_id)) return;
    const tree = effective_dock_tree();
    if (tree && DOCK.visible_panels(tree).includes(panel_id)) return;
    apply_dock_tree(DOCK.tree_with_panel_opened(tree, panel_id));
  }

  function setCenter(view_panel_id) {
    if (!DOCK.VIEW_PANEL_IDS.includes(view_panel_id)) return;
    show_panel(view_panel_id);
    focus_view(view_panel_id);
  }

  function close_panel(panel_id) {
    const tree = effective_dock_tree();
    if (!tree || DOCK.panels_in_tree(tree).length <= 1) return;
    layout.closed_panels = layout.closed_panels.concat([panel_id]);
    apply_dock_tree(DOCK.tree_without_panel(tree, panel_id));
  }

  function first_other_tab_beside(tree, panel_id) {
    const tabs_of_group = DOCK.node_at_path(tree, DOCK.group_path_of_panel(tree, panel_id)).tabs;
    const first_other_tab = tabs_of_group.find((other_panel_id) => other_panel_id !== panel_id) || panel_id;
    return first_other_tab;
  }

  function tree_with_returning_panel(panel_id) {
    const is_missing_from_layout = !layout.dock || !DOCK.panels_in_tree(layout.dock).includes(panel_id);
    if (!is_missing_from_layout || layout.closed_panels.includes(panel_id)) return layout.dock;
    const tree_with_panel = DOCK.tree_with_panel_opened(layout.dock, panel_id);
    const tree_keeping_neighbour_in_front = DOCK.tree_with_active_tab(tree_with_panel, first_other_tab_beside(tree_with_panel, panel_id));
    return tree_keeping_neighbour_in_front;
  }

  function set_panel_available(panel_id, is_available) {
    const was_available = !unavailable_panels.has(panel_id);
    if (was_available === !!is_available) return;
    if (is_available) unavailable_panels.delete(panel_id); else unavailable_panels.add(panel_id);
    if (is_available) layout.dock = tree_with_returning_panel(panel_id);
    render_dock_layout();
  }

  function set_panel_title(panel_id, title) {
    const tab_button = tab_buttons[panel_id];
    if (tab_button && tab_button.firstChild.textContent !== title) tab_button.firstChild.textContent = title;
  }

  function resetLayout() {
    layout = DOCK.default_layout();
    applyFolds();
    render_dock_layout();
    saveLayout();
    log("info", "Layout reset to the default.");
  }

  function window_menu_item_of_panel(panel_id) {
    const menu_item = {
      id: "w-panel-" + panel_id, label: panel_titles[panel_id],
      checked: () => is_panel_open(panel_id), enabled: () => !unavailable_panels.has(panel_id),
      run: () => (DOCK.VIEW_PANEL_IDS.includes(panel_id) ? setCenter(panel_id) : show_panel(panel_id)),
    };
    return menu_item;
  }

  function window_menu_items() {
    const panel_menu_items = DOCK.PANEL_IDS.map(window_menu_item_of_panel);
    return panel_menu_items;
  }

  function rect_of_element(measured_element) {
    const measured_rect = measured_element.getBoundingClientRect();
    const rect = { x: measured_rect.left, y: measured_rect.top, w: measured_rect.width, h: measured_rect.height };
    return rect;
  }

  function focused_view_group_rect() {
    const focused_view_element = panel_elements[layout.center];
    const group_element = focused_view_element && focused_view_element.closest(".dock-group");
    const measured_rect = rect_of_element(group_element || dock_area);
    const rounded_rect = { x: Math.round(measured_rect.x), y: Math.round(measured_rect.y), w: Math.round(measured_rect.w), h: Math.round(measured_rect.h) };
    return rounded_rect;
  }

  function geometry_of_rendered_group(geometry_source) {
    const group_geometry = {
      group_path: geometry_source.group_path,
      rect: rect_of_element(geometry_source.group_element),
      tab_bar_rect: rect_of_element(geometry_source.tab_bar),
      body_rect: rect_of_element(geometry_source.group_body),
      tab_rects: geometry_source.tabs.map((panel_id) => rect_of_element(tab_buttons[panel_id])),
    };
    return group_geometry;
  }

  function place_fixed_element(fixed_element, rect) {
    fixed_element.style.transform = `translate(${Math.round(rect.x)}px, ${Math.round(rect.y)}px)`;
    fixed_element.style.width = `${Math.round(rect.w)}px`;
    fixed_element.style.height = `${Math.round(rect.h)}px`;
  }

  function show_drop_highlight(drop_target, geometries, dock_area_rect) {
    const highlight_rect = DOCK.highlight_rect_of_drop_target(drop_target, geometries, dock_area_rect);
    drop_highlight.hidden = !highlight_rect;
    if (!highlight_rect) return;
    drop_highlight.classList.toggle("tab-marker", drop_target.kind === "tabs");
    place_fixed_element(drop_highlight, highlight_rect);
  }

  function listen_until_released(capturing_element, on_move, on_release) {
    const finish_listening = (release_event) => {
      capturing_element.removeEventListener("pointermove", on_move);
      capturing_element.removeEventListener("pointerup", finish_listening);
      capturing_element.removeEventListener("pointercancel", finish_listening);
      on_release(release_event.type === "pointerup");
    };
    capturing_element.addEventListener("pointermove", on_move);
    capturing_element.addEventListener("pointerup", finish_listening);
    capturing_element.addEventListener("pointercancel", finish_listening);
  }

  function start_tab_gesture(pointer_event, panel_id) {
    if (pointer_event.button === middle_mouse_button) { pointer_event.preventDefault(); close_panel(panel_id); return; }
    if (pointer_event.button !== 0) return;
    const tab_button = tab_buttons[panel_id];
    const tab_drag = {
      panel_id, start_point: { x: pointer_event.clientX, y: pointer_event.clientY }, is_dragging: false,
      geometries: null, dock_area_rect: null, drop_target: null, pending_point: null, frame_request: 0,
    };
    tab_button.setPointerCapture(pointer_event.pointerId);
    listen_until_released(tab_button,
      (move_event) => follow_tab_drag(tab_drag, { x: move_event.clientX, y: move_event.clientY }),
      (was_released_normally) => finish_tab_gesture(tab_drag, was_released_normally));
  }

  function begin_tab_drag(tab_drag) {
    tab_drag.is_dragging = true;
    tab_drag.geometries = rendered_group_geometry_sources.map(geometry_of_rendered_group);
    tab_drag.dock_area_rect = rect_of_element(dock_area);
    tab_ghost.textContent = panel_titles[tab_drag.panel_id];
    tab_ghost.hidden = false;
    tab_buttons[tab_drag.panel_id].classList.add("tab-dragging");
    document.body.classList.add("dock-dragging");
  }

  function follow_tab_drag(tab_drag, point) {
    const distance_moved = Math.hypot(point.x - tab_drag.start_point.x, point.y - tab_drag.start_point.y);
    if (!tab_drag.is_dragging && distance_moved < tab_drag_start_distance_in_pixels) return;
    if (!tab_drag.is_dragging) begin_tab_drag(tab_drag);
    tab_drag.pending_point = point;
    if (!tab_drag.frame_request) tab_drag.frame_request = requestAnimationFrame(() => show_tab_drag_frame(tab_drag));
  }

  function show_tab_drag_frame(tab_drag) {
    tab_drag.frame_request = 0;
    if (!tab_drag.is_dragging || !tab_drag.pending_point) return;
    const point = tab_drag.pending_point;
    tab_ghost.style.transform = `translate(${Math.round(point.x + 12)}px, ${Math.round(point.y + 10)}px)`;
    tab_drag.drop_target = DOCK.drop_target_at_point(tab_drag.geometries, point, tab_drag.dock_area_rect);
    show_drop_highlight(tab_drag.drop_target, tab_drag.geometries, tab_drag.dock_area_rect);
  }

  function end_tab_drag_visuals(panel_id) {
    tab_ghost.hidden = true;
    drop_highlight.hidden = true;
    tab_buttons[panel_id].classList.remove("tab-dragging");
    document.body.classList.remove("dock-dragging");
  }

  function finish_tab_gesture(tab_drag, was_released_normally) {
    if (tab_drag.frame_request) cancelAnimationFrame(tab_drag.frame_request);
    if (!tab_drag.is_dragging) { if (was_released_normally) activate_tab(tab_drag.panel_id); return; }
    show_tab_drag_frame(tab_drag);
    end_tab_drag_visuals(tab_drag.panel_id);
    if (!was_released_normally || !tab_drag.drop_target) return;
    drop_panel(tab_drag.panel_id, tab_drag.drop_target);
  }

  function drop_panel(panel_id, drop_target) {
    const tree = effective_dock_tree();
    const new_tree = DOCK.tree_after_dropping_panel(tree, panel_id, drop_target);
    if (new_tree !== tree) apply_dock_tree(new_tree);
    if (DOCK.VIEW_PANEL_IDS.includes(panel_id)) focus_view(panel_id);
  }

  function activate_tab(panel_id) {
    const tree = effective_dock_tree();
    const group_path = DOCK.group_path_of_panel(tree, panel_id);
    if (!group_path) return;
    if (DOCK.node_at_path(tree, group_path).active !== panel_id) apply_dock_tree(DOCK.tree_with_active_tab(tree, panel_id));
    if (DOCK.VIEW_PANEL_IDS.includes(panel_id)) focus_view(panel_id);
  }

  function start_divider_drag(pointer_event, divider, split_path, divider_index) {
    if (pointer_event.button !== 0) return;
    pointer_event.preventDefault();
    const split_element = divider.parentElement;
    const is_row = split_element.classList.contains("row");
    const split_rect = rect_of_element(split_element);
    const split_node = DOCK.node_at_path(effective_dock_tree(), split_path);
    const divider_drag = {
      split_path, divider_index, is_row,
      split_length_in_pixels: Math.max(1, is_row ? split_rect.w : split_rect.h),
      child_elements: [...split_element.children].filter((child_element) => !child_element.classList.contains("dock-divider")),
      start_position: is_row ? pointer_event.clientX : pointer_event.clientY,
      start_sizes: split_node.sizes.slice(), sizes: split_node.sizes.slice(), pending_position: null, frame_request: 0,
    };
    divider.setPointerCapture(pointer_event.pointerId);
    divider.classList.add("drag");
    document.body.classList.add("resizing", is_row ? "v" : "h");
    listen_until_released(divider,
      (move_event) => follow_divider_drag(divider_drag, is_row ? move_event.clientX : move_event.clientY),
      () => finish_divider_drag(divider_drag, divider));
  }

  function follow_divider_drag(divider_drag, pointer_position) {
    divider_drag.pending_position = pointer_position;
    if (!divider_drag.frame_request) divider_drag.frame_request = requestAnimationFrame(() => show_divider_drag_frame(divider_drag));
  }

  function show_divider_drag_frame(divider_drag) {
    divider_drag.frame_request = 0;
    if (divider_drag.pending_position == null) return;
    const moved_fraction = (divider_drag.pending_position - divider_drag.start_position) / divider_drag.split_length_in_pixels;
    const smallest_fraction = smallest_panel_side_in_pixels / divider_drag.split_length_in_pixels;
    divider_drag.sizes = DOCK.sizes_after_divider_drag(divider_drag.start_sizes, divider_drag.divider_index, moved_fraction, smallest_fraction);
    for (const child_index of [divider_drag.divider_index, divider_drag.divider_index + 1]) divider_drag.child_elements[child_index].style.flex = `${divider_drag.sizes[child_index]} 1 0px`;
  }

  function finish_divider_drag(divider_drag, divider) {
    if (divider_drag.frame_request) cancelAnimationFrame(divider_drag.frame_request);
    show_divider_drag_frame(divider_drag);
    divider.classList.remove("drag");
    document.body.classList.remove("resizing", "v", "h");
    layout.dock = DOCK.tree_with_split_sizes(effective_dock_tree(), divider_drag.split_path, divider_drag.sizes);
    saveLayout();
    resizeCbs.forEach((resize_callback) => resize_callback());
  }

  function wire_tab_button(panel_id) {
    const tab_button = tab_buttons[panel_id];
    add_close_button_to_tab(panel_id);
    tab_button.setAttribute("role", "tab");
    tab_button.addEventListener("pointerdown", (pointer_event) => start_tab_gesture(pointer_event, panel_id));
    tab_button.addEventListener("auxclick", (click_event) => click_event.preventDefault());
    tab_button.addEventListener("keydown", (key_event) => { if (key_event.key === "Enter" || key_event.key === " ") { key_event.preventDefault(); activate_tab(panel_id); } });
  }

  Object.keys(tab_buttons).forEach(wire_tab_button);
  for (const view_panel_id of DOCK.VIEW_PANEL_IDS) panel_elements[view_panel_id].addEventListener("pointerdown", () => focus_view(view_panel_id), true);
  window.addEventListener("resize", () => resizeCbs.forEach((resize_callback) => resize_callback()));

  // pinned panels: another module (e.g. improve.js) offers a panel via registerPanel();
  // dragging its header onto Hierarchy or Inspector parks it there, beneath that panel's
  // own content, until it's dragged back out or its "Unpin" button is clicked
  const pinHosts = { left: $("dockLeftExtra"), right: $("dockRightExtra") };
  const pin_zones = { left: panel_elements.hierarchy, right: panel_elements.inspector };
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
    const zone = pin_zones[side];
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
  window.addEventListener("dragend", () => { pin_zones.left.classList.remove("drop-target"); pin_zones.right.classList.remove("drop-target"); });

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
    if (is_panel_visible("console")) seenProblems = problems();
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
  function ask_in_page(question) {
    const ask_dialog = $("dlgAsk");
    const ask_input = $("askInput");
    const wants_text = typeof question.input === "string";
    $("askTitle").textContent = question.title || "";
    $("askMessage").textContent = question.message || "";
    $("askOk").textContent = question.ok_label || "OK";
    ask_input.hidden = !wants_text;
    ask_input.value = wants_text ? question.input : "";
    closeMenus();
    const answer_promise = new Promise((resolve_answer) => {
      ask_dialog.addEventListener("close", () => {
        const was_confirmed = ask_dialog.returnValue === "ok";
        if (!was_confirmed) { resolve_answer(null); return; }
        resolve_answer(wants_text ? ask_input.value : true);
      }, { once: true });
    });
    ask_dialog.returnValue = "";
    ask_dialog.showModal();
    if (wants_text) { ask_input.focus(); ask_input.select(); }
    return answer_promise;
  }

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
    saveLayout, setCenter, show_panel, close_panel, is_panel_open, is_panel_visible, shown_views, set_panel_available, set_panel_title,
    window_menu_items, focused_view_group_rect, resetLayout,
    onResize: (f) => resizeCbs.push(f),
    onCenter: (f) => centerCbs.push(f),
    on_panel_shown: (panel_shown_callback) => panel_shown_callbacks.push(panel_shown_callback),
    log, status,
    registerPanel,
    unpinPanel: unpinByKey,
    setMenus(m, extra) { menus = m; extraKeys = extra || []; renderMenubar(); },
    closeMenus,
    openDialog,
    ask: ask_in_page,
    showShortcuts() { renderKeys(); openDialog("dlgKeys"); },
    fmt: (s) => SL.formatShortcut(s, mac),
  };

  applyFolds();
  render_dock_layout();
})();
