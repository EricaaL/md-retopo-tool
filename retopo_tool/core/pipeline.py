"""Steps 7-10 per piece. The one core module allowed to import other step modules.

Work is per piece so a count or ring edit only rebuilds the affected pieces
(the piece, its seam partners and its mirror twin).
"""
from .assemble import assemble
from .guides import attach_guides, plan_guides
from .segments import build_segments
from .config import load_config
from .fill import fill_piece
from .flat import flat_layout, split_flat
from .geom import dist, poly_area_3d, signed_area
from .layout import loop_corners, plan_layouts, region_minimums
from .mesh import FlatPatch, attach_set_name, attach_set_pieces, piece_label, segment_edges
from .project import project_patch
from .relax import relax_patch
from .tidy import tidy_patch
from .ring import PLAIN, build_rings, sample_loops, sample_regions
from .solver import solve_counts
from .symmetry import apply_uv
from .validate import check_patch


def build_piece(piece_id, mesh, topo, model, counts, spec=None, cfg=None, layouts=None):
    """Build the flat quad patch of one piece. Errors are reported on the patch, not raised.

    layouts: (piece, loop) -> Layout from layout.plan_layouts; its corners steer
    the border ring and the fill.
    """
    cfg = cfg or load_config("defaults")
    eps = cfg["geometry"]["eps"]
    piece = topo.pieces[piece_id]
    patch = FlatPatch(piece_id)
    seg_ids = [s for li in range(len(piece.loops)) for s in model.loops[(piece_id, li)]]
    missing = [s for s in seg_ids if counts.get(s, 0) < 1]
    if missing:
        patch.errors.append(f"Piece {piece_label(piece_id)}: segments {missing} have no solved count. Run the solver first.")
        return patch

    rings = cfg["ring"]["auto_rings"] if spec is None or spec.rings is None else spec.rings
    if not isinstance(rings, int) or not 0 <= rings <= cfg["ring"]["max_rings"]:
        patch.errors.append(f"Piece {piece_label(piece_id)}: ring count {rings!r} must be 0-{cfg['ring']['max_rings']}.")
        return patch

    f0 = piece.faces[0]
    patch.uv_flipped = signed_area([mesh.uvs[t] for t in mesh.face_uvs[f0]]) < 0

    if piece_id in model.regions:
        if not _fill_regions(patch, model.regions[piece_id], mesh, topo, model, counts, rings, cfg, eps):
            return patch
        return _finish(patch, cfg, eps)

    loops, corners, edge = sample_loops(patch, piece, mesh, topo, model, counts)
    layout_ids = [None] * len(loops)
    lay = (layouts or {}).get((piece_id, 0))
    if lay is not None and lay.kind in ("quad", "npole"):
        layout_ids[0] = {patch.boundary[side[0]][0] for side in lay.sides}
    elif lay is not None and lay.kind == "loop":
        found = loop_corners(patch.points, loops[0], cfg["layout"], rings)
        if found:   # the ring must turn there too, so they become ring corners
            layout_ids[0] = set(found)
            corners[0] = set(corners[0]) | layout_ids[0]
    inner, kinds, follow = build_rings(patch, loops, corners, rings, edge, cfg["ring"], eps, layout_ids)
    outer_corners = None
    if layout_ids[0]:
        at = {v: i for i, v in enumerate(inner[0])}
        outer_corners = sorted({at[follow[v]] for v in layout_ids[0] if follow[v] in at})
    if not fill_piece(patch, inner, edge, cfg["fill"], eps, outer_corners, _ring_turns(patch, kinds)):
        listed = ", ".join(f"{s}={counts[s]}" for s in seg_ids)
        patch.errors.append(
            f"Piece {piece_label(piece_id)}: could not fill the inside with quads (segment counts {listed}). "
            "Change one count on this piece by 2, or lower its ring count.")
        return patch
    return _finish(patch, cfg, eps)


def _ring_turns(patch, kinds):
    """Indices on the innermost outer loop where the border ring turned (none without a ring)."""
    return [i for i, k in kinds[0].items() if k != PLAIN] if patch.rings else []


def _finish(patch, cfg, eps):
    piece_id = patch.piece
    tidy_patch(patch, cfg["fill"], eps)
    relax_patch(patch, cfg["fill"], eps)
    rep = check_patch(patch, eps)
    patch.errors += rep.errors
    patch.warnings += rep.warnings
    patch.poles = _poles(patch)
    doublets = sorted(v for v, n in patch.poles.items() if n < 3)
    if doublets:
        patch.warnings.append(f"Piece {piece_label(piece_id)}: vertices {doublets[:5]} have only 2 edges (doublets). "
                              "Change a count on this piece by 2 to clean them up.")
    return patch


