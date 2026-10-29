import pytest

import fixtures as fx
from retopo_tool.core.config import load_config
from retopo_tool.core.geom import signed_area
from retopo_tool.core.mesh import FlatPatch, RingSpec
from retopo_tool.core.pipeline import affected_pieces, build_pieces
from retopo_tool.core.segments import build_segments
from retopo_tool.core.solver import solve_counts
from retopo_tool.core.symmetry import detect_mirrors
from retopo_tool.core.topology import build_topology
from retopo_tool.core.validate import check_patch
from retopo_tool.core.weld import normalise_welding


def build(mesh, target=1.0, user_counts=None, specs=None, pieces=None):
    mesh, _ = normalise_welding(mesh)
    topo = build_topology(mesh)
    model = build_segments(mesh, topo)
    res = solve_counts(model, user_counts=user_counts, cfg=fx.solver_cfg(target))
    assert res.ok, res.errors
    ids = range(len(topo.pieces)) if pieces is None else pieces
    return build_pieces(ids, mesh, topo, model, res.counts, specs), model, res


def assert_clean(patch, model, res):
    assert patch.ok, patch.errors
    assert patch.quads and all(len(q) == 4 and len(set(q)) == 4 for q in patch.quads)
    assert all(signed_area([patch.points[v] for v in q]) > 0 for q in patch.quads)
    for s in model.segments:
        if s.piece == patch.piece:
            assert len(patch.boundary[s.id]) - 1 == res.counts[s.id]


def one(mesh, **kw):
    out, model, res = build(mesh, **kw)
    assert_clean(out[0], model, res)
    return out[0]


def test_rectangle_is_a_pole_free_grid_with_ring():
    p = one(fx.rectangle(6, 4))
    assert p.rings == 1 and not p.poles and len(p.quads) == 24
    assert all(abs(signed_area([p.points[v] for v in q]) - 1.0) < 1e-9 for q in p.quads)


def test_short_sides_drop_the_ring_with_warning():
    p = one(fx.rectangle(4, 2))
    assert p.rings == 0 and len(p.quads) == 8 and not p.poles
    assert any("too short for border ring" in w for w in p.warnings)


def test_triangle_gets_one_three_pole():
    p = one(fx.triangle())
    assert sorted(p.poles.values()) == [3]


def test_pentagon_gets_one_five_pole():
    p = one(fx.pentagon(), target=0.3)
    assert sorted(p.poles.values()) == [5]


def test_l_shape_splits_without_poles():
    p = one(fx.big_l())
    assert not p.poles


def test_disc_gets_four_three_poles():
    p = one(fx.disc())
    assert sorted(p.poles.values()) == [3, 3, 3, 3]


def test_panel_with_hole():
    one(fx.panel_with_hole())


def test_equal_count_hole_band_is_bridged():
    one(fx.grid_piece(6, 6, lambda i, j: (i, j, 0), skip={(i, j) for i in (2, 3) for j in (2, 3)}))


def test_flipped_uv_piece_fills_and_is_flagged():
    p = one(fx.flipped_uv_rectangle())
    assert p.uv_flipped and not p.poles


def test_uneven_triangle_falls_back_to_split():
    _, model, _ = build(fx.triangle())
    ids = model.loops[(0, 0)]
    p = one(fx.triangle(), user_counts={ids[0]: 8, ids[1]: 3, ids[2]: 3})
    assert len(p.quads) > 0


def test_unfillable_piece_names_its_segments():
    _, model, _ = build(fx.half_disc())
    ids = model.loops[(0, 0)]
    assert len(ids) == 2
    out, _, _ = build(fx.half_disc(), user_counts={ids[0]: 1, ids[1]: 1})
    assert not out[0].ok and f"{ids[0]}=1" in out[0].errors[0]


def test_seam_sides_get_identical_3d_points():
    out, model, res = build(fx.tube(nx=6, ny=4))
    for s in model.segments:
        if s.tag != -1:
            assert_clean(out[s.piece], model, res)
            t = model.segments[s.links[0]]
            mine = [out[s.piece].fixed3d[v] for v in out[s.piece].boundary[s.id]]
            theirs = [out[t.piece].fixed3d[v] for v in out[t.piece].boundary[t.id]]
            assert len(mine) == len(theirs)
            for a, b in zip(mine, theirs[::-1]):
                assert max(abs(x - y) for x, y in zip(a, b)) < 1e-9


def test_manual_ring_count():
    p = one(fx.rectangle(10, 10), specs={0: RingSpec(rings=2)})
    assert p.rings == 2 and not p.poles


def test_ring_count_out_of_range_is_error():
    out, _, _ = build(fx.rectangle(10, 10), specs={0: RingSpec(rings=4)})
    assert not out[0].ok and "ring count" in out[0].errors[0]


def test_incremental_rebuild_touches_only_affected_pieces():
    mesh, _ = normalise_welding(fx.mirrored_pair())
    topo = build_topology(mesh)
    model = build_segments(mesh, topo)
    assert affected_pieces(0, model, detect_mirrors(model, topo, mesh=mesh)) == {0, 1}
    assert affected_pieces(0, model, []) == {0}
    out, _, _ = build(fx.tube(nx=6, ny=4), pieces=[1])
    assert list(out) == [1]


def test_check_patch_rejects_a_triangle():
    p = FlatPatch(0, points=[(0, 0), (1, 0), (0, 1)], quads=[[0, 1, 2]])
    assert not check_patch(p, load_config("defaults")["geometry"]["eps"]).ok


def test_check_patch_rejects_a_folded_quad():
    p = FlatPatch(0, points=[(0, 0), (1, 0), (1, 1), (0, 1)], quads=[[0, 3, 2, 1]])
    assert not check_patch(p, load_config("defaults")["geometry"]["eps"]).ok


def test_dense_hole_panel_is_pole_free():
    # Chords extend the hole's sides to the border, giving corner and side rectangles.
    p = one(fx.panel_with_hole(12, 4, 7), target=0.7)
    assert not p.poles


@pytest.mark.parametrize("target", [1.0, 2.0, 3.0])
def test_bodice_fills_with_few_poles(target):
    p = one(fx.bodice(), target=target)
    assert len(p.poles) <= 8, p.poles


def test_a_dense_bodice_still_fills():
    # Finer than the unit edge: the split must find chords past the short cut-offs
    # that fill its chord pool, and recurse deep enough for ~1,300 quads.
    p = one(fx.bodice(), target=0.7)
    assert len(p.quads) > 1000


def test_spoke_counts():
    from retopo_tool.core.fill import spoke_counts
    assert spoke_counts([4, 4, 4]) == [2, 2, 2]
    assert spoke_counts([4] * 5) == [2] * 5
    assert spoke_counts([2] * 6) == [1] * 6
    assert spoke_counts([6, 1, 1]) is None        # a spoke would be negative
    assert spoke_counts([3] * 6) is None          # each 3-cycle sums odd
    assert spoke_counts([2, 2, 2, 2]) is None     # 4 sides: singular
