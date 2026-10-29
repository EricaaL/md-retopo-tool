import math
from collections import Counter

import fixtures as fx
from retopo_tool.core.geom import signed_area
from retopo_tool.core.pipeline import build_output, build_pieces
from retopo_tool.core.segments import build_segments
from retopo_tool.core.solver import solve_counts
from retopo_tool.core.topology import build_topology
from retopo_tool.core.weld import normalise_welding


def garment(mesh, target=1.0):
    mesh, _ = normalise_welding(mesh)
    topo = build_topology(mesh)
    model = build_segments(mesh, topo)
    res = solve_counts(model, cfg=fx.solver_cfg(target))
    patches = build_pieces(range(len(topo.pieces)), mesh, topo, model, res.counts)
    return build_output(patches, mesh, topo), patches


def open_edges(out):
    uses = Counter()
    for f in out.faces:
        for k in range(len(f)):
            uses[tuple(sorted((f[k], f[(k + 1) % len(f)])))] += 1
    return [e for e, n in uses.items() if n == 1]


def test_flat_panel_projects_onto_its_uvs():
    (out, face_piece), _ = garment(fx.rectangle(6, 4))
    assert all(len(f) == 4 for f in out.faces) and set(face_piece) == {0}
    for f, t in zip(out.faces, out.face_uvs):
        for v, u in zip(f, t):
            assert math.dist(out.positions[v], (out.uvs[u][0], out.uvs[u][1], 0)) < 1e-9


def test_tube_welds_its_seams_and_stays_on_the_cylinder():
    (out, face_piece), patches = garment(fx.tube(nx=8, ny=4))
    assert set(face_piece) == {0, 1}
    # Only the two rims stay open; both side seams are welded.
    rims = sum(len(ids) - 1 for p in patches.values() for s, ids in p.boundary.items()) - 2 * 2 * 4
    assert len(open_edges(out)) == rims
    for p in out.positions:
        # Points blend the MD facets, so they sit on or just inside the unit circle.
        assert 0.9 < math.hypot(p[0], p[2]) <= 1.0 + 1e-9


def test_flipped_uv_piece_gets_reversed_quads():
    (out, _), patches = garment(fx.flipped_uv_rectangle())
    assert patches[0].uv_flipped
    # UV winding is clockwise now, matching the MD faces of that piece.
    assert all(signed_area([out.uvs[t] for t in ft]) < 0 for ft in out.face_uvs)


def test_fold_seam_output_has_no_collapsed_faces():
    (out, _), patches = garment(fx.folded_cone(n=8, h=4), target=0.5)
    assert patches[0].ok, patches[0].errors
    assert all(len(set(f)) == 4 for f in out.faces)


def test_gapped_seam_closes_in_the_output():
    (out, _), patches = garment(fx.gapped_tube())
    assert all(p.ok for p in patches.values())
    # Every open edge is on a rim (y == 0 or y == 4); both side seams are welded shut.
    for a, b in open_edges(out):
        ya, yb = out.positions[a][1], out.positions[b][1]
        assert abs(ya - yb) < 1e-6 and (abs(ya) < 1e-6 or abs(ya - 4) < 1e-6)


def test_ear_on_a_seam_joins_as_manifold_sheets():
    # Ear halves pinched onto the seam between two panels: 4 faces per edge in
    # MD. The output pairs each ear half with the panel beside it instead.
    (out, face_piece), patches = garment(fx.ear_on_seam(n=4))
    assert all(p.ok for p in patches.values())
    uses = Counter()
    for f in out.faces:
        for k in range(4):
            uses[tuple(sorted((f[k], f[(k + 1) % 4])))] += 1
    assert max(uses.values()) <= 2
    # Along the seam line each edge is shared by one panel and one ear half: no gaps.
    line = [e for e in uses if all(abs(out.positions[v][1] - 4) < 1e-6 and abs(out.positions[v][2]) < 1e-6 for v in e)]
    assert line and all(uses[e] == 2 for e in line)
