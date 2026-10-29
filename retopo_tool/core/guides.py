"""Step 4 (attach part): edge loops where a piece is sewn onto another's surface.

An arm's opening sewn onto the torso has no torso border to seam to. Its
attached segments (Segment.attach_to) are chained by their 3D ends into runs
per base piece and projected onto the base surface (closest point, barycentric
-> base UV).
- A closed run becomes a guide loop: a hole of the region around it, and a
  region of its own (the part under the opening).
- An open run (a neck crossing several head pieces) becomes a chord whose ends
  sit on the base border. plan_guides tells build_segments to put corners
  there; attach_guides then runs on the rebuilt model and splits the region
  in two. The smaller side is the one under the opening.
- plan_guides also says which border edges belong to a run that is really
  sewn on. Contacts it rejects (cloth resting on cloth, like the scarf's
  layers) are dropped when the segments are rebuilt, so they don't cut seams.

Each attached segment gets a matching virtual guide segment on the base
(Segment.uv_points / points3d), linked so they share one count. Regions go in
SegmentModel.regions; regions under an opening carry `inside` (the attached
piece ids), so their faces can be kept in a face set or left out. `pinched`
marks an opening whose rim is two attached pieces already sewn to each other
(a puffy eye: front and back sewn round the rim and onto the head); it is
closed on its own, so the faces under it are never left out.

A guide that would cross the base border or another guide is skipped with a
warning; that opening then stays an open edge as before.
"""
import math
from collections import defaultdict

from .config import load_config
from .geom import closest_on_triangle, dist, point_in_polygon, segments_cross, signed_area
from .mesh import Segment, piece_label, piece_labels, segment_edges


def _poly3d(mesh, topo, seg):
    if seg.points3d is not None:
        return seg.points3d
    return [mesh.positions[topo.uv_vertex[t]] for t in seg.uv_ids]


def _representatives(model, base):
    """Attached segments on base; of halves sewn to each other (a pinched rim),
    keep the lowest id and list the rest as riders."""
    segs = [s for s in model.segments if s.attach_to == base and s.uv_points is None]
    ids = {s.id for s in segs}
    reps, riders = [], defaultdict(list)
    taken = set()
    for s in sorted(segs, key=lambda x: x.id):
        if s.id in taken:
            continue
        reps.append(s)
        for t in s.links:
            if t in ids and t not in taken:
                riders[s.id].append(t)
                taken.add(t)
        taken.add(s.id)
    return reps, riders


def _chain(mesh, topo, segs, tol):
    """Order segments into runs by matching 3D ends. Returns [([(seg, reversed)], closed)]."""
    ends = {s.id: (_poly3d(mesh, topo, s)[0], _poly3d(mesh, topo, s)[-1]) for s in segs}
    left = {s.id: s for s in segs}
    runs = []
    while left:
        run = [(left.pop(min(left)), False)]

        def tail():
            seg, rev = run[-1]
            return ends[seg.id][0 if rev else 1]

        def head():
            seg, rev = run[0]
            return ends[seg.id][1 if rev else 0]

        grew = True
        while grew:
            grew = False
            for sid in list(left):
                a, b = ends[sid]
                if dist(a, tail()) <= tol:
                    run.append((left.pop(sid), False))
                elif dist(b, tail()) <= tol:
                    run.append((left.pop(sid), True))
                elif dist(b, head()) <= tol:
                    run.insert(0, (left.pop(sid), False))
                elif dist(a, head()) <= tol:
                    run.insert(0, (left.pop(sid), True))
                else:
                    continue
                grew = True
        runs.append((run, dist(head(), tail()) <= tol))
    return runs


def _border_vertices(mesh, topo, base):
    """(loop index, position in loop, uv id, 3D point) for every border vertex of base."""
    out = []
    for li, loop in enumerate(topo.pieces[base].loops):
        for k, t in enumerate(loop):
            out.append((li, k, t, mesh.positions[topo.uv_vertex[t]]))
    return out


def _run_ends(mesh, topo, run):
    first, rev0 = run[0]
    last, rev1 = run[-1]
    return _poly3d(mesh, topo, first)[-1 if rev0 else 0], _poly3d(mesh, topo, last)[0 if rev1 else -1]


def _nearest_border(border, p):
    """The base border vertex nearest p in 3D (one rule for plan_guides and attach_guides)."""
    return min(border, key=lambda b: dist(b[3], p))


def _bases(model):
    return sorted({s.attach_to for s in model.segments if s.attach_to >= 0})


