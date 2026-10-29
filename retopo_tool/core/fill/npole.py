"""3, 5 or 6 corners: one pole in the middle, one grid per corner.

The region needn't be convex: it only needs every spoke (split point -> pole)
to stay inside it. The grids reject folded quads themselves.

Side i (corner i -> corner i+1) splits into a_i + b_i with a_i = s_{i-1} and
b_i = s_{i+1}, where s_i is the spoke count from side i's split point to the
pole. So c_i = s_{i-1} + s_{i+1}; the region fits only if every s_i >= 1.
"""
from ..geom import lerp, point_in_polygon, segments_cross, signed_area
from . import Region, fill_region, fill_template, spoke_counts


def _centroid(pts, eps):
    a = signed_area(pts)
    if abs(a) <= eps:
        return tuple(sum(p[k] for p in pts) / len(pts) for k in range(2))
    cx = cy = 0.0
    for i in range(len(pts)):
        (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % len(pts)]
        f = x0 * y1 - x1 * y0
        cx += (x0 + x1) * f
        cy += (y0 + y1) * f
    return cx / (6 * a), cy / (6 * a)


@fill_template("npole")
def fill_npole(ctx, region):
    n = len(region.corners)
    if region.holes or n not in ctx.cfg["npole_sides"]:
        return False
    loop, L = region.loop, len(region.loop)
    pts = [ctx.pt(v) for v in loop]
    s = spoke_counts(region.side_counts())
    if s is None:
        return False
    center_p = _centroid(pts, ctx.eps)
    if not point_in_polygon(center_p, pts):
        return False
    sides = region.sides()
    split = [sides[i][s[(i - 1) % n]] for i in range(n)]   # a_i edges from corner i
    for v in split:  # each spoke must stay inside the region
        for k in range(L):
            a, b = loop[k], loop[(k + 1) % L]
            if v not in (a, b) and segments_cross(ctx.pt(v), center_p, ctx.pt(a), ctx.pt(b), ctx.eps):
                return False
    center = ctx.add_point(center_p)

    spokes = []                                             # split point -> center
    for i in range(n):
        p0 = ctx.pt(split[i])
        spokes.append([split[i]] + [ctx.add_point(lerp(p0, center_p, k / s[i])) for k in range(1, s[i])]
                      + [center])
    for i in range(n):
        j = (i + 1) % n
        b_i = sides[i][s[(i - 1) % n]:]           # split_i -> corner_{i+1}
        a_j = sides[j][:s[i] + 1]                 # corner_{i+1} -> split_{i+1}
        sub = b_i + a_j[1:] + spokes[j][1:] + spokes[i][::-1][1:-1]
        c1 = len(b_i) - 1
        c2 = c1 + len(a_j) - 1
        c3 = c2 + len(spokes[j]) - 1
        if not fill_region(ctx, Region(sub, [0, c1, c2, c3])):
            return False
    return True
