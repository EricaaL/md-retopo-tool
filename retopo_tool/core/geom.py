"""Shared 2D/3D geometry helpers. Pure functions on tuples; one owner for each formula."""
import math


def dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def lerp(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def signed_area(pts):
    """Shoelace area of a 2D polygon; positive when CCW."""
    s = 0.0
    for i in range(len(pts)):
        x0, y0 = pts[i][0], pts[i][1]
        x1, y1 = pts[(i + 1) % len(pts)][0], pts[(i + 1) % len(pts)][1]
        s += x0 * y1 - x1 * y0
    return s / 2.0


def poly_area_3d(pts):
    sx = sy = sz = 0.0
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        sx += a[1] * b[2] - a[2] * b[1]
        sy += a[2] * b[0] - a[0] * b[2]
        sz += a[0] * b[1] - a[1] * b[0]
    return math.sqrt(sx * sx + sy * sy + sz * sz) / 2.0


def turn_deg(p0, p1, p2):
    """Unsigned turn at p1 in degrees (0 = straight on)."""
    return abs(signed_turn_deg(p0, p1, p2))


def signed_turn_deg(p0, p1, p2):
    """Turn at p1 in degrees; positive = left turn (convex on a CCW loop)."""
    ax, ay, bx, by = p1[0] - p0[0], p1[1] - p0[1], p2[0] - p1[0], p2[1] - p1[1]
    if (ax == 0 and ay == 0) or (bx == 0 and by == 0):
        return 0.0
    return math.degrees(math.atan2(ax * by - ay * bx, ax * bx + ay * by))


def left_normal(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    return (0.0, 0.0) if n == 0 else (-dy / n, dx / n)


def _orient(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def segments_cross(a, b, c, d, eps):
    """True if segments ab and cd touch or cross anywhere, endpoints included.

    Callers skip pairs that share a vertex id; distinct vertices that touch are a clash.
    """
    d1, d2 = _orient(c, d, a), _orient(c, d, b)
    d3, d4 = _orient(a, b, c), _orient(a, b, d)
    if ((d1 > eps and d2 < -eps) or (d1 < -eps and d2 > eps)) and \
       ((d3 > eps and d4 < -eps) or (d3 < -eps and d4 > eps)):
        return True
    for p, q, r, o in ((c, d, a, d1), (c, d, b, d2), (a, b, c, d3), (a, b, d, d4)):
        if abs(o) <= eps and _on_segment(p, q, r, eps):
            return True
    return False


def _on_segment(p, q, r, eps):
    return (min(p[0], q[0]) - eps <= r[0] <= max(p[0], q[0]) + eps and
            min(p[1], q[1]) - eps <= r[1] <= max(p[1], q[1]) + eps)


def point_in_polygon(p, pts):
    inside = False
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        if (a[1] > p[1]) != (b[1] > p[1]):
            x = a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if x > p[0]:
                inside = not inside
    return inside


def resample(params, values, targets):
    """Piecewise-linear lookup: values at increasing params, sampled at each target param."""
    out, k = [], 0
    for t in targets:
        while k < len(params) - 2 and params[k + 1] < t:
            k += 1
        span = params[k + 1] - params[k]
        f = 0.0 if span <= 0 else min(1.0, max(0.0, (t - params[k]) / span))
        out.append(lerp(values[k], values[k + 1], f))
    return out


def npole_spokes(counts):
    """Spoke counts s for an N-pole fill with side counts c: c_i = s_{i-1} + s_{i+1}.

    Walking the ring two steps at a time gives s_{i+2} = c_{i+1} - s_i, so every s
    on one cycle is (+/-x + const); closing a cycle with an odd number of sign
    flips fixes x. Returns the integer spokes (possibly < 1, which means the
    sides don't fit one pole), or None when singular (e.g. 4 sides) or when a
    cycle's total is odd.
    """
    n = len(counts)
    s = [None] * n
    for start in range(n):
        if s[start] is not None:
            continue
        walk, idx, sign, const = [], start, 1, 0
        while True:
            walk.append((idx, sign, const))
            sign, const = -sign, counts[(idx + 1) % n] - const
            idx = (idx + 2) % n
            if idx == start:
                break
        if sign == 1 or const % 2:
            return None
        x = const // 2
        for i, sg, cs in walk:
            s[i] = sg * x + cs
    return s


def closest_on_triangle(p, a, b, c):
    """Closest point to p on triangle abc in 3D. Returns (distance, (wa, wb, wc)).

    Ericson's region test (Real-Time Collision Detection, 5.1.5)."""
    ab = [b[i] - a[i] for i in range(3)]
    ac = [c[i] - a[i] for i in range(3)]
    ap = [p[i] - a[i] for i in range(3)]
    dot = lambda u, v: u[0] * v[0] + u[1] * v[1] + u[2] * v[2]
    d1, d2 = dot(ab, ap), dot(ac, ap)
    if d1 <= 0 and d2 <= 0:
        w = (1.0, 0.0, 0.0)
    else:
        bp = [p[i] - b[i] for i in range(3)]
        d3, d4 = dot(ab, bp), dot(ac, bp)
        cp = [p[i] - c[i] for i in range(3)]
        d5, d6 = dot(ab, cp), dot(ac, cp)
        vc = d1 * d4 - d3 * d2
        vb = d5 * d2 - d1 * d6
        va = d3 * d6 - d5 * d4
        if d3 >= 0 and d4 <= d3:
            w = (0.0, 1.0, 0.0)
        elif d6 >= 0 and d5 <= d6:
            w = (0.0, 0.0, 1.0)
        elif vc <= 0 and d1 >= 0 and d3 <= 0:
            t = d1 / (d1 - d3)
            w = (1 - t, t, 0.0)
        elif vb <= 0 and d2 >= 0 and d6 <= 0:
            t = d2 / (d2 - d6)
            w = (1 - t, 0.0, t)
        elif va <= 0 and (d4 - d3) >= 0 and (d5 - d6) >= 0:
            t = (d4 - d3) / ((d4 - d3) + (d5 - d6))
            w = (0.0, 1 - t, t)
        else:
            den = 1.0 / (va + vb + vc)
            v, u = vb * den, vc * den
            w = (1 - v - u, v, u)
    q = [w[0] * a[i] + w[1] * b[i] + w[2] * c[i] for i in range(3)]
    return dist(p, q), w
