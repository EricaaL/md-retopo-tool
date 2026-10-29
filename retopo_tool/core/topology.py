"""Step 3: pieces (UV shells) and seams (UV-border edges shared in 3D).

Expects a mesh that went through normalise_welding, so seam vertices share
3D ids on both sides while their UV ids stay separate.

MD meshes do not always conform along a seam: one side can have vertices the
other lacks, and the cloth sim leaves small gaps. Those border edges are found
by proximity (near_seams): an edge is sewn to another piece when both of its
ends and its midpoint lie within topology.seam_gap_ratio x its length of that
piece's border (capped at topology.seam_gap_max), and it runs nearly parallel to it.
"""
import math
from collections import defaultdict

from .config import load_config
from .geom import closest_on_triangle, dist, signed_area
from .mesh import Piece, SeamEdge, Topology, piece_label, piece_labels


def _trace_loops(piece_id, nxt):
    remaining = set(nxt)
    loops = []
    while remaining:
        start = min(remaining)
        loop, cur = [start], nxt[start][0]
        remaining.discard(start)
        while cur != start:
            if cur not in remaining:
                raise ValueError(
                    f"Piece {piece_label(piece_id)}: border breaks at UV vertex {cur}. "
                    "Faces likely have inconsistent winding; flip normals in the MD export.")
            loop.append(cur)
            remaining.discard(cur)
            cur = nxt[cur][0]
        loops.append(loop)
    return loops


def _point_seg(p, a, b):
    ab = [b[i] - a[i] for i in range(3)]
    ll = sum(x * x for x in ab)
    t = 0.0 if ll == 0 else max(0.0, min(1.0, sum((p[i] - a[i]) * ab[i] for i in range(3)) / ll))
    return dist(p, [a[i] + t * ab[i] for i in range(3)])


