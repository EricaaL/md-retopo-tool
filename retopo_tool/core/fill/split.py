"""Split a region along chords and fill the parts. Handles holes, concave shapes,
more than 6 corners, and regions no other template accepts.

A chord runs straight between two loop vertices through the inside. Chords are
ranked by the angles they leave at their ends: a quad mesh wants every region
corner near 90 or 180 degrees, so a chord square to a straight side, or one that
extends the side of a reflex corner, costs nothing, while a chord into a convex
corner or along a reflex bisector costs a pole. Its edge count stays near the
chord length; parity keeps both parts even.
"""
from ..geom import dist, lerp, point_in_polygon, segments_cross, signed_turn_deg
from . import Region, fill_region, fill_template, spoke_counts



def _q(ctx, score):
    """Scores are rounded before ranking, so float noise in the last digits (which
    differs between Python versions) can't flip a choice; ties fall back to index order."""
    return round(score, ctx.cfg["score_digits"])

def _angle_cost(alpha):
    """How far an interior angle (degrees) is from a clean quad corner: 0 at 90/180/270."""
    r = round(alpha / 90.0)
    cost = 2.0 * abs(alpha - 90.0 * r) / 90.0
    return cost + (1.0 if r < 1 else 0.0)  # a sliver wedge is always bad


def _end_cost(prev_p, p, next_p, chord_p):
    """Cost of a chord leaving p toward chord_p, between boundary neighbours prev_p and next_p."""
    a = 180.0 - signed_turn_deg(chord_p, p, next_p)   # part that continues to next_p
    b = 180.0 - signed_turn_deg(prev_p, p, chord_p)   # part that comes from prev_p
    return _angle_cost(a) + _angle_cost(b)


def _chord_count(ctx, a, b, parity_base):
    """Edges for chord a-b so that parity_base + count is even."""
    exact = dist(ctx.pt(a), ctx.pt(b)) / ctx.edge
    k = max(1, int(exact + 0.5))
    if (parity_base + k) % 2:
        k = k + 1 if (k <= 1 or exact >= k) else k - 1
    return k


def _visible(ctx, a, b, edges, outer_pts, hole_pts):
    pa, pb = ctx.pt(a), ctx.pt(b)
    for c, d in edges:
        if len({a, b, c, d}) < 4:
            continue
        if segments_cross(pa, pb, ctx.pt(c), ctx.pt(d), ctx.eps):
            return False
    mid = lerp(pa, pb, 0.5)
    return point_in_polygon(mid, outer_pts) and not any(point_in_polygon(mid, h) for h in hole_pts)


def _chord_points(ctx, a, b, k):
    pa, pb = ctx.pt(a), ctx.pt(b)
    return [ctx.add_point(lerp(pa, pb, s / k)) for s in range(1, k)]


def _edges(loop):
    return [(loop[i], loop[(i + 1) % len(loop)]) for i in range(len(loop))]


def _sub_corners(ctx, sub, inherited, ends):
    """inherited corner positions in sub, plus chord endpoints that turn enough."""
    return sorted(set(inherited) | set(ctx.angle_corners(sub, ends)))


def _is_corner(ctx, p0, p1, p2):
    return int(abs(signed_turn_deg(p0, p1, p2)) >= ctx.cfg["corner_angle_deg"])


def _fillable_score(ctx, sub, corners):
    """0 for a part a grid fills directly; a pole for an N-pole part; more for anything else."""
    w = ctx.cfg["split_weights"]
    cnt = Region(sub, corners).side_counts()
    if len(corners) == 4 and cnt[0] == cnt[2] and cnt[1] == cnt[3]:
        return 0.0
    if len(corners) in ctx.cfg["npole_sides"] and spoke_counts(cnt):
        return w["npole"]
    if len(sub) % 2 == 0 and len(sub) <= ctx.cfg["loop_any_max"]:
        return w["leftover"]  # a small leftover: virtual corners make it a grid
    return w["not_fillable"] + w["corner_mismatch"] * abs(len(corners) - 4)


@fill_template("split")
def fill_split(ctx, region):
    if region.holes:
        return _cut_hole(ctx, region)
    return _split(ctx, region)


