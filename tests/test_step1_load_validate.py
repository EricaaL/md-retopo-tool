import pytest

from retopo_tool.core.mesh import MeshData
from retopo_tool.core.validate import validate_mesh
from retopo_tool.sources import load_mesh

QUAD_OBJ = """v 0 0 0
v 1 0 0
v 1 1 0
v 0 1 0
vt 0 0
vt 1 0
vt 1 1
vt 0 1
f 1/1 2/2 3/3 4/4
"""


def _quad(**kw):
    d = dict(positions=[(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
             uvs=[(0, 0), (1, 0), (1, 1), (0, 1)],
             faces=[[0, 1, 2, 3]], face_uvs=[[0, 1, 2, 3]])
    d.update(kw)
    return MeshData(**d)


def test_load_and_validate_ok(tmp_path):
    p = tmp_path / "g.obj"
    p.write_text(QUAD_OBJ)
    mesh = load_mesh("md", str(p))
    assert mesh.source_id == "md" and mesh.faces == [[0, 1, 2, 3]]
    assert validate_mesh(mesh).ok


def test_negative_indices(tmp_path):
    p = tmp_path / "g.obj"
    p.write_text(QUAD_OBJ.replace("f 1/1 2/2 3/3 4/4", "f -4/-4 -3/-3 -2/-2 -1/-1"))
    assert load_mesh("md", str(p)).faces == [[0, 1, 2, 3]]


def test_unknown_source():
    with pytest.raises(ValueError, match="Unknown source"):
        load_mesh("zbrush", "x.obj")


def test_bad_extension(tmp_path):
    with pytest.raises(ValueError, match="expects"):
        load_mesh("md", str(tmp_path / "g.fbx"))


def test_corrupt_line_names_line(tmp_path):
    p = tmp_path / "g.obj"
    p.write_text("v 0 0 0\nv a b c\n")
    with pytest.raises(ValueError, match="line 2"):
        load_mesh("md", str(p))


def test_missing_uvs_is_error():
    rep = validate_mesh(_quad(uvs=[], face_uvs=[[]]))
    assert not rep.ok and "no UVs" in rep.errors[0]


def test_no_faces_is_error():
    assert not validate_mesh(_quad(faces=[], face_uvs=[])).ok


def test_bad_index_is_error():
    assert not validate_mesh(_quad(faces=[[0, 1, 2, 9]])).ok


def test_uv_count_mismatch_is_error():
    assert not validate_mesh(_quad(face_uvs=[[0, 1, 2]])).ok


def test_degenerate_faces_warn_only():
    rep = validate_mesh(_quad(uvs=[(0, 0)] * 4))
    assert rep.ok and rep.warnings
