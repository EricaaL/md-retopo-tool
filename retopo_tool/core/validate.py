"""Step 1: validate a loaded MD mesh. Input is untrusted; messages say how to fix."""
from .config import load_config
from .geom import poly_area_3d, signed_area
from .mesh import ValidationReport, piece_label


def validate_mesh(mesh, cfg=None, uv_required=True):
    cfg = cfg or load_config("defaults")["validation"]
    rep = ValidationReport()

    if len(mesh.faces) < cfg["min_faces"]:
        rep.errors.append("Mesh has no faces. Export the garment as a mesh, not just curves.")
        return rep
    if len(mesh.face_uvs) != len(mesh.faces):
        rep.errors.append("Face and UV face counts differ. Re-export the mesh with UVs.")
        return rep

    if uv_required and not mesh.uvs:
        rep.errors.append(
            "Mesh has no UVs. Export from Marvelous Designer with pattern UVs "
            "(OBJ; FBX goes through Maya import).")
        return rep

    npos, nuv = len(mesh.positions), len(mesh.uvs)
    bad_index = bad_sides = bad_uv_len = 0
    flat_3d = flat_uv = 0

    for fi, (face, fuv) in enumerate(zip(mesh.faces, mesh.face_uvs)):
        if not 3 <= len(face) <= cfg["max_face_sides"]:
            bad_sides += 1
            continue
        if len(fuv) != len(face):
            bad_uv_len += 1
            continue
        if any(not 0 <= v < npos for v in face) or any(not 0 <= t < nuv for t in fuv):
            bad_index += 1
            continue
        uv_pts = [mesh.uvs[t] for t in fuv]
        if poly_area_3d([mesh.positions[v] for v in face]) < cfg["degenerate_face_area"]:
            flat_3d += 1
        if abs(signed_area(uv_pts)) < cfg["degenerate_uv_area"]:
            flat_uv += 1

    if bad_sides:
        rep.errors.append(f"{bad_sides} face(s) have fewer than 3 or too many sides.")
    if bad_uv_len:
        rep.errors.append(f"{bad_uv_len} face(s) have a UV count that differs from the vertex count.")
    if bad_index:
        rep.errors.append(f"{bad_index} face(s) reference missing vertices or UVs. The file is corrupt.")
    if flat_3d:
        rep.warnings.append(f"{flat_3d} face(s) have zero area in 3D.")
    if flat_uv:
        rep.warnings.append(f"{flat_uv} face(s) have zero area in UV space; pieces may split wrongly.")
    return rep


def check_patch(patch, eps):
    """Steps 7-8 output check: quads only, none folded in UV, manifold edges."""
    rep = ValidationReport()
    bad = [i for i, q in enumerate(patch.quads) if len(q) != 4 or len(set(q)) != 4]
    if bad:
        rep.errors.append(f"Piece {piece_label(patch.piece)}: {len(bad)} face(s) are not quads (faces {bad[:5]}). "
                          "This is a bug in the fill.")
        return rep
    folded = [i for i, q in enumerate(patch.quads)
              if signed_area([patch.points[v] for v in q]) <= eps]
    if folded:
        rep.errors.append(f"Piece {piece_label(patch.piece)}: {len(folded)} quad(s) are folded in UV (faces {folded[:5]}). "
                          "Change the counts on this piece or lower its ring count.")
    uses = {}
    for q in patch.quads:
        for k in range(4):
            e = (q[k], q[(k + 1) % 4])
            if e in uses:
                rep.errors.append(f"Piece {piece_label(patch.piece)}: edge {e} is used twice in the same direction.")
                return rep
            uses[e] = True
    border = [e for e in uses if (e[1], e[0]) not in uses]
    expected = sum(len(ids) - 1 for sid, ids in patch.boundary.items() if sid not in patch.inner_segments)
    if len(border) != expected:
        rep.errors.append(f"Piece {piece_label(patch.piece)}: the quads leave {len(border)} open edges, "
                          f"expected {expected} on the piece border. The fill has a gap or overlap.")
    return rep
