"""Step 4 attach guides: openings sewn onto another piece's surface."""
from collections import Counter

import fixtures as fx
from retopo_tool.core.config import load_config
from retopo_tool.core.pipeline import build_model, build_output, build_pieces, solve_garment
from retopo_tool.core.topology import build_topology
from retopo_tool.core.weld import normalise_welding


def run(mesh, inside="keep"):
    cfg = load_config("defaults")
    cfg["solver"]["target_edge_length"] = 1.0
    cfg["attach"]["inside"] = inside
    mesh, _ = normalise_welding(mesh)
    topo = build_topology(mesh)
    model, warnings = build_model(mesh, topo, cfg)
    layouts, res = solve_garment(mesh, topo, model, cfg=cfg)
    assert res.ok, res.errors
    patches = build_pieces(range(len(topo.pieces)), mesh, topo, model, res.counts, cfg=cfg, layouts=layouts)
    out, face_piece = build_output(patches, mesh, topo, cfg)
    return model, res, patches, out, warnings


def edge_uses(out):
    uses = Counter()
    for f in out.faces:
        for k in range(4):
            uses[tuple(sorted((f[k], f[(k + 1) % 4])))] += 1
    return uses


def test_tube_on_a_panel_gets_a_guide_loop_with_shared_counts():
    model, res, patches, out, warnings = run(fx.tube_on_panel())
    assert not warnings and list(model.regions) == [0]
    regs = model.regions[0]
    assert [r["inside"] for r in regs] == [None, (1, 2)]
    guides = [s for s in model.segments if s.uv_points is not None]
    assert len(guides) == 2
    for g in guides:   # each guide shares its count with the tube rim it follows
        assert all(res.counts[t] == res.counts[g.id] for t in g.links)
    assert all(p.ok for p in patches.values()), [p.errors for p in patches.values()]
    assert patches[0].rings >= 1
    assert list(patches[0].face_tags) == ["attach_inside_01_02_03"]
    assert all(len(f) == 4 for f in out.faces)
    assert out.meta["face_sets"]["attach_inside_01_02_03"]


def test_keep_mode_puts_three_faces_only_on_the_attach_loop_and_delete_mode_is_manifold():
    model, res, patches, out, _ = run(fx.tube_on_panel(), "keep")
    loop = sum(res.counts[s.id] for s in model.segments if s.uv_points is not None)
    uses = edge_uses(out)
    assert Counter(uses.values())[3] == loop and max(uses.values()) == 3
    model, res, patches, out, _ = run(fx.tube_on_panel(), "delete")
    uses = edge_uses(out)
    assert max(uses.values()) == 2 and not patches[0].face_tags
    # Open edges: the panel's outer border and the tube's top rim; the tube's foot is welded in.
    rim = sum(res.counts[s] for s in model.loops[(1, 0)] + model.loops[(2, 0)]
              if model.segments[s].tag == -1 and model.segments[s].attach_to == -1)
    outer = sum(res.counts[s] for s in model.loops[(0, 0)])
    assert Counter(uses.values())[1] == outer + rim


def test_tube_across_a_seam_splits_each_panel_with_a_chord():
    model, res, patches, out, warnings = run(fx.tube_across_seam(), "delete")
    assert not warnings and sorted(model.regions) == [0, 1]
    for pid in (0, 1):
        insides = [r["inside"] for r in model.regions[pid]]
        assert sorted(insides, key=str) == sorted([None, insides[0] or insides[1]], key=str)
    assert all(p.ok for p in patches.values()), [p.errors for p in patches.values()]
    assert max(edge_uses(out).values()) == 2


def test_cloth_resting_on_a_panel_is_not_an_attach():
    # Layers that only touch (the scarf) sit further off than sewing pulls a border.
    model, _, patches, _, _ = run(fx.tube_on_panel(gap=0.3))
    assert model.regions == {} and not patches[0].face_tags


def test_a_pinched_rim_keeps_the_faces_under_it_and_joins_as_sheets():
    for mode in ("keep", "delete"):
        model, res, patches, out, warnings = run(fx.puff_on_panel(), mode)
        assert not warnings and list(model.regions) == [0]
        under = [r for r in model.regions[0] if r["inside"] is not None]
        assert len(under) == 1 and under[0]["pinched"] and under[0]["inside"] == (1, 2)
        assert all(p.ok for p in patches.values()), [p.errors for p in patches.values()]
        assert patches[0].face_tags                    # never left out, even in delete mode
        assert max(edge_uses(out).values()) == 2       # base + front + back + under: two sheets


def test_names_shown_to_the_artist_count_from_01():
    from retopo_tool.core.mesh import attach_set_name, attach_set_pieces, piece_label
    assert piece_label(0) == "01" and piece_label(11) == "12"
    assert attach_set_name(4, [9, 8]) == "attach_inside_05_09_10"
    assert attach_set_pieces("attach_inside_05_09_10") == (4, [8, 9])


def test_resting_contacts_do_not_cut_the_border_into_bits():
    # Rejected contacts are dropped before the final segments, so no corners come from them.
    model, *_ = run(fx.tube_on_panel(gap=0.3))
    assert all(s.attach_to == -1 for s in model.segments)
    sewn, *_ = run(fx.tube_on_panel())
    assert any(s.attach_to == 0 for s in sewn.segments)