def _split(ctx, region):
    loop, n = region.loop, len(region.loop)
    if n < 6:
        return False
    w = ctx.cfg["split_weights"]
    slack = ctx.cfg["chord_count_slack"]
    pts = [ctx.pt(v) for v in loop]
    corner_set = set(region.corners)

    # Cheap pass: rank every chord by its end angles, length and balance.
    pairs = []
    for i in range(n):
        for j in range(i + 2, n):
            if (i - j) % n < 2:
                continue
            ends = (_end_cost(pts[i - 1], pts[i], pts[(i + 1) % n], pts[j])
                    + _end_cost(pts[j - 1], pts[j], pts[(j + 1) % n], pts[i]))
            pairs.append((_q(ctx, w["end_angle"] * ends + w["chord_length"] * dist(pts[i], pts[j]) / ctx.edge
                             + w["imbalance"] * abs(2 * (j - i) - n) / n), i, j))
    pairs.sort()

    # Full pass on the best few visible chords: chord counts, part corners and fillability.
    edges, cands, pooled = _edges(loop), [], 0
    for base, i, j in pairs:
        # The pool can fill up with short cut-offs that can't shrink a part; it then
        # stretches until one usable chord turns up, so a large region isn't left with none.
        if pooled >= ctx.cfg["split_pair_pool"] and cands:
            break
        if not _visible(ctx, loop[i], loop[j], edges, pts, []):
            continue
        pooled += 1
        pi, pj = pts[i], pts[j]
        a_len = j - i
        length = dist(pi, pj) / ctx.edge
        # Chord counts near the length; parity keeps both parts even, and each part must shrink.
        ks = [k for k in range(max(1, int(length * (1 - slack))), int(length * (1 + slack)) + 2)
              if (a_len + k) % 2 == 0 and a_len + k < n and (n - a_len) + k < n]
        if not ks:
            continue
        # Corners of both parts, known before any chord vertex exists: inherited
        # corners plus chord ends, whose turn only depends on the chord direction.
        a_c = [s for s in range(1, a_len) if i + s in corner_set]
        a_c += [0] * _is_corner(ctx, pj, pi, pts[(i + 1) % n]) \
            + [a_len] * _is_corner(ctx, pts[j - 1], pj, pi)
        b_c = [s for s in range(1, n - a_len) if (j + s) % n in corner_set]
        b_c += [0] * _is_corner(ctx, pi, pj, pts[(j + 1) % n]) \
            + [n - a_len] * _is_corner(ctx, pts[i - 1], pi, pj)
        a_c, b_c = sorted(a_c), sorted(b_c)
        for k in ks:
            score = (base + w["count_stretch"] * abs(k - length) / max(length, 1.0)
                     + _fillable_score(ctx, range(a_len + k), a_c)
                     + _fillable_score(ctx, range(n - a_len + k), b_c))
            cands.append((_q(ctx, score), i, j, k, a_c, b_c))
    cands.sort(key=lambda c: c[:4])

    # Branch wide near the top, narrow deeper down, so backtracking stays bounded.
    width = max(ctx.cfg["min_split_candidates"], ctx.cfg["max_split_candidates"] // ctx.depth)
    for _, i, j, k, a_c, b_c in cands[:width]:
        mark = ctx.mark()
        chord = _chord_points(ctx, loop[i], loop[j], k)          # i -> j
        a_len = j - i
        a_sub = [loop[i + s] for s in range(a_len + 1)] + chord[::-1]
        b_sub = [loop[(j + s) % n] for s in range(n - a_len + 1)] + chord
        # Fill the harder part first so a dead end fails fast.
        parts = sorted([(a_sub, a_c), (b_sub, b_c)], key=lambda p: -_q(ctx, _fillable_score(ctx, *p)))
        if all(fill_region(ctx, Region(sub, c)) for sub, c in parts):
            return True
        ctx.rollback(mark)
    return False


def _cut_hole(ctx, region):
    """Two chords from the first hole to the outer loop split the region in two."""
    hole, rest = region.holes[0], region.holes[1:]
    outer, h = region.loop, hole.loop
    no, nh = len(outer), len(h)
    outer_pts = [ctx.pt(v) for v in outer]
    hp = [ctx.pt(v) for v in h]
    hole_pts = [[ctx.pt(v) for v in hr.loop] for hr in region.holes]
    edges = _edges(outer) + [e for hr in region.holes for e in _edges(hr.loop)]
    w = ctx.cfg["split_weights"]

    # Single chords hole vertex -> outer vertex, ranked by end angles and length.
    singles = []
    for hi in range(nh):
        for oi in range(no):
            ends = (_end_cost(hp[hi - 1], hp[hi], hp[(hi + 1) % nh], outer_pts[oi])
                    + _end_cost(outer_pts[oi - 1], outer_pts[oi], outer_pts[(oi + 1) % no], hp[hi]))
            singles.append((_q(ctx, w["end_angle"] * ends + w["chord_length"] * dist(hp[hi], outer_pts[oi]) / ctx.edge),
                            hi, oi))
    singles.sort()
    good = []
    for s in singles:
        if len(good) >= ctx.cfg["hole_cut_tries"] * 2:
            break
        if _visible(ctx, h[s[1]], outer[s[2]], edges, outer_pts, hole_pts):
            good.append(s)
    # Pairs whose hole ends sit roughly opposite each other.
    pairs = []
    for x, (s1, h1, o1) in enumerate(good):
        for s2, h2, o2 in good[x + 1:]:
            gap = (h1 - h2) % nh
            if o1 == o2 or h1 == h2 or not nh // 4 <= gap <= nh - nh // 4:
                continue
            pairs.append((_q(ctx, s1 + s2 + w["imbalance"] * abs(2 * gap - nh) / nh), h1, o1, h2, o2))
    pairs.sort()

    for _, h1, o1, h2, o2 in pairs[:ctx.cfg["hole_cut_tries"]]:
        if segments_cross(ctx.pt(h[h1]), ctx.pt(outer[o1]), ctx.pt(h[h2]), ctx.pt(outer[o2]), ctx.eps):
            continue
        # Orient so a_sub runs outer o1 -> o2, then hole h2 -> h1.
        outer_a, hole_a = (o2 - o1) % no, (h1 - h2) % nh
        k1 = max(1, int(dist(ctx.pt(h[h1]), ctx.pt(outer[o1])) / ctx.edge + 0.5))
        k2 = _chord_count(ctx, outer[o2], h[h2], outer_a + hole_a + k1)
        mark = ctx.mark()
        c1 = _chord_points(ctx, outer[o1], h[h1], k1)            # o1 -> h1
        c2 = _chord_points(ctx, outer[o2], h[h2], k2)            # o2 -> h2
        a_sub = ([outer[(o1 + s) % no] for s in range(outer_a + 1)] + c2
                 + [h[(h2 + s) % nh] for s in range(hole_a + 1)] + c1[::-1])
        b_sub = ([outer[(o2 + s) % no] for s in range(no - outer_a + 1)] + c1
                 + [h[(h1 + s) % nh] for s in range(nh - hole_a + 1)] + c2[::-1])
        regions = []
        for sub, o_start, o_len, h_start, h_len in ((a_sub, o1, outer_a, h2, hole_a),
                                                    (b_sub, o2, no - outer_a, h1, nh - hole_a)):
            inh = [s for s in range(1, o_len) if (o_start + s) % no in region.corners]
            off = o_len + 1 + len(c1 if sub is b_sub else c2)
            inh += [off + s for s in range(1, h_len) if (h_start + s) % nh in hole.corners]
            ends = [0, o_len, off, off + h_len]
            regions.append(Region(sub, _sub_corners(ctx, sub, inh, ends)))
        for hr in rest:
            target = regions[0] if point_in_polygon(ctx.pt(hr.loop[0]), [ctx.pt(v) for v in a_sub]) \
                else regions[1]
            target.holes.append(hr)
        if all(fill_region(ctx, r) for r in regions):
            return True
        ctx.rollback(mark)
    return False
