"""Step 5b: choose the layout corners of every border loop.

A segment corner is a layout candidate when the outline turns at least
layout.corner_min_deg there, measured over a window of the two neighbouring
segments (so MD's short edges don't add noise). From the candidates:
- 4 strong corners (>= layout.strong_deg) -> quad on those;
- 3, 5 or 6 strong corners -> npole on those;
- otherwise 4+ candidates -> quad on the 4 that turn most and give the most
  equal opposite sides; 3 -> npole; fewer -> loop.
A loop with a reflex corner, and every hole loop, is "free".

The solver balances counts so a quad layout's opposite sides match (a clean
grid), and keeps every side long enough for the border ring.
"""
import itertools

from .config import load_config
from .geom import dist, signed_area, signed_turn_deg
from .mesh import Layout


def _point_at(poly, frac):
    """Point at a fraction of a polyline's arc length."""
    lengths = [dist(a, b) for a, b in zip(poly, poly[1:])]
    total = sum(lengths)
    if total == 0:
        return poly[-1]
    target, run = total * frac, 0.0
    for (a, b), l in zip(zip(poly, poly[1:]), lengths):
        if run + l >= target and l > 0:
            t = (target - run) / l
            return tuple(a[k] + (b[k] - a[k]) * t for k in range(len(a)))
        run += l
    return poly[-1]


def segment_uv(mesh, seg):
    return [mesh.uvs[t] for t in seg.uv_ids]


def corner_turns(mesh, model, ids, window):
    """Signed UV turn at the start of each segment of a loop, over a window."""
    turns = []
    for k, sid in enumerate(ids):
        prev = segment_uv(mesh, model.segments[ids[k - 1]])
        cur = segment_uv(mesh, model.segments[sid])
        a = _point_at(prev[::-1], window)   # back along the previous segment
        b = cur[0]
        c = _point_at(cur, window)
        turns.append(signed_turn_deg(a, b, c))
    return turns


def _side_lengths(model, ids, starts):
    out = []
    for k, s in enumerate(starts):
        e = starts[(k + 1) % len(starts)]
        span = (e - s) % len(ids) or len(ids)
        out.append(sum(model.segments[ids[(s + j) % len(ids)]].length for j in range(span)))
    return out


def _sides(ids, starts):
    return [[ids[(s + j) % len(ids)] for j in range(((starts[(k + 1) % len(starts)] - s) % len(ids)) or len(ids))]
            for k, s in enumerate(starts)]


def plan_layouts(mesh, topo, model, cfg=None, rings=1):
    """(piece, loop) -> Layout for every border loop."""
    cfg = cfg or load_config("defaults")["layout"]
    out = {}
    for piece in topo.pieces:
        for li in range(len(piece.loops)):
            ids = model.loops[(piece.id, li)]
            turns = corner_turns(mesh, model, ids, cfg["window"])
            if li > 0 or any(t <= -cfg["reflex_deg"] for t in turns):
                out[(piece.id, li)] = Layout(piece.id, li, "free",
                                             segment_min=_segment_min(ids, turns, cfg, rings, sign=-1 if li else 1))
                continue
            cand = [k for k, t in enumerate(turns) if t >= cfg["corner_min_deg"]]
            strong = [k for k in cand if turns[k] >= cfg["strong_deg"]]
            if len(strong) == 4:
                kind, starts = "quad", strong
            elif len(strong) in (3, 5, 6):
                kind, starts = "npole", strong
            elif len(cand) >= 4:
                kind = "quad"
                pool = sorted(sorted(cand, key=lambda k: -turns[k])[:cfg["max_candidates"]])
                best = None
                for combo in itertools.combinations(pool, 4):
                    lens = _side_lengths(model, ids, combo)
                    unequal = (abs(lens[0] - lens[2]) / max(lens[0] + lens[2], 1e-12)
                               + abs(lens[1] - lens[3]) / max(lens[1] + lens[3], 1e-12))
                    score = sum(turns[k] for k in combo) / 90.0 - cfg["side_balance_weight"] * unequal
                    if best is None or score > best[0]:
                        best = (score, list(combo))
                starts = best[1]
            elif len(cand) == 3:
                kind, starts = "npole", cand
            else:
                out[(piece.id, li)] = Layout(piece.id, li, "loop",
                                             segment_min=_segment_min(ids, turns, cfg, rings))
                continue
            sides = _sides(ids, starts)
            out[(piece.id, li)] = Layout(piece.id, li, kind, sides, [2 * rings + 1] * len(sides))
    return out


