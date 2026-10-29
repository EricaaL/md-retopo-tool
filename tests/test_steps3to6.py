import pytest

import fixtures as fx
from retopo_tool.core.segments import build_segments
from retopo_tool.core.solver import solve_counts
from retopo_tool.core.symmetry import detect_mirrors
from retopo_tool.core.topology import build_topology
from retopo_tool.core.weld import normalise_welding


def run(mesh):
    mesh, _ = normalise_welding(mesh)
    topo = build_topology(mesh)
    model = build_segments(mesh, topo)
    return topo, model


def solve(model, mirrors=(), user_counts=None, target=1.0):
    return solve_counts(model, mirrors, user_counts, cfg=fx.solver_cfg(target))


def counts_of(res, model, piece=0, loop=0):
    return [res.counts[i] for i in model.loops[(piece, loop)]]


def test_rectangle():
    topo, model = run(fx.rectangle(4, 2))
    assert len(topo.pieces) == 1 and not topo.seam_edges
    assert [s.mesh_edges for s in model.segments] == [4, 2, 4, 2]
    res = solve(model)
    assert res.ok and counts_of(res, model) == [4, 2, 4, 2]


def test_rectangle_opposite_sides_follow_user_count():
    _, model = run(fx.rectangle(4, 2))
    res = solve(model, user_counts={0: 6})
    assert counts_of(res, model) == [6, 2, 6, 2]


def test_tube_pieces_and_seams():
    topo, model = run(fx.tube())
    assert len(topo.pieces) == 2 and len(topo.seam_edges) == 4
    sewn = [s for s in model.segments if s.tag != -1]
    assert len(sewn) == 4 and all(len(s.links) == 1 for s in sewn)
    assert all(model.segments[s.links[0]].piece != s.piece for s in sewn)


def test_tube_seam_partner_follows_user_count():
    _, model = run(fx.tube())
    sewn = next(s for s in model.segments if s.tag != -1 and s.piece == 0)
    res = solve(model, user_counts={sewn.id: 3})
    assert res.ok
    assert res.counts[sewn.links[0]] == 3


def test_seam_pattern_length_mismatch_warns_not_blocks():
    _, model = run(fx.tube(b_uv_scale=(1, 3)))
    res = solve(model)
    assert res.ok and any("pattern lengths differ" in w for w in res.warnings)


def test_mirror_detection_shares_counts():
    mesh, _ = normalise_welding(fx.mirrored_pair())
    topo = build_topology(mesh)
    model = build_segments(mesh, topo)
    pairs = detect_mirrors(model, topo, mesh=mesh)
    assert len(pairs) == 1 and (pairs[0].a, pairs[0].b) == (0, 1)
    res = solve(model, pairs, user_counts={model.loops[(0, 0)][0]: 5})
    assert counts_of(res, model, 0) == counts_of(res, model, 1)
    assert counts_of(res, model, 0)[0] == 5


def test_unlinked_mirror_is_independent():
    _, model = run(fx.mirrored_pair())
    res = solve(model, [], user_counts={model.loops[(0, 0)][0]: 5})
    assert counts_of(res, model, 1)[0] == 3


def test_l_shape_has_six_segments_and_even_total():
    _, model = run(fx.l_shape(2))
    assert len(model.segments) == 6
    res = solve(model)
    assert sum(counts_of(res, model)) % 2 == 0


def test_parity_fix_never_touches_user_counts():
    _, model = run(fx.l_shape(2))
    ids = model.loops[(0, 0)]
    res = solve(model, user_counts={ids[0]: 3})
    assert res.ok and res.counts[ids[0]] == 3
    assert sum(counts_of(res, model)) % 2 == 0


def test_parity_unsatisfiable_names_segments():
    _, model = run(fx.l_shape(2))
    ids = model.loops[(0, 0)]
    res = solve(model, user_counts={i: 1 for i in ids[:5]} | {ids[5]: 2})
    assert not res.ok and "segments" in res.errors[0]


def test_pentagon_gets_even_total_without_triangles():
    _, model = run(fx.pentagon())
    assert len(model.segments) == 5
    res = solve(model)
    assert res.ok and sum(counts_of(res, model)) % 2 == 0


def test_conflicting_user_counts_on_linked_sides():
    _, model = run(fx.rectangle())
    res = solve(model, user_counts={0: 4, 2: 6})
    assert not res.ok and "same number" in res.errors[0]


def test_bad_user_count_rejected():
    _, model = run(fx.rectangle())
    assert not solve(model, user_counts={0: 0}).ok
    assert not solve(model, user_counts={99: 2}).ok


