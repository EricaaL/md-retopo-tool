"""Step 8: FILL template registry. Fill a closed region of the flat patch with quads.

A template is a function (ctx, region) -> bool. It either fills the region and
returns True, or returns False; the dispatcher rolls back anything it added.
Templates are tried in the order of config fill.templates. Templates recurse
only through fill_region, never by importing each other.
"""
import importlib
from dataclasses import dataclass, field

from ..geom import npole_spokes, signed_turn_deg

_TEMPLATES = {}


def fill_template(name):
    def deco(fn):
        _TEMPLATES[name] = fn
        return fn
    return deco


@dataclass
class Region:
    """A loop of patch vertex ids (interior on the left) and the indices of its corners."""
    loop: list
    corners: list
    holes: list = field(default_factory=list)   # list of Region, interior on their left too

    def side_counts(self):
        c, n = self.corners, len(self.loop)
        return [(c[(k + 1) % len(c)] - c[k]) % n or n for k in range(len(c))]

    def sides(self):
        """Vertex-id polylines from each corner to the next, endpoints included."""
        c, n = self.corners, len(self.loop)
        return [[self.loop[(c[k] + s) % n] for s in range(cnt + 1)]
                for k, cnt in enumerate(self.side_counts())]


class FillContext:
    def __init__(self, patch, cfg, eps, edge):
        self.patch, self.cfg, self.eps, self.edge = patch, cfg, eps, edge
        self.attempts = 0
        self.depth = 0

    def pt(self, v):
        return self.patch.points[v]

    def add_point(self, p):
        self.patch.points.append(p)
        return len(self.patch.points) - 1

    def mark(self):
        return len(self.patch.points), len(self.patch.quads)

    def rollback(self, mark):
        del self.patch.points[mark[0]:]
        del self.patch.quads[mark[1]:]

    def angle_corners(self, loop, only=None):
        """Indices whose turn is at least fill.corner_angle_deg (optionally only those in `only`)."""
        thr, n = self.cfg["corner_angle_deg"], len(loop)
        idx = range(n) if only is None else only
        return sorted(i for i in idx
                      if abs(signed_turn_deg(self.pt(loop[i - 1]), self.pt(loop[i]),
                                             self.pt(loop[(i + 1) % n]))) >= thr)


def spoke_counts(counts):
    """Spokes for an N-pole fill (geom.npole_spokes), or None unless every spoke is >= 1."""
    s = npole_spokes(counts)
    return s if s is not None and all(v >= 1 for v in s) else None


def fill_region(ctx, region):
    """Try each template in config order. True when the region is filled."""
    if ctx.depth >= ctx.cfg["max_depth"] or ctx.attempts >= ctx.cfg["max_attempts"]:
        return False
    if len(region.loop) + sum(len(h.loop) for h in region.holes) < 4:
        return False
    ctx.attempts += 1
    ctx.depth += 1
    try:
        for name in ctx.cfg["templates"]:
            mark = ctx.mark()
            if _TEMPLATES[name](ctx, region):
                return True
            ctx.rollback(mark)
        return False
    finally:
        ctx.depth -= 1


def fill_piece(patch, loops, edge, cfg, eps, outer_corners=None, ring_corners=()):
    """Fill the area inside loops[0] and outside loops[1:]. True on success.

    outer_corners: layout corner indices on loops[0]; without them corners are
    found by angle, plus ring_corners (indices where the border ring turned:
    a fill corner anywhere else leaves a pole on the ring).
    """
    ctx = FillContext(patch, cfg, eps, edge)
    holes = [Region(lp, ctx.angle_corners(lp)) for lp in loops[1:]]
    if outer_corners is not None:
        corners = sorted(outer_corners)
    else:
        corners = sorted(set(ctx.angle_corners(loops[0])) | set(ring_corners))
    return fill_region(ctx, Region(loops[0], corners, holes))


# Load phase: declare and register only.
for _name in ("loop", "grid", "npole", "annulus", "split"):
    importlib.import_module(f"{__name__}.{_name}")
