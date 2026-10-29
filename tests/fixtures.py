"""Synthetic garment pieces. Unwelded by construction; run normalise_welding on them."""
import math

from retopo_tool.core.config import load_config
from retopo_tool.core.mesh import MeshData


def solver_cfg(target=1.0):
    """Solver config with a target edge length that matches the unit-edge fixtures."""
    return dict(load_config("defaults")["solver"], target_edge_length=target)


def grid_piece(nx, ny, pos_fn, uv_origin=(0, 0), uv_scale=(1, 1), skip=()):
    """nx*ny quad grid, CCW in UV. skip: set of (i, j) cells to leave out."""
    positions, uvs, faces, fuv = [], [], [], []
    vid = {}
    for j in range(ny + 1):
        for i in range(nx + 1):
            vid[i, j] = len(positions)
            positions.append(pos_fn(i, j))
            uvs.append((uv_origin[0] + i * uv_scale[0], uv_origin[1] + j * uv_scale[1]))
    for j in range(ny):
        for i in range(nx):
            if (i, j) in skip:
                continue
            f = [vid[i, j], vid[i + 1, j], vid[i + 1, j + 1], vid[i, j + 1]]
            faces.append(f)
            fuv.append(list(f))
    return MeshData(positions, uvs, faces, fuv)


def merge(*parts):
    pos, uvs, faces, fuv = [], [], [], []
    for p in parts:
        po, uo = len(pos), len(uvs)
        pos += p.positions
        uvs += p.uvs
        faces += [[v + po for v in f] for f in p.faces]
        fuv += [[t + uo for t in f] for f in p.face_uvs]
    return MeshData(pos, uvs, faces, fuv)


def rectangle(nx=4, ny=2):
    return grid_piece(nx, ny, lambda i, j: (i, j, 0))


def l_shape(n=2):
    return grid_piece(n, n, lambda i, j: (i, j, 0), skip={(n - 1, n - 1)})


def tube(nx=4, ny=2, b_uv_scale=(1, 1)):
    """Two half-cylinder panels sewn along both sides."""
    a = grid_piece(nx, ny, lambda i, j: (math.cos(math.pi * i / nx), j, math.sin(math.pi * i / nx)),
                   uv_origin=(0, 0))
    b = grid_piece(nx, ny, lambda i, j: (math.cos(math.pi + math.pi * i / nx), j,
                                         math.sin(math.pi + math.pi * i / nx)),
                   uv_origin=(100, 0), uv_scale=b_uv_scale)
    return merge(a, b)


def mirrored_pair(nx=3, ny=2):
    a = grid_piece(nx, ny, lambda i, j: (i + 1, j, 0), uv_origin=(0, 0))
    b = grid_piece(nx, ny, lambda i, j: (-(i + 1), j, 0), uv_origin=(100, 0))
    return merge(a, b)


def pentagon():
    pts = [(math.cos(2 * math.pi * k / 5), math.sin(2 * math.pi * k / 5)) for k in range(5)]
    return MeshData([(x, y, 0) for x, y in pts], list(pts), [[0, 1, 2, 3, 4]], [[0, 1, 2, 3, 4]])


def polygon_piece(pts):
    """One n-gon face in the XY plane; UV = XY. pts CCW."""
    return MeshData([(x, y, 0) for x, y in pts], list(pts), [list(range(len(pts)))],
                    [list(range(len(pts)))])


def triangle(side=6.0):
    h = side * math.sqrt(3) / 2
    return polygon_piece([(0, 0), (side, 0), (side / 2, h)])


def disc(r=2.0, n=16):
    return polygon_piece([(r * math.cos(2 * math.pi * k / n), r * math.sin(2 * math.pi * k / n))
                          for k in range(n)])


def half_disc(r=2.0, n=8):
    """Arc plus diameter: two corners, two segments."""
    return polygon_piece([(r * math.cos(math.pi * k / n), r * math.sin(math.pi * k / n))
                          for k in range(n + 1)])


def big_l(n=6):
    """n x n panel with the top-right quadrant cut away (one reflex corner)."""
    h = n // 2
    return grid_piece(n, n, lambda i, j: (i, j, 0), skip={(i, j) for i in range(h, n) for j in range(h, n)})


def panel_with_hole(n=10, lo=4, hi=6):
    return grid_piece(n, n, lambda i, j: (i, j, 0),
                      skip={(i, j) for i in range(lo, hi) for j in range(lo, hi)})


def flipped_uv_rectangle(nx=6, ny=4):
    """Pattern piece mirrored in UV, so its faces run clockwise in UV."""
    return grid_piece(nx, ny, lambda i, j: (i, j, 0), uv_scale=(-1, 1))


