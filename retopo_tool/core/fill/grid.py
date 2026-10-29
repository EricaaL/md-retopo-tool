"""4 corners with equal opposite sides: a Coons (transfinite) grid."""
from ..geom import signed_area
from . import fill_template


@fill_template("grid")
def fill_grid(ctx, region):
    if region.holes or len(region.corners) != 4:
        return False
    cnt = region.side_counts()
    if cnt[0] != cnt[2] or cnt[1] != cnt[3]:
        return False
    s0, s1, s2, s3 = region.sides()
    m, n = cnt[0], cnt[1]
    bottom, right, top, left = s0, s1, s2[::-1], s3[::-1]  # top/left run from corner 3 / corner 0
    pt = ctx.pt
    c0, c1, c2, c3 = pt(bottom[0]), pt(bottom[m]), pt(top[m]), pt(top[0])

    g = {}
    for i in range(m + 1):
        g[i, 0], g[i, n] = bottom[i], top[i]
    for j in range(n + 1):
        g[0, j], g[m, j] = left[j], right[j]
    for j in range(1, n):
        v = j / n
        for i in range(1, m):
            u = i / m
            b, t, lf, r = pt(bottom[i]), pt(top[i]), pt(left[j]), pt(right[j])
            g[i, j] = ctx.add_point(tuple(
                (1 - v) * b[k] + v * t[k] + (1 - u) * lf[k] + u * r[k]
                - ((1 - u) * (1 - v) * c0[k] + u * (1 - v) * c1[k] + u * v * c2[k] + (1 - u) * v * c3[k])
                for k in range(2)))
    quads = [[g[i, j], g[i + 1, j], g[i + 1, j + 1], g[i, j + 1]] for j in range(n) for i in range(m)]
    if any(signed_area([pt(v) for v in q]) <= ctx.eps for q in quads):
        return False  # folded: let another template or split try
    ctx.patch.quads += quads
    return True
