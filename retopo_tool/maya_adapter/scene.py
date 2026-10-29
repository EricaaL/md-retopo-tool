"""Create and read Maya meshes as core MeshData (OpenMaya 2)."""
import maya.api.OpenMaya as om
import maya.cmds as cmds

from ..core.mesh import MeshData, piece_label


def create_mesh(name, mesh, parent=None):
    """Build a polygon mesh with per-face-corner UVs. Returns the transform name."""
    fn = om.MFnMesh()
    verts = [om.MPoint(*p) for p in mesh.positions]
    counts = [len(f) for f in mesh.faces]
    connects = [v for f in mesh.faces for v in f]
    us = [u[0] for u in mesh.uvs]
    vs = [u[1] for u in mesh.uvs]
    obj = fn.create(verts, counts, connects, us, vs)
    fn.assignUVs(counts, [t for f in mesh.face_uvs for t in f])
    transform = om.MFnDependencyNode(obj).name()
    transform = cmds.rename(transform, name)
    cmds.sets(transform, edit=True, forceElement="initialShadingGroup")
    if parent:
        transform = cmds.parent(transform, parent)[0]
    return transform


def _fn(transform):
    sel = om.MSelectionList()
    sel.add(transform)
    return om.MFnMesh(sel.getDagPath(0))


def update_points(transform, positions):
    """Move a mesh's vertices in place (same topology). False if the counts differ."""
    fn = _fn(transform)
    if fn.numVertices != len(positions):
        return False
    fn.setPoints(om.MPointArray([om.MPoint(*p) for p in positions]), om.MSpace.kObject)
    fn.updateSurface()
    return True


def read_mesh(transform, world=True, need_uvs=True):
    """A Maya mesh as MeshData: points (world or object space), the current UV set,
    UV ids per face corner. Raises ValueError when need_uvs and a face has none."""
    fn = _fn(transform)
    pts = fn.getPoints(om.MSpace.kWorld if world else om.MSpace.kObject)
    counts, connects = fn.getVertices()
    us, vs = fn.getUVs()
    uv_counts, uv_ids = fn.getAssignedUVs()
    positions = [(p.x, p.y, p.z) for p in pts]
    faces, face_uvs, k, j = [], [], 0, 0
    uvs = list(zip(us, vs))
    for fi, n in enumerate(counts):
        faces.append(list(connects[k:k + n]))
        if uv_counts[fi] == n:
            face_uvs.append(list(uv_ids[j:j + n]))
        elif need_uvs:
            raise ValueError(f"{transform} face {fi} has no UVs. Use a mesh with its MD pattern UVs.")
        else:
            uvs.append((0.0, 0.0))
            face_uvs.append([len(uvs) - 1] * n)
        k += n
        j += uv_counts[fi]
    return MeshData(positions, uvs, faces, face_uvs, source_id="maya")


def _ranges(ids):
    """[3, 4, 5, 9] -> ["3:5", "9"]: compact component strings."""
    out, ids = [], sorted(ids)
    i = 0
    while i < len(ids):
        j = i
        while j + 1 < len(ids) and ids[j + 1] == ids[j] + 1:
            j += 1
        out.append(f"{ids[i]}:{ids[j]}" if j > i else f"{ids[i]}")
        i = j + 1
    return out


def face_components(transform, ids):
    return [f"{transform}.f[{r}]" for r in _ranges(ids)]


def add_face_sets(transform, face_piece, prefix):
    """One objectSet per pattern piece: <prefix>_piece_01, ... Returns {wanted name: set}."""
    by_piece = {}
    for fi, pid in enumerate(face_piece):
        by_piece.setdefault(pid, []).append(fi)
    out = {}
    for pid, ids in sorted(by_piece.items()):
        name = f"{prefix}_piece_{piece_label(pid)}"
        out[name] = cmds.sets(face_components(transform, ids), name=name)
    return out


def add_named_face_sets(transform, face_sets, prefix):
    """One objectSet per name -> face ids, named <prefix>_<name>. Returns {wanted name: set}."""
    out = {}
    for name, ids in sorted(face_sets.items()):
        if ids:
            full = f"{prefix}_{name}"
            out[full] = cmds.sets(face_components(transform, ids), name=full)
    return out
