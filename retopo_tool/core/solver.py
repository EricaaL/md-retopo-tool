"""Step 6: solve quad counts per segment.

Linked segments (seam partners, mirror twins, template-forced sides) share one
count. User-set counts are never changed. The solver only adjusts counts the
user has not set, to make every border loop's total even (a quad fill needs it).
Parity is solved exactly as a linear system over GF(2).

With layouts (layout.plan_layouts), a bounded local search then nudges the
counts the user has not set so each quad layout's opposite sides match (a
clean grid, no poles), every side stays long enough for the border ring, and
every loop stays even, while drifting as little as possible from the
length-based counts. Pieces it can't balance are named in a warning.
"""
from collections import defaultdict

from .config import load_config
from .geom import npole_spokes
from .mesh import SolveResult, piece_label

_SIDE_RULES = {}  # number of sides -> fn(segment ids of the loop) -> pairs forced equal


def side_rule(sides):
    def deco(fn):
        _SIDE_RULES[sides] = fn
        return fn
    return deco


@side_rule(4)
def _four_sided(ids):
    return [(ids[0], ids[2]), (ids[1], ids[3])]  # opposite sides of a grid match


class _UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.p[max(a, b)] = min(a, b)


def _solve_gf2(rows, ncols):
    pivots = []
    for mask, rhs in rows:
        for col, pm, pr in pivots:
            if mask >> col & 1:
                mask ^= pm
                rhs ^= pr
        if mask == 0:
            if rhs:
                return None
            continue
        col = mask.bit_length() - 1
        pivots = [(c, pm ^ mask, pr ^ rhs) if pm >> col & 1 else (c, pm, pr)
                  for c, pm, pr in pivots] + [(col, mask, rhs)]
    x = [0] * ncols
    for col, _, pr in pivots:
        x[col] = pr
    return x


