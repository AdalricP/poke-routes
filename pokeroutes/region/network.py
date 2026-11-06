"""Route network: least-cost paths between cities and landmarks, cut into route
rectangles on the region grid, with exits on shared edges and a role per route.

A link is laid one rectangle at a time. From the current node (a city or the
last route rectangle) a search finds the cheapest way on to the link's goal; the
path is grown into an axis-aligned rectangle flush against the current node,
padded out to route width; then the search runs again from that rectangle. So a
rectangle's exits always sit on a shared edge, aligned cell-for-cell with the
neighbour, and the path can bend between rectangles (that bend is what makes a
'turn' route). Branch links start from whatever is already built, which turns
the route they leave into a crossroads.
"""
from __future__ import annotations

import heapq
import math
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy import ndimage as ndi

from .world import CELL, World

Cell = Tuple[int, int]  # (x, y)
DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1)]  # east, west, south, north
SIDE_OF = {(1, 0): "east", (-1, 0): "west", (0, 1): "south", (0, -1): "north"}
OPP = {"east": "west", "west": "east", "north": "south", "south": "north"}

MAX_W, MAX_H = 24, 28  # cells; webui caps routes at 96 x 112 metatiles
MAP_BUDGET = 10240  # include/fieldmap.h: (w + 15) * (h + 14) metatiles
PAD = 5  # cells of route either side of the path
MIN_CROSS = 11
TARGET_ALONG = 20
MIN_ALONG = 7
TURN = 3.0

BIOME_COST = {"m": 1.0, "f": 1.3, "F": 1.8, "w": 2.0, "b": 1.2, "d": 1.4, "M": 3.5, "S": 6.0,
              "v": 2.2, "a": 1.5, "g": 1.4, "B": 1.3, "l": np.inf, "o": np.inf, "~": np.inf, "=": np.inf}


def size_ok(w: int, h: int) -> bool:
    return w <= MAX_W and h <= MAX_H and (w * CELL + 15) * (h * CELL + 14) <= MAP_BUDGET


# ------------------------------------------------------------------ cost grids

def land_cost(w: World) -> np.ndarray:
    c = np.vectorize(lambda b: BIOME_COST.get(b, 1.0), otypes=[float])(w.biome)
    gy, gx = np.gradient(w.height)
    c = c + 30.0 * np.hypot(gx, gy)
    c = np.where(w.river, c + 6.0, c)  # cross rivers, don't follow them
    c = np.where(w.overrides.get("no_route", np.zeros_like(w.land)), np.inf, c)
    c[:2, :] = c[-2:, :] = np.inf
    c[:, :2] = c[:, -2:] = np.inf
    return c


def sea_cost(w: World) -> np.ndarray:
    c = np.where(w.land, np.inf, 1.0)
    c = np.where(w.height < -0.30, 1.15, c)
    c = np.where(w.overrides.get("trench", np.zeros_like(w.land)), 0.55, c)  # the way out passes the Deep Rinne
    c = np.where(w.overrides.get("isles", np.zeros_like(w.land)), 1.0, c)
    c[:2, :] = c[-2:, :] = np.inf
    c[:, :2] = c[:, -2:] = np.inf
    return c


# ------------------------------------------------------------------ search

