"""One hole whose loop has as many edges as the outer loop: bridge them with one strip of quads.

This is how a ring around a hole meets the border ring; it adds no poles.
"""
from ..geom import dist, segments_cross, signed_area
from . import fill_template


@fill_template("annulus")
def fill_annulus(ctx, region):
    if len(region.holes) != 1 or region.holes[0].holes:
        return False
    outer, hole = region.loop, region.holes[0].loop
    n = len(outer)
    if len(hole) != n:
        return False
    pt = ctx.pt

    # The hole runs the other way round; pair outer i with hole (r - i). Pick the
    # rotation r with the shortest spokes, preferring one that lines corners up.
    oc, hc = set(region.corners), set(region.holes[0].corners)

    def cost(r):
        length = sum(dist(pt(outer[i]), pt(hole[(r - i) % n])) for i in range(n))
        misses = sum(1 for i in oc if (r - i) % n not in hc)
        return misses * ctx.edge * n + length

    r = min(range(n), key=cost)
    spokes = [(outer[i], hole[(r - i) % n]) for i in range(n)]
    for x in range(n):
        a, b = spokes[x]
        for y in range(x + 1, n):
            c, d = spokes[y]
            if segments_cross(pt(a), pt(b), pt(c), pt(d), ctx.eps):
                return False
    quads = []
    for i in range(n):
        (o0, h0), (o1, h1) = spokes[i], spokes[(i + 1) % n]
        q = [o0, o1, h1, h0]
        if signed_area([pt(v) for v in q]) <= ctx.eps:
            return False
        quads.append(q)
    ctx.patch.quads += quads
    return True
