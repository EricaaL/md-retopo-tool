"""core/flat.py and pipeline.flatten / transfer_flat: the manual flat stage."""
from collections import Counter

import fixtures as fx
from retopo_tool.core.config import load_config
from retopo_tool.core.flat import seam_colours, segment_labels, to_uv
from retopo_tool.core.mesh import MeshData
from retopo_tool.core.pipeline import (build_model, build_output, build_pieces, flatten, solve_garment,
                                       transfer_flat)
from retopo_tool.core.topology import build_topology
from retopo_tool.core.weld import normalise_welding


def garment(mesh):
    cfg = load_config("defaults")
    cfg["solver"]["target_edge_length"] = 1.0
    mesh, _ = normalise_welding(mesh)
    topo = build_topology(mesh)
    model, _ = build_model(mesh, topo, cfg)
    layouts, res = solve_garment(mesh, topo, model, cfg=cfg)
    patches = build_pieces(range(len(topo.pieces)), mesh, topo, model, res.counts, cfg=cfg, layouts=layouts)
    return cfg, mesh, topo, model, res, patches


def edge_uses(out):
    uses = Counter()
    for f in out.faces:
        for k in range(len(f)):
            uses[tuple(sorted((f[k], f[(k + 1) % len(f)])))] += 1
    return uses


def centroids(out):
    return sorted(tuple(round(sum(out.positions[v][k] for v in f) / len(f), 4) for k in range(3)) for f in out.faces)


def test_flat_pieces_do_not_overlap_even_when_md_stacks_them_in_uv():
    cfg, mesh, topo, model, res, patches = garment(fx.mirrored_uv_overlay())
    flat, lay = flatten(patches, mesh, model, cfg)
    boxes = []
    for pid, info in lay.pieces.items():
        xs = [flat.positions[v][0] for v in range(info["start"], info["start"] + info["count"])]
        boxes.append((min(xs), max(xs)))
    boxes.sort()
    assert len(boxes) == 2 and boxes[0][1] < boxes[1][0]
    floor = min(q[1] for q in mesh.positions)
    assert abs(min(p[1] for p in flat.positions) - floor) < 1e-9             # stands on the floor
    assert len({p[2] for p in flat.positions}) == 1                          # in one XY plane (faces +Z)
    assert min(p[0] for p in flat.positions) > max(p[0] for p in mesh.positions)               # beside it


def test_flat_maps_back_to_uv_exactly():
    cfg, mesh, topo, model, res, patches = garment(fx.tube())
    flat, lay = flatten(patches, mesh, model, cfg)
    for pid, info in lay.pieces.items():
        for v in range(info["count"]):
            u = to_uv(lay, pid, flat.positions[info["start"] + v])
            assert all(abs(a - b) < 1e-9 for a, b in zip(u, patches[pid].points[v]))


def test_unedited_flat_transfers_to_the_same_mesh():
    cfg, mesh, topo, model, res, patches = garment(fx.tube_on_panel())
    flat, lay = flatten(patches, mesh, model, cfg)
    out, face_piece, warnings = transfer_flat(flat, flat, lay, mesh, topo, cfg)
    ref, ref_piece = build_output(patches, mesh, topo, cfg)
    assert not warnings
    assert centroids(out) == centroids(ref)
    assert Counter(face_piece) == Counter(ref_piece)
    assert out.meta["face_sets"].keys() == ref.meta["face_sets"].keys()


def subdivide(flat):
    """Split every quad into 4 (what an artist gets by adding loops everywhere)."""
    pos = list(flat.positions)
    mids = {}

    def mid(a, b):
        key = (min(a, b), max(a, b))
        if key not in mids:
            mids[key] = len(pos)
            pos.append(tuple((x + y) / 2 for x, y in zip(pos[a], pos[b])))
        return mids[key]
    faces = []
    for f in flat.faces:
        c = len(pos)
        pos.append(tuple(sum(pos[v][k] for v in f) / 4 for k in range(3)))
        m = [mid(f[k], f[(k + 1) % 4]) for k in range(4)]
        for k in range(4):
            faces.append([f[k], m[k], c, m[k - 1]])
    return MeshData(pos, [(0, 0)] * len(pos), faces, [list(f) for f in faces])


def test_hand_edits_transfer_and_still_weld_across_seams():
    cfg, mesh, topo, model, res, patches = garment(fx.tube())
    flat, lay = flatten(patches, mesh, model, cfg)
    ref, _ = build_output(patches, mesh, topo, cfg)
    edited = subdivide(flat)
    # ...and drag one vertex a little, the way you would in the viewport.
    inner = next(v for v in range(len(flat.positions)) if v not in lay.fixed)
    p = edited.positions[inner]
    edited.positions[inner] = (p[0] + 0.05, p[1], p[2])
    out, _, warnings = transfer_flat(edited, flat, lay, mesh, topo, cfg)
    assert not warnings and all(len(f) == 4 for f in out.faces)
    assert len(out.faces) == 4 * len(ref.faces)
    open_ref = sum(1 for n in edge_uses(ref).values() if n == 1)
    open_out = sum(1 for n in edge_uses(out).values() if n == 1)
    assert open_out == 2 * open_ref     # only the tube's two rims; the side seams still weld


def test_non_quad_hand_edits_are_reported_not_dropped():
    cfg, mesh, topo, model, res, patches = garment(fx.rectangle())
    flat, lay = flatten(patches, mesh, model, cfg)
    f = flat.faces[0]
    edited = MeshData(list(flat.positions), list(flat.uvs), [f[:3], [f[0], f[2], f[3]]] + flat.faces[1:],
                      [[0, 0, 0]] * (len(flat.faces) + 1))
    out, _, warnings = transfer_flat(edited, flat, lay, mesh, topo, cfg)
    assert any("not quads" in w for w in warnings)
    assert len(out.faces) == len(flat.faces) + 1


def test_sewn_sides_share_a_colour_and_free_sides_each_get_their_own():
    cfg, mesh, topo, model, res, patches = garment(fx.tube_on_panel())
    colours = seam_colours(model)
    for s in model.segments:
        for t in s.links:
            assert colours[s.id] == colours[t]
    free = [s.id for s in model.segments if not s.links]
    assert len({colours[s] for s in free}) == len(free)
    guide = next(s for s in model.segments if s.uv_points is not None)
    assert colours[guide.id] == colours[guide.links[0]]          # the arm's rim and its loop on the body


def test_every_drawn_segment_gets_a_count_label():
    cfg, mesh, topo, model, res, patches = garment(fx.tube())
    flat, lay = flatten(patches, mesh, model, cfg)
    labels = segment_labels(lay, res.counts, 0.5)
    assert {sid for sid, _, _ in labels} == set(lay.curves)
    assert all(n == res.counts[sid] for sid, _, n in labels)
