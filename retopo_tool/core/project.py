"""Step 9: project a flat patch back onto the 3D drape.

Every patch vertex is a point in the piece's MD pattern UVs. It is located in
the MD faces of that piece (fan-triangulated in UV) and gets the barycentric
blend of their 3D positions. Piece-border vertices keep their exact sampled 3D
point (FlatPatch.fixed3d), so seam partners land on identical positions.
"""
import math
from collections import defaultdict


def _bary(p, a, b, c):
    d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
    if d == 0:
        return None
    l1 = ((b[1] - c[1]) * (p[0] - c[0]) + (c[0] - b[0]) * (p[1] - c[1])) / d
    l2 = ((c[1] - a[1]) * (p[0] - c[0]) + (a[0] - c[0]) * (p[1] - c[1])) / d
    return l1, l2, 1.0 - l1 - l2


def project_patch(patch, piece, mesh, cfg):
    """Return a 3D point for every vertex of patch (list aligned with patch.points)."""
    tris = []
    for fi in piece.faces:
        f, t = mesh.faces[fi], mesh.face_uvs[fi]
        for k in range(1, len(f) - 1):
            tris.append(((t[0], t[k], t[k + 1]), (f[0], f[k], f[k + 1])))
    uvs, pos = mesh.uvs, mesh.positions

    # Uniform grid over the piece's UV bounds, about cfg["cell_tris"] triangles per cell.
    xs = [uvs[t][0] for tr, _ in tris for t in tr]
    ys = [uvs[t][1] for tr, _ in tris for t in tr]
    x0, y0 = min(xs), min(ys)
    span = max(max(xs) - x0, max(ys) - y0, cfg["eps"])
    cells = max(1, int(math.sqrt(len(tris) / cfg["cell_tris"])))
    size = span / cells
    grid = defaultdict(list)
    for idx, (tr, _) in enumerate(tris):
        bx = [uvs[t][0] for t in tr]
        by = [uvs[t][1] for t in tr]
        for gx in range(int((min(bx) - x0) / size), int((max(bx) - x0) / size) + 1):
            for gy in range(int((min(by) - y0) / size), int((max(by) - y0) / size) + 1):
                grid[gx, gy].append(idx)

    def locate(p):
        cand = grid.get((int((p[0] - x0) / size), int((p[1] - y0) / size)), ())
        best = None
        for idx in cand:
            tr, vs = tris[idx]
            w = _bary(p, uvs[tr[0]], uvs[tr[1]], uvs[tr[2]])
            if w is None:
                continue
            worst = min(w)
            if worst >= -cfg["inside_tolerance"]:
                return w, vs
            if best is None or worst > best[0]:
                best = (worst, w, vs)
        if best is None:  # outside every nearby cell: fall back to a full scan
            for tr, vs in tris:
                w = _bary(p, uvs[tr[0]], uvs[tr[1]], uvs[tr[2]])
                if w is not None and (best is None or min(w) > best[0]):
                    best = (min(w), w, vs)
        w, vs = best[1], best[2]
        w = [max(0.0, x) for x in w]  # clamp onto the nearest triangle
        s = sum(w) or 1.0
        return [x / s for x in w], vs

    out = []
    for v, p in enumerate(patch.points):
        if v in patch.fixed3d:
            out.append(tuple(patch.fixed3d[v]))
            continue
        w, vs = locate(p)
        out.append(tuple(sum(w[k] * pos[vs[k]][i] for k in range(3)) for i in range(3)))
    return out