def search(cost: np.ndarray, starts: Dict[Cell, Tuple[float, int]], goals: Dict[Cell, float]) -> List[Cell]:
    """Dijkstra/A* over (cell, heading) with a turn penalty, from many starts (each
    with a start cost and initial heading index) to many goals (each with an extra
    cost). Returns cells from a start to a goal, or [] if none is reachable."""
    H, W = cost.shape
    if not goals or not starts:
        return []
    gmask = np.zeros((H, W), dtype=bool)
    for (x, y) in goals:
        gmask[y, x] = True
    heur = ndi.distance_transform_edt(~gmask).ravel().tolist()
    cl = cost.ravel().tolist()
    INF = float("inf")
    best: Dict[int, float] = {}
    came: Dict[int, int] = {}
    heap = []
    for (x, y), (c0, d) in starts.items():
        if not math.isfinite(cl[y * W + x]):
            continue
        s = (y * W + x) * 5 + d
        if c0 < best.get(s, INF):
            best[s] = c0
            came[s] = -1
            heapq.heappush(heap, (c0 + heur[y * W + x], c0, s))
    gpen = {y * W + x: p for (x, y), p in goals.items()}
    end = None
    while heap:
        f, g, s = heapq.heappop(heap)
        if s < 0:  # virtual goal entry
            end = -s - 1
            break
        if g > best.get(s, INF):
            continue
        i, d = divmod(s, 5)
        if i in gpen:
            heapq.heappush(heap, (g + gpen[i], g + gpen[i], -s - 1))
        y, x = divmod(i, W)
        for nd, (dx, dy) in enumerate(DIRS):
            nx, ny = x + dx, y + dy
            if not (0 <= nx < W and 0 <= ny < H):
                continue
            j = ny * W + nx
            cj = cl[j]
            if cj == INF:
                continue
            ng = g + cj + (TURN if d != 4 and nd != d else 0.0)
            t = j * 5 + nd
            if ng < best.get(t, INF):
                best[t] = ng
                came[t] = s
                heapq.heappush(heap, (ng + heur[j], ng, t))
    if end is None:
        return []
    out = []
    s = end
    while s != -1:
        i = s // 5
        out.append((i % W, i // W))
        s = came[s]
    return out[::-1]


# ------------------------------------------------------------------ the network

class Network:
    def __init__(self, w: World):
        self.w = w
        self.occ = np.full((w.H, w.W), -1, dtype=np.int32)
        self.nodes: List[dict] = []
        self.conns: List[dict] = []
        self.lcost = land_cost(w)
        self.scost = sea_cost(w)
        for c in w.cities:
            self.add_node("city", c.rect, name=c.name, id=c.id, map="MAP_" + c.id.upper())
        # landmark cores: only the route that leads to a landmark may cover it
        self.reserved = np.full((w.H, w.W), "", dtype=object)
        ys, xs = np.mgrid[0:w.H, 0:w.W]
        for lm in w.landmarks:
            if not lm.route_goal:
                continue
            rad = 3.5 if lm.kind in ("den", "volcano") else 2.5
            self.reserved[np.hypot(xs - lm.x, ys - lm.y) <= rad] = lm.id
        gate = w.overrides.get("den_gate")
        # Romowe's one way in should be left to the Romowe branch: other links pay to cross it
        self.gate_pen = np.zeros((w.H, w.W))
        if gate:
            self.gate_pen[np.hypot(xs - gate[0], ys - gate[1]) <= 3.0] = 12.0
        self.allow = ""

    # ---- nodes
    def add_node(self, kind: str, rect, **kw) -> int:
        k = len(self.nodes)
        x0, y0, x1, y1 = rect
        self.occ[y0:y1, x0:x1] = k
        self.nodes.append(dict(kind=kind, rect=list(rect), idx=k, paths=[], **kw))
        return k

    def free(self, rect) -> bool:
        x0, y0, x1, y1 = rect
        if x0 < 1 or y0 < 1 or x1 > self.w.W - 1 or y1 > self.w.H - 1 or x1 <= x0 or y1 <= y0:
            return False
        if not (self.occ[y0:y1, x0:x1] == -1).all():
            return False
        res = self.reserved[y0:y1, x0:x1]
        return bool(((res == "") | (res == self.allow)).all())

    def exits_of(self, k: int) -> List[Cell]:
        return [c["cell_a"] if c["a"] == k else c["cell_b"] for c in self.conns if k in (c["a"], c["b"])]

    def adjacent(self, k: int, penalty: float = 0.0, cost=None) -> Dict[Cell, Tuple[float, int]]:
        """Free cells just outside node k's edges (not near its corners or existing
        exits), each with the heading that points away from the node."""
        cost = self.lcost if cost is None else cost
        x0, y0, x1, y1 = self.nodes[k]["rect"]
        ex = self.exits_of(k)
        out = {}
        sides = [((1, 0), [(x1, y) for y in range(y0 + 2, y1 - 2)]),
                 ((-1, 0), [(x0 - 1, y) for y in range(y0 + 2, y1 - 2)]),
                 ((0, 1), [(x, y1) for x in range(x0 + 2, x1 - 2)]),
                 ((0, -1), [(x, y0 - 1) for x in range(x0 + 2, x1 - 2)])]
        for d, cells in sides:
            for (x, y) in cells:
                if not (0 <= x < self.w.W and 0 <= y < self.w.H) or self.occ[y, x] != -1:
                    continue
                if not math.isfinite(cost[y, x]):
                    continue
                near = min([abs(x - a) + abs(y - b) for a, b in ex] or [99])
                if near < 5:
                    continue
                out[(x, y)] = (penalty, DIRS.index(d))
        return out

    # ---- laying a link
    def lay(self, link: dict) -> bool:
        """link: kind, src (list of (node, penalty)), dst (list of (node, penalty)) or
        goal (cell), mode land|sea. Places rectangles; returns False if no path."""
        cost = self.scost if link.get("mode") == "sea" else self.lcost
        cost = cost.copy()
        # keep a little air between unrelated routes
        occd = ndi.distance_transform_edt(self.occ == -1)
        cost = cost + np.where(occd < 6, (6 - occd) * 1.2, 0)
        self.allow = link.get("landmark") or ""
        cost = cost + np.where((self.reserved != "") & (self.reserved != self.allow), 25.0, 0)
        if self.allow != "romowe":
            cost = cost + self.gate_pen
        if "goal" in link:
            gx, gy = link["goal"]
            cost[gy, gx] = min(float(cost[gy, gx]), 2.0) if math.isfinite(cost[gy, gx]) else 2.0
        cost[self.occ >= 0] = np.inf
        starts: Dict[Cell, Tuple[float, int]] = {}
        for k, pen in link["src"]:
            for c, v in self.adjacent(k, pen, cost).items():
                if c not in starts or v[0] < starts[c][0]:
                    starts[c] = v
        goals, goal_node = self._goals(link, cost)
        path = search(cost, starts, goals)
        if not path:
            self.w.log.append(f"link {link['name']}: no path")
            return False
        prev = self._node_at_start(path[0], link["src"])
        rects = []
        for _ in range(12):
            r, used, done = self._grow(path, prev, goal_node, link)
            if r is None:
                self.w.log.append(f"link {link['name']}: no room after {len(rects)} routes")
                break
            k = self.add_node("route", r, link=link["name"], mode=link.get("mode", "land"), kind_link=link["kind"])
            self.nodes[k]["paths"].append(used)
            self._connect(prev, k, path[0], link)
            rects.append(k)
            if done:
                last = used[-1]
                if goal_node is not None:
                    self._connect(k, goal_node, last, link, into=True)
                else:
                    self.nodes[k]["goal"] = list(link["goal"])
                    self.nodes[k]["goal_landmark"] = link.get("landmark")
                break
            # search on from the new rectangle
            cost2 = cost.copy()
            cost2[self.occ >= 0] = np.inf
            starts = self.adjacent(k, 0.0, cost2)
            goals, goal_node = self._goals(link, cost2)
            path = search(cost2, starts, goals)
            if not path:
                self.w.log.append(f"link {link['name']}: stuck after {len(rects)} routes")
                break
            prev = k
        link["routes"] = rects
        return True

    def _goals(self, link, cost):
        if "goal" in link:
            gx, gy = link["goal"]
            return {(gx, gy): 0.0}, None
        goals = {}
        for k, pen in link["dst"]:
            for c, (p, d) in self.adjacent(k, pen, cost).items():
                goals[c] = pen
        # a single target node is the usual case; with several, the nearest wins
        return goals, ("multi", [k for k, _ in link["dst"]])

    def _node_at_start(self, c: Cell, src) -> int:
        x, y = c
        for k, _ in src:
            x0, y0, x1, y1 = self.nodes[k]["rect"]
            if (x == x1 or x == x0 - 1) and y0 <= y < y1 or (y == y1 or y == y0 - 1) and x0 <= x < x1:
                return k
        # search restarted from a route rectangle
        for d in DIRS:
            k = self.occ[y - d[1], x - d[0]]
            if k >= 0:
                return int(k)
        raise RuntimeError("path start is not next to its source")

    def _touching(self, c: Cell) -> Optional[Tuple[int, Cell]]:
        x, y = c
        for dx, dy in DIRS:
            nx, ny = x + dx, y + dy
            if 0 <= nx < self.w.W and 0 <= ny < self.w.H and self.occ[ny, nx] >= 0:
                return int(self.occ[ny, nx]), (nx, ny)
        return None

    def _connect(self, a: int, b: int, cell_b: Cell, link, into: bool = False) -> None:
        """Connect node a to node b across the edge next to cell_b (a cell of b, or
        with into=True a cell of a next to b)."""
        if isinstance(b, tuple):  # multi-target: whichever node the path touches
            b = self._touching_any(cell_b, b[1])
        x, y = cell_b
        if into:
            # cell_b is inside a; find the neighbour cell in b
            for dx, dy in DIRS:
                nx, ny = x + dx, y + dy
                if 0 <= nx < self.w.W and 0 <= ny < self.w.H and self.occ[ny, nx] == b:
                    self.conns.append(dict(a=a, b=b, cell_a=(x, y), cell_b=(nx, ny), side_a=SIDE_OF[(dx, dy)],
                                           side_b=OPP[SIDE_OF[(dx, dy)]], link=link["name"]))
                    return
            raise RuntimeError("goal cell not next to goal node")
        for dx, dy in DIRS:
            nx, ny = x - dx, y - dy
            if 0 <= nx < self.w.W and 0 <= ny < self.w.H and self.occ[ny, nx] == a:
                self.conns.append(dict(a=a, b=b, cell_a=(nx, ny), cell_b=(x, y), side_a=SIDE_OF[(dx, dy)],
                                       side_b=OPP[SIDE_OF[(dx, dy)]], link=link["name"]))
                return
        raise RuntimeError("start cell not next to previous node")

    def _touching_any(self, c: Cell, ks) -> int:
        x, y = c
        for dx, dy in DIRS:
            nx, ny = x + dx, y + dy
            if 0 <= nx < self.w.W and 0 <= ny < self.w.H and int(self.occ[ny, nx]) in ks:
                return int(self.occ[ny, nx])
        raise RuntimeError("goal cell not next to any goal node")

    def _grow(self, path: List[Cell], prev: int, goal_node, link) -> Tuple[List[int], List[Cell], bool]:
        """Grow a rectangle along path (which starts next to prev). Returns the
        rectangle, the path cells it holds and whether it reaches the goal."""
        x, y = path[0]
        px0, py0, px1, py1 = self.nodes[prev]["rect"]
        if x == px1:
            fixed = ("x0", x)
        elif x == px0 - 1:
            fixed = ("x1", x + 1)
        elif y == py1:
            fixed = ("y0", y)
        else:
            fixed = ("y1", y + 1)
        L = len(path)
        cands = []
        bx0, by0, bx1, by1 = x, y, x + 1, y + 1
        for j in range(L):
            cx, cy = path[j]
            bx0, by0, bx1, by1 = min(bx0, cx), min(by0, cy), max(bx1, cx + 1), max(by1, cy + 1)
            core = [bx0, by0, bx1, by1]
            f, v = fixed
            if {"x0": bx0, "x1": bx1, "y0": by0, "y1": by1}[f] != v:
                break
            if not size_ok(bx1 - bx0, by1 - by0) or not self.free(core):
                break
            cands.append(j)
        if not cands:
            return None, None, False
        jmax = cands[-1]
        if jmax == L - 1:
            j = jmax
        else:
            remaining = L
            n = max(1, math.ceil(remaining / TARGET_ALONG))
            want = remaining / n
            ok = [j for j in cands if j <= want + 2 and (L - 1 - j) >= MIN_ALONG]
            j = ok[-1] if ok else jmax
        used = path[:j + 1]
        xs = [c[0] for c in used]
        ys = [c[1] for c in used]
        core = [min(xs), min(ys), max(xs) + 1, max(ys) + 1]
        rect = self._inflate(core, fixed[0], link)
        return rect, used, j == L - 1

    def _inflate(self, core: List[int], fixed: str, link) -> List[int]:
        r = list(core)
        sides = [s for s in ("x0", "x1", "y0", "y1") if s != fixed]
        sea = link.get("mode") == "sea"
        water = ~self.w.land | self.w.lake

        def try_side(s) -> bool:
            n = list(r)
            n[{"x0": 0, "y0": 1, "x1": 2, "y1": 3}[s]] += -1 if s in ("x0", "y0") else 1
            if not size_ok(n[2] - n[0], n[3] - n[1]) or not self.free(n):
                return False
            # the new strip: land routes stop growing into open sea, sea routes into land
            if s in ("x0", "x1"):
                xx = n[0] if s == "x0" else n[2] - 1
                strip = water[n[1]:n[3], xx]
            else:
                yy = n[1] if s == "y0" else n[3] - 1
                strip = water[yy, n[0]:n[2]]
            frac = float(strip.mean())
            if (not sea and frac > 0.85) or (sea and frac < 0.15):
                return False
            r[:] = n
            return True

        grown = {s: 0 for s in sides}
        for _ in range(PAD):
            for s in sides:
                if try_side(s):
                    grown[s] += 1
        # minimum width across
        for _ in range(8):
            wv, hv = r[2] - r[0], r[3] - r[1]
            want = [s for s in sides if (s in ("x0", "x1") and wv < MIN_CROSS) or (s in ("y0", "y1") and hv < MIN_CROSS)]
            if not want or not any([try_side(s) for s in want]):
                break
        return r


# ------------------------------------------------------------------ roles

def _side_rank(side: str) -> int:
    return ["north", "east", "south", "west"].index(side)


def classify(w: World, net: Network) -> None:
    """Shape from the exits; role from shape + link + terrain."""
    tiers, river = w.tiers, w.river
    sea = ~w.land
    for n in net.nodes:
        if n["kind"] != "route":
            continue
        k = n["idx"]
        ex = []
        for c in net.conns:
            if c["a"] == k:
                ex.append(dict(side=c["side_a"], cell=list(c["cell_a"]), to=c["b"], link=c["link"]))
            elif c["b"] == k:
                ex.append(dict(side=c["side_b"], cell=list(c["cell_b"]), to=c["a"], link=c["link"]))
        n["exits"] = ex
        sides = [e["side"] for e in ex]
        if len(ex) <= 1:
            shape = "dead_end"
        elif len(ex) == 2:
            a, b = sides
            shape = "straight" if OPP[a] == b else ("u_turn" if a == b else "turn")
        else:
            shape = "crossroads"
        x0, y0, x1, y1 = n["rect"]
        path = [c for p in n["paths"] for c in p]
        tags = []
        tp = [int(tiers[y, x]) for x, y in path]
        if any(river[y, x] for x, y in path):
            tags.append("river_crossing")
        if tp and max(tp) >= 3 and max(tp) - min(tp[0], tp[-1]) >= 1:
            tags.append("mountain_pass")
        sub_sea = sea[y0:y1, x0:x1]
        near_sea = ndi.binary_dilation(sea, iterations=3)
        if n.get("mode") != "sea" and sub_sea.mean() > 0.10 and path and \
                sum(near_sea[y, x] for x, y in path) / len(path) > 0.35:
            tags.append("coastal")
        if n.get("kind_link") == "loop":
            tags.append("loop")
        if len(set(sides)) < len(sides):
            tags.append("multi_start")
        n["shape"] = shape
        n["tags"] = tags
        if n.get("mode") == "sea":
            role = "sea"
        elif shape in ("crossroads", "dead_end"):
            role = shape
        elif "loop" in tags:
            role = "loop"
        elif "mountain_pass" in tags:
            role = "mountain_pass"
        elif "river_crossing" in tags:
            role = "river_crossing"
        elif "coastal" in tags:
            role = "coastal"
        else:
            role = "turn" if shape in ("turn", "u_turn") else "straight"
        n["role"] = role


def add_back_gates(net: Network, limit: int = 2) -> None:
    """A few routes get a second way into the town they touch (multiple starts)."""
    done = 0
    for n in net.nodes:
        if done >= limit or n["kind"] != "route" or n.get("mode") == "sea" or n.get("pinned"):
            continue
        k = n["idx"]
        mine = [c for c in net.conns if k in (c["a"], c["b"])]
        cities = [c["b"] if c["a"] == k else c["a"] for c in mine]
        cities = [c for c in cities if net.nodes[c]["kind"] == "city"]
        if len(mine) != 2 or not cities:
            continue
        ck = cities[0]
        conn = next(c for c in mine if ck in (c["a"], c["b"]))
        mycell = conn["cell_a"] if conn["a"] == k else conn["cell_b"]
        side = conn["side_a"] if conn["a"] == k else conn["side_b"]
        x0, y0, x1, y1 = n["rect"]
        cx0, cy0, cx1, cy1 = net.nodes[ck]["rect"]
        if side in ("east", "west"):
            lo, hi = max(y0, cy0) + 2, min(y1, cy1) - 2
            xs = x1 - 1 if side == "east" else x0
            spots = [(xs, y) for y in range(lo, hi) if abs(y - mycell[1]) >= 5]
            other = lambda c: (c[0] + (1 if side == "east" else -1), c[1])  # noqa: E731
        else:
            lo, hi = max(x0, cx0) + 2, min(x1, cx1) - 2
            ys = y1 - 1 if side == "south" else y0
            spots = [(x, ys) for x in range(lo, hi) if abs(x - mycell[0]) >= 5]
            other = lambda c: (c[0], c[1] + (1 if side == "south" else -1))  # noqa: E731
        if not spots:
            continue
        c = max(spots, key=lambda s: abs(s[0] - mycell[0]) + abs(s[1] - mycell[1]))
        net.conns.append(dict(a=k, b=ck, cell_a=c, cell_b=other(c), side_a=side, side_b=OPP[side],
                              link=conn["link"] + " (back gate)"))
        done += 1


# ------------------------------------------------------------------ names

def ascii_id(s: str) -> str:
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"), ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue")):
        s = s.replace(a, b)
    return "".join(ch for ch in s if ch.isalnum())