def test_nonmanifold_edge_raises():
    mesh = fx.merge(fx.rectangle(1, 1), fx.rectangle(1, 1), fx.rectangle(1, 1))
    mesh, _ = normalise_welding(mesh)
    with pytest.raises(ValueError, match="Non-manifold"):
        build_topology(mesh)


def test_initial_counts_follow_target_edge_length():
    _, model = run(fx.rectangle(4, 2))
    assert counts_of(solve(model, target=0.5), model) == [8, 4, 8, 4]
    assert counts_of(solve(model, target=2.0), model) == [2, 1, 2, 1]


def test_bad_target_edge_length_rejected():
    _, model = run(fx.rectangle())
    assert not solve(model, target=0).ok


def test_pinched_insert_links_all_three_pieces():
    topo, model = run(fx.pinched_insert())
    assert len(topo.pieces) == 3
    assert any(len(se.faces) == 3 for se in topo.seam_edges.values())
    assert any("share" in w for w in topo.warnings)
    base_seam = next(s for s in model.segments if s.piece == 0 and len(s.partners) == 2)
    assert sorted(model.segments[t].piece for t in base_seam.links) == [1, 2]
    res = solve(model, user_counts={base_seam.id: 6})
    assert res.ok and all(res.counts[t] == 6 for t in base_seam.links)


def test_three_way_edge_inside_a_piece_is_rejected():
    # A flap whose faces hang off an interior edge of another piece.
    base = fx.grid_piece(2, 1, lambda i, j: (i, j, 0))
    flap = fx.grid_piece(1, 1, lambda i, j: (1, j, i), uv_origin=(100, 0))
    with pytest.raises(ValueError, match="inside a pattern piece"):
        run(fx.merge(base, flap))


def test_fold_seam_splits_at_the_fold_and_links_both_halves():
    _, model = run(fx.folded_cone())
    sewn = [s for s in model.segments if s.tag == 0]
    assert len(sewn) == 2 and sewn[0].links == [sewn[1].id] and sewn[1].links == [sewn[0].id]
    res = solve(model, user_counts={sewn[0].id: 3})
    assert res.counts[sewn[1].id] == 3


def test_gapped_nonconforming_seam_is_found_and_linked():
    topo, model = run(fx.gapped_tube())
    assert not topo.seam_edges and topo.near_seams          # nothing welds exactly
    sewn = [s for s in model.segments if s.tag != -1]
    assert len(sewn) == 4 and all(len(s.links) == 1 for s in sewn)
    assert all(model.segments[s.links[0]].piece != s.piece for s in sewn)
    res = solve(model, user_counts={sewn[0].id: 7})
    assert res.ok and res.counts[sewn[0].links[0]] == 7


def test_multiway_seam_with_one_side_across_a_gap_agrees_on_partners():
    _, model = run(fx.pinched_insert(base_gap=0.05))
    seam = {s.piece: s for s in model.segments if len(s.partners) == 2}
    assert set(seam) == {0, 1, 2}                       # every piece sees both others
    assert all(sorted(model.segments[t].piece for t in s.links) == sorted({0, 1, 2} - {p})
               for p, s in seam.items())


def test_a_seam_found_only_by_proximity_must_be_sewn_tight():
    # 3 mm apart reads as two edges resting on each other (the scarf's folded tips), not a seam.
    topo, model = run(fx.gapped_tube(gap=0.3))
    assert topo.near_seams and all(s.tag == -1 for s in model.segments)


def test_a_proximity_seam_seen_from_one_side_only_is_dropped():
    from types import SimpleNamespace
    from retopo_tool.core.segments import _mutual_partners
    pos = [(float(i), 0.0, 0.0) for i in range(6)] + [(float(i), 0.0, 0.1) for i in range(6)]
    loop_a, loop_b = list(range(6)), list(range(6, 12))
    near_a = [{1}] * 5 + [set()]
    loops = [(SimpleNamespace(id=0), 0, loop_a, None, [set()] * 6, [set(s) for s in near_a]),
             (SimpleNamespace(id=1), 0, loop_b, None, [set()] * 6, [set() for _ in range(6)])]
    cfg = {"seam_gap_max": 0.5, "seam_min_run_edges": 4}
    _mutual_partners(loops, pos, list(range(12)), cfg)
    assert all(not s for s in loops[0][5])
    loops[1] = (loops[1][0], 0, loop_b, None, [set()] * 6, [{0}] * 5 + [set()])
    loops[0] = (loops[0][0], 0, loop_a, None, [set()] * 6, [set(s) for s in near_a])
    _mutual_partners(loops, pos, list(range(12)), cfg)
    assert [bool(s) for s in loops[0][5]] == [True] * 5 + [False]
