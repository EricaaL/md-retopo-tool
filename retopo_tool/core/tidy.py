"""Step 8 (before relax): remove poles the fill left that local edits can cancel.

Two edits, both inside the patch. They never move the piece border or attach
lines (patch.boundary), and never cut or remove an edge between two ring
vertices, so the border ring's inner loop stays whole:
- Doublet: an interior vertex with 2 edges sits between two quads that share
  both its edges. The two quads merge into one and the vertex goes.
- Edge flip: two quads sharing an edge form a hexagon; the shared edge can be
  replaced by either other diagonal. The edge's ends lose one edge each and the
  new diagonal's ends gain one. A flip is taken only when it lowers the sum of
  (valence - 4)^2 over the four vertices (e.g. a 5-5 edge between two 3-poles
  leaves four regular vertices) and both new quads are convex in UV.

Quads in different face sets (an attach inside and the area around it) are
never merged or flipped together. The patch keeps its border, so counts never
change.
"""
from collections import defaultdict

from .geom import signed_area


def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _convex(pts, eps):
    """CCW and every corner turning left."""
    return signed_area(pts) > eps and all(_cross(pts[k - 1], pts[k], pts[(k + 1) % 4]) > eps for k in range(4))


def tidy_patch(patch, cfg, eps):
    """Remove doublets, then flip edges that cancel poles. Returns the number of edits."""
    fixed = set(patch.fixed3d)
    for ids in patch.boundary.values():
        fixed.update(ids)
    ring = set(patch.ring_vertices) - fixed
    tag = {}
    for name, qs in patch.face_tags.items():
        for i in qs:
            tag[i] = name
    quads = [list(q) for q in patch.quads]
    alive = [True] * len(quads)
    edits = 0

    def valence():
        nb = defaultdict(set)
        for i, q in enumerate(quads):
            if alive[i]:
                for k in range(4):
                    nb[q[k]].add(q[(k + 1) % 4])
                    nb[q[(k + 1) % 4]].add(q[k])
        return nb

    def faces_of():
        out = defaultdict(list)
        for i, q in enumerate(quads):
            if alive[i]:
                for v in q:
                    out[v].append(i)
        return out

    # Doublets.
    for _ in range(cfg["tidy_max_passes"]):
        nb, fo = valence(), faces_of()
        done = False
        for v, n in nb.items():
            if v in fixed or len(n) != 2 or len(fo[v]) != 2:
                continue
            i, j = fo[v]
            if tag.get(i) != tag.get(j):
                continue
            qi, qj = quads[i], quads[j]
            k = qi.index(v)
            a, x, b = qi[(k + 1) % 4], qi[(k + 2) % 4], qi[(k + 3) % 4]
            m = qj.index(v)
            if qj[(m + 1) % 4] != b or qj[(m + 3) % 4] != a:
                continue
            y = qj[(m + 2) % 4]
            merged = [a, x, b, y]
            if len(set(merged)) != 4 or signed_area([patch.points[w] for w in merged]) <= eps:
                continue
            quads[i], alive[j] = merged, False
            edits += 1
            done = True
            break
        if not done:
            break

    # Edge flips.
    for _ in range(cfg["tidy_max_passes"]):
        nb = valence()
        val = {v: len(n) for v, n in nb.items()}
        owner = {}
        for i, q in enumerate(quads):
            if alive[i]:
                for k in range(4):
                    owner[q[k], q[(k + 1) % 4]] = i
        best = None
        for (u, v), i in owner.items():
            if u > v or (v, u) not in owner or u in fixed or v in fixed or (u in ring and v in ring):
                continue
            j = owner[v, u]
            if tag.get(i) != tag.get(j):
                continue
            qi, qj = quads[i], quads[j]
            k = qi.index(u)               # qi = u, v, p1, p2
            p1, p2 = qi[(k + 2) % 4], qi[(k + 3) % 4]
            m = qj.index(v)               # qj = v, u, q1, q2
            q1, q2 = qj[(m + 2) % 4], qj[(m + 3) % 4]
            if len({u, v, p1, p2, q1, q2}) != 6:
                continue
            for a, b, new_i, new_j in ((q1, p1, [q1, q2, v, p1], [p1, p2, u, q1]),
                                       (q2, p2, [q2, v, p1, p2], [p2, u, q1, q2])):
                if a in fixed or b in fixed or (a in ring and b in ring) or b in nb[a]:
                    continue
                before = sum((val[w] - 4) ** 2 for w in (u, v, a, b))
                after = (val[u] - 5) ** 2 + (val[v] - 5) ** 2 + (val[a] - 3) ** 2 + (val[b] - 3) ** 2
                gain = before - after
                if gain <= 0 or (best is not None and gain <= best[0]):
                    continue
                if not (_convex([patch.points[w] for w in new_i], eps) and _convex([patch.points[w] for w in new_j], eps)):
                    continue
                best = (gain, i, j, new_i, new_j)
        if best is None:
            break
        _, i, j, new_i, new_j = best
        quads[i], quads[j] = new_i, new_j
        edits += 1

    if not edits:
        return 0
    keep = [i for i in range(len(quads)) if alive[i]]
    index = {old: new for new, old in enumerate(keep)}
    patch.quads = [quads[i] for i in keep]
    patch.face_tags = {name: [index[i] for i in qs if i in index] for name, qs in patch.face_tags.items()}
    _compact(patch)
    return edits


def _compact(patch):
    """Drop points no quad uses any more (removed doublets) and renumber."""
    used = {v for q in patch.quads for v in q} | {v for ids in patch.boundary.values() for v in ids}
    if len(used) == len(patch.points):
        return
    vmap = {}
    pts = []
    for v, p in enumerate(patch.points):
        if v in used:
            vmap[v] = len(pts)
            pts.append(p)
    patch.points = pts
    patch.quads = [[vmap[v] for v in q] for q in patch.quads]
    patch.boundary = {s: [vmap[v] for v in ids] for s, ids in patch.boundary.items()}
    patch.fixed3d = {vmap[v]: p for v, p in patch.fixed3d.items() if v in vmap}
    patch.ring_vertices = {vmap[v] for v in patch.ring_vertices if v in vmap}