def name_routes(w: World, net: Network) -> None:
    used = {n["name"] for n in net.nodes if n["kind"] == "route" and n.get("pinned")}
    route_number = 1
    for n in net.nodes:
        if n["kind"] != "route":
            continue
        if n.get("pinned"):
            n["biome_code"] = (n.get("keep") or {}).get("biome_code") or dominant_biome_code_under_route(w, n)
            continue
        n["biome_code"] = dominant_biome_code_under_route(w, n)
        while f"Route {route_number}" in used:
            route_number += 1
        nm = f"Route {route_number}"
        used.add(nm)
        n["name"] = nm
        n["id"] = ascii_id(nm)
        n["map"] = "MAP_" + ascii_id(nm).upper()


def dominant_biome_code_under_route(w: World, route_node: dict) -> str:
    x0, y0, x1, y1 = route_node["rect"]
    biome_codes_under_route = w.biome[y0:y1, x0:x1]
    if route_node.get("mode") != "sea":
        land_biome_codes = biome_codes_under_route[np.isin(biome_codes_under_route, list("~=o"), invert=True)]
        biome_codes_under_route = land_biome_codes if land_biome_codes.size else biome_codes_under_route
    biome_codes, cell_counts = np.unique(biome_codes_under_route, return_counts=True)
    dominant_biome_code = str(biome_codes[int(np.argmax(cell_counts))])
    return dominant_biome_code