def _cell(model, cfg):
    lengths = sorted(s.length / s.mesh_edges for s in model.segments if s.mesh_edges) or [1.0]
    return max(lengths[len(lengths) // 2] * cfg["seam_cell_ratio"], cfg["seam_gap_max"], 1e-12)


def _runs(mesh, topo, model, base, surface, cfg):
    """(run, closed, end border vertices or None, untrimmed run) per attach run on base.

    An open run often starts with a short stretch that lies along the base's own
    border (the opening meets the border at a slant). Such end segments are
    dropped (up to guides_trim_segments per end) until the rest stays inside the
    base in UV; the new end must lie within the dropped length of the border.
    Both plan_guides and attach_guides call this, so they agree on the ends.

    Runs whose median gap to the base is above topology.attach_gap_median are
    skipped: sewing pulls a border tight onto the surface (about 1 mm in MD),
    while a layer of cloth merely resting on another sits further off (about
    2 mm on the scarf)."""
    tol = cfg["seam_gap_max"] * 2
    reps, _ = _representatives(model, base)
    border = _border_vertices(mesh, topo, base)
    edges = []
    for loop in topo.pieces[base].loops:
        edges += [(mesh.uvs[loop[i]], mesh.uvs[loop[(i + 1) % len(loop)]]) for i in range(len(loop))]
    trim = cfg["guide_trim_segments"]
    for run, closed in _chain(mesh, topo, reps, tol):
        pts = [p for seg, _ in run for p in _poly3d(mesh, topo, seg)]
        step = max(1, len(pts) // cfg["attach_gap_samples"])
        gaps = sorted(surface.gap(p) for p in pts[::step])
        if gaps[len(gaps) // 2] > cfg["attach_gap_median"]:
            continue
        if closed:
            yield run, True, None, run
            continue
        options = sorted(((i, j) for i in range(trim + 1) for j in range(trim + 1) if i + j < len(run)),
                         key=lambda ij: (ij[0] + ij[1], ij))
        for i, j in options:
            sub = run[i:len(run) - j]
            p0, p1 = _run_ends(mesh, topo, sub)
            b0, b1 = _nearest_border(border, p0), _nearest_border(border, p1)
            room0 = tol + sum(s.length for s, _ in run[:i])
            room1 = tol + sum(s.length for s, _ in run[len(run) - j:])
            if b0[2] == b1[2] or dist(b0[3], p0) > room0 or dist(b1[3], p1) > room1:
                continue
            poly = []
            for seg, rev in sub:
                p3 = _poly3d(mesh, topo, seg)
                poly += [surface.uv(q) for q in (p3[::-1] if rev else p3)][:-1]
            poly = [mesh.uvs[b0[2]]] + poly[1:] + [mesh.uvs[b1[2]]]
            if any(q is None for q in poly) or _poly_crosses(poly, edges):
                continue
            yield sub, False, (b0, b1), run
            break
        else:
            yield run, False, None, run


def _poly_crosses(poly, edges):
    """True if the polyline's inner edges (not the two end edges) touch any of edges."""
    for a, b in list(zip(poly, poly[1:]))[1:-1]:
        for c, d in edges:
            if segments_cross(a, b, c, d, 1e-14):
                return True
    return False


def plan_guides(mesh, topo, model, cfg=None):
    """Returns (corners, sewn).

    corners: where open runs end on their base's border, {(piece, loop): {loop positions}}.
    sewn: the border edges (segment_edges keys) of every run that passes the gap
    test, so build_segments can ignore the contacts that don't."""
    cfg = cfg or load_config("defaults")["topology"]
    corners, sewn = defaultdict(set), set()
    cell = _cell(model, cfg)
    for base in _bases(model):
        _, riders = _representatives(model, base)
        surface = _Surface(mesh, topo, base, cell)
        for _, closed, ends, whole in _runs(mesh, topo, model, base, surface, cfg):
            for li, k, _, _ in ends or ():
                corners[(base, li)].add(k)
            for seg, _ in whole:
                for sid in [seg.id] + riders[seg.id]:
                    sewn.update(segment_edges(model.segments[sid]))
    return dict(corners), sewn


class _Surface:
    """Closest-point lookup on one piece's faces, returning its UVs."""

    def __init__(self, mesh, topo, piece, cell):
        self.mesh, self.cell = mesh, cell
        self.tris = []
        for fi in topo.pieces[piece].faces:
            f, t = mesh.faces[fi], mesh.face_uvs[fi]
            for k in range(1, len(f) - 1):
                self.tris.append(((f[0], f[k], f[k + 1]), (t[0], t[k], t[k + 1])))
        self.grid = defaultdict(list)
        P = mesh.positions
        for idx, (vs, _) in enumerate(self.tris):
            lo = [int(math.floor(min(P[v][i] for v in vs) / cell)) for i in range(3)]
            hi = [int(math.floor(max(P[v][i] for v in vs) / cell)) for i in range(3)]
            for x in range(lo[0], hi[0] + 1):
                for y in range(lo[1], hi[1] + 1):
                    for z in range(lo[2], hi[2] + 1):
                        self.grid[x, y, z].append(idx)

    def uv(self, p):
        hit = self.closest(p)
        return None if hit is None else hit[1]

    def gap(self, p):
        hit = self.closest(p)
        return math.inf if hit is None else hit[0]

    def closest(self, p):
        """(3D distance, UV) of the nearest point on the piece, or None."""
        P, U = self.mesh.positions, self.mesh.uvs
        c = [int(math.floor(p[i] / self.cell)) for i in range(3)]
        best = None
        for reach in (1, 3, 8):
            cand = set()
            for x in range(c[0] - reach, c[0] + reach + 1):
                for y in range(c[1] - reach, c[1] + reach + 1):
                    for z in range(c[2] - reach, c[2] + reach + 1):
                        cand.update(self.grid.get((x, y, z), ()))
            for idx in cand:
                vs, ts = self.tris[idx]
                d, w = closest_on_triangle(p, P[vs[0]], P[vs[1]], P[vs[2]])
                if best is None or d < best[0]:
                    best = (d, w, ts)
            if best is not None:
                break
        if best is None:
            return None
        d, w, ts = best
        a, b, c = U[ts[0]], U[ts[1]], U[ts[2]]   # written out: sum() rounds differently across Python versions
        return d, (w[0] * a[0] + w[1] * b[0] + w[2] * c[0], w[0] * a[1] + w[1] * b[1] + w[2] * c[1])


def attach_guides(mesh, topo, model, cfg=None):
    """Add guide segments and regions to model (after build_segments with plan_guides' corners).

    Returns warnings."""
    cfg = cfg or load_config("defaults")["topology"]
    warnings = []
    cell = _cell(model, cfg)
    for base in _bases(model):
        _, riders = _representatives(model, base)
        surface = _Surface(mesh, topo, base, cell)
        piece = topo.pieces[base]
        # Start from the base's own loops: one region, outer loop plus holes.
        regions = [{"cycles": [[(sid, False) for sid in model.loops[(base, li)]] for li in range(len(piece.loops))],
                    "inside": None}]
        for run, closed, found, _ in _runs(mesh, topo, model, base, surface, cfg):
            ends = None if closed else [b[2] for b in found] if found else [None, None]
            attached = tuple(sorted({seg.piece for seg, _ in run}
                                    | {model.segments[t].piece for seg, _ in run for t in riders[seg.id]}))
            first = len(model.segments)
            for k, (seg, rev) in enumerate(run):
                p3 = _poly3d(mesh, topo, seg)
                p3 = p3[::-1] if rev else list(p3)
                uv = [surface.uv(p) for p in p3]
                if ends is not None and k == 0 and ends[0] is not None:
                    uv[0] = mesh.uvs[ends[0]]
                if ends is not None and k == len(run) - 1 and ends[1] is not None:
                    uv[-1] = mesh.uvs[ends[1]]
                model.segments.append(Segment(
                    id=len(model.segments), piece=base, loop=-1, index=k, uv_ids=[], edge_keys=[],
                    length=seg.length, uv_length=sum(dist(a, b) for a, b in zip(uv, uv[1:])),
                    tag=seg.piece, links=[seg.id] + riders[seg.id], partners=(seg.piece,),
                    uv_points=uv, points3d=p3,
                    ends=(ends[0] if k == 0 else None, ends[1] if k == len(run) - 1 else None) if ends else None))
            guide = model.segments[first:]
            placed = _add_loop(model, mesh, regions, guide, attached) if closed \
                else _add_chord(model, mesh, regions, guide, attached)
            if not placed:
                del model.segments[first:]
                warnings.append(f"Piece {piece_label(base)}: the attach line of pieces {piece_labels(attached)} crosses the piece "
                                "border or another attach line, or doesn't end on the border; it was left "
                                "out and stays an open edge.")
                continue
            for v in guide:
                for t in v.links:
                    model.segments[t].links.append(v.id)
        if len(regions) > 1:
            model.regions[base] = regions
    return warnings


def _cycle_uv(model, mesh, cycle):
    pts = []
    for sid, rev in cycle:
        seg = model.segments[sid]
        uv = seg.uv_points if seg.uv_points is not None else [mesh.uvs[t] for t in seg.uv_ids]
        pts += (uv[::-1] if rev else uv)[:-1]
    return pts


def _crosses(model, mesh, regions, poly, skip_ends):
    """True if UV polyline poly crosses itself or any region cycle.
    skip_ends: contacts on poly's first and last edge are allowed (a chord on the border)."""
    eps = 1e-14
    edges = []
    for reg in regions:
        for cyc in reg["cycles"]:
            pts = _cycle_uv(model, mesh, cyc)
            edges += [(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]
    segs = list(zip(poly, poly[1:]))
    last = len(segs) - 1
    for i, (a, b) in enumerate(segs):
        if skip_ends and i in (0, last):
            continue
        for c, d in edges:
            if segments_cross(a, b, c, d, eps):
                return True
        for j in range(i + 2, len(segs)):
            if not (i == 0 and j == last) and segments_cross(a, b, *segs[j], eps):
                return True
    return False


def _region_at(model, mesh, regions, p):
    """The region whose outer cycle holds p and none of whose holes do."""
    for reg in regions:
        cyc = reg["cycles"]
        if point_in_polygon(p, _cycle_uv(model, mesh, cyc[0])) and \
                not any(point_in_polygon(p, _cycle_uv(model, mesh, h)) for h in cyc[1:]):
            return reg
    return None


def _add_loop(model, mesh, regions, guide, attached):
    if any(p is None for v in guide for p in v.uv_points):
        return False
    cycle = [(v.id, False) for v in guide]
    pts = _cycle_uv(model, mesh, cycle)
    if len(pts) < 3 or abs(signed_area(pts)) <= 0 or _crosses(model, mesh, regions, pts + pts[:1], False):
        return False
    reg = _region_at(model, mesh, regions, pts[0])
    if reg is None:
        return False
    reg["cycles"].append(cycle)                               # a hole of the region around it ...
    regions.append({"cycles": [cycle], "inside": attached,    # ... and a region of its own
                    "pinched": any(len(v.links) > 1 for v in guide)})
    return True


def _add_chord(model, mesh, regions, guide, attached):
    """Split the uncovered region whose outer cycle holds both chord ends. True on success."""
    if any(p is None for v in guide for p in v.uv_points):
        return False
    a, b = guide[0].ends[0], guide[-1].ends[1]
    if a is None or b is None or a == b:
        return False
    poly = []
    for v in guide:
        poly += v.uv_points[:-1]
    poly.append(guide[-1].uv_points[-1])
    if _crosses(model, mesh, regions, poly, True):
        return False
    for idx, reg in enumerate(regions):
        if reg["inside"] is not None:
            continue
        cyc = reg["cycles"][0]
        starts = [_start_uv(model, sid, rev) for sid, rev in cyc]
        if a not in starts or b not in starts:
            continue
        ia, ib = starts.index(a), starts.index(b)
        n = len(cyc)
        part_ba = [cyc[(ib + k) % n] for k in range((ia - ib) % n)]   # border from b round to a
        part_ab = [cyc[(ia + k) % n] for k in range((ib - ia) % n)]   # border from a round to b
        one = {"cycles": [part_ba + [(v.id, False) for v in guide]], "inside": None}
        two = {"cycles": [part_ab + [(v.id, True) for v in reversed(guide)]], "inside": None}
        for hole in reg["cycles"][1:]:
            hp = _cycle_uv(model, mesh, hole)[0]
            (one if point_in_polygon(hp, _cycle_uv(model, mesh, one["cycles"][0])) else two)["cycles"].append(hole)
        # The covered side is the smaller one (an arm or neck opening on a larger piece).
        a1 = abs(signed_area(_cycle_uv(model, mesh, one["cycles"][0])))
        a2 = abs(signed_area(_cycle_uv(model, mesh, two["cycles"][0])))
        under = one if a1 < a2 else two
        under["inside"] = attached
        under["pinched"] = any(len(v.links) > 1 for v in guide)
        regions[idx] = one
        regions.append(two)
        return True
    return False


def _start_uv(model, sid, rev):
    seg = model.segments[sid]
    if seg.uv_points is not None:
        return (seg.ends or (None, None))[1 if rev else 0]
    return seg.uv_ids[-1] if rev else seg.uv_ids[0]