def near_seams(mesh, borders, cfg):
    """borders: list of (piece, uv0, uv1, v0, v1) for every UV border edge.

    Returns ({(min uv, max uv): partner pieces}, {(edge key, partner): midpoint gap})
    for edges that run along another piece's border within the gap tolerance. Edges that also weld exactly are
    checked too: on a multi-way seam some pieces weld and others sit a gap away
    (an arm sandwiched between head and body), and every piece must see all of them.
    """
    pos = mesh.positions
    lengths = sorted(dist(pos[v0], pos[v1]) for _, _, _, v0, v1 in borders) or [1.0]
    cell = max(lengths[len(lengths) // 2] * cfg["seam_cell_ratio"], 1e-12)
    grid = defaultdict(list)
    for idx, (_, _, _, v0, v1) in enumerate(borders):
        a, b = pos[v0], pos[v1]
        lo = [int(math.floor(min(a[i], b[i]) / cell)) for i in range(3)]
        hi = [int(math.floor(max(a[i], b[i]) / cell)) for i in range(3)]
        for x in range(lo[0], hi[0] + 1):
            for y in range(lo[1], hi[1] + 1):
                for z in range(lo[2], hi[2] + 1):
                    grid[x, y, z].append(idx)

    out, gaps = {}, {}
    for piece, t0, t1, v0, v1 in borders:
        a, b = pos[v0], pos[v1]
        length = dist(a, b)
        if length == 0:
            continue
        tol = min(cfg["seam_gap_ratio"] * length, cfg["seam_gap_max"])
        mid = [(a[i] + b[i]) / 2 for i in range(3)]
        reach = int(math.ceil((length / 2 + tol) / cell))
        c = [int(math.floor(mid[i] / cell)) for i in range(3)]
        near = set()
        for x in range(c[0] - reach, c[0] + reach + 1):
            for y in range(c[1] - reach, c[1] + reach + 1):
                for z in range(c[2] - reach, c[2] + reach + 1):
                    near.update(grid.get((x, y, z), ()))
        by_piece = defaultdict(list)
        for j in near:
            q, _, _, w0, w1 = borders[j]
            if {w0, w1} & {v0, v1}:
                continue  # touches this edge: its own neighbour, or a shared seam end
            by_piece[q].append((pos[w0], pos[w1]))
        partners = []
        for q, segs in by_piece.items():
            if all(min(_point_seg(p, s0, s1) for s0, s1 in segs) <= tol for p in (a, b, mid)):
                s0, s1 = min(segs, key=lambda s: _point_seg(mid, s[0], s[1]))
                d = [s1[i] - s0[i] for i in range(3)]
                e = [b[i] - a[i] for i in range(3)]
                dl = math.sqrt(sum(x * x for x in d))
                if dl > 0 and abs(sum(x * y for x, y in zip(d, e))) / (dl * length) >= cfg["seam_parallel_cos"]:
                    partners.append(q)
                    gaps[(min(t0, t1), max(t0, t1)), q] = min(_point_seg(mid, s0, s1) for s0, s1 in segs)
        if partners:
            out[(min(t0, t1), max(t0, t1))] = tuple(sorted(partners))
    return out, gaps


def build_topology(mesh, cfg=None):
    nuv = len(mesh.uvs)
    uv_vertex = [-1] * nuv
    edge3, edge_uv = defaultdict(list), defaultdict(list)

    for fi, (face, fuv) in enumerate(zip(mesh.faces, mesh.face_uvs)):
        n = len(face)
        for i in range(n):
            j = (i + 1) % n
            v0, v1, t0, t1 = face[i], face[j], fuv[i], fuv[j]
            uv_vertex[t0] = v0
            edge3[(min(v0, v1), max(v0, v1))].append((fi, t0, t1))
            edge_uv[(min(t0, t1), max(t0, t1))].append((fi, t0, t1))

    for key, ents in edge_uv.items():
        if len(ents) > 2:
            raise ValueError(
                f"Non-manifold UV edge {key} is used by {len(ents)} faces. "
                "Clean the MD mesh (remove overlapping or duplicated faces) and re-export.")
    seen_faces = {}
    for fi, face in enumerate(mesh.faces):
        k = tuple(sorted(face))
        if k in seen_faces:
            raise ValueError(
                f"Non-manifold mesh: faces {seen_faces[k]} and {fi} are duplicates. "
                "Clean the MD mesh (remove overlapping or duplicated faces) and re-export.")
        seen_faces[k] = fi
    for key, ents in edge3.items():
        # 3+ faces on one 3D edge is a multi-way seam only if it is a pattern border for every face.
        if len(ents) > 2 and any(len(edge_uv[(min(t0, t1), max(t0, t1))]) != 1 for _, t0, t1 in ents):
            raise ValueError(
                f"Non-manifold 3D edge {key} is used by {len(ents)} faces inside a pattern piece. "
                "Clean the MD mesh (remove overlapping or duplicated faces) and re-export.")

    # Union faces that share a UV edge -> pieces.
    parent = list(range(len(mesh.faces)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for ents in edge_uv.values():
        if len(ents) == 2:
            a, b = find(ents[0][0]), find(ents[1][0])
            if a != b:
                parent[max(a, b)] = min(a, b)

    root_to_piece, face_piece, piece_faces = {}, [0] * len(mesh.faces), []
    for fi in range(len(mesh.faces)):
        r = find(fi)
        if r not in root_to_piece:
            root_to_piece[r] = len(piece_faces)
            piece_faces.append([])
        face_piece[fi] = root_to_piece[r]
        piece_faces[root_to_piece[r]].append(fi)

    seam_edges, multi = {}, defaultdict(int)
    for key, ents in edge3.items():
        if len(ents) == 2:
            (f0, a0, b0), (f1, a1, b1) = ents
            if {a0, b0} != {a1, b1}:
                seam_edges[key] = SeamEdge(key, (f0, f1), (face_piece[f0], face_piece[f1]))
        elif len(ents) > 2:
            fs = tuple(e[0] for e in ents)
            seam_edges[key] = SeamEdge(key, fs, tuple(face_piece[f] for f in fs))
            multi[tuple(sorted(face_piece[f] for f in fs))] += 1
    warnings = [f"Pieces {piece_labels(p)} share {n} edge(s) in one seam (a piece sewn into a seam, "
                "or an opening pinched onto a line). Their counts are linked." for p, n in sorted(multi.items())]

    directed = defaultdict(lambda: defaultdict(list))
    for ents in edge_uv.values():
        if len(ents) == 1:
            fi, t0, t1 = ents[0]
            directed[face_piece[fi]][t0].append(t1)

    pieces = []
    for pid, faces in enumerate(piece_faces):
        nxt = directed[pid]
        for t, outs in nxt.items():
            if len(outs) > 1:
                raise ValueError(
                    f"Piece {piece_label(pid)} touches itself at UV vertex {t} (bow-tie border). "
                    "Split the pattern piece in MD so its outline is a simple loop.")
        loops = _trace_loops(pid, nxt)
        areas = [signed_area([mesh.uvs[t] for t in lp]) for lp in loops]
        order = sorted(range(len(loops)), key=lambda i: -abs(areas[i]))
        loops = [loops[i] for i in order]
        if areas[order[0]] < 0:
            loops[0] = loops[0][::-1]
        pieces.append(Piece(pid, faces, loops))

    cfg = cfg or load_config("defaults")["topology"]
    borders = []
    for ents in edge_uv.values():
        if len(ents) == 1:
            fi, t0, t1 = ents[0]
            borders.append((face_piece[fi], t0, t1, uv_vertex[t0], uv_vertex[t1]))
    near, gaps = near_seams(mesh, borders, cfg)
    contacts = surface_contacts(mesh, borders, face_piece, seam_edges, near, cfg)
    return Topology(pieces, seam_edges, uv_vertex, face_piece, warnings, near, contacts, gaps)


def surface_contacts(mesh, borders, face_piece, seam_edges, near, cfg):
    """Border edges sewn onto the middle of another piece (an arm onto a torso).

    An edge is on piece B when both its ends and its midpoint lie within
    topology.seam_gap_max of B's faces, and B is not already a seam partner of
    that edge. With layered parts (a pupil on an eye on a head) several pieces
    qualify; the nearest one is the base. Returns {(min uv, max uv): (piece,)}."""
    pos = mesh.positions
    gap = cfg["seam_gap_max"]
    lengths = sorted(dist(pos[v0], pos[v1]) for _, _, _, v0, v1 in borders) or [1.0]
    cell = max(lengths[len(lengths) // 2] * cfg["seam_cell_ratio"], gap, 1e-12)   # one ring of cells covers gap
    tris, boxes = [], []
    for fi, f in enumerate(mesh.faces):
        for k in range(1, len(f) - 1):
            a, b, c = pos[f[0]], pos[f[k]], pos[f[k + 1]]
            tris.append((face_piece[fi], a, b, c))
            boxes.append([min(a[i], b[i], c[i]) for i in range(3)] + [max(a[i], b[i], c[i]) for i in range(3)])
    grid = defaultdict(list)
    for idx, box in enumerate(boxes):
        lo = [int(math.floor(box[i] / cell)) for i in range(3)]
        hi = [int(math.floor(box[i + 3] / cell)) for i in range(3)]
        for x in range(lo[0], hi[0] + 1):
            for y in range(lo[1], hi[1] + 1):
                for z in range(lo[2], hi[2] + 1):
                    grid[x, y, z].append(idx)
    reach = 1

    def near_tris(p):
        c = [int(math.floor(p[i] / cell)) for i in range(3)]
        out = set()
        for x in range(c[0] - reach, c[0] + reach + 1):
            for y in range(c[1] - reach, c[1] + reach + 1):
                for z in range(c[2] - reach, c[2] + reach + 1):
                    out.update(grid.get((x, y, z), ()))
        return out

    out = {}
    for piece, t0, t1, v0, v1 in borders:
        key = (min(t0, t1), max(t0, t1))
        se = seam_edges.get((min(v0, v1), max(v0, v1)))
        sewn = set(near.get(key, ())) | (set(se.pieces) if se else set()) | {piece}
        a, b = pos[v0], pos[v1]
        mid = [(a[i] + b[i]) / 2 for i in range(3)]
        hits = None
        for p in (a, b, mid):
            on = {}
            for idx in near_tris(p):
                q, ta, tb, tc = tris[idx]
                box = boxes[idx]
                if q in sewn or any(p[i] < box[i] - gap or p[i] > box[i + 3] + gap for i in range(3)):
                    continue
                d = closest_on_triangle(p, ta, tb, tc)[0]
                if d <= gap and d < on.get(q, math.inf):
                    on[q] = d
            hits = on if hits is None else {q: hits[q] + d for q, d in on.items() if q in hits}
            if not hits:
                break
        if hits:
            out[key] = (min(hits, key=hits.get),)
    return out