# ------------------------------------------------------------------ build

WILD = {
    "summit": ("Hohe Warte", "The highest rock in the Oberland. A cairn, a survey mark from 398 AL and a view "
               "of the Glutberg smoking to the south."),
    "lake": ("Krähensee", "A cold lake in a basin with no road to it. Ice fishers' huts stand on the shore "
             "all year."),
    "marsh": ("Großes Moosbruch", "Peat bog and alder carr. Planks laid by peat cutters, half sunk; things "
              "move under the water."),
    "dunes": ("Wanderdüne", "Bare sand that moves a few steps east every year and has buried a village "
              "church up to its tower."),
    "deepwood": ("Schwarzer Grund", "The oldest stand of forest in the land, too dark for undergrowth."),
}


def wild_sites(w: World, net: "Network", n: int) -> list:
    from .world import Landmark

    free = net.occ == -1
    dnet = ndi.distance_transform_edt(free)
    cands = []
    v = w.landmark("glutberg")
    ys, xs = np.mgrid[0:w.H, 0:w.W]
    okland = w.land & (w.landmass == 1) | w.land & (w.landmass == 2)
    okland &= np.isfinite(net.lcost) & free
    if v is not None:
        okland &= np.hypot(xs - v.x, ys - v.y) > 10
    for lm in w.landmarks:
        okland &= np.hypot(xs - lm.x, ys - lm.y) > 8

    def comp(mask, kind):
        lab, k = ndi.label(mask)
        if k == 0:
            return
        sizes = ndi.sum(mask, lab, range(1, k + 1))
        i = int(np.argmax(sizes)) + 1
        if sizes[i - 1] < 6:
            return
        cy, cx = ndi.center_of_mass(lab == i)
        # nearest usable land cell to the centre
        dd = np.where(okland, np.hypot(xs - cx, ys - cy), np.inf)
        y, x = np.unravel_index(int(np.argmin(dd)), dd.shape)
        if np.isfinite(dd[y, x]) and dd[y, x] < 5:
            cands.append((kind, int(x), int(y)))

    hs = np.where(okland & (w.tiers >= 3), w.height, -1)
    if hs.max() > 0:
        y, x = np.unravel_index(int(np.argmax(hs)), hs.shape)
        cands.append(("summit", int(x), int(y)))
    comp(w.lake, "lake")
    comp(w.biome == "w", "marsh")
    comp((w.biome == "d") | (w.biome == "a"), "dunes")
    comp(w.biome == "F", "deepwood")
    cands.sort(key=lambda c: -dnet[c[2], c[1]])
    out = []
    for kind, x, y in cands:
        if dnet[y, x] < 12 or any(np.hypot(x - o.x, y - o.y) < 20 for o in out):
            continue
        nm, story = WILD[kind]
        out.append(Landmark(f"wild_{kind}", "", nm, kind, x, y, [x - 2, y - 2, x + 3, y + 3], story, True))
        if len(out) >= n:
            break
    return out


