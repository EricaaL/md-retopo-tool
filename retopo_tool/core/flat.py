"""The flat stage: every piece's patch laid out flat beside the garment, and back.

flat_layout stands each FlatPatch up in the XY plane at real size (its UVs
times the garment's median 3D/UV length ratio), keeping the pattern layout but
sliding a piece right while it overlaps one already placed (MD lays mirrored
pieces on top of each other in UV). split_flat reads a (possibly hand-edited)
flat mesh back into per-piece patches in UV, so the pipeline can project them
onto the drape. seam_colours gives sewn-together segments one colour.

Flat coordinates of piece p: x = ox + scale*u, y = oy + scale*v, z = origin z.
That is the pattern facing the front camera (+Z), upright as in the UV editor:
faces CCW in UV face +Z.
"""
import colorsys
import math
from collections import defaultdict

from .config import load_config
from .geom import point_in_polygon, signed_area
from .mesh import FlatLayout, FlatPatch, MeshData


def _scale(model):
    ratios = sorted(s.length / s.uv_length for s in model.segments
                    if s.uv_points is None and s.uv_length > 0 and s.length > 0)
    return ratios[len(ratios) // 2] if ratios else 1.0


def _overlaps(a, b, gap):
    return a[0] < b[2] + gap and b[0] < a[2] + gap and a[1] < b[3] + gap and b[1] < a[3] + gap


def flat_layout(patches, model, origin=(0.0, 0.0, 0.0), cfg=None):
    """Lay the patches out flat. Returns (MeshData, FlatLayout).

    origin: (x, y, z) of the layout's lower left corner (lowest x and y), in
    scene units; the layout spreads to +x and +y in the plane z = origin z."""
    cfg = cfg or load_config("defaults")["flat"]
    gap = cfg["gap"]
    scale = _scale(model)
    order = [pid for pid in sorted(patches) if patches[pid].ok and patches[pid].quads]
    boxes, placed = {}, []
    for pid in order:
        pts = patches[pid].points
        box = [scale * min(p[0] for p in pts), scale * min(p[1] for p in pts),
               scale * max(p[0] for p in pts), scale * max(p[1] for p in pts)]
        for _ in range(len(placed) + 1):        # each pass clears at least one blocker
            hit = [b for b in placed if _overlaps(box, b, gap)]
            if not hit:
                break
            dx = max(b[2] for b in hit) + gap - box[0]
            box = [box[0] + dx, box[1], box[2] + dx, box[3]]
        boxes[pid] = box
        placed.append(box)
    if not boxes:
        return MeshData([], [], [], []), FlatLayout(scale, {}, {}, {})
    x0 = min(b[0] for b in boxes.values())
    y0 = min(b[1] for b in boxes.values())

    positions, uvs, faces, face_uvs = [], [], [], []
    lay = FlatLayout(scale, {}, {}, {})
    for pid in order:
        patch = patches[pid]
        pts = patch.points
        shift_u = boxes[pid][0] - scale * min(p[0] for p in pts)
        ox, oy = origin[0] - x0 + shift_u, origin[1] - y0
        start = len(positions)
        lay.pieces[pid] = {"offset": (ox, oy), "start": start, "count": len(pts), "uv_flipped": patch.uv_flipped}
        for p in pts:
            positions.append((ox + scale * p[0], oy + scale * p[1], origin[2]))
            uvs.append(tuple(p))
        quads = [[start + v for v in q] for q in patch.quads]
        lay.quads[pid] = quads
        faces += quads
        face_uvs += [list(q) for q in quads]
        for v, p3 in patch.fixed3d.items():
            lay.fixed[start + v] = tuple(p3)
        lay.tags[pid] = {i: name for name, ids in patch.face_tags.items() for i in ids}
        for sid, ids in patch.boundary.items():
            g = [start + v for v in ids]
            for a, b in zip(g, g[1:]):
                lay.border_edges[(min(a, b), max(a, b))] = sid
            lay.curves[sid] = [positions[v] for v in g]
    return MeshData(positions, uvs, faces, face_uvs, source_id="flat"), lay


def segment_labels(lay, counts, lift):
    """(segment id, label point, count) at the middle of every drawn segment."""
    out = []
    for sid, poly in sorted(lay.curves.items()):
        p = poly[len(poly) // 2] if len(poly) % 2 else _mid(poly[len(poly) // 2 - 1], poly[len(poly) // 2])
        out.append((sid, (p[0], p[1], p[2] + lift), counts.get(sid)))   # lifted toward the camera
    return out


def _mid(a, b):
    return tuple((x + y) / 2 for x, y in zip(a, b))


def seam_colours(model, segment_ids=None):
    """One colour per group of segments sewn together (seams and attach guides);
    every unsewn segment gets its own. Hues step by the golden ratio so
    neighbouring groups differ. Returns {segment id: (r, g, b)} in 0-1."""
    parent = list(range(len(model.segments)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for s in model.segments:
        for t in s.links:
            parent[find(s.id)] = find(t)
    ids = sorted(segment_ids) if segment_ids is not None else [s.id for s in model.segments]
    groups = {}
    for sid in ids:
        groups.setdefault(find(sid), len(groups))
    golden = (math.sqrt(5) - 1) / 2
    out = {}
    for sid in ids:
        k = groups[find(sid)]
        hue = (k * golden) % 1.0
        sat, val = (0.85, 0.95) if k % 2 == 0 else (0.6, 0.8)
        out[sid] = colorsys.hsv_to_rgb(hue, sat, val)
    return out


def to_uv(lay, pid, p):
    """Flat point -> the piece's UV."""
    ox, oy = lay.pieces[pid]["offset"]
    return ((p[0] - ox) / lay.scale, (p[1] - oy) / lay.scale)


class _QuadFinder:
    """Which original flat quad (piece, index) holds a point, with a grid."""

    def __init__(self, positions, lay):
        self.cells = defaultdict(list)
        self.items = []
        sizes = []
        for pid, quads in lay.quads.items():
            for i, q in enumerate(quads):
                poly = [(positions[v][0], positions[v][1]) for v in q]
                self.items.append((pid, i, poly))
                sizes.append(max(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a in poly for b in poly))
        sizes.sort()
        self.cell = max(sizes[len(sizes) // 2] if sizes else 1.0, 1e-9)
        for k, (_, _, poly) in enumerate(self.items):
            for cx in range(int(math.floor(min(p[0] for p in poly) / self.cell)),
                            int(math.floor(max(p[0] for p in poly) / self.cell)) + 1):
                for cz in range(int(math.floor(min(p[1] for p in poly) / self.cell)),
                                int(math.floor(max(p[1] for p in poly) / self.cell)) + 1):
                    self.cells[cx, cz].append(k)

    def find(self, p):
        cx, cz = int(math.floor(p[0] / self.cell)), int(math.floor(p[1] / self.cell))
        best = None
        for reach in (0, 1, 3):
            for x in range(cx - reach, cx + reach + 1):
                for z in range(cz - reach, cz + reach + 1):
                    for k in self.cells.get((x, z), ()):
                        pid, i, poly = self.items[k]
                        if point_in_polygon(p, poly):
                            return pid, i
                        c = (sum(q[0] for q in poly) / 4, sum(q[1] for q in poly) / 4)
                        d = (c[0] - p[0]) ** 2 + (c[1] - p[1]) ** 2
                        if best is None or d < best[0]:
                            best = (d, pid, i)
            if best is not None:
                return best[1], best[2]
        return None


def split_flat(flat, original, lay, cfg=None):
    """A (possibly hand-edited) flat mesh -> {piece id: FlatPatch in UV}, plus warnings.

    original: the flat MeshData flat_layout made (to know where pieces were).
    Each face goes to the piece of the original quad under its centroid and
    keeps that quad's attach face set. Vertices still where a generated border
    vertex was keep its exact 3D point (so seams weld as before); every other
    border vertex is projected like the interior. Non-quad faces are kept and
    reported."""
    cfg = cfg or load_config("defaults")["flat"]
    tol = cfg["match_tolerance"] * max(lay.scale, 1.0)
    finder = _QuadFinder(original.positions, lay)
    fixed_at = {}
    for v, p3 in lay.fixed.items():
        p = original.positions[v]
        fixed_at[(round(p[0] / tol), round(p[1] / tol))] = p3

    by_piece, warnings, odd = defaultdict(list), [], []
    for fi, f in enumerate(flat.faces):
        if len(f) < 3:
            continue
        if len(f) != 4:
            odd.append(fi)
        c = (sum(flat.positions[v][0] for v in f) / len(f), sum(flat.positions[v][1] for v in f) / len(f))
        hit = finder.find(c)
        if hit is None:
            warnings.append(f"Flat face {fi} is outside every piece; it was left out.")
            continue
        by_piece[hit[0]].append((f, hit[1]))
    if odd:
        warnings.append(f"{len(odd)} face(s) on the flat mesh are not quads (faces {odd[:8]}). "
                        "They are kept; fix them if you want an all-quad mesh.")

    patches = {}
    for pid, items in sorted(by_piece.items()):
        patch = FlatPatch(pid)
        patch.uv_flipped = lay.pieces[pid]["uv_flipped"]
        local = {}
        for f, _ in items:
            for v in f:
                if v not in local:
                    local[v] = len(patch.points)
                    p = flat.positions[v]
                    patch.points.append(to_uv(lay, pid, p))
                    hit = fixed_at.get((round(p[0] / tol), round(p[1] / tol)))
                    if hit is not None:
                        patch.fixed3d[local[v]] = hit
        tags = lay.tags.get(pid, {})
        for f, qi in items:
            q = [local[v] for v in f]
            if signed_area([patch.points[v] for v in q]) < 0:
                q = q[::-1]               # keep CCW in UV, whatever the hand edit did
            if qi in tags:
                patch.face_tags.setdefault(tags[qi], []).append(len(patch.quads))
            patch.quads.append(q)
        # Border edges (one face in this piece) form the boundary that assemble welds on.
        uses = defaultdict(int)
        for q in patch.quads:
            for k in range(len(q)):
                uses[(min(q[k], q[(k + 1) % len(q)]), max(q[k], q[(k + 1) % len(q)]))] += 1
        patch.boundary = {i: list(e) for i, e in enumerate(e for e, n in sorted(uses.items()) if n == 1)}
        patch.ring_vertices = {v for e in patch.boundary.values() for v in e}
        patches[pid] = patch
    return patches, warnings
