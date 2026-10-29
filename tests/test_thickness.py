"""core/thickness.py: inward shell, rim with support loops."""
from collections import Counter

import pytest

import fixtures as fx
from retopo_tool.core.thickness import thicken


def uses(mesh):
    u, d = Counter(), Counter()
    for f in mesh.faces:
        for k in range(len(f)):
            a, b = f[k], f[(k + 1) % len(f)]
            u[(min(a, b), max(a, b))] += 1
            d[(a, b)] += 1
    return u, d


def test_a_panel_becomes_a_closed_quad_shell_with_support_loops():
    m = fx.rectangle(nx=4, ny=2)
    open_edges = sum(1 for n in uses(m)[0].values() if n == 1)
    out, face_piece, rim = thicken(m, 0.2, support=True, face_piece=[7] * len(m.faces))
    u, d = uses(out)
    assert all(len(f) == 4 for f in out.faces)
    assert set(u.values()) == {2} and max(d.values()) == 1      # closed, manifold, consistently wound
    assert len(rim) == 3 * open_edges
    assert len(out.faces) == 2 * len(m.faces) + len(rim)
    assert set(face_piece) == {7}


def test_without_support_loops_the_rim_is_one_quad_high():
    m = fx.rectangle(nx=4, ny=2)
    open_edges = sum(1 for n in uses(m)[0].values() if n == 1)
    _, _, rim = thicken(m, 0.2, support=False)
    assert len(rim) == open_edges


def test_the_shell_grows_inward_against_the_face_normals():
    m = fx.rectangle(nx=4, ny=2)            # faces CCW in XY: normals point +Z
    out, _, _ = thicken(m, 0.25)
    n = len(m.positions)
    assert all(abs(out.positions[n + v][2] - (p[2] - 0.25)) < 1e-9 for v, p in enumerate(m.positions))


def test_a_closed_tube_seam_does_not_crack():
    from retopo_tool.core.pipeline import build_output, build_pieces
    from retopo_tool.core.segments import build_segments
    from retopo_tool.core.solver import solve_counts
    from retopo_tool.core.topology import build_topology
    from retopo_tool.core.weld import normalise_welding
    mesh, _ = normalise_welding(fx.tube(nx=6, ny=3))
    topo = build_topology(mesh)
    model = build_segments(mesh, topo)
    res = solve_counts(model, cfg=fx.solver_cfg())
    out, fp = build_output(build_pieces(range(2), mesh, topo, model, res.counts), mesh, topo)
    thick, _, rim = thicken(out, 0.1, face_piece=fp)
    u, d = uses(thick)
    assert set(u.values()) == {2} and max(d.values()) == 1


def test_bad_values_are_refused():
    with pytest.raises(ValueError):
        thicken(fx.rectangle(), 0)
    with pytest.raises(ValueError):
        thicken(fx.rectangle(), 0.1, support_ratio=0.6)
