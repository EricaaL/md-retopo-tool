"""Step 10 (optional): give the finished mesh a thickness.

The outer surface stays where the drape put it; an inner shell is offset by
-normal x thickness (inward) with its faces reversed, and every open border is
bridged to it with a rim. With support loops the rim is three quads high: cuts
at thickness.support_ratio from each surface keep both rim edges crisp when the
artist subdivides. Quads in, quads out.
"""
import math
from collections import defaultdict

from .mesh import MeshData


def _face_normal(pts):
    """Newell normal (length = 2 x area), so bigger faces weigh more."""
    nx = ny = nz = 0.0
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
    return nx, ny, nz


def vertex_normals(mesh, eps):
    """Area-weighted unit normals. Copies of one point (split at an ear fin) share one."""
    acc = [[0.0, 0.0, 0.0] for _ in mesh.positions]
    for f in mesh.faces:
        n = _face_normal([mesh.positions[v] for v in f])
        for v in f:
            for k in range(3):
                acc[v][k] += n[k]
    key = lambda p: tuple(round(c / max(eps, 1e-12)) for c in p)
    same = defaultdict(list)
    for v, p in enumerate(mesh.positions):
        same[key(p)].append(v)
    out = [None] * len(acc)
    for vs in same.values():
        s = [sum(acc[v][k] for v in vs) for k in range(3)]
        length = math.sqrt(sum(c * c for c in s))
        n = tuple(c / length for c in s) if length > 0 else (0.0, 1.0, 0.0)
        for v in vs:
            out[v] = n
    return out


def thicken(mesh, thickness, support=True, support_ratio=0.15, face_piece=None, eps=1e-6):
    """Returns (MeshData, face_piece for the new faces' pieces, rim face ids).

    face_piece: piece per input face (both shells keep it; rim faces take the
    piece of the face they border)."""
    if not thickness > 0:
        raise ValueError(f"Thickness must be above 0, got {thickness!r}.")
    if not 0 < support_ratio < 0.5:
        raise ValueError(f"thickness.support_ratio must be between 0 and 0.5, got {support_ratio!r}.")
    face_piece = list(face_piece) if face_piece is not None else [0] * len(mesh.faces)
    normals = vertex_normals(mesh, eps)
    n = len(mesh.positions)
    positions = list(mesh.positions)
    uvs = list(mesh.uvs)
    faces = [list(f) for f in mesh.faces]
    face_uvs = [list(t) for t in mesh.face_uvs]
    out_piece = list(face_piece)

    # Inner shell: offset inward, faces reversed so they face the other way.
    for v, p in enumerate(mesh.positions):
        positions.append(tuple(p[k] - normals[v][k] * thickness for k in range(3)))
    for f, t, pid in zip(mesh.faces, mesh.face_uvs, face_piece):
        faces.append([v + n for v in f[::-1]])
        face_uvs.append(list(t[::-1]))
        out_piece.append(pid)

    # Rim: bridge every open border edge from the outer to the inner shell.
    fracs = [support_ratio, 1 - support_ratio] if support else []
    layers = {}

    def layer(v, k):               # k-th cut between outer (0) and inner (len(fracs)+1)
        if k == 0:
            return v
        if k == len(fracs) + 1:
            return v + n
        if (v, k) not in layers:
            layers[v, k] = len(positions)
            p = mesh.positions[v]
            positions.append(tuple(p[c] - normals[v][c] * thickness * fracs[k - 1] for c in range(3)))
        return layers[v, k]

    uses = defaultdict(int)
    for f in mesh.faces:
        for k in range(len(f)):
            uses[(min(f[k], f[(k + 1) % len(f)]), max(f[k], f[(k + 1) % len(f)]))] += 1
    rim = []
    steps = len(fracs) + 1
    for f, t, pid in zip(mesh.faces, mesh.face_uvs, face_piece):
        for k in range(len(f)):
            a, b = f[k], f[(k + 1) % len(f)]
            if uses[(min(a, b), max(a, b))] != 1:
                continue
            ua, ub = mesh.uvs[t[k]], mesh.uvs[t[(k + 1) % len(f)]]
            du, dv = ub[0] - ua[0], ub[1] - ua[1]
            length = math.hypot(du, dv) or 1.0
            out_u, out_v = dv / length, -du / length       # right of a->b: away from the face in UV
            width = 0.1 * length / steps
            row = []
            for j in range(steps + 1):
                row.append((len(uvs), len(uvs) + 1))
                uvs.append((ub[0] + out_u * width * j, ub[1] + out_v * width * j))
                uvs.append((ua[0] + out_u * width * j, ua[1] + out_v * width * j))
            for j in range(steps):
                rim.append(len(faces))
                faces.append([layer(b, j), layer(a, j), layer(a, j + 1), layer(b, j + 1)])
                face_uvs.append([row[j][0], row[j][1], row[j + 1][1], row[j + 1][0]])
                out_piece.append(pid)
    return MeshData(positions, uvs, faces, face_uvs, source_id="retopo"), out_piece, rim
