"""Step 10 (minimal): combine projected patches into one welded quad mesh.

Linked seam segments are sampled at the same fractions of their own 3D length,
so matching border samples on the two sides sit at most a seam gap apart (or
exactly together when the MD seam conformed). Each border vertex gets a snap
distance of seam_snap_ratio x the spacing to its nearest border neighbour; two
border vertices weld when they are closer than both snaps, into one vertex at
their average position. Samples along one border are a full spacing apart, so
they never merge with each other. Each piece keeps its own UVs (the MD pattern UVs), so UV
seams stay split. Pieces whose MD faces run clockwise in UV get their quads
reversed so the 3D normals match the MD mesh. Thickness is not done yet.
"""
import math
from collections import defaultdict

from .mesh import MeshData


def _weld_border(points, snaps):
    """points: 3D points; snaps: weld distance per point. Returns (cluster id per point, cluster positions)."""
    parent = list(range(len(points)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    cell = max(max(snaps, default=0.0), 1e-12)
    grid = defaultdict(list)
    for i, p in enumerate(points):
        c = tuple(int(math.floor(x / cell)) for x in p)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for j in grid.get((c[0] + dx, c[1] + dy, c[2] + dz), ()):
                        if math.dist(p, points[j]) <= min(snaps[i], snaps[j]):
                            a, b = find(i), find(j)
                            if a != b:
                                parent[max(a, b)] = min(a, b)
        grid[c].append(i)
    roots, sums = {}, []
    ids = []
    for i, p in enumerate(points):
        r = find(i)
        if r not in roots:
            roots[r] = len(sums)
            sums.append([0.0, 0.0, 0.0, 0])
        k = roots[r]
        for a in range(3):
            sums[k][a] += p[a]
        sums[k][3] += 1
        ids.append(k)
    return ids, [tuple(s[a] / s[3] for a in range(3)) for s in sums]


def assemble(patches, points3d, snap_ratio):
    """patches: piece id -> FlatPatch; points3d: piece id -> 3D point per patch vertex.

    snap_ratio: border weld distance as a share of the local border spacing.
    Returns (MeshData, face_piece). Pieces with errors are skipped. Attach face
    tags (FlatPatch.face_tags) land in MeshData.meta["face_sets"] as output face ids.
    """
    order = [pid for pid in sorted(patches) if patches[pid].ok]
    used = {pid: {v for q in patches[pid].quads for v in q} for pid in order}
    border_refs, border_pts, snaps = [], [], []
    for pid in order:
        patch, pts = patches[pid], points3d[pid]
        spacing = {}
        for ids in patch.boundary.values():
            for a, b in zip(ids, ids[1:]):
                d = math.dist(pts[a], pts[b])
                spacing[a] = min(spacing.get(a, math.inf), d)
                spacing[b] = min(spacing.get(b, math.inf), d)
        for v in sorted(patch.fixed3d.keys() & used[pid]):
            border_refs.append((pid, v))
            border_pts.append(pts[v])
            snaps.append(snap_ratio * spacing.get(v, 0.0))
    cluster, welded = _weld_border(border_pts, snaps)
    border_of = {ref: cluster[i] for i, ref in enumerate(border_refs)}

    positions = list(welded)
    uvs, faces, face_uvs, face_piece, face_sets = [], [], [], [], {}
    for pid in order:
        patch, pts = patches[pid], points3d[pid]
        for name, ids in patch.face_tags.items():
            face_sets.setdefault(name, []).extend(len(faces) + i for i in ids)
        vmap, tmap = {}, {}
        for v, p in enumerate(pts):
            if v not in used[pid]:
                continue   # e.g. border under a deleted attach opening
            if (pid, v) in border_of:
                vmap[v] = border_of[pid, v]
            else:
                vmap[v] = len(positions)
                positions.append(p)
            tmap[v] = len(uvs)
            uvs.append(tuple(patch.points[v]))
        for q in patch.quads:
            q = q[::-1] if patch.uv_flipped else q
            f = [vmap[v] for v in q]
            for k, v in enumerate(q):  # the weld merged two corners of this quad: keep this one apart
                if f[k] in f[:k]:
                    f[k] = len(positions)
                    positions.append(pts[v])
            faces.append(f)
            face_uvs.append([tmap[v] for v in q])
            face_piece.append(pid)
    _split_fins(positions, faces)
    out = MeshData(positions, uvs, faces, face_uvs, source_id="retopo", meta={"face_sets": face_sets})
    return out, face_piece


def _split_fins(positions, faces):
    """Turn edges shared by an even number (4+) of faces into manifold sheets.

    A plush ear's two halves pinched onto the head seam put 4 faces on each
    edge there. The faces around such an edge are sorted by angle around it and
    paired with their neighbour, starting after the widest gap (head + ear
    front, ear back + head), so the ear joins the head as two sheets. Vertices
    are then split per group of faces that stay connected around them.
    Edges with an odd count (an attach loop that keeps the faces under it) are
    left as they are.
    """
    edge_faces = defaultdict(list)
    for fi, f in enumerate(faces):
        for k in range(len(f)):
            edge_faces[tuple(sorted((f[k], f[(k + 1) % len(f)])))].append(fi)
    paired = {}  # (edge, face) -> partner face across that edge
    for e, fs in edge_faces.items():
        if len(fs) == 2:
            paired[e, fs[0]], paired[e, fs[1]] = fs[1], fs[0]
        elif len(fs) >= 4 and len(fs) % 2 == 0:
            for a, b in _radial_pairs(positions, faces, e, fs):
                paired[e, a], paired[e, b] = b, a
    fins = {e for e, fs in edge_faces.items() if len(fs) >= 4 and len(fs) % 2 == 0}
    if not fins:
        return
    hot = {v for e in fins for v in e}
    splits = []  # (vertex, faces that move to a new copy) -- decided on the original ids first
    for v in sorted(hot):
        around = [fi for fi, f in enumerate(faces) if v in f]
        parent = {fi: fi for fi in around}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for fi in around:
            f = faces[fi]
            k = f.index(v)
            for w in (f[k - 1], f[(k + 1) % len(f)]):
                other = paired.get((tuple(sorted((v, w))), fi))
                if other is not None and other in parent:
                    parent[find(fi)] = find(other)
        groups = defaultdict(list)
        for fi in around:
            groups[find(fi)].append(fi)
        splits += [(v, g) for g in list(groups.values())[1:]]
    for v, g in splits:
        nv = len(positions)
        positions.append(positions[v])
        for fi in g:
            faces[fi] = [nv if x == v else x for x in faces[fi]]


def _radial_pairs(positions, faces, e, fs):
    """Pair the faces around edge e by angle, starting after the widest gap."""
    pu, pv = positions[e[0]], positions[e[1]]
    d = [pv[i] - pu[i] for i in range(3)]
    dl = math.sqrt(sum(x * x for x in d)) or 1.0
    d = [x / dl for x in d]
    ref = [1.0, 0.0, 0.0] if abs(d[0]) < 0.9 else [0.0, 1.0, 0.0]
    e1 = [ref[i] - d[i] * sum(r * x for r, x in zip(ref, d)) for i in range(3)]
    n1 = math.sqrt(sum(x * x for x in e1))
    e1 = [x / n1 for x in e1]
    e2 = [d[1] * e1[2] - d[2] * e1[1], d[2] * e1[0] - d[0] * e1[2], d[0] * e1[1] - d[1] * e1[0]]
    mid = [(pu[i] + pv[i]) / 2 for i in range(3)]
    ang = []
    for fi in fs:
        c = [sum(positions[x][i] for x in faces[fi]) / len(faces[fi]) - mid[i] for i in range(3)]
        ang.append((math.atan2(sum(a * b for a, b in zip(c, e2)), sum(a * b for a, b in zip(c, e1))), fi))
    ang.sort()
    n = len(ang)
    gaps = [((ang[(k + 1) % n][0] - ang[k][0]) % (2 * math.pi), k) for k in range(n)]
    start = (max(gaps)[1] + 1) % n
    order = [ang[(start + k) % n][1] for k in range(n)]
    return [(order[k], order[k + 1]) for k in range(0, n, 2)]
