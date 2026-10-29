"""ui/session.py: the UI's garment state, tested without Qt or Maya."""
import pytest

import fixtures as fx
from retopo_tool.core.config import load_config
from retopo_tool.ui.session import GarmentSession, SessionError


def write_obj(mesh, path):
    """Write a fixture as an MD OBJ (MD exports mm; the loader scales by 0.1)."""
    lines = [f"v {x * 10} {y * 10} {z * 10}" for x, y, z in mesh.positions]
    lines += [f"vt {u} {v}" for u, v in mesh.uvs]
    lines += ["f " + " ".join(f"{v + 1}/{t + 1}" for v, t in zip(f, ft)) for f, ft in zip(mesh.faces, mesh.face_uvs)]
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def session(steps=None):
    cfg = load_config("defaults")
    cfg["solver"]["target_edge_length"] = 1.0
    return GarmentSession(cfg, progress=(lambda s, f: steps.append(s)) if steps is not None else None)


def test_load_analyze_build_reports_progress_and_rows(tmp_path):
    steps = []
    s = session(steps)
    s.load(write_obj(fx.tube_on_panel(), tmp_path / "g.obj"))
    s.analyze()
    rows = s.piece_rows()
    assert [r["id"] for r in rows] == [0, 1, 2] and rows[0]["quads"] is None   # analyzed, not built
    out, face_piece = s.build()
    assert out.faces and all(len(f) == 4 for f in out.faces)
    rows = s.piece_rows()
    assert rows[0]["attach_sets"] == ["attach_inside_01_02_03"] and all(r["ok"] for r in rows)
    kinds = {r["kind"] for r in s.segment_rows(0)}
    assert "attach guide" in kinds
    assert steps[0] == "Reading OBJ" and steps[-1] == "Built"


def test_a_count_edit_rebuilds_only_the_pieces_it_changes(tmp_path):
    s = session()
    s.load(write_obj(fx.twin_panels(), tmp_path / "g.obj"))
    s.configure(similar_tol=0.0)             # no sharing: the two panels stay independent
    s.build()
    before = {pid: p for pid, p in s.patches.items()}
    seg = s.segment_rows(0)[0]
    s.set_count(seg["id"], seg["count"] + 2)
    changed = s.apply_counts()
    assert changed == {0}
    assert s.res.counts[seg["id"]] == seg["count"] + 2
    assert s.patches[1] is before[1] and s.patches[0] is not before[0]
    assert s.segment_rows(0)[0]["yours"]


def test_a_refused_count_keeps_the_previous_result(tmp_path):
    s = session()
    s.load(write_obj(fx.twin_panels(), tmp_path / "g.obj"))
    s.configure(similar_tol=0.0)
    s.build()
    counts, output = dict(s.res.counts), s.output
    seg = s.segment_rows(0)[0]
    s.set_count(seg["id"], seg["count"] + 1)   # odd loop, and every other count on it is pinned too
    for sid in [r["id"] for r in s.segment_rows(0)[1:]]:
        s.set_count(sid, s.res.counts[sid])
    with pytest.raises(SessionError):
        s.apply_counts()
    assert s.res.counts == counts and s.output is output


def test_settings_only_redo_what_they_affect(tmp_path):
    s = session()
    s.load(write_obj(fx.twin_panels(), tmp_path / "g.obj"))
    s.build()
    model = s.model
    s.configure(attach_inside="delete")
    assert "solve" in s._done and "build" not in s._done
    s.configure(similar_tol=0.1)
    assert "group" not in s._done and "load" in s._done
    s.build()
    assert s.model is model                    # nothing reloaded


def test_bad_input_is_a_clear_session_error(tmp_path):
    s = session()
    with pytest.raises(SessionError, match="Load"):
        s.analyze()
    with pytest.raises(SessionError, match="Couldn't read"):
        s.load(str(tmp_path / "missing.obj"))
    with pytest.raises(SessionError):
        s.configure(target=0)


def test_prefix_comes_from_the_file_or_mesh_name(tmp_path):
    from retopo_tool.ui.session import clean_prefix
    s = session()
    s.load(write_obj(fx.rectangle(), tmp_path / "teddy bear.obj"))
    assert s.prefix == "teddy_bear"
    assert clean_prefix("|grp|char:teddy_bear") == "teddy_bear" and clean_prefix("3d-shirt") == "_3d_shirt"


def test_a_scene_mesh_gives_the_same_result_as_its_obj(tmp_path):
    a, b = session(), session()
    a.load(write_obj(fx.tube_on_panel(), tmp_path / "g.obj"))
    b.load_mesh_data(fx.tube_on_panel(), "pCube1")     # scene meshes are already in scene units
    out_a, _ = a.build()
    out_b, _ = b.build()
    assert len(out_a.faces) == len(out_b.faces) and b.prefix == "pCube1"