def _segment_min(ids, turns, cfg, rings, sign=1):
    """Least count per segment so the ring keeps an inner edge: a convex corner at
    either end takes one edge per ring. Hole loops run the other way round."""
    convex = [sign * t >= cfg["ring_corner_deg"] for t in turns]
    n = len(ids)
    return {sid: 1 + rings * (convex[k] + convex[(k + 1) % n]) for k, sid in enumerate(ids)}


def region_minimums(mesh, model, cfg, rings):
    """Least counts on pieces split by attach guides (model.regions): the ring rule
    of _segment_min on every region cycle (no ring under an opening), and at
    least 4 edges round each cycle, the least a quad fill can close."""
    out = {}
    for regs in model.regions.values():
        for reg in regs:
            r = rings if reg["inside"] is None else 0
            for c, cyc in enumerate(reg["cycles"]):
                polys = []
                for sid, rev in cyc:
                    seg = model.segments[sid]
                    uv = seg.uv_points if seg.uv_points is not None else segment_uv(mesh, seg)
                    polys.append(uv[::-1] if rev else uv)
                pts = [p for poly in polys for p in poly[:-1]]
                sign = 1 if (signed_area(pts) > 0) == (c == 0) else -1   # interior on the left
                turns = [signed_turn_deg(_point_at(polys[k - 1][::-1], cfg["window"]), polys[k][0],
                                         _point_at(polys[k], cfg["window"])) for k in range(len(polys))]
                ids = [sid for sid, _ in cyc]
                each = -(-4 // len(ids))
                for sid, m in _segment_min(ids, turns, cfg, r, sign).items():
                    out[sid] = max(out.get(sid, 1), m, each)
    return out


def loop_corners(points, loop, cfg, rings):
    """Grid corners for a "loop" layout, picked among the sampled border vertices.

    A loop layout has fewer than 3 corners between segments, but its outline may
    still turn sharply inside a segment (a lens, a tapered strap). Four border
    vertices with side counts (a, b, a, b) make the inside one pole-free grid,
    and the ring then turns at the same vertices. The turn at each vertex is
    measured over layout.loop_corner_window samples either side. Returns the 4
    vertex ids, or None when no set turns at least layout.loop_corner_min_deg,
    and layout.loop_corner_mean_ratio times a circle's turn, at every corner
    (a circle: the fill's virtual corners do better there).
    """
    n = len(loop)
    w = cfg["loop_corner_window"]
    if n % 2 or n < 4 * (2 * rings + 1) or n <= 2 * w:
        return None
    turn = [signed_turn_deg(points[loop[i - w]], points[loop[i]], points[loop[(i + w) % n]]) for i in range(n)]
    # A circle turns w * 360 / n at every vertex: a corner must stand out from that.
    least = max(cfg["loop_corner_min_deg"], cfg["loop_corner_mean_ratio"] * 360.0 * w / n)
    half, best = n // 2, None
    side = 2 * rings + 1
    for s in range(n):
        if turn[s] < least:
            continue
        for a in range(side, half - side + 1):
            pos = (s, (s + a) % n, (s + half) % n, (s + half + a) % n)
            t = [turn[k] for k in pos]
            if min(t) < least:
                continue
            score = round(t[0] + t[1] + t[2] + t[3], 6)
            if best is None or score > best[0]:
                best = (score, pos)
    return None if best is None else [loop[k] for k in best[1]]


def ring_corner_segments(layouts):
    """Segment ids that start a layout corner on quad/npole loops (where rings put corner quads)."""
    return {(lay.piece, lay.loop): {side[0] for side in lay.sides}
            for lay in layouts.values() if lay.kind in ("quad", "npole")}
