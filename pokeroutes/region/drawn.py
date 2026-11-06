"""Routes painted freehand in the editor.

A drawn line is a list of waypoints (cells) with one leg per gap between them; a leg
is ``"h"`` (go across first, then up or down) or ``"v"`` (up or down first). The
editor sends the corners of a freehand stroke, so the legs rarely matter.

The stroke is split wherever it passes through a town or a route: each piece in
between is laid on its own, joined to the place before it and the place after it.
A piece that ends on open ground is a dead end; a stroke that only starts on open
ground is turned round so it grows out of the place it reaches. Every piece is cut
into route rectangles the way the network does for a searched path
(``Network._grow``), so exits, roles, specs and Scene work as for any other route.
Water never stops a stroke: sea and lake stretches become sea (Surf) routes, short
ones (and rivers, which are land cells) stay part of the land route as a crossing.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from . import network
from .world import World

Cell = Tuple[int, int]
Piece = Tuple[Optional[int], List[Cell], Optional[int]]
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


def is_water(w: World, cell: Cell) -> bool:
    x, y = cell
    return bool(not w.land[y, x] or w.lake[y, x])


def modes_along(w: World, cells: Sequence[Cell]) -> List[str]:
    modes = ["sea" if is_water(w, cell) else "land" for cell in cells]
    for mode, start, end in stretch_bounds(modes):
        if is_short_inner_stretch(mode, start, end, len(modes)):
            modes[start:end] = [modes[start - 1]] * (end - start)
    return modes


def stretch_end(modes: Sequence[str], start: int) -> int:
    end = start
    while end < len(modes) and modes[end] == modes[start]:
        end += 1
    return end


def node_at(net: network.Network, cell: Cell) -> Optional[int]:
    node_index = int(net.occ[cell[1], cell[0]])
    return node_index if node_index >= 0 else None


def leading_count_inside(net: network.Network, cells: Sequence[Cell], node_index: int, start: int = 0) -> int:
    index = start
    while index < len(cells) and net.occ[cells[index][1], cells[index][0]] == node_index:
        index += 1
    return index


def stroke_pieces(net: network.Network, cells: Sequence[Cell]) -> List[Piece]:
    pieces: List[Piece] = []
    current: List[Cell] = []
    before = node_at(net, cells[0])
    for cell in cells:
        node_index = node_at(net, cell)
        if node_index is None:
            current.append(cell)
            continue
        if current:
            pieces.append((before, current, node_index))
            current = []
        before = node_index
    if current:
        pieces.append((before, current, None))
    return pieces


def anchored_pieces(pieces: List[Piece]) -> List[Piece]:
    if not pieces:
        raise DrawnLineProblem("that stroke only runs over towns and routes that are already there")
    source, cells, target = pieces[0]
    if source is None and target is None:
        raise DrawnLineProblem("start or end the route on a town or a route")
    if source is None:
        pieces[0] = (target, cells[::-1], None)
    return pieces


def edge_problem(w: World, cells: Sequence[Cell]) -> str:
    for x, y in cells:
        if x < MAP_EDGE or y < MAP_EDGE or x >= w.W - MAP_EDGE or y >= w.H - MAP_EDGE:
            return f"too close to the map edge at {x}, {y}"
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


def without_cells_in_new_rectangles(net: network.Network, piece: Piece) -> Piece:
    source, cells, target = piece
    while cells and node_at(net, cells[0]) is not None:
        source = node_at(net, cells[0])
        cells = cells[1:]
    while cells and node_at(net, cells[-1]) is not None:
        target = node_at(net, cells[-1])
        cells = cells[:-1]
    return source, cells, target


def lay_piece(net: network.Network, link: dict, piece: Piece) -> List[int]:
    source, cells, target = without_cells_in_new_rectangles(net, piece)
    if not cells:
        return []
    problem = edge_problem(net.w, cells)
    if problem:
        raise DrawnLineProblem(problem)
    rectangles = lay_rectangles(net, link, cells, modes_along(net.w, cells), source)
    if target is not None:
        net._connect(rectangles[-1], ("multi", [target]), cells[-1], link, into=True)
    else:
        net.nodes[rectangles[-1]]["goal"] = list(cells[-1])
    return rectangles


def lay_drawn(net: network.Network, link: dict) -> Tuple[List[int], List[int]]:
    """Lay every piece of the stroke; returns the new rectangles and the places it touched."""
    pieces = anchored_pieces(stroke_pieces(net, link["cells"]))
    first_new = len(net.nodes)
    rectangles: List[int] = []
    touched: List[int] = []
    for piece in pieces:
        rectangles.extend(lay_piece(net, link, piece))
        touched.extend(k for k in (piece[0], piece[2]) if k is not None and k < first_new and k not in touched)
    if not rectangles:
        raise DrawnLineProblem("that stroke only runs over towns and routes that are already there")
    link["mode"] = "sea" if all(net.nodes[k]["mode"] == "sea" for k in rectangles) else "land"
    return rectangles, touched


def ref_of_node(net: network.Network, node_index: int) -> dict:
    node = net.nodes[node_index]
    if node["kind"] == "city":
        return {"city": node["id"]}
    return {"route": node["name"]}


def link_record(net: network.Network, drawn: dict, link: dict, rectangles: List[int], touched: List[int]) -> dict:
    touches = [ref_of_node(net, k) for k in touched]
    record = dict(name=drawn["name"], kind="drawn", mode=link["mode"],
                  routes=[net.nodes[k]["name"] for k in rectangles], landmark=None,
                  src_ref=touches[0] if touches else None, dst_ref=touches[-1] if len(touches) > 1 else None,
                  touches=touches, waypoints=[[int(x), int(y)] for x, y in drawn["waypoints"]],
                  legs=list(drawn.get("legs") or []))
    return record


def add_drawn_line(w: World, drawn: dict) -> Tuple[List[str], List[dict]]:
    """Lay ``drawn`` (name, waypoints, legs) with every route in ``w.routes`` kept where
    it is. Returns the new route names and refs of the towns and routes it touched."""
    net = network.Network(w)
    network.add_kept(w, net, list(w.routes), list(w.links))
    cells = cells_of_corners(corners_of_line(drawn["waypoints"], drawn.get("legs") or []))
    link = dict(name=drawn["name"], kind="drawn", mode="land", cells=cells)
    rectangles, touched = lay_drawn(net, link)
    network.settle_routes(w, net)
    record = link_record(net, drawn, link, rectangles, touched)
    w.links = list(w.links) + [record]
    w.routes = [network.route_json(w, net, n) for n in net.nodes if n["kind"] == "route"]
    return [net.nodes[k]["name"] for k in rectangles], record["touches"]


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
