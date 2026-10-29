import fixtures as fx
from retopo_tool.core.config import load_config
from retopo_tool.core.pipeline import build_pieces, solve_garment
from retopo_tool.core.segments import build_segments
from retopo_tool.core.topology import build_topology
from retopo_tool.core.weld import normalise_welding


def cfg_for(target=1.0):
    cfg = load_config("defaults")
    cfg["solver"]["target_edge_length"] = target
    return cfg


def garment(mesh, user_counts=None, target=1.0):
    cfg = cfg_for(target)
    mesh, _ = normalise_welding(mesh)
    topo = build_topology(mesh)
    model = build_segments(mesh, topo)
    layouts, res = solve_garment(mesh, topo, model, user_counts=user_counts, cfg=cfg)
    patches = build_pieces(range(len(topo.pieces)), mesh, topo, model, res.counts, cfg=cfg, layouts=layouts)
    return model, layouts, res, patches


def side_totals(layout, counts):
    return [sum(counts[s] for s in side) for side in layout.sides]


def test_clipped_square_is_a_quad_layout_and_a_pole_free_grid():
    model, layouts, res, patches = garment(fx.clipped_square())
    lay = layouts[(0, 0)]
    assert lay.kind == "quad" and len(lay.sides) == 4
    t = side_totals(lay, res.counts)
    assert t[0] == t[2] and t[1] == t[3]
    assert patches[0].ok and patches[0].rings == 1 and not patches[0].poles


def test_unbalanced_outline_gets_its_free_counts_nudged():
    # Clips of different sizes: length-based counts don't balance on their own.
    mesh = fx.polygon_piece([(0, 0), (7, 0), (10, 3), (10, 10), (4.5, 10), (0, 5.5)])
    model, layouts, res, patches = garment(mesh)
    lay = layouts[(0, 0)]
    assert lay.kind == "quad"
    t = side_totals(lay, res.counts)
    assert t[0] == t[2] and t[1] == t[3]
    assert any(res.counts[s] != res.initial[s] for s in model.loops[(0, 0)])   # it did nudge
    assert all(abs(res.counts[s] - res.initial[s]) <= 2 for s in model.loops[(0, 0)])
    assert patches[0].ok and not patches[0].poles


def test_user_counts_that_block_balancing_warn_and_still_fill():
    mesh = fx.clipped_square()
    model, layouts, _, _ = garment(mesh)
    side0 = layouts[(0, 0)].sides[0]
    side2 = layouts[(0, 0)].sides[2]
    # Pin both opposite sides to different totals: no free count can fix that.
    user = {side0[0]: 9}
    user.update({s: 1 for s in side2[:-1]})
    user[side2[-1]] = 6 if len(side2) > 1 else 5
    model, layouts, res, patches = garment(mesh, user_counts=user)
    assert res.ok and any("opposite sides" in w for w in res.warnings)
    assert patches[0].ok and all(len(q) == 4 for q in patches[0].quads)


def test_triangle_is_an_npole_layout():
    _, layouts, _, patches = garment(fx.triangle())
    assert layouts[(0, 0)].kind == "npole" and sorted(patches[0].poles.values()) == [3]


def test_l_shape_stays_free():
    _, layouts, _, patches = garment(fx.big_l())
    assert layouts[(0, 0)].kind == "free" and patches[0].ok


def test_short_sides_are_raised_so_the_ring_fits():
    # A 6 x 1 strip: the short sides would get 1 edge; the ring needs 3.
    model, layouts, res, patches = garment(fx.strip(6, 1))
    assert all(sum(res.counts[s] for s in side) >= 3 for side in layouts[(0, 0)].sides)
    assert patches[0].ok and patches[0].rings == 1


def test_long_narrow_strip_keeps_its_ring():
    model, layouts, res, patches = garment(fx.strip(12, 1), target=0.5)
    assert patches[0].ok and patches[0].rings == 1


def test_tiny_piece_gets_its_own_finer_density_and_a_ring():
    from retopo_tool.core.pipeline import detail_targets
    mesh, _ = normalise_welding(fx.big_with_tiny())
    topo = build_topology(mesh)
    targets = detail_targets(mesh, topo, cfg_for(1.0))
    assert list(targets) == [1] and targets[1] < 0.2
    model, layouts, res, patches = garment(fx.big_with_tiny())
    assert all(p.ok for p in patches.values()) and patches[1].rings == 1


def similar_garment(mesh, tol=None, target=1.0):
    from retopo_tool.core.symmetry import similar_groups
    cfg = cfg_for(target)
    if tol is not None:
        cfg["symmetry"]["uv_tolerance"] = tol
    mesh, _ = normalise_welding(mesh)
    topo = build_topology(mesh)
    model = build_segments(mesh, topo)
    pairs = similar_groups(mesh, topo, model, cfg["symmetry"])
    layouts, res = solve_garment(mesh, topo, model, pairs, cfg=cfg)
    patches = build_pieces(range(len(topo.pieces)), mesh, topo, model, res.counts, cfg=cfg,
                           layouts=layouts, similar=pairs)
    return pairs, patches


def same_topology(p, q):
    from collections import Counter
    return (len(p.quads) == len(q.quads) and p.rings == q.rings
            and Counter(p.poles.values()) == Counter(q.poles.values()))


def test_mirrored_twin_gets_the_same_topology():
    pairs, patches = similar_garment(fx.twin_panels(mirror=True))
    assert len(pairs) == 1 and pairs[0].reflected
    assert all(p.ok for p in patches.values())
    assert same_topology(patches[0], patches[1])
    assert any("copied" in w for w in patches[1].warnings)


def test_twin_within_tolerance_is_shared_and_beyond_it_is_not():
    pairs, patches = similar_garment(fx.twin_panels(stretch=1.02), tol=0.03)
    assert len(pairs) == 1 and any("copied" in w for w in patches[1].warnings)
    pairs, patches = similar_garment(fx.twin_panels(stretch=1.25), tol=0.03)
    assert pairs == [] and not any("copied" in w for w in patches[1].warnings)
    pairs, _ = similar_garment(fx.twin_panels(stretch=1.25), tol=0.3)   # the slider widens it
    assert len(pairs) == 1
