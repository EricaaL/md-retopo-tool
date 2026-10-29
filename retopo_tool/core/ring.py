"""Step 7: sample the border loops at the solved counts and build the border ring(s).

Each segment is sampled at equal 3D arc length, then mapped to UV at the same
edge parameter. Both sides of a seam walk the same 3D polyline, so they get
identical 3D points and weld by index later.

A ring is a strip of quads between a loop and its inward offset (interior is on
the left of every loop). Corners shape the strip the way an artist would:
- convex corner: one corner quad, the two neighbours share an inner vertex
  (each adjacent inner side loses one edge);
- reflex corner: three quads fan around it (each adjacent inner side gains one);
- plain corner or no corner: one straight spoke.
"""
import math

from .geom import dist, left_normal, resample, segments_cross, signed_area, signed_turn_deg
from .mesh import piece_label

CONVEX, REFLEX, PLAIN = "convex", "reflex", "plain"


def sample_loops(patch, piece, mesh, topo, model, counts):
    """Add border vertices to patch. Returns (loops, corners, mean UV edge length).

    loops[i] is a list of vertex ids with the interior on the left; corners[i] is
    the set of vertex ids that start a segment.
    """
    uvv = topo.uv_vertex
    loops, corners, edge_sum, edge_n = [], [], 0.0, 0
    for li in range(len(piece.loops)):
        ids = model.loops[(piece.id, li)]
        loop, starts, seg_ids = [], set(), []
        for sid in ids:
            c = counts[sid]
            first = len(patch.points)
            pts2, pts3 = _sample(model.segments[sid], c, mesh, uvv)
            for q2, q3 in zip(pts2[:-1], pts3[:-1]):
                patch.fixed3d[len(patch.points)] = q3
                patch.points.append(q2)
            starts.add(first)
            seg_ids.append(list(range(first, first + c)))
            loop.extend(seg_ids[-1])
        for k, sid in enumerate(ids):  # close each segment on the next one's first vertex
            patch.boundary[sid] = seg_ids[k] + [seg_ids[(k + 1) % len(ids)][0]]
        area = signed_area([patch.points[v] for v in loop])
        if (li == 0) != (area > 0):  # outer must be CCW, holes CW
            loop.reverse()
        loops.append(loop)
        corners.append(starts)
        for a, b in zip(loop, loop[1:] + loop[:1]):
            edge_sum += dist(patch.points[a], patch.points[b])
            edge_n += 1
    patch.ring_vertices.update(v for lp in loops for v in lp)
    return loops, corners, edge_sum / max(edge_n, 1)


def _sample(seg, c, mesh, uvv):
    """c + 1 points (both ends) at equal 3D arc length: (UV points, 3D points)."""
    if seg.uv_points is not None:
        uv, p3 = seg.uv_points, seg.points3d
    else:
        uv = [mesh.uvs[t] for t in seg.uv_ids]
        p3 = [mesh.positions[uvv[t]] for t in seg.uv_ids]
    params = [0.0]
    for a, b in zip(p3, p3[1:]):
        params.append(params[-1] + dist(a, b))
    if params[-1] <= 0:  # zero 3D length: fall back to UV arc length
        params = [0.0]
        for a, b in zip(uv, uv[1:]):
            params.append(params[-1] + dist(a, b))
    targets = [params[-1] * k / c for k in range(c + 1)]
    return resample(params, uv, targets), resample(params, p3, targets)


