"""Headless run: MD OBJ -> steps 1-10 -> Maya scene (.ma) plus a JSON report.

    mayapy -m retopo_tool.maya_adapter.batch <garment.obj> <out.ma>
        [--target <cm>] [--similar-tol <0-1>] [--attach-inside keep|delete] [--prefix <name>]

Every node carries a prefix (default: the OBJ file name). The original MD mesh
is kept, hidden, under <prefix>_MD_original; the retopo mesh is <prefix>_retopo
with one face set per pattern piece (<prefix>_piece_01, ...), plus
<prefix>_attach_inside_<base>_<attached> sets for the faces under an attached
opening (keep mode; delete them to open the hole).
"""
import argparse
import json
import sys
import time
from collections import Counter


def run(obj_path, out_path, target=None, similar_tol=None, attach_inside=None, prefix=None):
    import maya.standalone
    maya.standalone.initialize(name="python")
    import maya.cmds as cmds

    from ..core.config import load_config
    from ..core.pipeline import build_model, build_output, build_pieces, solve_garment
    from ..core.symmetry import similar_groups
    from ..core.topology import build_topology
    from ..core.validate import validate_mesh
    from ..core.weld import normalise_welding
    from ..sources import load_mesh
    from ..ui.session import clean_prefix
    from .host import import_original, replace_mesh

    report, t0 = {"input": obj_path, "output": out_path}, time.time()
    cfg = load_config("defaults")
    if target is not None:
        cfg["solver"]["target_edge_length"] = target
    if similar_tol is not None:
        cfg["symmetry"]["uv_tolerance"] = similar_tol
    if attach_inside is not None:
        cfg["attach"]["inside"] = attach_inside
    report["target_edge_length"] = cfg["solver"]["target_edge_length"]
    report["similar_tolerance"] = cfg["symmetry"]["uv_tolerance"]
    report["attach_inside"] = cfg["attach"]["inside"]
    prefix = clean_prefix(prefix or obj_path)
    report["prefix"] = prefix

    src = load_mesh("md", obj_path)
    val = validate_mesh(src)
    report["validation"] = {"errors": val.errors, "warnings": val.warnings}
    if not val.ok:
        return report
    mesh, wrep = normalise_welding(src, cfg["weld"])
    topo = build_topology(mesh)
    model, gwarn = build_model(mesh, topo, cfg)
    similar = similar_groups(mesh, topo, model, cfg["symmetry"])
    layouts, res = solve_garment(mesh, topo, model, similar, cfg=cfg)
    report["steps1to6"] = {"pieces": len(topo.pieces), "segments": len(model.segments),
                           "similar_pairs": [(m.a, m.b) for m in similar],
                           "attach_bases": sorted(model.regions),
                           "warnings": wrep.warnings + topo.warnings + gwarn + res.warnings, "errors": res.errors}
    if not res.ok:
        return report
    patches = build_pieces(range(len(topo.pieces)), mesh, topo, model, res.counts, cfg=cfg,
                           layouts=layouts, similar=similar)
    report["pieces"] = {pid: {"ok": p.ok, "layout": layouts[(pid, 0)].kind, "quads": len(p.quads),
                              "rings": p.rings, "poles": sorted(p.poles.values()),
                              "attach_sets": {k: len(v) for k, v in p.face_tags.items()},
                              "errors": p.errors, "warnings": p.warnings}
                        for pid, p in patches.items()}
    report["total_poles"] = sum(len(p.poles) for p in patches.values())
    out, face_piece = build_output(patches, mesh, topo, cfg)

    cmds.file(new=True, force=True)
    import_original(src, prefix)
    retopo, _ = replace_mesh(out, face_piece, prefix)

    # Checks done by Maya itself on the built mesh.
    sides = Counter(len(cmds.polyInfo(f"{retopo}.f[{i}]", faceToVertex=True)[0].split(":")[1].split())
                    for i in range(cmds.polyEvaluate(retopo, face=True)))
    report["maya"] = {
        "faces": cmds.polyEvaluate(retopo, face=True),
        "vertices": cmds.polyEvaluate(retopo, vertex=True),
        "uvs": cmds.polyEvaluate(retopo, uvcoord=True),
        "face_sides": dict(sides),
        "nonmanifold_edges": len(cmds.polyInfo(retopo, nonManifoldEdges=True) or []),
        "open_edges": _open_edges(cmds, retopo),
        "nonmanifold_vertices": len(cmds.polyInfo(retopo, nonManifoldVertices=True) or []),
        "lamina_faces": len(cmds.polyInfo(retopo, laminaFaces=True) or []),
        "bounding_box_cm": [round(x, 2) for x in cmds.exactWorldBoundingBox(retopo)],
        "original_bounding_box_cm": [round(x, 2) for x in cmds.exactWorldBoundingBox(prefix + "_md_mesh")],
    }
    cmds.file(rename=out_path)
    cmds.file(save=True, type="mayaAscii", force=True)
    report["seconds"] = round(time.time() - t0, 2)
    return report


def _open_edges(cmds, mesh):
    """Edges with one face (Maya's own count)."""
    import maya.api.OpenMaya as om
    sel = om.MSelectionList()
    sel.add(mesh)
    it = om.MItMeshEdge(sel.getDagPath(0))
    count = 0
    while not it.isDone():
        count += it.onBoundary()
        it.next()
    return count


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("obj")
    ap.add_argument("out")
    ap.add_argument("--target", type=float, default=None, help="target edge length in scene units (cm)")
    ap.add_argument("--similar-tol", type=float, default=None,
                    help="how alike two pieces must be in UV to share one topology (symmetry.uv_tolerance)")
    ap.add_argument("--attach-inside", choices=("keep", "delete"), default=None,
                    help="faces under an attached opening: keep in a face set, or leave a hole")
    ap.add_argument("--prefix", default=None, help="name prefix for the nodes and sets (default: the OBJ name)")
    a = ap.parse_args(argv)
    report = run(a.obj, a.out, a.target, a.similar_tol, a.attach_inside, a.prefix)
    with open(a.out.rsplit(".", 1)[0] + "_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