def bodice():
    """A rough front bodice: hem, side seam, armhole curve, shoulder, neckline curve, centre front."""
    pts = [(0, 0), (20, 0), (21, 25)]
    pts += [(21 - 6 * math.sin(math.pi / 2 * t / 6), 25 + 12 * (1 - math.cos(math.pi / 2 * t / 6)))
            for t in range(1, 7)]
    pts += [(10, 42)]
    pts += [(8 - 8 * math.cos(math.pi / 2 * t / 6), 42 - 8 * math.sin(math.pi / 2 * t / 6)) for t in range(1, 7)]
    pts += [(0, 34)]
    out = []
    for p in pts:
        if not out or math.dist(p, out[-1]) > 1e-6:
            out.append(p)
    return polygon_piece(out)


def pinched_insert(n=4, base_gap=0.0):
    """A base panel with a flat tab (front + back halves) sewn onto one line of it,
    like a plush ear: three faces meet on each edge of that line. With base_gap the
    tab halves still weld to each other but the base sits that far away, as MD
    leaves a multi-way seam where only some pieces weld."""
    base = grid_piece(n, n, lambda i, j: (i, j - base_gap, 0), uv_origin=(0, 0))
    front = grid_piece(n, 2, lambda i, j: (i, n - 0.3 * j, j), uv_origin=(100, 0))
    back = grid_piece(n, 2, lambda i, j: (n - i, n + 0.3 * j, j), uv_origin=(200, 0))
    return merge(base, front, back)


def mirrored_uv_overlay(n=3):
    """Left and right panels sewn on the centre line x=0, with the right panel's UVs
    laid on top of the left one, mirrored, the way MD lays out a symmetric pair."""
    left = grid_piece(n, n, lambda i, j: (i - n, j, 0))          # UV x in 0..n, centre line at UV x=n
    right = grid_piece(n, n, lambda i, j: (n - i, j, 0))          # UV x 0..n mirrored: i=0 -> 3D x=n
    right.faces = [f[::-1] for f in right.faces]                  # keep the 3D normals consistent
    right.face_uvs = [f[::-1] for f in right.face_uvs]
    return merge(left, right)


def folded_cone(n=6, h=3):
    """A half disc in UV whose diameter is sewn back onto itself: 3D closes it into
    a cone, so the border folds at the apex (straight in UV, no corner by angle)."""
    import math as _m
    positions, uvs, faces, fuv = [], [], [], []
    # rows j = 0 (apex) .. h, columns i = 0..n around; column n coincides with column 0 in 3D
    vid, tid = {}, {}
    for j in range(h + 1):
        for i in range(n + 1):
            ang = _m.pi * i / n                              # half disc: straight at the fold
            tid[i, j] = len(uvs)
            uvs.append((j * _m.cos(ang), j * _m.sin(ang)))
            if j == 0 and i > 0:
                vid[i, j] = vid[0, 0]
                continue
            if i == n:
                vid[i, j] = vid[0, j]
                continue
            a3 = 2 * _m.pi * i / n
            vid[i, j] = len(positions)
            positions.append((j * 0.4 * _m.cos(a3), j * 0.4 * _m.sin(a3), -j))
    for j in range(h):
        for i in range(n):
            if j == 0:
                faces.append([vid[i, 1], vid[i + 1, 1], vid[0, 0]])
                fuv.append([tid[i, 1], tid[i + 1, 1], tid[0, 0]])
            else:
                faces.append([vid[i, j], vid[i, j + 1], vid[i + 1, j + 1], vid[i + 1, j]])
                fuv.append([tid[i, j], tid[i, j + 1], tid[i + 1, j + 1], tid[i + 1, j]])
    return MeshData(positions, uvs, faces, fuv)


def gapped_tube(nx=4, ny=4, gap=0.05):
    """Two half-cylinder panels sewn along both sides, as an MD sim leaves them:
    the halves don't share a vertex (one has an extra row) and sit a small gap apart."""
    a = grid_piece(nx, ny, lambda i, j: (math.cos(math.pi * i / nx), 4 * j / ny, math.sin(math.pi * i / nx)))
    b = grid_piece(nx, ny + 1, lambda i, j: (math.cos(math.pi + math.pi * i / nx), 4 * j / (ny + 1),
                                               math.sin(math.pi + math.pi * i / nx) - gap),
                   uv_origin=(100, 0), uv_scale=(1, ny / (ny + 1)))
    return merge(a, b)


def clipped_square(size=10.0, clip=3.0):
    """A square with two opposite corners cut off, like the scarf's main piece:
    two 90-degree corners and four soft 135-degree ones."""
    s, c = size, clip
    return polygon_piece([(0, 0), (s - c, 0), (s, c), (s, s), (c, s), (0, s - c)])


