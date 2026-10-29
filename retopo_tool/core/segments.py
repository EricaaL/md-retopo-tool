"""Step 5: split border loops into segments at corners.

A corner is where the seam partners change (including seam <-> free border),
where the border turns sharper than topology.corner_angle_deg in UV space, or
at a fold point: a border sewn back onto itself (a cone closure) folds where the
previous and next border vertices are the same 3D vertex. Splitting there gives
two halves with the same edges, so they link and share one count.

Partners are the union of exact seam edges and topology.near_seams (sides that
do not share vertices, or a multi-way seam where only some pieces weld). A free run of at most topology.seam_gap_fill_edges
between two runs with the same partners is treated as part of that seam (a spot
where the sim pulled the seam open). Segments with identical edges link
directly; otherwise they link when each covers the other along its length.
"""
import math
from collections import defaultdict

from .config import load_config
from .geom import dist, turn_deg
from .mesh import Segment, SegmentModel, piece_label


def _fill_gaps(tags, max_gap):
    """Relabel short free runs that sit between two runs with the same partners."""
    n = len(tags)
    if not max_gap or all(t == () for t in tags) or all(t != () for t in tags):
        return tags
    out = list(tags)
    start = next(k for k in range(n) if tags[k] != () and tags[k - 1] == ())  # a run start after free
    k = start
    while True:
        j = (k + 1) % n
        if tags[k] != () and tags[j] == ():
            run = 0
            while tags[(j + run) % n] == () and run < n:
                run += 1
            after = tags[(j + run) % n]
            if run <= max_gap and after == tags[k]:
                for r in range(run):
                    out[(j + r) % n] = tags[k]
        k = j
        if k == start:
            return out


def _partner_runs(has):
    """Runs of consecutive True positions around a loop, as lists of positions."""
    n = len(has)
    if all(has):
        return [list(range(n))]
    start = next(k for k in range(n) if not has[k])
    runs, run = [], []
    for i in range(1, n + 1):
        k = (start + i) % n
        if has[k]:
            run.append(k)
        elif run:
            runs.append(run)
            run = []
    return runs


