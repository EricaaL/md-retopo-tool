"""Step 2: normalise welding so welded and unwelded MD exports take one path.

Positions are merged by coordinate, so a seam shared by two pieces ends up
with the same vertex ids on both sides whether or not the export welded it.
UVs are merged only when they sit on the same welded vertex at the same
coordinate inside the same input UV shell, so separate pattern pieces never
fuse in UV space, even when MD lays a mirrored pair on top of each other.
"""
import math
from collections import defaultdict

from .config import load_config
from .geom import signed_area
from .mesh import MeshData, WeldReport


def _weld_positions(positions, tol):
    cells = {}
    out, pmap = [], []
    for p in positions:
        c = tuple(math.floor(x / tol) for x in p)
        found = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for idx in cells.get((c[0] + dx, c[1] + dy, c[2] + dz), ()):
                        q = out[idx]
                        if (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2 <= tol * tol:
                            found = idx
                            break
                    if found is not None:
                        break
                if found is not None:
                    break
            if found is not None:
                break
        if found is None:
            found = len(out)
            out.append(p)
            cells.setdefault(c, []).append(found)
        pmap.append(found)
    return out, pmap


def _uv_shells(mesh, pmap, utol):
    """Input UV shell per face: faces joined across edges that match in welded 3D
    vertices and UV coordinates and run the same way round in UV. Mirrored pieces
    overlaid in UV run opposite ways, so they stay apart."""
    parent = list(range(len(mesh.faces)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    sign, edges = [], defaultdict(list)
    for fi, (face, fuv) in enumerate(zip(mesh.faces, mesh.face_uvs)):
        a = signed_area([mesh.uvs[t] for t in fuv])
        sign.append((a > 0) - (a < 0))
        n = len(face)
        for i in range(n):
            j = (i + 1) % n
            ends = sorted([(pmap[face[i]], round(mesh.uvs[fuv[i]][0] / utol), round(mesh.uvs[fuv[i]][1] / utol)),
                           (pmap[face[j]], round(mesh.uvs[fuv[j]][0] / utol), round(mesh.uvs[fuv[j]][1] / utol))])
            edges[tuple(ends)].append(fi)
    for fs in edges.values():
        for f in fs[1:]:
            if sign[f] * sign[fs[0]] >= 0:  # same winding (or a flat face)
                a, b = find(fs[0]), find(f)
                if a != b:
                    parent[max(a, b)] = min(a, b)
    return [find(f) for f in range(len(mesh.faces))]


def normalise_welding(mesh, cfg=None):
    """Return (MeshData, WeldReport). Input mesh must already be validated."""
    cfg = cfg or load_config("defaults")["weld"]
    ptol, utol = cfg["position_tolerance"], cfg["uv_tolerance"]
    rep = WeldReport()

    positions, rep.position_map = _weld_positions(mesh.positions, ptol)
    shell = _uv_shells(mesh, rep.position_map, utol)

    uvs, uv_lookup = [], {}
    faces, face_uvs = [], []
    for fi, (face, fuv) in enumerate(zip(mesh.faces, mesh.face_uvs)):
        vs, ts = [], []
        for v, t in zip(face, fuv):
            nv = rep.position_map[v]
            u = mesh.uvs[t]
            key = (shell[fi], nv, round(u[0] / utol), round(u[1] / utol))
            if key not in uv_lookup:
                uv_lookup[key] = len(uvs)
                uvs.append(u)
            nt = uv_lookup[key]
            if vs and vs[-1] == nv:  # collapsed edge
                continue
            vs.append(nv)
            ts.append(nt)
        if len(vs) > 1 and vs[0] == vs[-1]:
            vs.pop()
            ts.pop()
        if len(vs) < 3 or len(set(vs)) != len(vs):
            rep.dropped_faces.append(fi)
            continue
        faces.append(vs)
        face_uvs.append(ts)
        rep.kept_faces.append(fi)

    if rep.dropped_faces:
        rep.warnings.append(
            f"{len(rep.dropped_faces)} face(s) collapsed or pinched when welding and were dropped. "
            "Check the MD mesh for overlapping or zero-length edges.")

    out = MeshData(positions, uvs, faces, face_uvs, source_id=mesh.source_id,
                   meta=dict(mesh.meta, welded=True))
    return out, rep
