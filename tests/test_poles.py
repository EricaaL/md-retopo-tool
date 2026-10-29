"""Pole clean-up: loop-layout grid corners, ring corners kept by the fill, tidy edits."""
import math

import fixtures as fx
from retopo_tool.core.config import load_config
from retopo_tool.core.fill import fill_piece
from retopo_tool.core.layout import loop_corners
from retopo_tool.core.mesh import FlatPatch
from retopo_tool.core.pipeline import build_model, build_pieces, solve_garment
from retopo_tool.core.tidy import tidy_patch
from retopo_tool.core.topology import build_topology
from retopo_tool.core.validate import check_patch
from retopo_tool.core.weld import normalise_welding


def _build(mesh, cfg):
    mesh, _ = normalise_welding(mesh)
    topo = build_topology(mesh)
    model, _ = build_model(mesh, topo, cfg)
    layouts, res = solve_garment(mesh, topo, model, [], {}, cfg)
    assert res.ok, res.errors
    return layouts, build_pieces(range(len(topo.pieces)), mesh, topo, model, res.counts, None, cfg, layouts, [])


def _rounded_rect(w=10.0, h=4.0, r=1.0, steps=3):
    """A rectangle whose corners turn in steps too small to be segment corners."""
    pts = []
    for cx, cy, a0 in ((w - r, r, -90), (w - r, h - r, 0), (r, h - r, 90), (r, r, 180)):
        for s in range(steps + 1):
            a = math.radians(a0 + 90 * s / steps)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def test_a_rounded_rectangle_without_segment_corners_fills_as_one_grid():
    cfg = load_config("defaults")
    cfg["solver"]["target_edge_length"] = 1.0
    layouts, patches = _build(fx.polygon_piece(_rounded_rect()), cfg)
    assert layouts[(0, 0)].kind == "loop"          # no corner between segments
    assert patches[0].ok and not patches[0].poles
    cfg["layout"]["loop_corner_min_deg"] = 999.0   # without the picked corners: virtual ones, and poles
    _, before = _build(fx.polygon_piece(_rounded_rect()), cfg)
    assert before[0].poles


def test_a_circle_keeps_the_fills_virtual_corners():
    cfg = load_config("defaults")["layout"]
    pts = [(math.cos(2 * math.pi * k / 24), math.sin(2 * math.pi * k / 24)) for k in range(24)]
    assert loop_corners(pts, list(range(24)), cfg, 1) is None


def test_the_fill_turns_where_the_border_ring_turned():
    # A 16-gon turns 22.5 degrees per vertex: no angle corners, so the fill would
    # pick its own. Ring corners at 0, 4, 8, 12 must be the grid's corners instead.
    patch = FlatPatch(0)
    patch.points = [(math.cos(2 * math.pi * k / 16), math.sin(2 * math.pi * k / 16)) for k in range(16)]
    loop = list(range(16))
    assert fill_piece(patch, [loop], 0.4, load_config("defaults")["fill"], 1e-9, ring_corners=[2, 6, 10, 14])
    uses = {v: sum(v in q for q in patch.quads) for v in loop}
    assert [v for v in loop if uses[v] == 1] == [2, 6, 10, 14]


def _patch(points, quads, border):
    p = FlatPatch(0)
    p.points, p.quads = [tuple(q) for q in points], [list(q) for q in quads]
    p.boundary = {0: border + border[:1]}
    return p


def test_tidy_merges_the_two_quads_of_a_doublet():
    #   y          a doublet v=4 between [v,a,x,b] and [v,b,y,a]
    # a v b
    #   x
    p = _patch([(0, 1), (1, 0), (2, 1), (1, 2), (1, 1)], [[4, 0, 1, 2], [4, 2, 3, 0]], [0, 1, 2, 3])
    assert tidy_patch(p, load_config("defaults")["fill"], 1e-9) == 1
    assert p.quads == [[0, 1, 2, 3]] and len(p.points) == 4
    assert check_patch(p, 1e-9).ok


def test_tidy_flips_an_edge_back_to_cancel_two_pole_pairs():
    nx, ny = 4, 3
    vid = {(i, j): j * (nx + 1) + i for j in range(ny + 1) for i in range(nx + 1)}
    pts = [(i, j) for j in range(ny + 1) for i in range(nx + 1)]
    quads = [[vid[i, j], vid[i + 1, j], vid[i + 1, j + 1], vid[i, j + 1]] for j in range(ny) for i in range(nx)]
    border = [vid[i, 0] for i in range(nx)] + [vid[nx, j] for j in range(ny)] \
        + [vid[i, ny] for i in range(nx, 0, -1)] + [vid[0, j] for j in range(ny, 0, -1)]
    # Flip the edge (2,1)-(2,2) to (3,1)-(1,2): two 3-poles and two 5-poles.
    u, v, q1, q2, p1, p2 = vid[2, 1], vid[2, 2], vid[3, 1], vid[3, 2], vid[1, 2], vid[1, 1]
    quads[quads.index([p2, u, v, p1])] = [q1, q2, v, p1]
    quads[quads.index([u, q1, q2, v])] = [p1, p2, u, q1]
    p = _patch(pts, quads, border)
    assert tidy_patch(p, load_config("defaults")["fill"], 1e-9) == 1
    assert sorted(map(sorted, p.quads)) == sorted(map(sorted, [
        [vid[i, j], vid[i + 1, j], vid[i + 1, j + 1], vid[i, j + 1]] for j in range(ny) for i in range(nx)]))


def test_tidy_never_touches_the_border():
    # The same doublet, but v is a border vertex: nothing to do.
    p = _patch([(0, 1), (1, 0), (2, 1), (1, 2), (1, 1)], [[4, 0, 1, 2], [4, 2, 3, 0]], [0, 1, 2, 3])
    p.boundary[1] = [4, 0]
    assert tidy_patch(p, load_config("defaults")["fill"], 1e-9) == 0