def sample_regions(patch, regions, mesh, topo, model, counts):
    """sample_loops for a piece split by attach guides (model.regions).

    Every segment is sampled once; segments that follow each other in a cycle
    share the vertex where they meet (border vertices win, so a chord end sits
    exactly on the border). Returns ([(loops, corners)] per region, mean UV edge).
    """
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for reg in regions:
        for cyc in reg["cycles"]:
            for (s1, r1), (s2, r2) in zip(cyc, cyc[1:] + cyc[:1]):
                parent[find((s1, 0 if r1 else 1))] = find((s2, 1 if r2 else 0))
    node = {}

    def vertex(key, q2, q3):
        root = find(key)
        if root not in node:
            node[root] = len(patch.points)
            patch.fixed3d[node[root]] = q3
            patch.points.append(q2)
        return node[root]
    for sid in sorted({s for reg in regions for cyc in reg["cycles"] for s, _ in cyc}):
        pts2, pts3 = _sample(model.segments[sid], counts[sid], mesh, topo.uv_vertex)
        ids = [vertex((sid, 0), pts2[0], pts3[0])]
        for q2, q3 in zip(pts2[1:-1], pts3[1:-1]):
            ids.append(len(patch.points))
            patch.fixed3d[ids[-1]] = q3
            patch.points.append(q2)
        ids.append(vertex((sid, 1), pts2[-1], pts3[-1]))
        patch.boundary[sid] = ids
    out, edge_sum, edge_n = [], 0.0, 0
    for reg in regions:
        loops, corners = [], []
        for c, cyc in enumerate(reg["cycles"]):
            loop, starts = [], set()
            for sid, rev in cyc:
                ids = patch.boundary[sid][::-1] if rev else patch.boundary[sid]
                starts.add(ids[0])
                loop += ids[:-1]
            if (c == 0) != (signed_area([patch.points[v] for v in loop]) > 0):
                loop.reverse()
            loops.append(loop)
            corners.append(starts)
            for a, b in zip(loop, loop[1:] + loop[:1]):
                edge_sum += dist(patch.points[a], patch.points[b])
                edge_n += 1
        out.append((loops, corners))
    patch.ring_vertices.update(v for loops, _ in out for lp in loops for v in lp)
    return out, edge_sum / max(edge_n, 1)


def corner_kinds(points, loop, corner_ids, cfg, layout_ids=None):
    """Classify segment corners by their signed turn: convex, reflex or plain.

    With layout_ids (the layout corners of a quad/npole loop), exactly those get
    corner quads and every other corner a straight spoke, so each layout side
    loses the same number of edges per ring and a balanced outline stays balanced.
    """
    n, kinds = len(loop), {}
    for i, v in enumerate(loop):
        if v in corner_ids:
            t = signed_turn_deg(points[loop[i - 1]], points[v], points[loop[(i + 1) % n]])
            if t <= -cfg["reflex_corner_turn_deg"]:
                kinds[i] = REFLEX
            elif layout_ids is not None:
                kinds[i] = CONVEX if v in layout_ids else PLAIN
            else:
                kinds[i] = CONVEX if t >= cfg["convex_corner_turn_deg"] else PLAIN
    return kinds


def _inner_side_counts_ok(n, kinds):
    """Every inner side between shaping corners must keep at least one edge."""
    shaping = sorted(i for i, k in kinds.items() if k != PLAIN)
    for a, b in zip(shaping, shaping[1:] + shaping[:1]):
        edges = (b - a) % n or n
        inner = edges - (kinds[a] == CONVEX) - (kinds[b] == CONVEX) \
            + (kinds[a] == REFLEX) + (kinds[b] == REFLEX)
        if inner < 1:
            return False
    return True


def _offset(points, loop, i, depth, miter_min):
    n = len(loop)
    p0, p, p2 = points[loop[i - 1]], points[loop[i]], points[loop[(i + 1) % n]]
    na, nb = left_normal(p0, p), left_normal(p, p2)
    sx, sy = na[0] + nb[0], na[1] + nb[1]
    sl = math.hypot(sx, sy)
    bis = nb if sl < 1e-9 else (sx / sl, sy / sl)  # unit normals cancel: a 180° spike
    m = max(bis[0] * nb[0] + bis[1] * nb[1], miter_min)
    return (p[0] + bis[0] * depth / m, p[1] + bis[1] * depth / m), na, nb