def _fill_regions(patch, regions, mesh, topo, model, counts, rings, cfg, eps):
    """Ring and fill each region of a piece split by attach guides. Regions under an
    attached opening are tagged as a face set (attach.inside = "keep") or left
    empty ("delete")."""
    sampled, edge = sample_regions(patch, regions, mesh, topo, model, counts)
    keep = cfg["attach"]["inside"] == "keep"
    got = []
    for reg, (loops, corners) in zip(regions, sampled):
        inside = reg["inside"]
        if inside is not None and not keep and not reg.get("pinched"):
            continue
        first = len(patch.quads)
        patch.rings = 0
        inner, kinds, _ = build_rings(patch, loops, corners, 0 if inside else rings, edge, cfg["ring"], eps)
        if inside is None:
            got.append(patch.rings)
        if not fill_piece(patch, inner, edge, cfg["fill"], eps, ring_corners=_ring_turns(patch, kinds)):
            listed = ", ".join(f"{s}={counts[s]}" for cyc in reg["cycles"] for s, _ in cyc)
            patch.errors.append(
                f"Piece {piece_label(patch.piece)}: could not fill the area {'under' if inside else 'around'} an attach "
                f"line (segment counts {listed}). Change one of those counts by 2, or lower the ring count.")
            return False
        if inside is not None:
            name = attach_set_name(patch.piece, inside)
            patch.face_tags.setdefault(name, []).extend(range(first, len(patch.quads)))
    filled = [reg for reg in regions if reg["inside"] is None or keep or reg.get("pinched")]
    under = {s for reg in filled if reg["inside"] is not None for cyc in reg["cycles"] for s, _ in cyc}
    # Guide segments with faces on both sides.
    patch.inner_segments = {s for s in under if model.segments[s].uv_points is not None}
    used = {s for reg in filled for cyc in reg["cycles"] for s, _ in cyc}
    for s in set(patch.boundary) - used:   # border under a deleted opening has no faces left
        del patch.boundary[s]
    patch.rings = min(got) if got else 0
    return True


def build_model(mesh, topo, cfg=None):
    """Steps 3-4 wiring: segments, then attach guides (which may add border corners
    where an attach line meets a piece border, so segments are built twice).
    Returns (SegmentModel, warnings)."""
    cfg = cfg or load_config("defaults")
    model = build_segments(mesh, topo, cfg["topology"])
    extra, sewn = plan_guides(mesh, topo, model, cfg["topology"])
    resting = any(e not in sewn for s in model.segments if s.attach_to >= 0 for e in segment_edges(s))
    if extra or resting:   # resting: contacts that aren't sewing would otherwise cut seams into bits
        model = build_segments(mesh, topo, cfg["topology"], extra, sewn)
    return model, attach_guides(mesh, topo, model, cfg["topology"])


def _poles(patch):
    """Interior vertices whose valence is not 4."""
    nbrs, border = {}, set()
    for q in patch.quads:
        for k in range(4):
            a, b = q[k], q[(k + 1) % 4]
            nbrs.setdefault(a, set()).add(b)
            nbrs.setdefault(b, set()).add(a)
    for ids in patch.boundary.values():
        border.update(ids)
    return {v: len(n) for v, n in nbrs.items() if v not in border and len(n) != 4}


def affected_pieces(piece_id, model, mirrors=()):
    """The piece, its seam partners and its mirror twin: what a local edit must rebuild."""
    out = {piece_id}
    for s in model.segments:
        if s.piece == piece_id:
            out.update(model.segments[t].piece for t in s.links)
    for m in mirrors:
        if piece_id in (m.a, m.b):
            out.update((m.a, m.b))
    return out


def build_pieces(piece_ids, mesh, topo, model, counts, specs=None, cfg=None, layouts=None, similar=(),
                 progress=None):
    """Build several pieces. specs: piece id -> RingSpec; layouts from layout.plan_layouts;
    similar: MirrorPairs from symmetry.similar_groups. A similar piece gets its
    group's canonical topology (built here if it wasn't asked for).
    progress(piece id, done, total) is called after each piece; it may raise to stop."""
    cfg = cfg or load_config("defaults")
    specs = specs or {}
    member_of = {m.b: m for m in similar}
    wanted = sorted(piece_ids)
    built = {}

    def build(p):
        if p not in built:
            built[p] = build_piece(p, mesh, topo, model, counts, specs.get(p), cfg, layouts)
        return built[p]

    out = {}
    for done, p in enumerate(wanted):
        if progress is not None:
            progress(p, done, len(wanted))
        pair = member_of.get(p)
        if pair is not None and specs.get(p) == specs.get(pair.a):
            src = build(pair.a)
            if src.ok:
                moved = transfer_patch(src, pair, p, mesh, topo, model, counts, cfg, similar)
                if moved is not None:
                    out[p] = moved
                    continue
            out[p] = build(p)
            out[p].warnings.append(f"Piece {piece_label(p)}: could not copy the topology of similar piece {piece_label(pair.a)}; built on its own.")
        else:
            out[p] = build(p)
    return out