ARKLARNA_TOWNS = ("tvangsburg", "pilsau", "huettenau", "kaup")


def build_network(w: World, keep: Optional[List[dict]] = None, plan: Optional[List[dict]] = None) -> Network:
    """Lay the route network. ``keep``: route dicts (as in ``w.routes``) to keep as they
    are; links are then only laid where a kept chain has a gap. ``plan``: the previous
    ``w.links`` (for link order, links the editor dropped and user-drawn links)."""
    net = Network(w)
    C = {n["id"]: n["idx"] for n in net.nodes if n["kind"] == "city"}
    plan = plan or []
    pins = add_kept(w, net, keep or [], plan)
    dropped = {pl["name"] for pl in plan if pl.get("dropped")}

    links: List[dict] = []

    def add(link):
        links.append(link)
        if link["name"] in dropped:
            link["routes"] = pins.get(link["name"], [])
            link["dropped"] = True
            return
        if keep is None or not pins.get(link["name"]):
            net.lay(link)
            if keep is not None and not link.get("routes"):
                link["routes"] = []
            return
        lay_around(net, link, pins[link["name"]])

    if w.preset == "arklarna" and all(town_id in C for town_id in ARKLARNA_TOWNS):
        arklarna_links(w, net, C, add, links)
    else:
        town_links(w, net, C, add, towns_joined_by_drawn_lines(plan))
    links.extend(kept_drawn_links(plan, pins))
    # links drawn by hand in the editor
    for ul in plan:
        if ul.get("kind") != "user":
            continue
        link = user_link(w, net, ul)
        if link is not None:
            add(link)
        else:
            # keep the ref around with no routes, so a later rebuild can retry once it resolves
            links.append(dict(name=ul["name"], kind="user", mode=ul.get("mode", "land"),
                              src_ref=ul.get("src_ref"), dst_ref=ul.get("dst_ref"), routes=[]))
            w.log.append(f"{ul['name']}: an end is gone; not laid")
    add_back_gates(net)
    settle_routes(w, net)
    w.links = []
    for lk in links:
        d = dict(name=lk["name"], kind=lk["kind"], mode=lk.get("mode", "land"),
                 routes=[net.nodes[k]["name"] for k in lk.get("routes", [])], landmark=lk.get("landmark"))
        for k in ("dropped", "src_ref", "dst_ref", "waypoints", "legs"):
            if lk.get(k):
                d[k] = lk[k]
        w.links.append(d)
    w.routes = [route_json(w, net, n) for n in net.nodes if n["kind"] == "route"]
    w.overrides["network"] = net
    w.log.append(f"routes: {len(w.routes)} ({', '.join(r['name'] + ':' + r['role'] for r in w.routes)})")
    return net