def strip(length=12, width=1):
    """A long narrow panel: one edge across, many along."""
    return grid_piece(length, width, lambda i, j: (i, j, 0))


def big_with_tiny(n=8, tiny=0.5):
    """A big panel and a tiny square patch sewn to the middle of its top edge."""
    big = grid_piece(n, n, lambda i, j: (i, j, 0))
    k = 4
    small = grid_piece(k, k, lambda i, j: (n / 2 - tiny / 2 + tiny * i / k, n + tiny * j / k, 0),
                       uv_origin=(100, 0), uv_scale=(tiny / k, tiny / k))
    return merge(big, small)


def twin_panels(stretch=1.0, mirror=False, nx=6, ny=4):
    """Two unsewn panels with one corner cell cut (so a mirror image is not just a
    rotated copy). The second is a copy whose UVs are stretched in u by `stretch`
    and optionally mirrored, the way MD lays out similar pieces."""
    cut = {(nx - 1, ny - 1)}
    a = grid_piece(nx, ny, lambda i, j: (i, j, 0), skip=cut)
    sx = -stretch if mirror else stretch
    b = grid_piece(nx, ny, lambda i, j: (i * stretch, j, 5), uv_origin=(100, 0), uv_scale=(sx, 1), skip=cut)
    if mirror:  # keep the 3D normals consistent with a mirrored UV layout
        b.faces = [f[::-1] for f in b.faces]
        b.face_uvs = [f[::-1] for f in b.face_uvs]
    return merge(a, b)


def ear_on_seam(n=4):
    """Two panels sewn along y = n (like two head pieces) with an ear's two halves
    pinched onto that same seam line: 4 faces on every edge of the line."""
    lower = grid_piece(n, n, lambda i, j: (i, j, 0), uv_origin=(0, 0))
    upper = grid_piece(n, n, lambda i, j: (i, n + j, 0), uv_origin=(100, 0))
    front = grid_piece(n, 2, lambda i, j: (i, n - 0.3 * j, j), uv_origin=(200, 0))
    back = grid_piece(n, 2, lambda i, j: (n - i, n + 0.3 * j, j), uv_origin=(300, 0))
    return merge(lower, upper, front, back)


def _tube_half(cx, cy, r, a0, a1, nx, ny, gap, uv_origin):
    """Half of an open tube standing on z = 0, its bottom rim `gap` above it."""
    return grid_piece(nx, ny, lambda i, j: (cx + r * math.cos(a0 + (a1 - a0) * i / nx),
                                             cy + r * math.sin(a0 + (a1 - a0) * i / nx), gap + j),
                      uv_origin=uv_origin)


def tube_on_panel(gap=0.05, n=8, r=2.0, arc=4):
    """A flat panel with a tube (two halves sewn up the sides) standing on its
    middle, the way an arm is sewn onto a torso: no seam on the panel there."""
    base = grid_piece(n, n, lambda i, j: (i, j, 0))
    c = n / 2
    a = _tube_half(c, c, r, -math.pi / 2, math.pi / 2, arc, 2, gap, (100, 0))
    b = _tube_half(c, c, r, math.pi / 2, 3 * math.pi / 2, arc, 2, gap, (200, 0))
    return merge(base, a, b)


def tube_across_seam(gap=0.05, n=4, r=2.0, arc=4):
    """Two panels sewn along x = n with a tube standing across that seam: each
    tube half sits on one panel, its rim ending on the seam (a chord per panel)."""
    left = grid_piece(n, 2 * n, lambda i, j: (i, j, 0))
    right = grid_piece(n, 2 * n, lambda i, j: (n + i, j, 0), uv_origin=(50, 0))
    a = _tube_half(n, n, r, -math.pi / 2, math.pi / 2, arc, 2, gap, (100, 0))
    b = _tube_half(n, n, r, math.pi / 2, 3 * math.pi / 2, arc, 2, gap, (200, 0))
    return merge(left, right, a, b)


def puff_on_panel(gap=0.05, n=8, k=4):
    """A puffy patch (front and back sewn round the rim, like the teddy's eyes)
    whose rim is sewn onto the middle of a panel."""
    base = grid_piece(n, n, lambda i, j: (i, j, 0))
    o = (n - k) / 2

    def front(i, j):
        inner = 0 < i < k and 0 < j < k
        return (o + i, o + j, gap + (0.6 if inner else 0.0))
    back = grid_piece(k, k, lambda i, j: (o + k - i, o + j, gap), uv_origin=(200, 0))
    return merge(base, grid_piece(k, k, front, uv_origin=(100, 0)), back)
