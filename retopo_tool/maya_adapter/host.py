"""Maya host services for the UI: Qt binding, docking, scene meshes, the flat
stage (mesh, seam-colour curves, count labels), selection and renaming.

Every node the tool makes carries the garment's prefix:
  <prefix>_retopo, <prefix>_retopo_sets (parent of <prefix>_piece_01, ...,
  <prefix>_attach_inside_..., <prefix>_thickness_rim), <prefix>_MD_original,
  <prefix>_flat_stage (holding <prefix>_flat, <prefix>_flat_curves, <prefix>_flat_labels).
The UI (retopo_tool.ui) never imports maya; it calls these.
"""
import maya.cmds as cmds

from .scene import add_face_sets, add_named_face_sets, create_mesh, face_components, read_mesh, update_points

SUFFIXES = ("_retopo_sets", "_retopo", "_MD_original", "_flat_stage", "_flat_curves", "_flat_labels", "_flat")


def names(prefix):
    return {k: prefix + "_" + k for k in ("retopo", "retopo_sets", "MD_original", "flat_stage", "flat",
                                          "flat_curves", "flat_labels")}


def qt():
    """(QtCore, QtGui, QtWidgets): PySide6 on Maya 2025+, PySide2 on Maya 2024."""
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide2 import QtCore, QtGui, QtWidgets
    return QtCore, QtGui, QtWidgets


def interactive():
    return not cmds.about(batch=True)


def dockable_bases(widget_class):
    """Base classes for a dockable window: Maya's dockable mixin in the Maya window,
    just the widget class in mayapy (tests)."""
    if interactive():
        from maya.app.general.mayaMixin import MayaQWidgetDockableMixin
        return (MayaQWidgetDockableMixin, widget_class)
    return (widget_class,)


def close_dock(object_name):
    control = object_name + "WorkspaceControl"
    if interactive() and cmds.workspaceControl(control, exists=True):
        cmds.deleteUI(control)


# ---- meshes --------------------------------------------------------------
def _delete_sets(parent):
    if cmds.objExists(parent):
        for s in cmds.sets(parent, query=True) or []:
            if cmds.objExists(s):
                cmds.delete(s)
        cmds.delete(parent)


def replace_mesh(out, face_piece, prefix):
    """Put the built mesh in the scene as <prefix>_retopo, replacing the previous
    one and its face sets. Returns (transform, {set name the tool means: Maya set})."""
    n = names(prefix)
    if cmds.objExists(n["retopo"]):
        cmds.delete(n["retopo"])
    _delete_sets(n["retopo_sets"])
    transform = create_mesh(n["retopo"], out)
    made = add_face_sets(transform, face_piece, prefix)
    made.update(add_named_face_sets(transform, out.meta.get("face_sets", {}), prefix))
    if made:
        cmds.sets(list(made.values()), name=n["retopo_sets"])
    return transform, made


def move_points(node, positions):
    """Update a mesh in place when only its points changed. False if it can't."""
    return cmds.objExists(node) and update_points(node, positions)


def import_original(mesh, prefix):
    """The MD mesh, hidden under <prefix>_MD_original, for comparison (replaces an earlier one)."""
    grp = names(prefix)["MD_original"]
    if cmds.objExists(grp):
        cmds.delete(grp)
    grp = cmds.group(empty=True, name=grp)
    create_mesh(prefix + "_md_mesh", mesh, parent=grp)
    cmds.hide(grp)
    return grp


def selected_mesh():
    """The first selected mesh transform, or None."""
    for node in cmds.ls(selection=True, long=True, objectsOnly=True) or []:
        if cmds.nodeType(node) == "mesh":
            node = cmds.listRelatives(node, parent=True, fullPath=True)[0]
        if cmds.listRelatives(node, shapes=True, type="mesh", noIntermediate=True):
            return node
    return None


def read_scene_mesh(transform):
    """MeshData of a scene mesh in world space (its UVs are required)."""
    return read_mesh(transform, world=True, need_uvs=True)


def set_visible(node, on):
    if node and cmds.objExists(node):
        cmds.setAttr(node + ".visibility", bool(on))


def select_sets(sets):
    """Select the faces of these Maya sets (missing ones are skipped). Returns how many sets."""
    found = [s for s in sets if s and cmds.objExists(s)]
    if found:
        cmds.select(found, replace=True)
    else:
        cmds.select(clear=True)
    return len(found)


def select_faces(node, ids):
    if cmds.objExists(node) and ids:
        cmds.select(face_components(node, ids), replace=True)