def _drop_short_runs(near, exact, min_run, gaps=None, max_gap=None):
    """Keep a proximity-only partner only where it holds for min_run edges in a
    row (counting edges where that partner is welded exactly). Sewing runs
    along a border; cloth that merely touches (a knot, a fold) does not.

    gaps: [{partner: gap}] per edge. A run with no exact edge whose median gap
    is above max_gap is dropped too: sewing pulls a seam to about 1 mm, while a
    fold resting on another edge sits at 2-3 mm (the scarf's tips)."""
    out = [set(s) for s in near]
    for q in {q for s in near for q in s}:
        has = [q in near[k] or q in exact[k] for k in range(len(near))]
        for run in _partner_runs(has):
            drop = len(run) < min_run and not all(has)
            if not drop and gaps is not None and not any(q in exact[k] for k in run):
                g = sorted(gaps[k].get(q, 0.0) for k in run)
                drop = g[len(g) // 2] > max_gap
            if drop:
                for r in run:
                    out[r].discard(q)
    return out


def build_segments(mesh, topo, cfg=None, extra_corners=None, sewn=None):
    """extra_corners: {(piece id, loop index): loop positions} that must start a
    segment (where an attach line meets the border; see guides.plan_guides).
    sewn: if given, only these border edges (segment_edges keys) may be attached
    to another piece's surface; other surface contacts are ignored."""
    cfg = cfg or load_config("defaults")["topology"]
    extra_corners = extra_corners or {}
    thr = cfg["corner_angle_deg"]
    uvv, uvs, pos = topo.uv_vertex, mesh.uvs, mesh.positions
    segments, loops_out = [], {}

    # Pass 1: partners per border edge.
    loops = []   # (piece, loop index, loop, edge keys, exact, near)
    for piece in topo.pieces:
        for li, loop in enumerate(piece.loops):
            n = len(loop)
            keys, exact, near, gaps = [], [], [], []
            for k in range(n):
                a, b = uvv[loop[k]], uvv[loop[(k + 1) % n]]
                key = (min(a, b), max(a, b))
                keys.append(key)
                t0, t1 = loop[k], loop[(k + 1) % n]
                ek = (min(t0, t1), max(t0, t1))
                near.append(set(topo.near_seams.get(ek, ())))
                gaps.append({q: topo.near_gaps.get((ek, q), 0.0) for q in near[-1]})
                se = topo.seam_edges.get(key)
                others = []
                if se is not None:
                    others = list(se.pieces)
                    others.remove(piece.id)  # one entry is this edge itself; a dart keeps the other
                exact.append(set(others))
            near = _drop_short_runs(near, exact, cfg["seam_min_run_edges"], gaps, cfg["seam_gap_median"])
            loops.append((piece, li, loop, keys, exact, near))
    _mutual_partners(loops, pos, uvv, cfg)

    # Pass 2: corners per loop.
    records = []   # (piece, loop index, loop, edge keys, tags, corner set, attach)
    for piece, li, loop, keys, exact, near in loops:
            n = len(loop)
            tags = [tuple(sorted(e | q)) for e, q in zip(exact, near)]
            # Openings sewn onto another piece's surface (an arm on a torso).
            on = []
            for k in range(n):
                t0, t1 = loop[k], loop[(k + 1) % n]
                ek = (min(t0, t1), max(t0, t1))
                hit = set(topo.surface_contacts.get(ek, ())) if sewn is None or ek in sewn else set()
                on.append(hit - set(tags[k]))
            on = _drop_short_runs(on, [set()] * n, cfg["seam_min_run_edges"])
            attach = [min(a) if a else -1 for a in on]
            tags = _fill_gaps(tags, cfg["seam_gap_fill_edges"])
            corners = {k for k in range(n)
                       if tags[k - 1] != tags[k] or attach[k - 1] != attach[k]
                       or uvv[loop[k - 1]] == uvv[loop[(k + 1) % n]]
                       or turn_deg(uvs[loop[k - 1]], uvs[loop[k]], uvs[loop[(k + 1) % n]]) > thr}
            corners |= set(extra_corners.get((piece.id, li), ()))
            records.append((piece, li, loop, keys, tags, corners, attach))

    _propagate_corners(records, pos, uvv, cfg)

    # Pass 3: segments between corners.
    for piece, li, loop, keys, tags, corner_set, attach in records:
        n = len(loop)
        corners = sorted(corner_set) or [0]
        ids = []
        for ci, c in enumerate(corners):
            end = corners[(ci + 1) % len(corners)]
            if end <= c:
                end += n
            uv_ids = [loop[k % n] for k in range(c, end + 1)]
            ekeys = [keys[k % n] for k in range(c, end)]
            p3 = [pos[uvv[t]] for t in uv_ids]
            p2 = [uvs[t] for t in uv_ids]
            seg = Segment(
                id=len(segments), piece=piece.id, loop=li, index=ci,
                uv_ids=uv_ids, edge_keys=ekeys,
                length=sum(dist(p3[i], p3[i + 1]) for i in range(len(p3) - 1)),
                uv_length=sum(dist(p2[i], p2[i + 1]) for i in range(len(p2) - 1)),
                tag=tags[c][0] if tags[c] else -1, partners=tags[c], attach_to=attach[c])
            segments.append(seg)
            ids.append(seg.id)
        loops_out[(piece.id, li)] = ids

    model = SegmentModel(segments, loops_out)
    _link_seams(model, mesh, topo, cfg)
    return model


def _mutual_partners(loops, pos, uvv, cfg):
    """A seam found only by proximity must be found from both sides.

    The short-run and resting-gap filters judge each side of a seam on its own,
    so one side can keep a stretch the other dropped. Each proximity-only
    partner edge is kept only if the partner piece has an edge, seamed back to
    this piece, whose midpoint is within topology.seam_gap_max plus half of
    both edge lengths of its own (sides needn't share vertices); short leftovers
    are then dropped again. On a piece sewn to itself, edges within
    seam_min_run_edges along the same loop don't count. Changes `near` in place."""
    gap = cfg["seam_gap_max"]
    skip = cfg["seam_min_run_edges"]
    half = []    # per loop: half of each edge's 3D length
    for _, _, loop, _, _, _ in loops:
        n = len(loop)
        half.append([dist(pos[uvv[loop[k]]], pos[uvv[loop[(k + 1) % n]]]) / 2 for k in range(n)])
    reach = gap + 2 * max((h for hs in half for h in hs), default=0.0)   # cell size: the widest reach
    for _ in range(2):
        grid = defaultdict(list)   # (piece, partner, cell) -> (loop record, position, midpoint)
        mids = []
        for r, (piece, _, loop, _, exact, near) in enumerate(loops):
            n = len(loop)
            m = [tuple((pos[uvv[loop[k]]][i] + pos[uvv[loop[(k + 1) % n]]][i]) / 2 for i in range(3))
                 for k in range(n)]
            mids.append(m)
            for k in range(n):
                for q in exact[k] | near[k]:
                    grid[piece.id, q, tuple(int(math.floor(c / reach)) for c in m[k])].append((r, k, m[k]))
        changed = False
        for r, ((piece, _, loop, _, exact, near), m) in enumerate(zip(loops, mids)):
            n = len(loop)
            for k in range(n):
                for q in list(near[k] - exact[k]):
                    c = [int(math.floor(x / reach)) for x in m[k]]
                    found = any(dist(p, m[k]) <= gap + half[r][k] + half[r2][j]
                                and (r2 != r or min((j - k) % n, (k - j) % n) > skip)
                                for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)
                                for r2, j, p in grid.get((q, piece.id, (c[0] + dx, c[1] + dy, c[2] + dz)), ()))
                    if not found:
                        near[k].discard(q)
                        changed = True
        if not changed:
            return
        for i, (piece, li, loop, keys, exact, near) in enumerate(loops):
            loops[i] = (piece, li, loop, keys, exact, _drop_short_runs(near, exact, cfg["seam_min_run_edges"]))


