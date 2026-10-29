"""Virtual corners: fill as a grid by choosing which 4 vertices are its corners.

A grid needs side counts (a, b, a, b) with a + b = n / 2. With fewer than 4 real
corners (a circle, a lens) every real corner is kept. A small leftover region
(at most fill.loop_any_max edges) may also drop real corners, preferring corner
sets that keep the most of them. Candidates prefer vertices where the border
already turns, then balanced sides.
"""
from ..geom import signed_turn_deg
from . import Region, fill_region, fill_template


@fill_template("loop")
def fill_loop(ctx, region):
    loop, n = region.loop, len(region.loop)
    few = len(region.corners) < 4
    if region.holes or n < 4 or n % 2 or not (few or n <= ctx.cfg["loop_any_max"]):
        return False
    turn = [abs(signed_turn_deg(ctx.pt(loop[i - 1]), ctx.pt(loop[i]), ctx.pt(loop[(i + 1) % n])))
            for i in range(n)]
    real = set(region.corners)
    if few:
        starts = region.corners or [max(range(n), key=lambda i: turn[i])]
    else:
        starts = range(n)
    half, options = n // 2, []
    for s in starts:
        for a in range(1, half):
            b = half - a
            pos = [s, (s + a) % n, (s + a + b) % n, (s + 2 * a + b) % n]
            if few and not real <= set(pos):
                continue
            dropped = len(real - set(pos))
            score = 1000 * dropped - sum(turn[p] for p in pos if p not in real) + abs(a - b)
            options.append((score, s, a, b))
    options.sort()
    for _, s, a, b in options[:ctx.cfg["max_split_candidates"]]:
        rotated = loop[s:] + loop[:s]
        mark = ctx.mark()
        if fill_region(ctx, Region(rotated, [0, a, a + b, 2 * a + b])):
            return True
        ctx.rollback(mark)
    return False