def solve_counts(model, mirrors=(), user_counts=None, cfg=None, layouts=None, min_counts=None, targets=None):
    """layouts: (piece, loop) -> Layout; min_counts: segment -> least count;
    targets: piece -> target edge length overriding solver.target_edge_length."""
    cfg = cfg or load_config("defaults")["solver"]
    user_counts = user_counts or {}
    min_counts = min_counts or {}
    targets = targets or {}
    res = SolveResult()
    segs = model.segments
    minc = cfg["min_count"]
    target = cfg["target_edge_length"]
    eps = load_config("defaults")["geometry"]["eps"]

    for sid, c in user_counts.items():
        if not 0 <= sid < len(segs):
            res.errors.append(f"Count set on unknown segment {sid}.")
        elif not isinstance(c, int) or c < minc:
            res.errors.append(f"Segment {sid}: count {c!r} must be a whole number >= {minc}.")
    if res.errors:
        return res

    if not target > 0:
        res.errors.append(f"solver.target_edge_length must be > 0, got {target!r}.")
        return res
    res.initial = {s.id: max(minc, int(s.length / targets.get(s.piece, target) + 0.5)) for s in segs}

    uf = _UF(len(segs))
    for s in segs:
        for t in s.links:
            uf.union(s.id, t)
    for m in mirrors:
        for a, b in m.seg_map.items():
            uf.union(a, b)
    if not layouts:  # with layouts, the layout decides the sides and balancing evens them
        for ids in model.loops.values():
            rule = _SIDE_RULES.get(len(ids))
            if rule:
                for a, b in rule(ids):
                    uf.union(a, b)

    groups = {}
    for s in segs:
        groups.setdefault(uf.find(s.id), []).append(s.id)

    value, fixed, raw, gmin = {}, set(), {}, {}
    for g, members in groups.items():
        gmin[g] = max([minc] + [min_counts.get(m, minc) for m in members])
        user = {user_counts[m] for m in members if m in user_counts}
        if len(user) > 1:
            names = ", ".join(f"{m}={user_counts[m]}" for m in members if m in user_counts)
            res.errors.append(
                f"Segments {names} must share one count (seam, mirror or opposite sides) "
                "but were set to different values. Set them to the same number or unlink the mirror.")
            continue
        raw[g] = sum(res.initial[m] for m in members) / len(members)
        if user:
            value[g] = user.pop()
            fixed.add(g)
            if value[g] < gmin[g]:
                res.warnings.append(
                    f"Segments {members}: count {value[g]} is below {gmin[g]}, the least that keeps "
                    "the border ring; that side will get a thinner outline.")
        else:
            value[g] = max(gmin[g], int(raw[g] + 0.5))
    if res.errors:
        return res

    free = sorted((g for g in groups if g not in fixed), key=lambda g: -abs(raw[g] - value[g]))
    if len(free) > cfg["max_gf2_columns"]:
        res.errors.append(f"Too many adjustable seams ({len(free)}) for the parity solver.")
        return res
    col = {g: i for i, g in enumerate(free)}

    rows, row_loops = [], []
    for (pid, li), ids in model.cycles().items():
        mask, total = 0, 0
        for sid in ids:
            g = uf.find(sid)
            total += value[g]
            if g in col:
                mask ^= 1 << col[g]
        rows.append((mask, total & 1))
        row_loops.append((pid, li, ids))

    sol = _solve_gf2(rows, len(free))
    if sol is None:
        bad = [f"piece {piece_label(p)} loop {l} (segments {ids})" for (m, r), (p, l, ids) in zip(rows, row_loops)
               if m == 0 and r]
        res.errors.append(
            "Cannot make every border loop even without changing counts you set: "
            + ("; ".join(bad) if bad else "the set counts conflict across linked pieces")
            + ". Change one of the user-set counts on that loop by 1.")
        return res

    for g, i in col.items():
        if sol[i]:
            up = abs(value[g] + 1 - raw[g])
            down = abs(value[g] - 1 - raw[g])
            value[g] += -1 if (down < up and value[g] - 1 >= gmin[g]) else 1

    if layouts:
        _balance(model, uf, value, fixed, raw, gmin, layouts, cfg, res)

    res.counts = {s.id: value[uf.find(s.id)] for s in segs}
    res.warnings.extend(model.warnings)

    ratio = cfg["seam_uv_length_ratio_warn"]
    for s in segs:
        for t in s.links:
            if s.id < t and s.uv_points is None and segs[t].uv_points is None:
                a, b = s.uv_length, segs[t].uv_length
                if max(a, b) > ratio * max(min(a, b), eps):
                    res.warnings.append(
                        f"Seam between segments {s.id} and {t}: pattern lengths differ "
                        f"({a:.2f} vs {b:.2f}). Check ease or the sewing in MD; counts still match.")
    return res


