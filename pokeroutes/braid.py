"""Braided-linear route skeleton.

A route is a line with braids in it, not an open field. The spine runs from the
entry exit to the far exit (one straight leg, or an L / U when the two exits are
not on opposite sides). Along the spine sit *nodes*: the exits, junction points,
and clearings ("spectacle" nodes, where a special area lives). Between two nodes
is a *section*: the main lane runs node to node, and 0-2 side lanes split off it,
run parallel, and then either rejoin at the next node, merge into a neighbour
early, or stop short in a reward pocket.

Everything is planned in a per-leg (u, v) frame: u runs along the spine, v across
it. Lanes are orthogonal polylines in that frame (straight runs joined by 90 degree
turns), so on the map they are d-pad friendly: no staircases, no one-tile jogs.
A side lane follows the main lane's doglegs at a fixed offset, turning earlier or
later at each corner so the gap of trees between the two stays constant.

Plans are validated against an owner grid: a new lane may only touch other
structures at its own junction points, and keeps ``GAP`` tiles of forest to
everything else. Nothing is carved here; the stages in ``generate`` do that.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np
from scipy.ndimage import binary_dilation

Cell = Tuple[int, int]
UV = Tuple[int, int]

SIDE_IN = {"north": (1, 0), "south": (-1, 0), "west": (0, 1), "east": (0, -1)}
GAP = 3  # tiles of forest kept between unrelated structures
MARGIN = 3  # lanes stay this far from the map edge (the outer 2 rows are sealed)
COVER_BLANK = 8  # a forest mass this many tiles (chessboard) from any lane gets a side path
JUNCTION_R = 5  # radius around a junction point where touching is allowed
EXIT_DIST = 20  # a special area is never closer than this to an exit / town gate
CAM_X, CAM_Y = 8, 6  # GBA camera: 15x10 tiles around the player, so a site this far off is unseen
KEEP = 5  # no planned lane comes nearer a hidden site than this (only its own trail)
MAIN_W = 3
SIDE_W = 2


def wspan(width: int) -> Tuple[int, int]:
    return -((width - 1) // 2), width // 2


@dataclass
class Frame:
    oy: int
    ox: int
    d: Tuple[int, int]  # along the spine
    l: Tuple[int, int]  # across it (always +x or +y)

    def yx(self, u: int, v: int) -> Cell:
        return (self.oy + u * self.d[0] + v * self.l[0], self.ox + u * self.d[1] + v * self.l[1])

    def uv(self, y: int, x: int) -> UV:
        dy, dx = y - self.oy, x - self.ox
        return (dy * self.d[0] + dx * self.d[1], dy * self.l[0] + dx * self.l[1])

    @property
    def vertical(self) -> bool:
        """True when u runs north-south on the map."""
        return self.d[0] != 0


def raster(frame: Frame, pts: Sequence[UV], width: int) -> Set[Cell]:
    lo, hi = wspan(width)
    out: Set[Cell] = set()
    if len(pts) == 1:
        pts = [pts[0], pts[0]]
    for (u0, v0), (u1, v1) in zip(pts, pts[1:]):
        if v0 == v1:
            us, vs = range(min(u0, u1) + lo, max(u0, u1) + hi + 1), range(v0 + lo, v0 + hi + 1)
        else:
            us, vs = range(u0 + lo, u0 + hi + 1), range(min(v0, v1) + lo, max(v0, v1) + hi + 1)
        for u in us:
            for v in vs:
                out.add(frame.yx(u, v))
    return out


def centerline(frame: Frame, pts: Sequence[UV]) -> List[Cell]:
    out: List[Cell] = []
    for (u0, v0), (u1, v1) in zip(pts, pts[1:]):
        n = abs(u1 - u0) + abs(v1 - v0)
        su, sv = np.sign(u1 - u0), np.sign(v1 - v0)
        for i in range(n):
            out.append(frame.yx(int(u0 + su * i), int(v0 + sv * i)))
    if pts:
        out.append(frame.yx(*pts[-1]))
    dedup: List[Cell] = []
    for c in out:
        if not dedup or dedup[-1] != c:
            dedup.append(c)
    return dedup


def simplify(pts: Sequence[UV]) -> List[UV]:
    out: List[UV] = []
    for p in pts:
        if out and out[-1] == p:
            continue
        if len(out) >= 2:
            (a0, b0), (a1, b1) = out[-2], out[-1]
            if (a0 == a1 == p[0]) or (b0 == b1 == p[1]):
                out[-1] = p
                continue
        out.append(p)
    return out


Profile = List[Tuple[float, int]]  # [(u where this run starts, v)]


def v_at(prof: Profile, u: float) -> int:
    v = prof[0][1]
    for us, vv in prof:
        if us <= u:
            v = vv
        else:
            break
    return v


def jogs_in(prof: Profile, a: float, b: float) -> List[Tuple[int, int, int]]:
    """(u, v_before, v_after) for every jog strictly inside (a, b)."""
    out = []
    for i in range(1, len(prof)):
        u = prof[i][0]
        if a < u < b:
            out.append((int(u), prof[i - 1][1], prof[i][1]))
    return out


def shifted(prof: Profile, o: int) -> Profile:
    """A profile parallel to ``prof`` at lateral offset ``o``, with every corner moved
    so the spacing stays |o| through the turn (inner lanes turn first)."""
    out: Profile = [(-1e9, prof[0][1] + o)]
    for i in range(1, len(prof)):
        u, v = prof[i]
        dv = v - prof[i - 1][1]
        out.append((u - np.sign(o) * np.sign(dv) * abs(o), v + o))
    return out


@dataclass
class Node:
    id: int
    kind: str  # exit | junction | clearing | spectacle | corner
    y: int
    x: int
    hy: int = 0  # half extents of the clearing (0 = a point)
    hx: int = 0
    area: Optional[dict] = None
    exit_index: Optional[int] = None
    oy: int = 0  # clearing centre relative to the node point (a spectacle clearing sits
    ox: int = 0  # to one side of the main lane, so the road runs past the landmark)
    area_center: Optional[Cell] = None

    def rect(self) -> Tuple[int, int, int, int]:
        cy, cx = self.y + self.oy, self.x + self.ox
        return (cy - self.hy, cy + self.hy, cx - self.hx, cx + self.hx)

    def inside(self, y: int, x: int, pad: int = 0) -> bool:
        if not (self.hy or self.hx):
            return False
        y0, y1, x0, x1 = self.rect()
        return y0 - pad <= y <= y1 + pad and x0 - pad <= x <= x1 + pad

    def half_uv(self, f: Frame) -> Tuple[int, int]:
        return (self.hy, self.hx) if f.vertical else (self.hx, self.hy)

    def cells(self) -> List[Cell]:
        if not (self.hy or self.hx):
            return [(self.y, self.x)]
        y0, y1, x0, x1 = self.rect()
        out = []
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                corner = (y in (y0, y1)) and (x in (x0, x1))
                if not corner:  # knock the corners off so it doesn't read as a box
                    out.append((y, x))
        return out


@dataclass
class Lane:
    id: int
    kind: str  # main | loop | dead_end
    frame: Frame
    pts: List[UV]
    width: int
    section: int
    side: int = 0
    level: int = 0
    fate: str = "full"  # full | merge | dead
    parent: Optional[int] = None
    pocket: Optional[Tuple[int, int, int, int]] = None  # u0, u1, v0, v1
    cells: Set[Cell] = field(default_factory=set)
    pocket_cells: Set[Cell] = field(default_factory=set)
    area: Optional[dict] = None
    forced: bool = False
    meta: dict = field(default_factory=dict)

    def center(self) -> List[Cell]:
        return centerline(self.frame, self.pts)


@dataclass
class Section:
    id: int
    leg: int
    a: int  # node ids
    b: int
    prof: Profile = field(default_factory=list)
    lanes: List[int] = field(default_factory=list)


class Planner:
    def __init__(self, route, areas: List[dict], exits: List[Tuple[object, Cell, List[Cell]]]):
        self.r = route
        self.spec = route.spec
        self.rng = route.rng
        self.h, self.w = route.h, route.w
        self.owner = np.full((self.h, self.w), -1, dtype=int)
        self.kind_of: Dict[int, str] = {}
        self.nodes: List[Node] = []
        self.lanes: List[Lane] = []
        self.sections: List[Section] = []
        self.legs: List[Tuple[Frame, int]] = []
        # special areas never take a road clearing or a spur: reserve_specials() hides them
        self.special_queue = list(areas)
        self.areas: List[dict] = []
        self.exits = exits  # (Exit, centre cell, cells)
        self.endings: List[dict] = []
        self.next_id = 0
        self.min_run = max(4, int(getattr(self.spec, "min_run", 5)))
        from collections import Counter
        self.why = Counter()
        self.keep_main = np.zeros((self.h, self.w), dtype=bool)  # main road may not enter
        self.keep_any = np.zeros((self.h, self.w), dtype=bool)  # nothing planned may enter
        self.sites: List[dict] = []  # reserved hidden special-area sites

    # ------------------------------------------------------------ owner grid

    def _oid(self, kind: str) -> int:
        i = self.next_id
        self.next_id += 1
        self.kind_of[i] = kind
        return i

    def _mask(self, cells) -> np.ndarray:
        m = np.zeros((self.h, self.w), dtype=bool)
        for (y, x) in cells:
            if 0 <= y < self.h and 0 <= x < self.w:
                m[y, x] = True
        return m

    def _in_node(self, y: int, x: int) -> Optional[Node]:
        for n in self.nodes:
            if n.inside(y, x):
                return n
        return None

    def fits(self, cells: Set[Cell], junctions: Sequence[Cell], ignore_nodes: Sequence[int] = ()) -> bool:
        ign = [n for n in self.nodes if n.id in ignore_nodes]
        own = set()
        for (y, x) in cells:
            if any(n.inside(y, x) for n in ign):
                continue  # inside the clearing it attaches to
            if not (MARGIN <= y < self.h - MARGIN and MARGIN <= x < self.w - MARGIN):
                if not any(abs(y - jy) <= JUNCTION_R and abs(x - jx) <= JUNCTION_R for (jy, jx) in junctions):
                    return False
            own.add((y, x))
        if not own:
            return True
        m = self._mask(own)
        if (m & self.keep_any).any():
            return False  # stays well clear of a reserved hidden special site
        dil = binary_dilation(m, structure=np.ones((2 * GAP + 1, 2 * GAP + 1), dtype=bool))
        bad = dil & (self.owner >= 0)
        for (jy, jx) in junctions:
            bad[max(0, jy - JUNCTION_R): jy + JUNCTION_R + 1, max(0, jx - JUNCTION_R): jx + JUNCTION_R + 1] = False
        return not bad.any()

    def claim(self, cells, oid: int) -> None:
        for (y, x) in cells:
            if 0 <= y < self.h and 0 <= x < self.w and self.owner[y, x] < 0:
                self.owner[y, x] = oid

    def junction_out(self, f: Frame, pts: Sequence[UV], node: Optional[Node], from_end: bool) -> Cell:
        """Where a lane's centreline leaves a clearing (or its first point if no clearing)."""
        cl = centerline(f, pts)
        if from_end:
            cl = cl[::-1]
        if node is None or not (node.hy or node.hx):
            return cl[0]
        for c in cl:
            if not node.inside(*c):
                return c
        return cl[-1]

    # ------------------------------------------------------------ spine

    def build_spine(self) -> None:
        (e0, c0, _), (e1, c1, _) = self.exits[0], self.exits[1]
        d0, d1 = SIDE_IN[e0.side], SIDE_IN[e1.side]

        def frame(o: Cell, d) -> Frame:
            return Frame(o[0], o[1], d, (abs(d[1]), abs(d[0])))

        def dist(a: Cell, b: Cell) -> int:
            return abs(a[0] - b[0]) + abs(a[1] - b[1])

        if d0 == (-d1[0], -d1[1]):
            f = frame(c0, d0)
            self.legs = [(f, f.uv(*c1)[0])]
        elif d0[0] * d1[0] + d0[1] * d1[1] == 0:
            corner = (c1[0], c0[1]) if d0[0] != 0 else (c0[0], c1[1])
            self.legs = [(frame(c0, d0), dist(c0, corner)), (frame(corner, (-d1[0], -d1[1])), dist(corner, c1))]
        else:  # both exits on one side: in, across, back out
            ext = self.h if d0[0] else self.w
            depth = int(ext * 0.55)
            p1 = (c0[0] + d0[0] * depth, c0[1] + d0[1] * depth)
            p2 = (c1[0] + d0[0] * depth, c1[1] + d0[1] * depth)
            dl = (int(np.sign(p2[0] - p1[0])), int(np.sign(p2[1] - p1[1])))
            self.legs = [(frame(c0, d0), depth), (frame(p1, dl), dist(p1, p2)), (frame(p2, (-d0[0], -d0[1])), depth)]

    def vrange(self, f: Frame) -> Tuple[int, int]:
        if f.l[1]:  # v along x
            lo, hi = MARGIN - f.ox, self.w - 1 - MARGIN - f.ox
        else:
            lo, hi = MARGIN - f.oy, self.h - 1 - MARGIN - f.oy
        if self.spec.biome == "beach":  # the far third is sea
            hi = lo + int((hi - lo) * 0.62)
        return lo, hi

    # ------------------------------------------------------------ nodes

    def place_nodes(self) -> None:
        """Nodes along the spine. Sizes first (a spectacle clearing is as big as its
        special area), then positions, so every section gets a fair share of the
        free length between clearings."""
        spec, rng = self.spec, self.rng
        total = sum(U for _, U in self.legs)
        n_sec = int(getattr(spec, "sections", 0) or 0)
        if n_sec <= 0:
            n_sec = int(np.clip(round(total / 30), 2, 5))
        n_legs = len(self.legs)
        n_int = max(0, n_sec - n_legs)  # corners are nodes too
        # internal nodes per leg, proportional to leg length
        per_leg = [0] * n_legs
        for _ in range(n_int):
            i = max(range(n_legs), key=lambda k: self.legs[k][1] / (per_leg[k] + 1))
            per_leg[i] += 1
        # what each internal node is: spectacle (special area), plain clearing, junction
        # special areas are mostly hidden (spur pockets, the back road); now and then
        # one sits right by the road at a convergence clearing
        # owner rule: special areas are never on the road (no spectacle clearings for
        # them); a plain clearing on the spine is a rest stop, at most one per route
        n_spect = 0
        n_plain = min(n_int, 1 if int(spec.clearings) > 0 else 0)
        kinds = ["spectacle"] * n_spect + ["clearing"] * n_plain
        kinds += ["junction"] * (n_int - len(kinds))
        # spectacle in the middle of the route, junctions near the ends
        order = sorted(range(n_int), key=lambda i: abs(i - (n_int - 1) / 2))
        kind_at = [""] * n_int
        for k, i in zip(kinds, order):
            kind_at[i] = k
        area_q = list(self.areas)
        e0, c0, _ = self.exits[0]
        e1, c1, _ = self.exits[1]
        self.nodes.append(Node(self._oid("node"), "exit", c0[0], c0[1], exit_index=0))
        k_int = 0
        prev_v = 0
        for li, (f, U) in enumerate(self.legs):
            vlo, vhi = self.vrange(f)
            m = per_leg[li]
            sizes = []
            for j in range(m):
                kind = kind_at[k_int + j]
                vis_ok = [a for a in area_q if _visibility(a) in ("visible", "territorial", "")]
                if kind == "spectacle" and vis_ok:
                    area = vis_ok[0]
                    area_q.remove(area)
                    sw, sh = _area_size(area)
                    au, av = (sh, sw) if f.vertical else (sw, sh)
                    hu_, hv_ = au // 2 + 1, (av + 6) // 2
                    sizes.append((kind, hu_ if f.vertical else hv_, hv_ if f.vertical else hu_, area))
                elif kind == "clearing":
                    sizes.append((kind, int(rng.integers(3, 5)), int(rng.integers(3, 5)), None))
                else:
                    sizes.append(("junction", 0, 0, None))
            k_int += m
            end_corner = li < n_legs - 1
            hu_end = 4 if end_corner else 0
            hus = [(hy if f.vertical else hx) for (_, hy, hx, _) in sizes]
            hu_start = 4 if li > 0 else 0
            free = U - hu_start - hu_end - 2 * sum(hus)
            gap = free / (m + 1)
            u = hu_start
            v_end = f.uv(*c1)[1] if li == n_legs - 1 else 0
            for j, (kind, hy, hx, area) in enumerate(sizes):
                hu, hv = (hy, hx) if f.vertical else (hx, hy)
                u = u + gap * rng.uniform(0.85, 1.15) + hu
                uu = int(round(u))
                t = uu / max(1, U)
                v_lin = (prev_v if li == 0 else 0) * (1 - t) + v_end * t if li == n_legs - 1 else 0
                v = int(v_lin + rng.uniform(-1, 1) * (vhi - vlo) * 0.3 * spec.meander)
                sig, ov = 0, 0
                if kind == "spectacle":
                    sig = 1 if (vhi - v) > (v - vlo) else -1
                    ov = sig * (hv - 2)
                v = int(np.clip(v, vlo + hv + 1 - ov, vhi - hv - 1 - ov))
                last_v = f.uv(self.nodes[-1].y, self.nodes[-1].x)[1]
                if abs(v - last_v) < 4 and vlo + hv + 1 - ov <= last_v <= vhi - hv - 1 - ov:
                    v = last_v  # no tiny offsets between consecutive nodes: straight on
                y, x = f.yx(uu, v)
                n = Node(self._oid("node"), kind, y, x, hy, hx, area)
                if sig:
                    n.oy, n.ox = ov * f.l[0], ov * f.l[1]
                    au_v = (hv - 3)  # half the lateral room left for the area
                    c_v = v + sig * (2 + 1 + au_v)
                    n.area_center = f.yx(uu, c_v)
                if (n.hy or n.hx) and not self.fits(set(n.cells()), [(n.y, n.x)]):
                    if area:
                        area_q.insert(0, area)
                    n.area, n.kind, n.hy, n.hx = None, "junction", 0, 0
                self.claim(n.cells(), n.id)
                self.nodes.append(n)
                u = uu + hu
            if end_corner:
                y, x = f.yx(U, 0)
                n = Node(self._oid("node"), "corner", y, x, 4, 4)
                vis_ok = [a for a in area_q if _visibility(a) in ("visible", "territorial", "")]
                if vis_ok and n_int == 0:
                    area = vis_ok[0]
                    area_q.remove(area)
                    sw, sh = _area_size(area)
                    n.area, n.kind, n.hy, n.hx = area, "spectacle", (sh + 2) // 2 + 1, (sw + 2) // 2 + 1
                if not self.fits(set(n.cells()), [(n.y, n.x)]):
                    if n.area:
                        area_q.insert(0, n.area)
                    n.area, n.kind, n.hy, n.hx = None, "corner", 3, 3
                self.claim(n.cells(), n.id)
                self.nodes.append(n)
        self.nodes.append(Node(self._oid("node"), "exit", c1[0], c1[1], exit_index=1))
        self.areas = area_q  # left for dead-end pockets
        for i in range(len(self.nodes) - 1):
            a, b = self.nodes[i], self.nodes[i + 1]
            li = self._leg_of(a, b)
            self.sections.append(Section(len(self.sections), li, a.id, b.id))

    def _leg_of(self, a: Node, b: Node) -> int:
        best, bi = None, 0
        for li, (f, U) in enumerate(self.legs):
            ua, va = f.uv(a.y, a.x)
            ub, vb = f.uv(b.y, b.x)
            ok = -1 <= ua <= U + 1 and -1 <= ub <= U + 1 and ub > ua
            score = (0 if ok else 1, abs(va) + abs(vb))
            if best is None or score < best:
                best, bi = score, li
        return bi

    def node(self, nid: int) -> Node:
        return next(n for n in self.nodes if n.id == nid)

    # ------------------------------------------------------------ main lane

    def main_profile(self, f: Frame, A: Node, B: Node, band: Tuple[int, int], spacing: int, jog_scale: float) -> Profile:
        rng, spec = self.rng, self.spec
        ua, va = f.uv(A.y, A.x)
        ub, vb = f.uv(B.y, B.x)
        hua, _ = A.half_uv(f)
        hub, _ = B.half_uv(f)
        lead = self.min_run + 2 + (self._lead_pad if hasattr(self, "_lead_pad") else 0)
        u_lo = ua + hua + lead
        u_hi = ub - hub - lead
        room = u_hi - u_lo
        # a road runs as straight as it can: at most one bend of its own per section
        # (bends get a pond / obstacle later so they have a reason)
        k = int(round(jog_scale * spec.meander * max(0, room) / 30 + rng.uniform(-0.4, 0.4)))
        k = max(0, min(k, 1))
        amp_hi = 4 + int(8 * spec.meander)
        levels = [va]
        for _ in range(k):
            for _try in range(10):
                step = int(rng.integers(4, amp_hi + 1)) * (1 if rng.random() < 0.5 else -1)
                nv = int(np.clip(levels[-1] + step, band[0], band[1]))
                if abs(nv - levels[-1]) >= 4:
                    levels.append(nv)
                    break
        levels.append(vb)
        # drop jogs smaller than 4 tiles (no one-tile wiggles)
        changed = True
        while changed and len(levels) > 2:
            changed = False
            for i in range(1, len(levels)):
                if levels[i] == levels[i - 1] or abs(levels[i] - levels[i - 1]) < 4:
                    if 0 < i < len(levels) - 1:
                        levels.pop(i)
                    elif len(levels) > 2:
                        levels.pop(i - 1 if i - 1 > 0 else i)
                    changed = True
                    break
        if len(levels) == 2 and 0 < abs(levels[1] - levels[0]) < 4:
            # the two ends almost line up: a wide detour instead of a tiny jog
            off = 6 * (1 if rng.random() < 0.5 else -1)
            if not (band[0] <= levels[0] + off <= band[1]):
                off = -off
            levels = [levels[0], levels[0] + off, levels[1]]
        if levels[0] == levels[-1] and len(levels) == 2:
            levels = [levels[0]]
        n_j = len(levels) - 1
        if n_j == 0:
            return [(-1e9, va)]
        # spread the jogs, at least `spacing` apart
        sp = max(self.min_run + 1, spacing)
        if room < sp * (n_j - 1):
            sp = max(self.min_run + 1, room // max(1, n_j - 1) if n_j > 1 else sp)
        if room < 0:
            us = [int((ua + ub) / 2 + i * (self.min_run + 1)) for i in range(n_j)]
        else:
            slack = room - sp * (n_j - 1)
            cuts = sorted(rng.uniform(0, max(0, slack), n_j))
            us = [int(u_lo + cuts[i] + sp * i) for i in range(n_j)]
        prof: Profile = [(-1e9, levels[0])]
        for u, v in zip(us, levels[1:]):
            prof.append((u, v))
        return prof

    def prof_pts(self, prof: Profile, ua: int, ub: int) -> List[UV]:
        pts = [(ua, v_at(prof, ua))]
        for (u, v0, v1) in jogs_in(prof, ua, ub):
            pts += [(u, v0), (u, v1)]
        pts.append((ub, v_at(prof, ub)))
        return simplify(pts)

    def runs_ok(self, f: Frame, pts: Sequence[UV], nodes: Sequence[Node], dead_tail: bool = False) -> bool:
        """Every straight run along the spine is at least min_run long (outside
        clearings), every sideways run at least 4: no jaggies."""
        segs = list(zip(pts, pts[1:]))
        for (u0, v0), (u1, v1) in segs:
            if u0 != u1 and v0 != v1:
                return False  # never a diagonal
        us_along = [p[0] for p in pts]
        for i, ((u0, v0), (u1, v1)) in enumerate(segs):
            n = abs(u1 - u0) + abs(v1 - v0)
            cells = centerline(f, [(u0, v0), (u1, v1)])
            vis = sum(1 for c in cells if not any(nd.inside(*c) for nd in nodes))
            if vis == 0:
                continue
            first_or_last = i == 0 or i == len(segs) - 1
            if v0 == v1:
                need = 3 if (dead_tail and i == len(segs) - 1) else self.min_run
                if first_or_last:
                    need = 3 if vis < n else need  # partly inside a clearing / junction stub
                if vis < need and n < need:
                    return False
                if vis < need and not first_or_last:
                    return False
            else:
                if n < 4:
                    return False
        return True

    # ------------------------------------------------------------ sections

    def plan_sections(self) -> None:
        spec, rng = self.spec, self.rng
        max_lanes = int(np.clip(getattr(spec, "max_lanes", 3), 1, 3))
        for s in self.sections:
            f, U = self.legs[s.leg]
            A, B = self.node(s.a), self.node(s.b)
            ua, va = f.uv(A.y, A.x)
            ub, vb = f.uv(B.y, B.x)
            hua, hva = A.half_uv(f)
            hub, hvb = B.half_uv(f)
            length = (ub - hub) - (ua + hua)
            vlo, vhi = self.vrange(f)
            # how many lanes: limited options, but rarely none
            if length < 14 or max_lanes == 1:
                n = 1
            else:
                n = 2
                if max_lanes >= 3 and length >= 20 and rng.random() < 0.5:
                    n = 3
                if s.id > 0 and rng.random() < 0.1:
                    n = 1  # now and then a plain stretch: limited options
            plan = []  # (side, level, offset)
            if n >= 2:
                # lanes go where there is room: compare the space either side of the nodes
                mid_v = (va + vb) / 2
                room_p, room_n = vhi - mid_v, mid_v - vlo
                p_pos = room_p / max(1.0, room_p + room_n)
                s1 = 1 if rng.random() < p_pos else -1
                o1 = int(rng.integers(6, 11))
                plan.append((s1, 1, s1 * o1))
                if n == 3:
                    other_room = room_n if s1 > 0 else room_p
                    if other_room > 12 and rng.random() < 0.75:
                        o = int(rng.integers(6, 11))
                        plan.append((-s1, 1, -s1 * o))
                    else:
                        plan.append((s1, 2, s1 * (o1 + int(rng.integers(6, 9)))))
            room_pos = max([abs(o) for (sd, _, o) in plan if sd > 0] + [0]) + (5 if any(sd > 0 for sd, _, _ in plan) else 1)
            room_neg = max([abs(o) for (sd, _, o) in plan if sd < 0] + [0]) + (5 if any(sd < 0 for sd, _, _ in plan) else 1)
            band = (vlo + room_neg, vhi - room_pos)
            if band[0] > band[1]:
                band = (vlo + 2, vhi - 2)
                plan = plan[:1]
            omax = max([abs(o) for _, _, o in plan] + [0])
            self._lead_pad = 6 if plan else 0
            spacing = self.min_run + 2 * omax + 2
            mid = Lane(-1, "main", f, [], MAIN_W, s.id)
            ok = False
            for attempt in range(40):
                scale = 1.0 if attempt < 15 else (0.5 if attempt < 30 else 0.0)
                prof = self.main_profile(f, A, B, band, spacing, scale)
                pts = self.prof_pts(prof, ua, ub)
                cells = raster(f, pts, MAIN_W)
                ja = self.junction_out(f, pts, A, False)
                jb = self.junction_out(f, pts, B, True)
                if self.runs_ok(f, pts, [A, B]) and self.fits(cells, [ja, jb], [A.id, B.id]) \
                        and not (self._mask(cells) & self.keep_main).any():
                    ok = True
                    break
            if not ok:
                self.r.log.append(f"section {s.id}: main lane forced")
            s.prof = prof
            mid.id = self._oid("main")
            mid.pts, mid.cells = pts, cells
            mid.meta["junctions"] = [ja, jb]
            self.claim(cells, mid.id)
            self.lanes.append(mid)
            s.lanes.append(mid.id)
            for (side, level, o) in plan:
                self._side_lane(s, f, A, B, prof, side, level, o, n_planned=len(plan))

    def _fate(self) -> str:
        spec, rng = self.spec, self.rng
        r = rng.random()
        dc = float(getattr(spec, "dead_end_chance", 0.3))
        mc = float(getattr(spec, "merge_chance", 0.35))
        if r < dc:
            return "dead"
        if r < dc + mc:
            return "merge"
        return "full"

    def _side_lane(self, s: Section, f: Frame, A: Node, B: Node, mprof: Profile, side: int, level: int, o: int,
                   n_planned: int) -> Optional[Lane]:
        rng = self.rng
        ua, _ = f.uv(A.y, A.x)
        ub, _ = f.uv(B.y, B.x)
        hua, _ = A.half_uv(f)
        hub, _ = B.half_uv(f)
        parent = None
        pprof = mprof
        if level == 2:
            sibs = [self.lane(i) for i in s.lanes if self.lane(i).side == side and self.lane(i).level == 1]
            if not sibs:
                return None
            parent = sibs[0]
            pprof = parent.meta["prof"]
        else:
            parent = self.lane(s.lanes[0])
        fate0 = self._fate()
        outermost = level == 2 or not any(l2 for l2 in [1] if n_planned == 2 and level == 1 and False)
        for attempt in range(60):
            fate = fate0 if attempt < 25 else ("full" if attempt < 45 else "dead")
            prof = shifted(mprof, o)
            # an outward dogleg of its own now and then, so lanes aren't all parallel
            if False and outermost:  # no outward doglegs for their own sake (owner: lanes that wander)
                prof = self._bump(prof, side, ua + hua, ub - hub)
            if level == 2:
                p_lo, p_hi = parent.meta["u_s"] + 6, parent.meta["u_e"] - 6
            else:
                p_lo, p_hi = ua, ub
            # where it leaves its parent
            if A.hy or A.hx:
                u_s = int(rng.integers(ua - hua + 1, ua + hua)) if level == 1 else int(rng.integers(p_lo, max(p_lo + 1, p_lo + 10)))
            else:
                lead_lo = 4 if A.kind == "exit" else 0
                u_s = int(p_lo + rng.integers(lead_lo, lead_lo + 9)) if level == 1 else int(rng.integers(p_lo, max(p_lo + 1, p_lo + 10)))
            # where it ends
            if fate == "full":
                if level == 2:
                    u_e = int(rng.integers(max(u_s + 14, p_hi - 10), max(u_s + 15, p_hi)))
                elif B.hy or B.hx:
                    u_e = int(rng.integers(ub - hub + 1, ub + hub))
                else:
                    lead_lo = 4 if B.kind == "exit" else 0
                    u_e = int(ub - rng.integers(lead_lo, lead_lo + 9))
            elif fate == "merge":
                lo_e, hi_e = u_s + 12, (p_hi if level == 2 else ub - hub) - 5
                if hi_e <= lo_e:
                    self.why["short"] += 1
                    continue
                u_e = int(rng.integers(lo_e, hi_e))
            else:
                lo_e, hi_e = u_s + 9, (p_hi if level == 2 else ub - hub) - 2
                if hi_e <= lo_e:
                    self.why["short"] += 1
                    continue
                u_e = int(rng.integers(lo_e, hi_e))
            # the parent must be on a straight run where we leave and rejoin it
            if not (A.inside(*f.yx(u_s, v_at(pprof, u_s)))) and jogs_in(pprof, u_s - 3, u_s + 3):
                self.why["jogpar"] += 1
                continue
            if fate != "dead" and not B.inside(*f.yx(u_e, v_at(pprof, u_e))) and jogs_in(pprof, u_e - 3, u_e + 3):
                self.why["jogend"] += 1
                continue
            pts = [(u_s, v_at(pprof, u_s)), (u_s, v_at(prof, u_s))]
            for (u, v0, v1) in jogs_in(prof, u_s, u_e):
                pts += [(u, v0), (u, v1)]
            pts.append((u_e, v_at(prof, u_e)))
            if fate != "dead":
                pts.append((u_e, v_at(pprof, u_e)))
            pts = simplify(pts)
            if len(pts) < 2:
                continue
            nodes = [A, B]
            if not self.runs_ok(f, pts, nodes, dead_tail=(fate == "dead")):
                self.why["runs"] += 1
                continue
            cells = raster(f, pts, SIDE_W)
            pocket = None
            pcells: Set[Cell] = set()
            if fate == "dead":
                vL = v_at(prof, u_e)
                v0, v1 = (vL, vL + 4) if side > 0 else (vL - 3, vL + 1)
                pocket = (u_e - 2, u_e + 2, v0, v1)
                pcells = {f.yx(u, v) for u in range(u_e - 2, u_e + 3) for v in range(v0, v1 + 1)}
            js = [self.junction_out(f, pts, A if A.inside(*f.yx(*pts[0])) else None, False)]
            if fate != "dead":
                js.append(self.junction_out(f, pts, B if B.inside(*f.yx(*pts[-1])) else None, True))
            if not self.fits(cells | pcells, js, [A.id, B.id]):
                self.why["fits"] += 1
                continue
            kind = "dead_end" if fate == "dead" else "loop"
            lane = Lane(self._oid(kind), kind, f, pts, SIDE_W, s.id, side, level, fate,
                        parent.id if parent else None, pocket, cells, pcells)
            lane.meta.update(prof=prof, u_s=u_s, u_e=u_e, junctions=js)
            self.claim(cells | pcells, lane.id)
            self.lanes.append(lane)
            s.lanes.append(lane.id)
            return lane
        return None

    def _bump(self, prof: Profile, side: int, lo: float, hi: float) -> Profile:
        """Insert an outward dogleg into one long straight run."""
        rng, mr = self.rng, self.min_run
        marks = [lo] + [u for (u, _) in prof[1:] if lo < u < hi] + [hi]
        runs = [(a, b) for a, b in zip(marks, marks[1:]) if b - a >= 3 * mr + 4]
        if not runs:
            return prof
        a, b = runs[int(rng.integers(0, len(runs)))]
        u1 = int(rng.integers(int(a + mr + 1), int(b - 2 * mr - 1)))
        u2 = int(rng.integers(u1 + mr + 1, int(b - mr)))
        amp = int(rng.integers(4, 7)) * side
        v = v_at(prof, u1)
        out = [p for p in prof if p[0] < u1] + [(u1, v + amp), (u2, v)] + [p for p in prof if p[0] > u2]
        out.sort(key=lambda p: p[0])
        return out

    # ------------------------------------------------------------ hidden special sites

    def reserve_specials(self) -> None:
        """Owner rule: special areas are rare and HIDDEN. Each one gets a site that is
        - at least EXIT_DIST tiles from every exit / town gate,
        - outside the GBA camera window (CAM_X x CAM_Y around the player) from every
          point of the main road, and
        - well clear of every other planned lane (only its gated hidden trail reaches it).
        Junction / rest-stop nodes may be nudged sideways so the road passes far enough
        away. An area with no such site is dropped (the route simply has none)."""
        from .generate import exit_cells

        if not self.special_queue:
            return
        H, W = self.h, self.w
        exit_pts = []
        for e in self.spec.exits:
            _, cells = exit_cells(self.r, e)
            exit_pts.extend(cells)
        for area in self.special_queue:
            w, h = _real_size(area)
            best = None
            for y0 in range(MARGIN, H - h - MARGIN + 1):
                for x0 in range(MARGIN, W - w - MARGIN + 1):
                    y1, x1 = y0 + h - 1, x0 + w - 1
                    dmin = min((max(0, y0 - ey, ey - y1) + max(0, x0 - ex, ex - x1) for ey, ex in exit_pts),
                               default=99)
                    if dmin < EXIT_DIST:
                        continue
                    if (self.owner[max(0, y0 - GAP):y1 + GAP + 1, max(0, x0 - GAP):x1 + GAP + 1] >= 0).any():
                        # only movable nodes may be in the way; checked in _site_moves
                        pass
                    moves = self._site_moves(y0, x0, h, w)
                    if moves is None:
                        continue
                    # deep: far from the exits, and towards the middle of the route
                    cy, cx = y0 + h / 2, x0 + w / 2
                    mid = abs(cy - H / 2) / H + abs(cx - W / 2) / W
                    score = dmin - 3 * len(moves) - 6 * mid + float(self.rng.random()) * 2
                    if best is None or score > best[0]:
                        best = (score, y0, x0, moves)
            if best is None:
                self.r.log.append(f"special: no hidden site for {area.get('id')} (>= {EXIT_DIST} from exits, "
                                  f"out of sight of the road); left out")
                continue
            _, y0, x0, moves = best
            for nid, (ny, nx) in moves.items():
                n = self.node(nid)
                self.owner[self.owner == n.id] = -1
                n.y, n.x = ny, nx
                self.claim(n.cells(), n.id)
            box = (y0, x0, h, w)
            oid = self._oid("special_site")
            self.claim([(y, x) for y in range(y0, y0 + h) for x in range(x0, x0 + w)], oid)
            self.keep_main[max(0, y0 - CAM_Y):y0 + h + CAM_Y, max(0, x0 - CAM_X):x0 + w + CAM_X] = True
            self.keep_any[max(0, y0 - KEEP):y0 + h + KEEP, max(0, x0 - KEEP):x0 + w + KEEP] = True
            self.sites.append({"area": area, "box": box, "oid": oid})
            self.r.log.append(f"special: {area.get('id')} hidden at {box}, {int(best[0])} score")

    def align_nodes(self) -> None:
        """No tiny offsets between consecutive spine nodes (they turn into pointless
        wiggles or detours in the road): snap a movable node onto its neighbour's line
        when they are less than 4 apart, if the road may go there."""
        for i in range(1, len(self.nodes) - 1):
            n = self.nodes[i]
            if n.kind in ("exit", "corner"):
                continue
            f = self.legs[self._leg_of_node(n)][0]
            u, v = f.uv(n.y, n.x)
            for m in (self.nodes[i - 1], self.nodes[i + 1]):
                mu, mv = f.uv(m.y, m.x)
                if 0 < abs(mv - v) < 4:
                    ny, nx = f.yx(u, mv)
                    hy, hx = n.hy, n.hx
                    box = self.keep_main[max(0, ny - hy - 2):ny + hy + 3, max(0, nx - hx - 2):nx + hx + 3]
                    if not box.any():
                        self.owner[self.owner == n.id] = -1
                        n.y, n.x = ny, nx
                        self.claim(n.cells(), n.id)
                        v = mv
                    break

    def _site_moves(self, y0: int, x0: int, h: int, w: int) -> Optional[Dict[int, Cell]]:
        """Node moves that keep the road out of this site's camera window, or None if
        the site can't work (an exit / corner in the way, or no room for the road)."""
        ky0, ky1, kx0, kx1 = y0 - CAM_Y, y0 + h - 1 + CAM_Y, x0 - CAM_X, x0 + w - 1 + CAM_X
        moves: Dict[int, Cell] = {}
        for n in self.nodes:
            ny0, ny1, nx0, nx1 = n.rect() if (n.hy or n.hx) else (n.y, n.y, n.x, n.x)
            pad = 2  # the road is 3 wide
            if ny1 + pad < ky0 or ny0 - pad > ky1 or nx1 + pad < kx0 or nx0 - pad > kx1:
                continue
            if n.kind in ("exit", "corner"):
                return None
            # nudge across the leg (v) to whichever side of the window has room
            f = self.legs[self._leg_of_node(n)][0]
            vlo, vhi = self.vrange(f)
            _, hv = n.half_uv(f)
            u, v = f.uv(n.y, n.x)
            corners = [f.uv(yy, xx)[1] for yy in (ky0, ky1) for xx in (kx0, kx1)]
            kv0, kv1 = min(corners), max(corners)
            opts = [kv0 - hv - pad - 1, kv1 + hv + pad + 1]
            opts = [o for o in opts if vlo + hv + 1 <= o <= vhi - hv - 1]
            if not opts:
                return None
            nv = min(opts, key=lambda o: abs(o - v))
            if abs(nv - v) > 12:
                return None
            moves[n.id] = f.yx(u, nv)
        # nothing else may sit in the site itself
        own = self.owner[max(0, y0 - GAP):y0 + h + GAP, max(0, x0 - GAP):x0 + w + GAP]
        if (own >= 0).any() and not set(np.unique(own[own >= 0])) <= set(moves):
            return None
        return moves

    def _leg_of_node(self, n: Node) -> int:
        for li, (f, U) in enumerate(self.legs):
            u, _ = f.uv(n.y, n.x)
            if 0 <= u <= U:
                return li
        return 0

    def check_sites(self) -> None:
        """If a main lane had to be forced through a site's window, the site is dropped."""
        main = np.zeros((self.h, self.w), dtype=bool)
        for l in self.lanes:
            if l.kind == "main":
                main |= self._mask(l.cells)
        for st in list(self.sites):
            y0, x0, h, w = st["box"]
            win = np.zeros_like(main)
            win[max(0, y0 - CAM_Y):y0 + h + CAM_Y, max(0, x0 - CAM_X):x0 + w + CAM_X] = True
            if (win & main).any():
                self.r.log.append(f"special: road runs within sight of {st['area'].get('id')}; dropped")
                self.sites.remove(st)
                self.owner[self.owner == st["oid"]] = -1

    def lane(self, lid: int) -> Lane:
        return next(l for l in self.lanes if l.id == lid)

    # ------------------------------------------------------------ quotas, pockets, budget

    def enforce_quotas(self) -> None:
        """Old knobs as minimums: `dead_ends` + `branches` side paths that end in
        something (spurs), `loops` lanes that rejoin. Special areas without a
        clearing on the spine get a spur of their own with the area at its end."""
        spec = self.spec
        for area in list(self.areas):
            if self.spur(area=area):
                self.areas.remove(area)
        want = max(int(spec.dead_ends), 0)  # fewer, meaningful spurs (each gets a purpose)
        have = sum(1 for l in self.lanes if l.fate == "dead")
        tries = 0
        while have < want and tries < want * 3:
            tries += 1
            if self.spur():
                have += 1

    def spur(self, area: Optional[dict] = None) -> bool:
        """A short side path off a lane, into the trees: out sideways, maybe one turn,
        and it ends in something (an item, a lookout, a pocket, or a special area)."""
        rng, mr = self.rng, self.min_run
        hosts = [l for l in self.lanes if l.kind in ("main", "loop")]
        if not hosts:
            return False
        nodes = self.nodes
        for attempt in range(120):
            par = hosts[int(rng.integers(0, len(hosts)))]
            f = par.frame
            segs = [(a, b) for a, b in zip(par.pts, par.pts[1:]) if a[1] == b[1] and abs(b[0] - a[0]) >= 10]
            if not segs:
                continue
            a, b = segs[int(rng.integers(0, len(segs)))]
            lo_u, hi_u = min(a[0], b[0]) + 4, max(a[0], b[0]) - 4
            if hi_u <= lo_u:
                continue
            u0, vp = int(rng.integers(lo_u, hi_u)), a[1]
            if any(nd.inside(*f.yx(u0, vp), pad=2) for nd in nodes):
                continue
            side = 1 if rng.random() < 0.5 else -1
            plo, phi = wspan(par.width)
            reach = int(rng.integers(5, 11))
            v1 = vp + side * (reach + (phi if side > 0 else -plo))
            pts = [(u0, vp), (u0, v1)]
            last = (0, side)  # direction of the last run, in (du, dv)
            if rng.random() < 0.6:
                tau = 1 if rng.random() < 0.5 else -1
                u1 = u0 + tau * int(rng.integers(mr, mr + 6))
                pts.append((u1, v1))
                last = (tau, 0)
            end = pts[-1]
            if area is not None:
                sw, sh = _area_size(area)
                su, sv = ((sh, sw) if f.vertical else (sw, sh))
                su, sv = su + 2, sv + 2
            elif rng.random() < 0.3:
                su, sv = 4, 4
            else:
                su = sv = 0
            pc: Set[Cell] = set()
            pocket = None
            if su:
                if last[1] != 0:  # ran sideways: pocket further out, centred on the run
                    u_lo, v_lo = end[0] - su // 2, (end[1] + 1 if side > 0 else end[1] - sv)
                else:
                    u_lo = end[0] + 1 if last[0] > 0 else end[0] - su
                    v_lo = end[1] - (1 if side > 0 else sv - 2)
                pocket = (u_lo, u_lo + su - 1, v_lo, v_lo + sv - 1)
                pc = {f.yx(u, v) for u in range(u_lo, u_lo + su) for v in range(v_lo, v_lo + sv)}
            if not self.runs_ok(f, pts, nodes, dead_tail=True):
                continue
            cells = raster(f, pts, SIDE_W)
            js = [f.yx(u0, vp)]
            if not self.fits(cells | pc, js):
                continue
            lane = Lane(self._oid("dead_end"), "dead_end", f, pts, SIDE_W, par.section, side, par.level + 1, "dead",
                        par.id, pocket, cells, pc, area=area)
            lane.meta.update(prof=[(-1e9, v1)], u_s=u0, u_e=end[0], junctions=js, spur=True)
            self.claim(cells | pc, lane.id)
            self.lanes.append(lane)
            for s in self.sections:
                if s.id == par.section:
                    s.lanes.append(lane.id)
            return True
        return False

    def _path_to_lanes(self, start: Cell, max_len: int = 30, hosts_only: bool = False) -> Optional[Tuple[List[Cell], int]]:
        """Orthogonal path (few, long straight runs) from ``start`` to the nearest lane,
        keeping GAP tiles of forest from everything else on the way. ``hosts_only``: only
        the road and its braids, never another spur (no spur-off-a-spur strings)."""
        import heapq

        ok = [i for i, k in self.kind_of.items() if k in ("main", "loop", "dead_end")]
        if hosts_only:
            ok = [i for i in ok if self.kind_of[i] in ("main", "loop")
                  or any(l.id == i and l.fate == "dead" and not l.meta.get("spur") for l in self.lanes)]
        goal = np.isin(self.owner, ok)
        near = binary_dilation(self.owner >= 0, structure=np.ones((2 * GAP + 1, 2 * GAP + 1), dtype=bool))
        h, w = self.h, self.w
        TURN = 10
        q = [(0.0, start, (0, 0))]
        dist = {(start, (0, 0)): 0.0}
        prev = {}
        found = None
        while q:
            g, cur, d = heapq.heappop(q)
            if g > dist.get((cur, d), 1e18) or g > max_len * 4:
                continue
            if goal[cur]:
                found = (cur, d)
                break
            for nd in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                if nd == (-d[0], -d[1]):
                    continue
                ny, nx = cur[0] + nd[0], cur[1] + nd[1]
                if not (MARGIN <= ny < h - MARGIN and MARGIN <= nx < w - MARGIN):
                    continue
                cost = 1.0 + (TURN if (d != (0, 0) and nd != d) else 0) + (5.0 if near[ny, nx] and not goal[ny, nx] else 0)
                key = ((ny, nx), nd)
                if g + cost < dist.get(key, 1e18):
                    dist[key] = g + cost
                    prev[key] = (cur, d)
                    heapq.heappush(q, (g + cost, (ny, nx), nd))
        if found is None:
            return None
        path = [found[0]]
        k = found
        while k in prev:
            k = prev[k]
            path.append(k[0])
        return path, int(self.owner[found[0]])  # lane end first, start (the target) last

    def back_road(self) -> None:
        """Use the space: a second, older path through the route area, parallel to the
        main braid in the widest unused band, joined to it only by one secret entrance
        (a narrow hidden trail, often behind a Cut tree). It ends at a hidden special
        area if one is left, otherwise at a reward pocket."""
        if self.spec.biome == "beach" or not self.legs:
            return
        rng = self.rng
        li = max(range(len(self.legs)), key=lambda i: self.legs[i][1])
        f, U = self.legs[li]
        vlo, vhi = self.vrange(f)
        used = [f.uv(y, x)[1] for (y, x) in zip(*np.nonzero(self.owner >= 0))]
        if not used:
            return
        used = np.array(used)
        bands = sorted(range(vlo + 1, vhi - 1), key=lambda v: int((np.abs(used - v) <= GAP + 2).sum()))[:8]
        for _ in range(200):
            # the least-crowded bands first, then anywhere in the lateral range
            v0 = int(bands[rng.integers(0, len(bands))]) if (bands and _ < 150) else int(rng.integers(vlo + 1, vhi - 1))
            ln = int(rng.integers(12, max(13, min(22, int(U * 0.4)))))  # an old trail, not a second road
            u1 = int(rng.integers(6, max(7, U - ln - 8)))
            u2 = u1 + ln
            v1 = int(np.clip(v0 + rng.integers(-3, 4), vlo + 1, vhi - 1))
            pts = [(u1, v0)]
            if abs(v1 - v0) >= 4:
                um = (u1 + u2) // 2
                pts += [(um, v0), (um, v1), (u2, v1)]
            else:
                pts += [(u2, v0)]
            pts = simplify(pts)
            if not self.runs_ok(f, pts, self.nodes, dead_tail=True):
                continue
            cells = raster(f, pts, SIDE_W)
            area = self.areas[0] if (self.areas and _ < 100) else None
            if area:
                sw, sh = _area_size(area)
                su, sv = ((sh, sw) if f.vertical else (sw, sh))
                su, sv = su + 2, sv + 2
            else:
                su, sv = 5, 5
            end = pts[-1]
            # pocket beside the trail's end, on the side away from the map edge
            toward = 1 if (end[1] - vlo) < (vhi - end[1]) else -1
            pu0 = end[0] - su + 2
            pv0 = end[1] + (2 if toward > 0 else -1 - sv)
            pc = {f.yx(u, v) for u in range(pu0, pu0 + su) for v in range(pv0, pv0 + sv)}
            if not self.fits(cells | pc, []):
                self.why["backroad_fit"] += 1
                continue
            oid = self._oid("back_road")
            self.claim(cells | pc, oid)
            # the one secret way in: a 1-wide orthogonal trail from its start to the nearest lane
            res = self._path_to_lanes(f.yx(*pts[0]), max_len=24)
            if res is None or len(res[0]) < 3:
                self.why["backroad_link"] += 1
                self.owner[self.owner == oid] = -1
                self.kind_of.pop(oid, None)
                continue
            link, hit = res
            self.kind_of[oid] = "dead_end"
            lane = Lane(oid, "dead_end", f, pts, SIDE_W, 0, 0, 1, "dead", hit,
                        (pu0, pu0 + su - 1, pv0, pv0 + sv - 1), cells, pc, area=area)
            lane.meta.update(prof=[(-1e9, v0)], u_s=pts[0][0], u_e=end[0], junctions=[], spur=True,
                             back_road=True, secret_link=link)
            self.lanes.append(lane)
            self.claim(set(link), self._oid("hidden"))
            if area:
                self.areas.pop(0)
            return

    def coverage(self) -> None:
        """No big blank tree mass: every region more than ~10x10 tiles away from any
        lane gets a short side path into it, ending in a pocket (a reward, a lookout,
        later perhaps a lore spot)."""
        from scipy.ndimage import distance_transform_cdt

        ident = Frame(0, 0, (1, 0), (0, 1))
        tried = np.zeros((self.h, self.w), dtype=bool)
        for _ in range(40):
            d = distance_transform_cdt(self.owner < 0, metric="chessboard").astype(float)
            d[:MARGIN + 3, :] = d[-MARGIN - 3:, :] = 0
            d[:, :MARGIN + 3] = d[:, -MARGIN - 3:] = 0
            if self.spec.biome == "beach":  # no lanes out into the sea third
                f0 = self.legs[0][0]
                vhi = self.vrange(f0)[1]
                for y in range(self.h):
                    for x in range(self.w):
                        if f0.uv(y, x)[1] > vhi - 4:
                            d[y, x] = 0
            if d.max() < COVER_BLANK:  # only really big blank masses get a lane (with a purpose)
                break
            # a failed try only rules out its own spot, not the whole mass around it
            d[tried] = 0
            if d.max() < COVER_BLANK - 3:
                break
            d += self.rng.random(d.shape) * 0.9
            ty, tx = np.unravel_index(int(np.argmax(d)), d.shape)
            ty, tx = int(ty), int(tx)
            tried[max(0, ty - 3): ty + 4, max(0, tx - 3): tx + 4] = True
            res = self._path_to_lanes((ty, tx), hosts_only=True)
            if res is None:
                continue
            path, hit = res
            pts = simplify([(y, x) for (y, x) in path])
            if len(path) < 6:
                continue
            # pocket around the target, 4x4 or 5x4 (sometimes just a stop)
            ph, pw = int(self.rng.integers(4, 6)), int(self.rng.integers(4, 6))
            py0, px0 = ty - ph // 2, tx - pw // 2
            pc = {(y, x) for y in range(py0, py0 + ph) for x in range(px0, px0 + pw)}
            if not self.runs_ok(ident, pts, self.nodes, dead_tail=True):
                continue
            cells = raster(ident, pts, SIDE_W)
            js = [path[0]]
            if not self.fits(cells | pc, js):
                continue
            par = next((l for l in self.lanes if l.id == hit), None)
            lane = Lane(self._oid("dead_end"), "dead_end", ident, pts, SIDE_W, par.section if par else 0, 0,
                        (par.level + 1) if par else 1, "dead", hit, (py0, py0 + ph - 1, px0, px0 + pw - 1), cells, pc)
            lane.meta.update(prof=[(-1e9, 0)], u_s=pts[0][0], u_e=pts[-1][0], junctions=js, spur=True, coverage=True)
            self.claim(cells | pc, lane.id)
            self.lanes.append(lane)
            for s in self.sections:
                if par and s.id == par.section:
                    s.lanes.append(lane.id)

    def pocket_specials(self) -> None:
        pass

    def walk_cells(self) -> Set[Cell]:
        out: Set[Cell] = set()
        for n in self.nodes:
            if n.hy or n.hx:
                out |= set(n.cells())
        for l in self.lanes:
            out |= l.cells | l.pocket_cells
        return out

    def budget(self) -> None:
        """Keep it a forest with paths in it: drop optional lanes until the planned
        open ground fits under the walkable budget (leaving room for later stages)."""
        cap = float(getattr(self.spec, "max_walkable", 0.38)) - 0.05
        area = self.h * self.w
        while len(self.walk_cells()) / area > cap:
            opt = [l for l in self.lanes if l.kind != "main" and l.area is None
                   and not any(c.parent == l.id for c in self.lanes)]
            if not opt:
                break
            l = max(opt, key=lambda l: len(l.cells))
            self.lanes.remove(l)
            self.owner[self.owner == l.id] = -1
            for s in self.sections:
                if l.id in s.lanes:
                    s.lanes.remove(l.id)
            self.r.log.append(f"budget: dropped lane {l.id}")

    # ------------------------------------------------------------ endings

    def endings_plan(self, specs: List[dict]) -> None:
        """Extra exits (declared or auto) and secret exits: a straight spur from the map
        edge to the nearest lane. Special endings were handled as dead-end pockets."""
        from .spec import Exit

        for k, e in enumerate(specs):
            kind = e.get("kind", "exit")
            if kind == "special":
                continue
            secret = kind == "secret"
            width = int(e.get("width", 1 if secret else 2))
            sides = [e["side"]] if e.get("side") else self._lateral_sides()
            best = None
            for side in sides:
                ats = [float(e["at"])] if e.get("at") is not None else list(np.linspace(0.12, 0.88, 31))
                for at in ats:
                    ex = _make_exit(side, at, width, e)
                    res = self._march(ex)
                    if res is None:
                        continue
                    path, hit = res
                    hk = self.kind_of.get(hit, "")
                    pref = {"loop": 0, "dead_end": 1, "node": 2, "main": 2}.get(hk, 3)
                    if secret and hk == "main":
                        pref = 3
                    score = (pref if e.get("at") is None else 0, len(path) if e.get("at") is None else 0,
                             self.rng.random())
                    if len(path) < 3 and e.get("at") is None:
                        continue
                    if best is None or score < best[0]:
                        best = (score, ex, path, hit)
            if best is None:
                self.r.log.append(f"ending {k} ({kind}): no room")
                continue
            _, ex, path, hit = best
            self.endings.append({"kind": kind, "exit": ex, "path": path, "hit": hit, "width": width,
                                 "declared": bool(e.get("_declared"))})
            cells = set()
            sd = SIDE_IN[ex.side]
            lat = (abs(sd[1]), abs(sd[0]))
            lo, hi = wspan(width)
            for (y, x) in path:
                for o in range(lo, hi + 1):
                    cells.add((y + lat[0] * o, x + lat[1] * o))
            self.claim(cells, self._oid("branch" if not secret else "hidden"))

    def _lateral_sides(self) -> List[str]:
        f = self.legs[0][0]
        return ["west", "east"] if f.vertical else ["north", "south"]

    def _march(self, ex) -> Optional[Tuple[List[Cell], int]]:
        """Path from the exit to the nearest lane or clearing: straight in from the edge,
        then at most a couple of 90 degree turns, keeping clear of other lanes."""
        import heapq

        from .generate import exit_cells

        c, cells = exit_cells(self.r, ex)
        for (_, oc, ocells) in self.exits:
            if abs(oc[0] - c[0]) + abs(oc[1] - c[1]) < 8:
                return None
        for e2 in self.endings:
            c2 = e2["path"][0]
            if abs(c2[0] - c[0]) + abs(c2[1] - c[1]) < 8:
                return None
        d0 = SIDE_IN[ex.side]
        goal = np.isin(self.owner, [i for i, k in self.kind_of.items() if k in ("main", "loop", "dead_end", "node")])
        if not goal.any():
            return None
        near = binary_dilation(self.owner >= 0, structure=np.ones((2 * GAP + 1, 2 * GAP + 1), dtype=bool))
        h, w = self.h, self.w
        TURN = 14
        start = (c, d0)
        dist = {start: 0.0}
        prev = {}
        q = [(0.0, c, d0)]
        found = None
        while q:
            g, cur, d = heapq.heappop(q)
            if g > dist.get((cur, d), 1e18):
                continue
            y, x = cur
            if goal[y, x]:
                found = (cur, d)
                break
            for nd in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                if nd == (-d[0], -d[1]):
                    continue
                ny, nx = y + nd[0], x + nd[1]
                if not (0 <= ny < h and 0 <= nx < w) or self.keep_any[ny, nx]:
                    continue
                depth = abs(ny - c[0]) + abs(nx - c[1])
                if depth < 4 and nd != d0:
                    continue  # leave the edge straight
                if not goal[ny, nx] and not (MARGIN <= ny < h - MARGIN and MARGIN <= nx < w - MARGIN) and depth >= 4:
                    continue
                cost = 1.0 + (TURN if nd != d else 0) + (6.0 if near[ny, nx] and not goal[ny, nx] else 0)
                ng = g + cost
                key = ((ny, nx), nd)
                if ng < dist.get(key, 1e18):
                    dist[key] = ng
                    prev[key] = (cur, d)
                    heapq.heappush(q, (ng, (ny, nx), nd))
        if found is None:
            return None
        hit = int(self.owner[found[0]])
        path = []
        k = found
        while k in prev:
            k = prev[k]
            path.append(k[0])
        path = path[::-1]
        if len(path) > int(max(h, w) * 0.6):
            return None
        return path, hit


def _visibility(area: dict) -> str:
    """hidden | secret | visible | territorial (areas package), '' for built-in archetypes."""
    try:
        from . import areas as api  # type: ignore

        if hasattr(api, "requirements") and "archetype" not in area:
            return str(api.requirements(area).get("visibility", "hidden"))
    except Exception:
        pass
    return ""


def _real_size(area: dict) -> Tuple[int, int]:
    """(w, h) the areas package will actually paint (no clipping)."""
    w, h = area.get("size") or area.get("footprint") or [9, 8]
    try:
        from . import areas as api  # type: ignore

        if hasattr(api, "requirements") and "archetype" not in area:
            w, h = api.requirements(area).get("footprint", (w, h))
    except Exception:
        pass
    return int(w), int(h)


def _area_size(area: dict) -> Tuple[int, int]:
    """(w, h) footprint of a special area, from the areas package if it says so."""
    w, h = area.get("size") or area.get("footprint") or [9, 8]
    try:
        from . import areas as api  # type: ignore

        if hasattr(api, "requirements"):
            w, h = api.requirements(area).get("footprint", (w, h))
    except Exception:
        pass
    return int(np.clip(w, 5, 11)), int(np.clip(h, 5, 10))


def _make_exit(side: str, at: float, width: int, e: dict):
    from dataclasses import fields as dc_fields

    from .spec import Exit

    names = {f.name for f in dc_fields(Exit)}
    kw = {k: v for k, v in e.items() if k in names}
    kw.update(side=side, at=float(at), width=width)
    kw.setdefault("to", "")
    return Exit(**kw)


def _virtual_goal(route, e0):
    """A stand-in far end for a one-exit route: an inset point at the region goal
    (or across the map from the entry). It is never added to route.exits."""
    from .spec import Exit

    reg = getattr(route.spec, "region", None) or {}
    goal = reg.get("goal") or {}
    opp = {"north": "south", "south": "north", "east": "west", "west": "east"}
    side = goal.get("side") or opp[e0.side]
    if side == e0.side:
        side = opp[e0.side]
    cellsz = int(reg.get("cell", 4) or 4)
    if "x" in goal and "y" in goal:
        gx = int(goal["x"]) * cellsz + cellsz // 2
        gy = int(goal["y"]) * cellsz + cellsz // 2
    else:
        gx, gy = route.w // 2, route.h // 2
    inset = MARGIN + 5
    gx = int(np.clip(gx, inset, route.w - 1 - inset))
    gy = int(np.clip(gy, inset, route.h - 1 - inset))
    d = SIDE_IN[side]
    # pin to the inset line on that side so the spine geometry stays orthogonal
    if side == "north":
        gy = inset
    elif side == "south":
        gy = route.h - 1 - inset
    elif side == "west":
        gx = inset
    else:
        gx = route.w - 1 - inset
    ex = Exit(side=side, at=0.5, width=3, to="")
    ex.__dict__["virtual"] = True
    return (ex, (gy, gx), [(gy, gx)])


def plan(route, areas: List[dict]) -> Planner:
    from .generate import exit_cells
    from .spec import Exit

    spec = route.spec
    exits = []
    for e in spec.exits:
        c, cells = exit_cells(route, e)
        exits.append((e, c, cells))
    if len(exits) < 2:
        # dead end (region role): one real exit; the main lane runs to the goal instead
        exits.append(_virtual_goal(route, exits[0][0]))
    # a hidden site only counts if the road can keep out of sight of it; re-plan a few
    # times (same seed stream, so still deterministic) before giving the area up
    p = None
    for attempt in range(12):
        q = Planner(route, areas, exits[:2])
        q.build_spine()
        q.place_nodes()
        q.reserve_specials()
        q.align_nodes()
        q.plan_sections()
        q.check_sites()
        if p is None or len(q.sites) > len(p.sites):
            p = q
        if len(p.sites) >= len(areas):
            break
    if attempt:
        route.log.append(f"plan: {attempt + 1} tries to hide {len(p.sites)}/{len(areas)} special areas")
    p.back_road()
    p.enforce_quotas()
    p.coverage()
    p.budget()
    endings = [dict(vars(e), kind="exit", _declared=True) for e in spec.exits[2:]]
    endings += [dict(x) for x in (getattr(spec, "endings", None) or [])]
    endings += [dict(kind="exit") for _ in range(int(getattr(spec, "extra_exits", 0) or 0))]
    p.endings_plan(endings)
    inb = lambda c: 0 <= c[0] < route.h and 0 <= c[1] < route.w  # noqa: E731
    for l in p.lanes:
        l.cells = {c for c in l.cells if inb(c)}
        l.pocket_cells = {c for c in l.pocket_cells if inb(c)}
    return p
