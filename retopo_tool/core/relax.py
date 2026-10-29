"""Step 8 (end): Laplacian relax of the free vertices of a flat patch in UV.

Piece-border vertices never move. Ring vertices stay put when fill.relax_pin_rings
is set, so the border band keeps its even width. An iteration that folds a quad
is undone and relaxing stops there.
"""
from collections import defaultdict

from .geom import signed_area
from .mesh import piece_label


def relax_patch(patch, cfg, eps):
    nbrs = defaultdict(set)
    for q in patch.quads:
        for k in range(4):
            a, b = q[k], q[(k + 1) % 4]
            nbrs[a].add(b)
            nbrs[b].add(a)
    pinned = set(patch.ring_vertices) if cfg["relax_pin_rings"] else set(patch.fixed3d)
    pinned |= set(patch.fixed3d)
    free = [v for v in nbrs if v not in pinned]
    pts = patch.points
    for it in range(cfg["relax_iterations"]):
        moved = {v: tuple(sum(pts[u][k] for u in nbrs[v]) / len(nbrs[v]) for k in range(2)) for v in free}
        old = {v: pts[v] for v in free}
        for v, p in moved.items():
            pts[v] = p
        if any(signed_area([pts[v] for v in q]) <= eps for q in patch.quads):
            for v, p in old.items():
                pts[v] = p
            patch.warnings.append(
                f"Piece {piece_label(patch.piece)}: relaxing stopped after {it} of {cfg['relax_iterations']} "
                "iterations because a quad would fold.")
            break