def _balance(model, uf, value, fixed, raw, gmin, layouts, cfg, res):
    """Local search over the free groups; see the module docstring."""
    w = cfg["balance_weights"]
    loops = [[uf.find(s) for s in ids] for ids in model.cycles().values()]
    quads, mins, npoles = [], [], []
    for lay in layouts.values():
        sides = [[uf.find(s) for s in side] for side in lay.sides]
        if lay.kind == "quad" and len(sides) == 4:
            quads.append((lay.piece, sides))
        elif lay.kind == "npole":
            npoles.append((lay.piece, sides))
        for side, m in zip(sides, lay.side_min):
            mins.append((side, m))
        for sid, m in lay.segment_min.items():
            mins.append(([uf.find(sid)], m))
    touch = defaultdict(lambda: ([], [], [], []))  # group -> (loop, quad, min, npole idx)
    for i, gs in enumerate(loops):
        for g in set(gs):
            touch[g][0].append(i)
    for i, (_, sides) in enumerate(quads):
        for g in {g for side in sides for g in side}:
            touch[g][1].append(i)
    for i, (side, _) in enumerate(mins):
        for g in set(side):
            touch[g][2].append(i)
    for i, (_, sides) in enumerate(npoles):
        for g in {g for side in sides for g in side}:
            touch[g][3].append(i)

    def loop_cost(i):
        return w["odd_loop"] * (sum(value[g] for g in loops[i]) % 2)

    def quad_cost(i):
        t = [sum(value[g] for g in side) for side in quads[i][1]]
        return w["unbalanced"] * (abs(t[0] - t[2]) + abs(t[1] - t[3]))

    def min_cost(i):
        side, m = mins[i]
        return w["below_min"] * max(0, m - sum(value[g] for g in side))

    def npole_cost(i):
        # One pole fits only if every spoke is >= 1 (for 3 sides: each side
        # shorter than the other two together). Odd totals are the parity term's job.
        s = npole_spokes([sum(value[g] for g in side) for side in npoles[i][1]])
        return 0.0 if s is None else w["unbalanced"] * sum(max(0, 1 - x) for x in s)

    def drift(g):
        return w["drift"] * abs(value[g] - raw[g]) / max(raw[g], 1.0)

    def local(gs):
        li, qi, mi, ni = set(), set(), set(), set()
        for g in gs:
            a, b, c, d = touch[g]
            li.update(a), qi.update(b), mi.update(c), ni.update(d)
        return (sum(loop_cost(i) for i in li) + sum(quad_cost(i) for i in qi)
                + sum(min_cost(i) for i in mi) + sum(npole_cost(i) for i in ni) + sum(drift(g) for g in gs))

    def delta(moves):
        gs = [g for g, _ in moves]
        before = local(gs)
        for g, d in moves:
            value[g] += d
        after = local(gs)
        for g, d in moves:
            value[g] -= d
        return after - before

    start = dict(value)
    free = [g for g in value if g not in fixed]
    ok = lambda g, d: value[g] + d >= gmin[g]
    for _ in range(cfg["balance_max_steps"]):
        best = None
        for g in free:
            for d in (1, -1, 2, -2):  # +-2 keeps parity on its own
                if ok(g, d):
                    dl = delta([(g, d)])
                    if dl < -1e-9 and (best is None or dl < best[0]):
                        best = (dl, [(g, d)])
        if best is None:
            # No single step helps: try pairs inside one violated constraint.
            hot = set()
            for i, (_, sides) in enumerate(quads):
                if quad_cost(i) > 0:
                    hot.update(g for side in sides for g in side if g not in fixed)
            for i in range(len(loops)):
                if loop_cost(i) > 0:
                    hot.update(g for g in loops[i] if g not in fixed)
            for i, (_, sides) in enumerate(npoles):
                if npole_cost(i) > 0:
                    hot.update(g for side in sides for g in side if g not in fixed)
            hot = sorted(hot)
            for x, g1 in enumerate(hot):
                for g2 in hot[x + 1:]:
                    for d1 in (1, -1):
                        for d2 in (1, -1):
                            if ok(g1, d1) and ok(g2, d2):
                                dl = delta([(g1, d1), (g2, d2)])
                                if dl < -1e-9 and (best is None or dl < best[0]):
                                    best = (dl, [(g1, d1), (g2, d2)])
        if best is None:
            break
        for g, d in best[1]:
            value[g] += d

    if any(loop_cost(i) for i in range(len(loops))):
        value.update(start)  # never trade parity for balance: keep the parity-correct counts
        res.warnings.append("Balancing opposite sides would leave a border loop odd; kept the unbalanced counts.")
    for piece, sides in quads:
        t = [sum(value[g] for g in side) for side in sides]
        if t[0] != t[2] or t[1] != t[3]:
            res.warnings.append(
                f"Piece {piece_label(piece)}: opposite sides have {t[0]} vs {t[2]} and {t[1]} vs {t[3]} edges "
                "(blocked by counts you set or by its seams); the fill adds poles there.")
