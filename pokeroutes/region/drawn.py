"""Routes drawn by hand in the editor, Mini Metro style.

A drawn line is a list of waypoints (cells) with one leg per gap between them; a leg
is ``"h"`` (go across first, then up or down) or ``"v"`` (up or down first). The
line runs from inside the place it starts on to inside the place it ends on. Laying
it walks its cells and cuts them into route rectangles the same way the network
does for a searched path (``Network._grow``), so exits, specs and Scene all work
as for any other route. Sea stretches become sea (Surf) routes; lakes, lava and
other routes are refused.
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

from . import network
from .world import World

Cell = Tuple[int, int]
SHORTEST_SEA_STRETCH = 4
SHORTEST_LAND_STRETCH = 3
MAP_EDGE = 2


class DrawnLineProblem(ValueError):
    pass


def corners_of_line(waypoints: Sequence[Sequence[int]], legs: Sequence[str]) -> List[Cell]:
    corners = [(int(waypoints[0][0]), int(waypoints[0][1]))]
    for index, (end_x, end_y) in enumerate(waypoints[1:]):
        start_x, start_y = corners[-1]
        end_x, end_y = int(end_x), int(end_y)
        leg = legs[index] if index < len(legs) else "h"
        if start_x != end_x and start_y != end_y:
            corners.append((end_x, start_y) if leg == "h" else (start_x, end_y))
        if (end_x, end_y) != corners[-1]:
            corners.append((end_x, end_y))
    return corners


def cells_of_corners(corners: Sequence[Cell]) -> List[Cell]:
    cells = [tuple(corners[0])]
    for end_x, end_y in corners[1:]:
        x, y = cells[-1]
        while x != end_x:
            x += 1 if end_x > x else -1
            cells.append((x, y))
        while y != end_y:
            y += 1 if end_y > y else -1
            cells.append((x, y))
    return cells


def stretch_bounds(modes: Sequence[str]) -> List[Tuple[str, int, int]]:
    stretches = []
    start = 0
    for index in range(1, len(modes) + 1):
        if index == len(modes) or modes[index] != modes[start]:
            stretches.append((modes[start], start, index))
            start = index
    return stretches


def is_short_inner_stretch(mode: str, start: int, end: int, length: int) -> bool:
    shortest = SHORTEST_SEA_STRETCH if mode == "sea" else SHORTEST_LAND_STRETCH
    return start > 0 and end < length and end - start < shortest


def modes_along(w: World, cells: Sequence[Cell]) -> List[str]:
    modes = ["land" if w.land[y, x] else "sea" for x, y in cells]
    for mode, start, end in stretch_bounds(modes):
        if is_short_inner_stretch(mode, start, end, len(modes)):
            modes[start:end] = [modes[start - 1]] * (end - start)
    return modes


def stretch_end(modes: Sequence[str], start: int) -> int:
    end = start
    while end < len(modes) and modes[end] == modes[start]:
        end += 1
    return end


def node_name(net: network.Network, node_index: int) -> str:
    return net.nodes[node_index].get("name") or "another route"


def leading_count_inside(net: network.Network, cells: Sequence[Cell], node_index: int, start: int = 0) -> int:
    index = start
    while index < len(cells) and net.occ[cells[index][1], cells[index][0]] == node_index:
        index += 1
    return index


def first_index_inside_any(net: network.Network, cells: Sequence[Cell], node_indices: Sequence[int]) -> Optional[int]:
    wanted = set(node_indices)
    for index, (x, y) in enumerate(cells):
        if int(net.occ[y, x]) in wanted:
            return index
    return None


def trimmed_cells(net: network.Network, cells: Sequence[Cell], source: int, targets: Sequence[int]) -> List[Cell]:
    inside_source = leading_count_inside(net, cells, source)
    if inside_source == 0:
        raise DrawnLineProblem("a drawn route has to start on a town or a route")
    between = list(cells[inside_source:])
    if targets:
        arrival = first_index_inside_any(net, between, targets)
        if arrival is None:
            raise DrawnLineProblem("finish the route on a town, a route or a landmark")
        between = between[:arrival]
    if not between:
        raise DrawnLineProblem("those two places touch; there is nothing to draw between them")
    return between


def cell_problem(w: World, net: network.Network, cell: Cell, mode: str) -> str:
    x, y = cell
    where = f"at {x}, {y}"
    if x < MAP_EDGE or y < MAP_EDGE or x >= w.W - MAP_EDGE or y >= w.H - MAP_EDGE:
        return f"too close to the map edge {where}"
    if net.occ[y, x] >= 0:
        return f"crosses {node_name(net, int(net.occ[y, x]))} {where}; end the line there or go around"
    reserved_for = net.reserved[y, x]
    if reserved_for and reserved_for != net.allow:
        return f"runs through the landmark {reserved_for} {where}"
    if w.lake[y, x]:
        return f"crosses a lake {where}; Surf routes only run over the sea"
    if mode == "land" and not math.isfinite(net.lcost[y, x]):
        return f"crosses ground no route can take (lava or a closed area) {where}"
    return ""


def first_cell_problem(w: World, net: network.Network, cells: Sequence[Cell], modes: Sequence[str]) -> str:
    for cell, mode in zip(cells, modes):
        problem = cell_problem(w, net, cell, mode)
        if problem:
            return problem
    return ""


def no_room_problem(cell: Cell) -> str:
    return f"no room for a route at {cell[0]}, {cell[1]}: too close to another route, a town or the map edge"


def lay_rectangles(net: network.Network, link: dict, cells: List[Cell], modes: List[str], source: int) -> List[int]:
    rectangles: List[int] = []
    previous = source
    start = 0
    while start < len(cells):
        mode = modes[start]
        piece = cells[start:stretch_end(modes, start)]
        rect, used, _reaches_end = net._grow(piece, previous, None, {"mode": mode})
        if rect is None:
            raise DrawnLineProblem(no_room_problem(piece[0]))
        node_index = net.add_node("route", rect, link=link["name"], mode=mode, kind_link="drawn")
        net._connect(previous, node_index, piece[0], link)
        rectangles.append(node_index)
        end = leading_count_inside(net, cells, node_index, start + len(used))
        net.nodes[node_index]["paths"].append(cells[start:end])
        start = end
        if start < len(cells) and net.occ[cells[start][1], cells[start][0]] >= 0:
            raise DrawnLineProblem(no_room_problem(cells[start]))
        previous = node_index
    return rectangles


def finish_at_target(net: network.Network, link: dict, last_rectangle: int, last_cell: Cell) -> None:
    if link.get("dst"):
        net._connect(last_rectangle, ("multi", [k for k, _ in link["dst"]]), last_cell, link, into=True)
        return
    net.nodes[last_rectangle]["goal"] = list(last_cell)
    net.nodes[last_rectangle]["goal_landmark"] = link.get("landmark")


def lay_drawn(net: network.Network, link: dict) -> List[int]:
    w = net.w
    source = link["src"][0][0]
    targets = [k for k, _ in link.get("dst", [])]
    cells = trimmed_cells(net, link["cells"], source, targets)
    net.allow = link.get("landmark") or ""
    modes = modes_along(w, cells)
    problem = first_cell_problem(w, net, cells, modes)
    if problem:
        raise DrawnLineProblem(problem)
    rectangles = lay_rectangles(net, link, cells, modes, source)
    finish_at_target(net, link, rectangles[-1], cells[-1])
    link["mode"] = "sea" if all(mode == "sea" for mode in modes) else "land"
    return rectangles


def resolved_link(w: World, net: network.Network, drawn: dict) -> dict:
    source = network.node_for_ref(net, drawn.get("src_ref"))
    if source is None:
        raise DrawnLineProblem("the place this route starts on is gone")
    link = dict(name=drawn["name"], kind="drawn", src=[(source, 0.0)], mode="land",
                cells=cells_of_corners(corners_of_line(drawn["waypoints"], drawn.get("legs") or [])))
    target_ref = drawn.get("dst_ref") or {}
    if "landmark" in target_ref:
        landmark = w.landmark(target_ref["landmark"])
        if landmark is None:
            raise DrawnLineProblem("the landmark this route ends at is gone")
        link["landmark"] = landmark.id
        return link
    target = network.node_for_ref(net, target_ref)
    if target is None or target == source:
        raise DrawnLineProblem("finish the route on a different town or route")
    link["dst"] = [(target, 0.0)]
    return link


def link_record(net: network.Network, drawn: dict, link: dict, rectangles: List[int]) -> dict:
    record = dict(name=drawn["name"], kind="drawn", mode=link["mode"],
                  routes=[net.nodes[k]["name"] for k in rectangles], landmark=link.get("landmark"),
                  src_ref=drawn.get("src_ref"), dst_ref=drawn.get("dst_ref"),
                  waypoints=[[int(x), int(y)] for x, y in drawn["waypoints"]], legs=list(drawn.get("legs") or []))
    return record


def add_drawn_line(w: World, drawn: dict) -> List[str]:
    """Lay ``drawn`` (name, waypoints, legs, src_ref, dst_ref) with every route in
    ``w.routes`` kept where it is. Returns the new route names."""
    net = network.Network(w)
    network.add_kept(w, net, list(w.routes), list(w.links))
    link = resolved_link(w, net, drawn)
    rectangles = lay_drawn(net, link)
    network.settle_routes(w, net)
    w.links = list(w.links) + [link_record(net, drawn, link, rectangles)]
    w.routes = [network.route_json(w, net, n) for n in net.nodes if n["kind"] == "route"]
    return [net.nodes[k]["name"] for k in rectangles]


def without_drawn_line(w: World, link_name: str) -> None:
    gone = {r["name"] for r in w.routes if r.get("link") == link_name}
    w.routes = [r for r in w.routes if r["name"] not in gone]
    for route in w.routes:
        route["exits"] = [e for e in route.get("exits", []) if e.get("to") not in gone]
    w.links = [l for l in w.links if l["name"] != link_name]


def next_line_name(w: World) -> str:
    taken = {l["name"] for l in w.links}
    number = 1
    while f"Drawn line {number}" in taken:
        number += 1
    return f"Drawn line {number}"