# ---- flat stage ----------------------------------------------------------
def show_flat(prefix, flat, curves, colours, labels, same_topology, show_curves=True, show_labels=True):
    """Draw the flat stage. The mesh is updated in place when only points moved
    (live sliders); curves and labels are redrawn. Returns the flat mesh name."""
    n = names(prefix)
    if not cmds.objExists(n["flat_stage"]):
        cmds.group(empty=True, name=n["flat_stage"])
    cmds.setAttr(n["flat_stage"] + ".visibility", True)
    if not (same_topology and move_points(n["flat"], flat.positions)):
        if cmds.objExists(n["flat"]):
            cmds.delete(n["flat"])
        create_mesh(n["flat"], flat, parent=n["flat_stage"])
    _draw_curves(n, curves, colours)
    _draw_labels(n, labels)
    set_visible(n["flat_curves"], show_curves)
    set_visible(n["flat_labels"], show_labels)
    return n["flat"]


def _fresh_group(name, parent):
    if cmds.objExists(name):
        cmds.delete(name)
    return cmds.group(empty=True, name=name, parent=parent)


def _draw_curves(n, curves, colours):
    grp = _fresh_group(n["flat_curves"], n["flat_stage"])
    for sid, poly in sorted(curves.items()):
        if len(poly) < 2:
            continue
        crv = cmds.curve(degree=1, point=poly, name=f"seg_{sid}_crv")
        shape = cmds.listRelatives(crv, shapes=True)[0]
        r, g, b = colours.get(sid, (1.0, 1.0, 1.0))
        cmds.setAttr(shape + ".overrideEnabled", 1)
        cmds.setAttr(shape + ".overrideRGBColors", 1)
        cmds.setAttr(shape + ".overrideColorRGB", r, g, b)
        if cmds.attributeQuery("lineWidth", node=shape, exists=True):
            cmds.setAttr(shape + ".lineWidth", 3)
        cmds.setAttr(shape + ".template", 0)
        cmds.parent(crv, grp)
    # Curves are for looking at: clicks go through to the flat mesh edges.
    cmds.setAttr(grp + ".overrideEnabled", 1)
    cmds.setAttr(grp + ".overrideDisplayType", 2)


def _draw_labels(n, labels):
    grp = _fresh_group(n["flat_labels"], n["flat_stage"])
    for sid, p, count in labels:
        if count is None:
            continue
        t = cmds.createNode("transform", name=f"seg_{sid}_count", parent=grp)
        cmds.xform(t, translation=p)
        shape = cmds.createNode("annotationShape", name=f"seg_{sid}_countShape", parent=t)
        cmds.setAttr(shape + ".text", str(count), type="string")
        cmds.setAttr(shape + ".displayArrow", 0)
    cmds.setAttr(grp + ".overrideEnabled", 1)
    cmds.setAttr(grp + ".overrideDisplayType", 2)


def read_flat(prefix):
    """The flat mesh as you left it (object space, so moving its transform doesn't matter)."""
    node = names(prefix)["flat"]
    if not cmds.objExists(node):
        raise ValueError(f"{node} is not in the scene. Press Flatten first.")
    return read_mesh(node, world=False, need_uvs=False)


def selected_flat_edges(prefix):
    """Selected edges of the flat mesh as (vertex, vertex) pairs."""
    node = names(prefix)["flat"]
    out = []
    for comp in cmds.ls(selection=True, flatten=True) or []:
        if ".e[" in comp and comp.split(".")[0].split("|")[-1] == node:
            info = cmds.polyInfo(comp, edgeToVertex=True)[0].split(":")[1].split()
            out.append((int(info[0]), int(info[1])))
    return out


def watch_selection(callback):
    """Call callback() whenever Maya's selection changes. Returns a job id (kill_job)."""
    return cmds.scriptJob(event=["SelectionChanged", callback], protected=True)


def kill_job(job):
    if job is not None and cmds.scriptJob(exists=job):
        cmds.scriptJob(kill=job, force=True)


# ---- names ---------------------------------------------------------------
def rename_prefix(old, new):
    """Give everything the tool made for <old> the prefix <new>. Returns {old name: new name}."""
    if not new or old == new:
        return {}
    o, n = names(old), names(new)
    taken = [n[k] for k in o if cmds.objExists(o[k]) and cmds.objExists(n[k])]
    if taken:
        raise ValueError(f"{', '.join(taken)} already exist; pick another prefix.")
    done = {}
    if cmds.objExists(o["retopo_sets"]):
        for s in cmds.sets(o["retopo_sets"], query=True) or []:
            if s.startswith(old + "_"):
                done[s] = cmds.rename(s, new + s[len(old):])
    for key in ("retopo_sets", "retopo", "MD_original", "flat_stage", "flat", "flat_curves", "flat_labels"):
        if cmds.objExists(o[key]):
            done[o[key]] = cmds.rename(o[key], n[key])
    md = old + "_md_mesh"
    if cmds.objExists(md):
        done[md] = cmds.rename(md, new + "_md_mesh")
    return done
