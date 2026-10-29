"""Step 4 (symmetry part): find pieces that look the same in the UV editor.

Two pieces are similar when their outlines have the same number of segments,
a cyclic segment correspondence (reversal allowed, for mirrored pieces) whose
UV lengths agree within symmetry.uv_tolerance, and a 2D similarity transform
(rotation, uniform scale, translation, optional reflection) that maps one
outline onto the other with an RMS error within uv_tolerance of the piece size.

Pieces are grouped: each piece is matched against the first piece (canonical)
of every group so far. Similar pieces share counts (the solver links them) and
one topology (pipeline.transfer_patch). The artist can unlink a pair.
Pieces with holes are not grouped yet.
"""
import math

from .config import load_config
from .geom import dist, resample
from .mesh import MirrorPair


def _samples(poly, k):
    params = [0.0]
    for a, b in zip(poly, poly[1:]):
        params.append(params[-1] + dist(a, b))
    if params[-1] == 0:
        return [poly[0]] * k
    return resample(params, poly, [params[-1] * i / (k - 1) for i in range(k)])


def _fit(pa, pb):
    """Best similarity b ~ z*a + t (or z*conj(a) + t). Returns (rms, z, t, reflected)."""
    a = [complex(*p) for p in pa]
    b = [complex(*p) for p in pb]
    ca, cb = sum(a) / len(a), sum(b) / len(b)
    a0 = [x - ca for x in a]
    b0 = [x - cb for x in b]
    norm = sum(abs(x) ** 2 for x in a0) or 1e-30
    best = None
    for reflected in (False, True):
        src = [x.conjugate() for x in a0] if reflected else a0
        z = sum(s.conjugate() * y for s, y in zip(src, b0)) / norm
        rms = math.sqrt(sum(abs(z * s - y) ** 2 for s, y in zip(src, b0)) / len(a0))
        t = cb - z * (ca.conjugate() if reflected else ca)
        if best is None or rms < best[0]:
            best = (rms, z, t, reflected)
    return best


def _match(mesh, model, ca, cb, cfg, eps):
    """Correspondence of piece cb onto canonical ca, or None."""
    tol, k = cfg["uv_tolerance"], cfg["samples_per_segment"]
    ida, idb = ca[1], cb[1]
    n = len(ida)
    if n != len(idb):
        return None
    la = [model.segments[s].uv_length for s in ida]
    lb = [model.segments[s].uv_length for s in idb]
    size = max(sum(la), eps) / (2 * math.pi)   # about the piece's radius
    best = None
    for rev in (False, True):
        for r in range(n):
            idx = [((-i if rev else i) + r) % n for i in range(n)]
            if any(abs(la[i] - lb[idx[i]]) > tol * max(la[i], lb[idx[i]], eps) for i in range(n)):
                continue
            pa, pb = [], []
            for i in range(n):
                sa, sb = model.segments[ida[i]], model.segments[idb[idx[i]]]
                ua = [mesh.uvs[t] for t in sa.uv_ids]
                ub = [mesh.uvs[t] for t in sb.uv_ids]
                pa += _samples(ua, k)
                pb += _samples(ub[::-1] if rev else ub, k)
            rms, z, t, reflected = _fit(pa, pb)
            if rms <= tol * size and (best is None or rms < best[0]):
                best = (rms, idx, rev, z, t, reflected)
    if best is None:
        return None
    _, idx, rev, z, t, reflected = best
    seg_map = {ida[i]: idb[idx[i]] for i in range(n)}
    affine = (z.real, -z.imag, t.real, z.imag, z.real, t.imag)   # u' = a u + b v + c ; v' = d u + e v + f
    if reflected:
        affine = (z.real, z.imag, t.real, z.imag, -z.real, t.imag)
    return MirrorPair(ca[0], cb[0], seg_map, uv_transform=affine, reflected=reflected, reversed=rev)


def similar_groups(mesh, topo, model, cfg=None):
    """Pairs (canonical, member) for every similar piece, with segment maps and UV transforms."""
    cfg = cfg or load_config("defaults")["symmetry"]
    eps = load_config("defaults")["geometry"]["eps"]
    canon, pairs = [], []
    for p in topo.pieces:
        if len(p.loops) != 1:
            continue
        cand = (p.id, model.loops[(p.id, 0)])
        for c in canon:
            m = _match(mesh, model, c, cand, cfg, eps)
            if m is not None:
                pairs.append(m)
                break
        else:
            canon.append(cand)
    return pairs


def detect_mirrors(model, topo, cfg=None, mesh=None):
    """Similar-piece pairs (see similar_groups). mesh is needed for the UV fit."""
    if mesh is None:
        raise ValueError("detect_mirrors needs the mesh to compare pieces in UV space.")
    return similar_groups(mesh, topo, model, cfg)


def apply_uv(affine, p):
    a, b, c, d, e, f = affine
    return (a * p[0] + b * p[1] + c, d * p[0] + e * p[1] + f)