def settle_routes(w: World, net: Network) -> None:
    classify(w, net)
    for n in net.nodes:  # roles the owner set by hand stay
        man = (n.get("keep") or {}).get("manual") or {}
        if n["kind"] == "route" and man.get("role"):
            n["role"] = man["role"]
    name_routes(w, net)


def kept_drawn_links(plan: List[dict], pins: Dict[str, List[int]]) -> List[dict]:
    kept = []
    for drawn in plan:
        if drawn.get("kind") != "drawn" or not pins.get(drawn["name"]):
            continue
        record = {k: v for k, v in drawn.items() if k != "routes"}
        record["routes"] = pins[drawn["name"]]
        kept.append(record)
    return kept


def towns_joined_by_drawn_lines(plan: List[dict]) -> List[Tuple[str, str]]:
    pairs = []
    for drawn in plan:
        if drawn.get("kind") != "drawn" or drawn.get("dropped"):
            continue
        source, target = drawn.get("src_ref") or {}, drawn.get("dst_ref") or {}
        if "city" in source and "city" in target:
            pairs.append((source["city"], target["city"]))
    return pairs


class TownGroups:
    def __init__(self, pairs: List[Tuple[str, str]]):
        self.parent: Dict[str, str] = {}
        for town_id, other_town_id in pairs:
            self.join(town_id, other_town_id)

    def root(self, town_id: str) -> str:
        while self.parent.get(town_id, town_id) != town_id:
            town_id = self.parent[town_id]
        return town_id

    def join(self, town_id: str, other_town_id: str) -> None:
        self.parent[self.root(town_id)] = self.root(other_town_id)

    def joined(self, town_id: str, other_town_id: str) -> bool:
        return self.root(town_id) == self.root(other_town_id)