def _ray_hit(o, d, a, b):
    """Distance along unit ray o + t*d to segment ab, or None."""
    ex, ey = b[0] - a[0], b[1] - a[1]
    den = d[0] * ey - d[1] * ex
    if abs(den) < 1e-15:
        return None
    wx, wy = a[0] - o[0], a[1] - o[1]
    t = (wx * ey - wy * ex) / den
    u = (wx * d[1] - wy * d[0]) / den
    return t if t > 0 and 0.0 <= u <= 1.0 else None


def clearance(points, loops, skip):
    """Per loop, per vertex: room for a ring there, as the distance along the
    inward bisector to the first border edge it meets. Edges within `skip` steps
    along the same loop don't count: near a corner the ray crosses the corner's
    own wedge, which the corner quad already handles. A narrow strip has little
    room; a corner has plenty."""
    edges = [(k, i, lp[i], lp[(i + 1) % len(lp)]) for k, lp in enumerate(loops) for i in range(len(lp))]
    out = []
    for k, lp in enumerate(loops):
        n, row = len(lp), []
        for i, v in enumerate(lp):
            p0, p, p2 = points[lp[i - 1]], points[v], points[lp[(i + 1) % n]]
            na, nb = left_normal(p0, p), left_normal(p, p2)
            sx, sy = na[0] + nb[0], na[1] + nb[1]
            sl = math.hypot(sx, sy)
            d = nb if sl < 1e-9 else (sx / sl, sy / sl)
            best = math.inf
            for ek, ei, a, b in edges:
                if ek == k and min((ei - i) % n, (i - ei - 1) % n) < skip:
                    continue
                t = _ray_hit(p, d, points[a], points[b])
                if t is not None and t < best:
                    best = t
            row.append(best)
        out.append(row)
    return out


def _ring_step(points, loop, kinds, depth, cfg):
    """One ring around loop. Ids of new points start at len(points).

    depth: one depth, or a depth per loop vertex. Returns (new points, quads,
    inner loop, inner kinds, corner map); the corner map sends each corner's
    outer index to the inner vertex it becomes.
    """
    n, base = len(loop), len(points)
    dep = depth if isinstance(depth, list) else [depth] * n
    new, d_of = [], {}

    def add(p):
        new.append(p)
        return base + len(new) - 1

    def corner_vertex(i):
        if i not in d_of:
            d_of[i] = add(_offset(points, loop, i, dep[i], cfg["miter_min"])[0])
        return d_of[i]

    spokes, inner, inner_kinds, cmap = [], [], {}, {}

    def push(v, kind=None):
        if not inner or inner[-1] != v:
            inner.append(v)
        if kind:
            inner_kinds[v] = kind

    for i in range(n):
        kind = kinds.get(i)
        if kind == CONVEX:
            continue
        prev_c = kinds.get((i - 1) % n) == CONVEX
        next_c = kinds.get((i + 1) % n) == CONVEX
        if kind == REFLEX:
            q, na, nb = _offset(points, loop, i, dep[i], cfg["miter_min"])
            p = points[loop[i]]
            a = corner_vertex((i - 1) % n) if prev_c else add((p[0] + na[0] * dep[i], p[1] + na[1] * dep[i]))
            push(a, CONVEX if prev_c else None)
            cmap[i] = add(q)
            push(cmap[i], REFLEX)
            b = corner_vertex((i + 1) % n) if next_c else add((p[0] + nb[0] * dep[i], p[1] + nb[1] * dep[i]))
            push(b, CONVEX if next_c else None)
            spokes += [(i, a), (i, b)]
        else:
            if prev_c:
                t = corner_vertex((i - 1) % n)
            elif next_c:
                t = corner_vertex((i + 1) % n)
            else:
                t = add(_offset(points, loop, i, dep[i], cfg["miter_min"])[0])
            push(t, CONVEX if (prev_c or next_c) else kind)
            spokes.append((i, t))
            if kind == PLAIN:
                cmap[i] = t
    for i, kind in kinds.items():
        if kind == CONVEX:
            cmap[i] = corner_vertex(i)
    if len(inner) > 1 and inner[0] == inner[-1]:
        inner.pop()

    at = {v: k for k, v in enumerate(inner)}
    m, quads = len(inner), []
    for k in range(len(spokes)):
        (o1, t1), (o2, t2) = spokes[k], spokes[(k + 1) % len(spokes)]
        outer = [loop[(o1 + s) % n] for s in range((o2 - o1) % n + 1)]
        back = [inner[(at[t2] - s) % m] for s in range((at[t2] - at[t1]) % m + 1)]
        quad = outer + back
        if len(quad) != 4 or len(set(quad)) != 4:
            raise RuntimeError(f"Ring built a {len(quad)}-sided face {quad}; this is a bug.")
        quads.append(quad)
    return new, quads, inner, {at[v]: k for v, k in inner_kinds.items()}, cmap