def build_output(patches, mesh, topo, cfg=None):
    """Steps 9-10: project every patch onto the drape and combine them into one mesh.

    Returns (MeshData, face_piece). Pieces that failed in steps 7-8 are left out.
    """
    cfg = cfg or load_config("defaults")
    pcfg = dict(cfg["project"], eps=cfg["geometry"]["eps"])
    points3d = {pid: project_patch(p, topo.pieces[pid], mesh, pcfg) for pid, p in patches.items() if p.ok}
    return assemble(patches, points3d, cfg["weld"]["seam_snap_ratio"])


def solve_garment(mesh, topo, model, mirrors=(), user_counts=None, cfg=None, rings=None, targets=None):
    """Steps 5b-6: plan every loop's layout, then solve counts balanced to it.

    Returns (layouts, SolveResult)."""
    cfg = cfg or load_config("defaults")
    rings = cfg["ring"]["auto_rings"] if rings is None else rings
    if targets is None:
        targets = detail_targets(mesh, topo, cfg)
    layouts = plan_layouts(mesh, topo, model, cfg["layout"], rings)
    mins = region_minimums(mesh, model, cfg["layout"], rings)
    res = solve_counts(model, mirrors, user_counts, cfg["solver"], layouts=layouts, min_counts=mins, targets=targets)
    return layouts, res


def estimate_quads(mesh, topo, target):
    """Roughly how many quads a full build makes: each piece's 3D area / target^2."""
    total = 0.0
    for piece in topo.pieces:
        total += sum(poly_area_3d([mesh.positions[v] for v in mesh.faces[f]]) for f in piece.faces)
    return int(total / (target * target))


def detail_targets(mesh, topo, cfg):
    """Pieces smaller than detail.size_ratio target edges get their own finer
    target (span / detail.edges_across), so they still get an outline and a fill."""
    target = cfg["solver"]["target_edge_length"]
    out = {}
    for piece in topo.pieces:
        vs = {v for f in piece.faces for v in mesh.faces[f]}
        span = max(max(mesh.positions[v][k] for v in vs) - min(mesh.positions[v][k] for v in vs) for k in range(3))
        if span < cfg["detail"]["size_ratio"] * target:
            out[piece.id] = span / cfg["detail"]["edges_across"]
    return out


def _guide_map(model, pair, similar):
    """Guide segments of pair.a -> those of pair.b, matched through the attached
    pieces' own similar pairs (head 0's neck guide follows body 6, head 1's
    follows body 5, and 5/6 are a pair). None if any guide has no counterpart."""
    twin = {}
    for m in similar:
        for x, y in m.seg_map.items():
            twin[x], twin[y] = y, x
    on_b = {}
    for g in model.segments:
        if g.piece == pair.b and g.uv_points is not None:
            for t in g.links:
                on_b[t] = g.id
    out = {}
    for g in model.segments:
        if g.piece == pair.a and g.uv_points is not None:
            hits = {on_b[twin.get(t, t)] for t in g.links if twin.get(t, t) in on_b}
            if len(hits) != 1:
                return None
            out[g.id] = hits.pop()
    if len(set(out.values())) != len(out) or len(out) != len({v for v in on_b.values()}):
        return None
    return out


def _align(src_pts, dst_ids, dst_points):
    """dst_ids reordered to follow src_pts (already in dst UV): either direction,
    and for a closed run (first == last) any starting point."""
    closed = len(dst_ids) > 2 and dst_ids[0] == dst_ids[-1]
    best = None
    for seq in (dst_ids, dst_ids[::-1]):
        ring = seq[:-1] if closed else seq
        for k in range(len(ring) if closed else 1):
            cand = ring[k:] + ring[:k] + ([ring[k]] if closed else [])
            cost = sum(dist(p, dst_points[v]) for p, v in zip(src_pts, cand))
            if best is None or cost < best[0]:
                best = (cost, cand)
    return best[1]