def arklarna_links(w: World, net: Network, C: Dict[str, int], add, links: List[dict]) -> None:
    """The Arklarna preset: three roads between its four towns, the legendary
    branches, two wild places and the Hüttenau loop."""
    add(dict(name="Tvangsburg-Pilsau", kind="main", src=[(C["tvangsburg"], 0)], dst=[(C["pilsau"], 0)]))
    add(dict(name="Tvangsburg-Huettenau", kind="main", src=[(C["tvangsburg"], 0)], dst=[(C["huettenau"], 0)]))
    add(dict(name="Pilsau-Kaup", kind="main", src=[(C["pilsau"], 0)], dst=[(C["kaup"], 0)]))

    def branch(name, lid, cell=None, mode="land", prefer_routes=True, avoid=()):
        lm = w.landmark(lid)
        if lm is None:
            return
        src = []
        for n in net.nodes:
            if n["idx"] in avoid:
                continue
            if mode == "sea":
                src.append((n["idx"], 0.0 if n["kind"] == "city" else 2.0))
            else:
                src.append((n["idx"], 40.0 if (n["kind"] == "city" and prefer_routes) else 0.0))
        goal = cell or (lm.x, lm.y)
        occ_k = int(net.occ[goal[1], goal[0]])
        if occ_k >= 0 and net.nodes[occ_k].get("link") != name:
            w.log.append(f"{name}: {lid} already sits inside a route")
            net.nodes[occ_k].setdefault("landmarks", []).append(lid)
            return
        add(dict(name=name, kind="branch", src=src, goal=goal, mode=mode, landmark=lid))

    # Glutberg: up the flank from the Hüttenau side
    v = w.landmark("glutberg")
    hu = w.city("huettenau")
    if v and hu:
        a = math.atan2(hu.cy - v.y, hu.cx - v.x)
        gx, gy = int(round(v.x + 3.0 * math.cos(a))), int(round(v.y + 3.0 * math.sin(a)))
        branch("Glutberg branch", "glutberg", (gx, gy), prefer_routes=False)
    branch("Gold Feather branch", "gold_feather_tower")
    gate = w.overrides.get("den_gate")
    branch("Romowe branch", "romowe", gate)
    branch("Brandung branch", "kyogre_caves")
    branch("Skerries crossing", "silver_skerries", mode="sea")
    # wild places in whatever part of the map no route reaches yet: long dead ends
    wild = [lm for lm in w.landmarks if lm.id.startswith("wild_")]
    if not wild:  # first build: find them (an edited map keeps the ones it has)
        wild = wild_sites(w, net, 2)
        w.landmarks.extend(wild)
    for lm in wild:
        if 0 <= lm.y < w.H and 0 <= lm.x < w.W:
            net.reserved[lm.y, lm.x] = lm.id
        branch(f"{lm.name} branch", lm.id)
    # a loop: Hüttenau gets a second way out that rejoins the Pilsau road
    first = [k for k in links[0].get("routes", [])]
    add(dict(name="Huettenau-Pilsau loop", kind="loop", src=[(C["huettenau"], 0)] + [(k, 4.0) for k in links[1].get("routes", [])[-1:]],
             dst=[(C["pilsau"], 2.0)] + [(k, 0.0) for k in first]))


def town_links(w: World, net: Network, C: Dict[str, int], add, drawn_pairs: Optional[List[Tuple[str, str]]] = None) -> None:
    groups = TownGroups(drawn_pairs or [])
    for town, other_town in town_tree(w.cities):
        if groups.joined(town.id, other_town.id):
            continue
        groups.join(town.id, other_town.id)
        mode = "land" if same_landmass(w, town, other_town) else "sea"
        add(dict(name=f"{town.name}-{other_town.name}", kind="main", mode=mode,
                 src=[(C[town.id], 0)], dst=[(C[other_town.id], 0)]))


def town_tree(towns: list) -> List[tuple]:
    """Pairs of towns joined by the shortest tree over their centres (Prim)."""
    if len(towns) < 2:
        return []
    joined, pairs = [towns[0]], []
    waiting = list(towns[1:])
    while waiting:
        town, other_town = min(((a, b) for a in joined for b in waiting),
                               key=lambda pair: math.hypot(pair[0].cx - pair[1].cx, pair[0].cy - pair[1].cy))
        pairs.append((town, other_town))
        joined.append(other_town)
        waiting.remove(other_town)
    return pairs


def same_landmass(w: World, town, other_town) -> bool:
    labels, _count = ndi.label(w.land & ~w.lake)
    first = labels[min(w.H - 1, int(town.cy)), min(w.W - 1, int(town.cx))]
    second = labels[min(w.H - 1, int(other_town.cy)), min(w.W - 1, int(other_town.cx))]
    return first == 0 or second == 0 or first == second


def route_json(w: World, net: Network, n: dict) -> dict:
    exits = []
    for e in n["exits"]:
        to = net.nodes[e["to"]]
        exits.append(dict(side=e["side"], cell=e["cell"], to=to.get("name"), to_map=to.get("map"), link=e["link"]))
    path = [list(c) for p in n["paths"] for c in p]
    d = dict(name=n["name"], id=n["id"], map=n["map"], rect=n["rect"], role=n["role"], shape=n["shape"],
             tags=n["tags"], mode=n.get("mode", "land"), link=n.get("link"), exits=exits, path=path,
             goal=n.get("goal"), goal_landmark=n.get("goal_landmark"), biome_code=n.get("biome_code"),
             kind_link=n.get("kind_link"))
    old = n.get("keep") or {}
    for k in ("pinned", "manual", "spec_overrides", "gate", "drawn"):  # editor state rides along
        if old.get(k):
            d[k] = old[k]
    return d


# ------------------------------------------------------------------ editing support

DELTA = {"east": (1, 0), "west": (-1, 0), "south": (0, 1), "north": (0, -1)}