def _loops_clean(points, loops, eps):
    """No edge of any loop crosses another edge (same or other loop)."""
    edges = [(lp[i], lp[(i + 1) % len(lp)]) for lp in loops for i in range(len(lp))]
    for x in range(len(edges)):
        a, b = edges[x]
        for y in range(x + 1, len(edges)):
            c, d = edges[y]
            if len({a, b, c, d}) < 4:
                continue
            if segments_cross(points[a], points[b], points[c], points[d], eps):
                return False
    return True


def build_rings(patch, loops, corners, n_rings, edge, cfg, eps, layout_ids=None):
    """Add up to n_rings rings to patch.

    layout_ids: per loop, the layout corner vertex ids (or None). Returns the
    innermost loops, their corner kinds, and for every layout corner the vertex
    it became on the innermost loop.
    """
    layout_ids = layout_ids or [None] * len(loops)
    kinds = [corner_kinds(patch.points, lp, cs, cfg, ids) for lp, cs, ids in zip(loops, corners, layout_ids)]
    follow = {v: v for ids in layout_ids if ids for v in ids}
    for ring in range(n_rings):
        if not all(_inner_side_counts_ok(len(lp), kd) for lp, kd in zip(loops, kinds)):
            patch.warnings.append(
                f"Piece {piece_label(patch.piece)}: sides are too short for border ring {ring + 1}; "
                f"using {ring} ring(s). Raise the counts on the short sides to get the ring.")
            break
        depth = cfg["depth_ratio"] * edge
        # Narrow spots get a shallower ring instead of losing it.
        room = clearance(patch.points, loops, cfg["clearance_skip"])
        local = [[min(depth, cfg["clearance_ratio"] * c) for c in row] for row in room]
        for _ in range(cfg["shrink_steps"] + 1):
            pts = list(patch.points)
            steps = []
            for lp, kd, dl in zip(loops, kinds, local):
                new, quads, inner, ik, cmap = _ring_step(pts, lp, kd, dl, cfg)
                pts += new
                steps.append((quads, inner, ik, cmap))
            quads = [q for s in steps for q in s[0]]
            inners = [s[1] for s in steps]
            if all(signed_area([pts[v] for v in q]) > eps for q in quads) and \
               _loops_clean(pts, loops + inners, eps):
                break
            local = [[d * 0.5 for d in row] for row in local]
        else:
            patch.warnings.append(
                f"Piece {piece_label(patch.piece)}: border ring {ring + 1} would overlap itself or a hole; "
                f"using {ring} ring(s).")
            break
        patch.points[:] = pts
        patch.quads += quads
        patch.ring_vertices.update(v for lp in inners for v in lp)
        patch.rings = ring + 1
        where = {v: (k, i) for k, lp in enumerate(loops) for i, v in enumerate(lp)}
        for v0, v in follow.items():
            k, i = where[v]
            follow[v0] = steps[k][3][i]
        loops, kinds = inners, [s[2] for s in steps]
    return loops, kinds, follow