def _propagate_corners(records, pos, uvv, cfg):
    """Make both sides of a seam break at the same places.

    A corner inside a seam run (by angle or fold, not a partner change) adds a
    corner at the nearest border vertex of each partner piece whose adjacent
    edges are sewn back to this piece. Repeats until stable, so corners also
    travel across multi-way seams.
    """
    by_piece = {}
    for r, (piece, _, loop, _, _, _, _) in enumerate(records):
        by_piece.setdefault(piece.id, []).extend((r, k) for k in range(len(loop)))
    reach = cfg["seam_gap_max"]
    for _ in range(cfg["corner_propagation_passes"]):
        added = False
        for piece, _, loop, _, tags, corners, _ in records:
            n = len(loop)
            for k in sorted(corners):
                if not tags[k] or tags[k - 1] != tags[k]:
                    continue
                p = pos[uvv[loop[k]]]
                local = max(dist(p, pos[uvv[loop[k - 1]]]), dist(p, pos[uvv[loop[(k + 1) % n]]]))
                for q in tags[k]:
                    best = None
                    for r, j in by_piece.get(q, ()):
                        _, _, qloop, _, qtags, _, _ = records[r]
                        if piece.id not in qtags[j] and piece.id not in qtags[j - 1]:
                            continue
                        d = dist(p, pos[uvv[qloop[j]]])
                        if d <= reach + local and (best is None or d < best[0]):
                            best = (d, r, j)
                    if best and best[2] not in records[best[1]][5]:
                        records[best[1]][5].add(best[2])
                        added = True
        if not added:
            return


def _polyline(mesh, topo, seg):
    return [mesh.positions[topo.uv_vertex[t]] for t in seg.uv_ids]


