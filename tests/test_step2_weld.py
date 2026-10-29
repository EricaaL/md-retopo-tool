from retopo_tool.core.mesh import MeshData
from retopo_tool.core.weld import normalise_welding


def _two_quads(welded):
    # Two side-by-side pieces sharing the seam x=1; piece B's UVs live elsewhere.
    uv_a = [(0, 0), (1, 0), (1, 1), (0, 1)]
    uv_b = [(5, 0), (6, 0), (6, 1), (5, 1)]
    if welded:
        pos = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (2, 0, 0), (2, 1, 0)]
        faces = [[0, 1, 2, 3], [1, 4, 5, 2]]
    else:
        pos = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
               (1, 0, 0), (2, 0, 0), (2, 1, 0), (1, 1, 0)]
        faces = [[0, 1, 2, 3], [4, 5, 6, 7]]
    return MeshData(pos, uv_a + uv_b, faces, [[0, 1, 2, 3], [4, 5, 6, 7]])


def test_welded_and_unwelded_converge():
    a, _ = normalise_welding(_two_quads(True))
    b, _ = normalise_welding(_two_quads(False))
    assert len(a.positions) == len(b.positions) == 6
    assert a.faces == b.faces
    assert a.face_uvs == b.face_uvs


def test_seam_uvs_stay_split():
    m, _ = normalise_welding(_two_quads(False))
    assert len(m.uvs) == 8  # shared 3D seam vertices keep a UV per piece


def test_idempotent():
    once, _ = normalise_welding(_two_quads(False))
    twice, _ = normalise_welding(once)
    assert (once.positions, once.faces, once.uvs) == (twice.positions, twice.faces, twice.uvs)


def test_collapsed_face_dropped_with_warning():
    m = MeshData([(0, 0, 0), (0, 0, 0), (1, 0, 0), (0, 1, 0)],
                 [(0, 0), (0, 0), (1, 0), (0, 1)],
                 [[0, 1, 2], [0, 2, 3, 1]], [[0, 1, 2], [0, 2, 3, 1]])
    out, rep = normalise_welding(m)
    assert rep.dropped_faces == [0] and rep.warnings
    assert out.faces == [[0, 1, 2]] and rep.kept_faces == [1]  # quad pinched to a valid tri


def test_user_data_not_mutated():
    m = _two_quads(False)
    n = len(m.positions)
    normalise_welding(m)
    assert len(m.positions) == n


def test_mirrored_uv_overlay_stays_two_pieces():
    import fixtures as fx
    from retopo_tool.core.topology import build_topology
    m, _ = normalise_welding(fx.mirrored_uv_overlay())
    topo = build_topology(m)
    assert len(topo.pieces) == 2 and len(topo.seam_edges) == 3