def add_kept(w: World, net: Network, keep: List[dict], plan: List[dict]) -> Dict[str, List[int]]:
    """Put kept routes into the network as fixed nodes, with the connections they
    still have to cities and to each other. Returns link name -> kept node indices
    in the order the link visits them."""
    by_name = {n["name"]: n["idx"] for n in net.nodes}
    kinds = {pl["name"]: pl.get("kind", "main") for pl in plan}
    for r in keep:
        k = net.add_node("route", r["rect"], link=r.get("link"), mode=r.get("mode", "land"),
                         kind_link=r.get("kind_link") or kinds.get(r.get("link"), "main"),
                         name=r["name"], id=r["id"], map=r["map"], pinned=True, keep=r)
        net.nodes[k]["paths"].append([tuple(c) for c in r.get("path") or []])
        if r.get("goal"):
            net.nodes[k]["goal"] = list(r["goal"])
            net.nodes[k]["goal_landmark"] = r.get("goal_landmark")
        by_name[r["name"]] = k
    seen = set()
    for n in net.nodes:
        r = n.get("keep")
        if not r:
            continue
        for e in r.get("exits", []):
            t = by_name.get(e.get("to"))
            if t is None or t == n["idx"]:
                continue
            dx, dy = DELTA[e["side"]]
            ca = (int(e["cell"][0]), int(e["cell"][1]))
            cb = (ca[0] + dx, ca[1] + dy)
            key = frozenset([(n["idx"], ca), (t, cb)])
            if key in seen:
                continue
            seen.add(key)
            net.conns.append(dict(a=n["idx"], b=t, cell_a=ca, cell_b=cb, side_a=e["side"], side_b=OPP[e["side"]],
                                  link=e.get("link") or r.get("link") or ""))
    pins: Dict[str, List[int]] = {}
    order = {pl["name"]: pl.get("routes", []) for pl in plan}
    for n in net.nodes:
        r = n.get("keep")
        if r:
            pins.setdefault(r.get("link") or "", []).append(n["idx"])
    for name, ks in pins.items():
        seq = order.get(name, [])
        ks.sort(key=lambda k: seq.index(net.nodes[k]["name"]) if net.nodes[k]["name"] in seq else len(seq))
    return pins


def _linked(net: Network, a: int, bs) -> bool:
    bs = set(bs)
    return any((c["a"] == a and c["b"] in bs) or (c["b"] == a and c["a"] in bs) for c in net.conns)


def lay_around(net: Network, link: dict, pins: List[int]) -> None:
    """Lay ``link`` through its kept routes: only the missing pieces (start -> first
    kept route, between kept routes that lost their joint, last kept route -> end)."""
    out: List[int] = []

    def piece(src, dst=None, goal=None):
        sub = {k: v for k, v in link.items() if k not in ("src", "dst", "goal", "routes")}
        sub["src"] = src
        if goal is not None:
            sub["goal"] = goal
        else:
            sub["dst"] = dst
        net.lay(sub)
        out.extend(sub.get("routes", []))

    srcs = [k for k, _ in link["src"] if k not in pins]
    if not _linked(net, pins[0], srcs):
        piece([(k, p) for k, p in link["src"] if k not in pins], dst=[(pins[0], 0.0)])
    for a, b in zip(pins, pins[1:]):
        out.append(a)
        if not _linked(net, a, [b]):
            piece([(a, 0.0)], dst=[(b, 0.0)])
    out.append(pins[-1])
    last = net.nodes[pins[-1]]
    if "goal" in link:
        src_ks = {k for k, _ in link["src"]}
        others = [k for k in range(len(net.nodes)) if k not in pins and k not in src_ks]
        if not last.get("goal") and not _linked(net, pins[-1], others):
            piece([(pins[-1], 0.0)], goal=link["goal"])
    elif not _linked(net, pins[-1], [k for k, _ in link["dst"]]):
        piece([(pins[-1], 0.0)], dst=link["dst"])
    link["routes"] = out


def user_link(w: World, net: Network, ul: dict) -> Optional[dict]:
    """A link the owner drew: {name, src_ref, dst_ref}; refs are {city|route|landmark: id}."""
    def node(ref):
        return node_for_ref(net, ref)

    s = node(ul.get("src_ref"))
    if s is None:
        return None
    link = dict(name=ul["name"], kind="user", src=[(s, 0.0)], mode=ul.get("mode", "land"),
                src_ref=ul.get("src_ref"), dst_ref=ul.get("dst_ref"))
    dref = ul.get("dst_ref") or {}
    if "landmark" in dref:
        lm = w.landmark(dref["landmark"])
        if lm is None:
            return None
        if lm.mode == "sea":
            link["mode"] = "sea"
        # aim at the nearest open, passable cell (the landmark itself may be lava or rock)
        cost = net.scost if link["mode"] == "sea" else net.lcost
        ys, xs = np.mgrid[0:w.H, 0:w.W]
        d = np.hypot(xs - lm.x, ys - lm.y)
        ok = np.isfinite(cost) & (net.occ == -1) & (d <= 8)
        if link["mode"] == "sea":
            ok |= (d <= 2.5) & (net.occ == -1)  # an island: land at the very end is fine
        if not ok.any():
            return None
        gy, gx = np.unravel_index(int(np.argmin(np.where(ok, d, np.inf))), d.shape)
        link["goal"] = (int(gx), int(gy))
        link["landmark"] = lm.id
    else:
        d = node(dref)
        if d is None or d == s:
            return None
        link["dst"] = [(d, 0.0)]
    return link


def node_for_ref(net: Network, ref: Optional[dict]) -> Optional[int]:
    if not ref:
        return None
    for n in net.nodes:
        if ("city" in ref and n["kind"] == "city" and n.get("id") == ref["city"]) or \
                ("route" in ref and n["kind"] == "route" and n.get("name") == ref["route"]):
            return n["idx"]
    return None