def scaled(mesh, k):
    from retopo_tool.core.mesh import MeshData
    return MeshData([tuple(c * k for c in p) for p in mesh.positions], mesh.uvs, mesh.faces, mesh.face_uvs)


def test_an_md_obj_imported_in_mm_is_worked_in_cm_and_handed_back_at_scene_size():
    real, big = session(), session()
    real.load_mesh_data(fx.tube_on_panel(), "teddy")
    big.load_mesh_data(scaled(fx.tube_on_panel(), 10), "teddy", units="mm")
    out_r, _ = real.build()
    out_b, _ = big.build()
    assert len(out_b.faces) == len(out_r.faces) and big.scene_scale == 10
    span = lambda m: max(p[0] for p in m.positions) - min(p[0] for p in m.positions)
    assert abs(span(out_b) - 10 * span(out_r)) < 1e-6
    flat = big.flatten()
    out_f, _ = big.to_3d(flat["mesh"])           # the flat mesh comes back in scene units too
    assert abs(span(out_f) - span(out_b)) < 1e-3
    thick, _ = big.thickened(0.2)
    assert abs(span(thick) - span(out_b)) < 1e-3


def test_units_are_guessed_from_the_size():
    s = session()
    s.load_mesh_data(scaled(fx.tube_on_panel(), 100), "huge")      # 800 units across: not a garment in cm
    assert s.scene_scale == 10 and any("millimetres" in m for _, m in s.log)
    with pytest.raises(SessionError):
        s.load_mesh_data(fx.tube_on_panel(), "x", units="inch")


def test_a_very_dense_build_asks_first(tmp_path):
    from retopo_tool.ui.session import TooDense
    s = session()
    s.cfg["ui"]["quad_budget"] = 10
    s.load(write_obj(fx.tube_on_panel(), tmp_path / "g.obj"))
    with pytest.raises(TooDense) as e:
        s.build()
    assert e.value.estimate > 10 and e.value.suggested > s.target
    out, _ = s.build(force=True)
    assert out.faces


def test_cancel_stops_without_changing_anything(tmp_path):
    from retopo_tool.ui.session import Cancelled
    s = session()
    s.load(write_obj(fx.tube_on_panel(), tmp_path / "g.obj"))
    s.build()
    patches, version = dict(s.patches), s.output_version
    s.configure(ring_width=0.7)
    s.cancel_requested = True
    with pytest.raises(Cancelled):
        s.build()
    assert s.patches == patches and s.output_version == version and not s.cancel_requested


def test_auto_build_after_to_3d_builds_from_the_patches_again(tmp_path):
    s = session()
    s.load(write_obj(fx.tube_on_panel(), tmp_path / "g.obj"))
    auto, _ = s.build()
    flat = s.flatten()
    edited = scaled(flat["mesh"], 1.0)
    edited.positions[0] = (edited.positions[0][0] + 0.01,) + tuple(edited.positions[0][1:])
    s.to_3d(edited)
    assert s.output_kind == "flat"
    again, _ = s.build()
    assert s.output_kind == "auto" and again.positions == auto.positions


def test_flat_stage_lock_and_back_to_3d(tmp_path):
    s = session()
    s.load(write_obj(fx.tube_on_panel(), tmp_path / "g.obj"))
    flat = s.flatten()
    assert flat["mesh"].faces and flat["labels"] and not flat["same_topology"]
    assert set(flat["colours"]) == set(flat["curves"])
    s.configure(ring_width=0.6)                  # live slider: rebuild, same counts
    again = s.flatten()
    assert again["mesh"].faces                   # around an attach loop the fill itself may change
    grid = session()
    grid.load(write_obj(fx.rectangle(nx=6, ny=4), tmp_path / "grid.obj"))
    grid.flatten()
    grid.configure(ring_width=0.6)
    assert grid.flatten()["same_topology"]       # plain grids: only positions move (updated in place)
    e = next(iter(s.flat_layout.border_edges))
    assert s.segment_of_edge(*e) == s.flat_layout.border_edges[e]
    s.lock()
    with pytest.raises(SessionError, match="locked"):
        s.configure(ring_width=0.9)
    with pytest.raises(SessionError, match="locked"):
        s.set_count(0, 5)
    out, face_piece = s.to_3d()
    assert out.faces and len(face_piece) == len(out.faces)
    assert "attach_inside_01_02_03" in out.meta["face_sets"]


def test_thickness_keeps_the_sets_and_adds_a_rim(tmp_path):
    s = session()
    s.load(write_obj(fx.tube_on_panel(), tmp_path / "g.obj"))
    out, _ = s.build()
    thick, face_piece = s.thickened(0.2)
    sets = thick.meta["face_sets"]
    assert len(sets["attach_inside_01_02_03"]) == 2 * len(out.meta["face_sets"]["attach_inside_01_02_03"])
    assert sets["thickness_rim"] and len(face_piece) == len(thick.faces)
    with pytest.raises(SessionError):
        s.thickened(0)