def transfer_patch(src, pair, piece_id, mesh, topo, model, counts, cfg=None, similar=()):
    """Give piece_id the same topology as src (the patch of pair.a).

    The member's own border (and attach guides) is sampled; canonical border
    vertices map onto it segment by segment, interior vertices go through the
    UV transform, quads are kept (reversed if the transform mirrors), then the
    interior is relaxed. Returns None if the counts or the attach guides differ,
    or the result folds."""
    cfg = cfg or load_config("defaults")
    eps = cfg["geometry"]["eps"]
    piece = topo.pieces[piece_id]
    patch = FlatPatch(piece_id)
    guide_map = {}
    if pair.a in model.regions or piece_id in model.regions:
        if pair.a not in model.regions or piece_id not in model.regions:
            return None
        guide_map = _guide_map(model, pair, similar)
        if guide_map is None:
            return None
        sample_regions(patch, model.regions[piece_id], mesh, topo, model, counts)
    else:
        sample_loops(patch, piece, mesh, topo, model, counts)
    vmap = {}
    for sa, sb in pair.seg_map.items():
        if sa not in src.boundary:      # under a deleted attach opening
            patch.boundary.pop(sb, None)
            continue
        a, b = src.boundary[sa], patch.boundary[sb]
        if len(a) != len(b):
            return None
        for x, y in zip(a, b[::-1] if pair.reversed else b):
            vmap[x] = y
    for sa, sb in guide_map.items():
        a, b = src.boundary[sa], patch.boundary[sb]
        if len(a) != len(b):
            return None
        b = _align([apply_uv(pair.uv_transform, src.points[x]) for x in a], b, patch.points)
        for x, y in zip(a, b):
            if vmap.setdefault(x, y) != y:
                return None
    for v, p in enumerate(src.points):
        if v not in vmap:
            vmap[v] = len(patch.points)
            patch.points.append(apply_uv(pair.uv_transform, p))
    patch.quads = [[vmap[v] for v in (q[::-1] if pair.reflected else q)] for q in src.quads]
    patch.ring_vertices = {vmap[v] for v in src.ring_vertices}
    patch.rings = src.rings
    seg_of = {**pair.seg_map, **guide_map}
    patch.inner_segments = {seg_of[x] for x in src.inner_segments}
    twin = {}
    for m in similar:
        twin[m.a], twin[m.b] = m.b, m.a
    for name, ids in src.face_tags.items():
        _, attached = attach_set_pieces(name)
        patch.face_tags[attach_set_name(piece_id, [twin.get(p, p) for p in attached])] = list(ids)
    f0 = piece.faces[0]
    patch.uv_flipped = signed_area([mesh.uvs[t] for t in mesh.face_uvs[f0]]) < 0
    relax_patch(patch, cfg["fill"], eps)
    rep = check_patch(patch, eps)
    if not rep.ok:
        return None
    patch.poles = _poles(patch)
    patch.warnings.append(f"Piece {piece_label(piece_id)}: topology copied from similar piece {piece_label(pair.a)}.")
    return patch


def flatten(patches, mesh, model, cfg=None):
    """The flat stage beside the garment (core.flat): (flat MeshData, FlatLayout).
    It stands facing the front camera, flat.beside_gap past the garment's +x side,
    from its floor height, through the middle of its depth."""
    cfg = cfg or load_config("defaults")
    pos = mesh.positions
    origin = (max(p[0] for p in pos) + cfg["flat"]["beside_gap"], min(p[1] for p in pos),
              (min(p[2] for p in pos) + max(p[2] for p in pos)) / 2)
    return flat_layout(patches, model, origin, cfg["flat"])


def transfer_flat(flat, original, lay, mesh, topo, cfg=None):
    """A (possibly hand-edited) flat mesh back onto the drape (steps 9-10).

    Returns (MeshData, face_piece, warnings). Border vertices the artist added
    are projected and then weld like the generated ones."""
    cfg = cfg or load_config("defaults")
    patches, warnings = split_flat(flat, original, lay, cfg["flat"])
    pcfg = dict(cfg["project"], eps=cfg["geometry"]["eps"])
    points3d = {}
    for pid, patch in patches.items():
        pts = project_patch(patch, topo.pieces[pid], mesh, pcfg)
        for v in patch.ring_vertices:
            patch.fixed3d.setdefault(v, pts[v])
        points3d[pid] = pts
    out, face_piece = assemble(patches, points3d, cfg["weld"]["seam_snap_ratio"])
    return out, face_piece, warnings