class _Line:
    """A 3D polyline with a grid of its edges, for fast point-to-line distance queries."""

    def __init__(self, pts):
        self.pts = pts
        self.segs = list(zip(pts, pts[1:]))
        self.lo = [min(p[i] for p in pts) for i in range(3)]
        self.hi = [max(p[i] for p in pts) for i in range(3)]
        self.cell = max([dist(a, b) for a, b in self.segs] + [1e-9])
        self.grid = defaultdict(list)
        for k, (a, b) in enumerate(self.segs):
            for key in self._cells(a, b, 0.0):
                self.grid[key].append(k)

    def _cells(self, a, b, pad):
        lo = [int(math.floor((min(a[i], b[i]) - pad) / self.cell)) for i in range(3)]
        hi = [int(math.floor((max(a[i], b[i]) + pad) / self.cell)) for i in range(3)]
        for x in range(lo[0], hi[0] + 1):
            for y in range(lo[1], hi[1] + 1):
                for z in range(lo[2], hi[2] + 1):
                    yield x, y, z

    def near(self, p, tol):
        """True if p lies within tol of the line."""
        if any(p[i] < self.lo[i] - tol or p[i] > self.hi[i] + tol for i in range(3)):
            return False
        for key in self._cells(p, p, tol):
            for k in self.grid.get(key, ()):
                a, b = self.segs[k]
                ab = [b[i] - a[i] for i in range(3)]
                ll = sum(x * x for x in ab)
                t = 0.0 if ll == 0 else max(0.0, min(1.0, sum((p[i] - a[i]) * ab[i] for i in range(3)) / ll))
                if dist(p, [a[i] + t * ab[i] for i in range(3)]) <= tol:
                    return True
        return False


def _coverage(pts, line, tol):
    """Share of pts lying within tol of line (a _Line)."""
    if not pts or not line.segs:
        return 0.0
    return sum(1 for p in pts if line.near(p, tol)) / len(pts)


def _warn_partial(model, s, t, seen):
    key = (min(s.id, t.id), max(s.id, t.id))
    if key not in seen:
        seen.add(key)
        model.warnings.append(
            f"Segments {s.id} (piece {piece_label(s.piece)}) and {t.id} (piece {piece_label(t.piece)}) share a seam "
            "only partly (T-junction); their counts are not linked. "
            "Add a corner where the seam ends or set both counts by hand.")


def _link_seams(model, mesh, topo, cfg):
    by_key = defaultdict(set)
    for s in model.segments:
        if s.tag != -1:
            for k in s.edge_keys:
                by_key[k].add(s.id)
    seen = set()
    for s in model.segments:
        if s.tag == -1:
            continue
        cands = set()
        for k in s.edge_keys:
            cands |= by_key[k]
        cands.discard(s.id)
        for t in sorted(cands):
            other = model.segments[t]
            if set(other.edge_keys) == set(s.edge_keys):
                s.links.append(t)

    # Seams whose sides don't share every edge: link by mutual geometric coverage.
    by_piece = defaultdict(list)
    for s in model.segments:
        by_piece[s.piece].append(s)
    lines = {}
    for s in model.segments:
        if s.tag == -1:
            continue
        for q in s.partners:
            for t in by_piece[q]:
                if t.id == s.id or t.id in s.links or s.piece not in t.partners:
                    continue
                if s.id > t.id and s.id in t.links:
                    continue
                if (min(s.id, t.id), max(s.id, t.id)) in seen:
                    continue
                for g in (s, t):
                    if g.id not in lines:
                        lines[g.id] = _Line(_polyline(mesh, topo, g))
                ls, lt = lines[s.id], lines[t.id]
                tol = min(cfg["seam_gap_max"], cfg["seam_gap_ratio"]
                          * max(s.length / max(s.mesh_edges, 1), t.length / max(t.mesh_edges, 1)))
                cs, ct = _coverage(ls.pts, lt, tol), _coverage(lt.pts, ls, tol)
                need = cfg["seam_link_coverage"]
                if cs >= need and ct >= need:
                    s.links.append(t.id)
                    t.links.append(s.id)
                    seen.add((min(s.id, t.id), max(s.id, t.id)))
                elif len(ls.pts) > 2 and len(lt.pts) > 2 and max(_coverage(ls.pts[1:-1], lt, tol),
                                                                 _coverage(lt.pts[1:-1], ls, tol)) > 1 - need:
                    _warn_partial(model, s, t, seen)  # real overlap, not two segments meeting at a corner
    for s in model.segments:
        s.links = sorted(set(s.links))
