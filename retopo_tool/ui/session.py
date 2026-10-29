"""State of one garment in the UI: load, analyze, build, edit counts, flat stage, thickness.

Pure Python (no Qt, no maya) so it is tested outside Maya. The window calls
these methods and draws what they return; Maya I/O stays in maya_adapter.

Stages, each cached until a setting or count invalidates it:
  load    -> mesh, topology, segments and attach guides (pipeline.build_model)
  group   -> similar pieces (symmetry.similar_groups; uv_tolerance)
  solve   -> layouts and counts (pipeline.solve_garment; target, rings, your counts)
  build   -> flat patches and the combined mesh (pipeline.build_pieces / build_output)

Manual mode adds the flat stage (flatten, then the artist may lock it and edit
it by hand in Maya) and to_3d, which projects the flat mesh back onto the drape.

Units: everything inside works in real centimetres, like an MD OBJ after its
unit_scale. A scene mesh that is really MD millimetres read as centimetres
(an OBJ imported into Maya as is: 10x too big) is scaled down on the way in;
every mesh this session hands back (build, flatten, to_3d, thickened) is
scaled back to the scene (scene_scale), and to_3d takes the flat mesh in scene
units too.
"""
import os
import re

from ..core.config import load_config
from ..core.flat import seam_colours, segment_labels
from ..core.mesh import MeshData, RingSpec, piece_label, piece_labels
from ..core.pipeline import (build_model, build_output, build_pieces, estimate_quads, flatten, solve_garment,
                             transfer_flat)
from ..core.symmetry import similar_groups
from ..core.thickness import thicken
from ..core.topology import build_topology
from ..core.validate import validate_mesh
from ..core.weld import normalise_welding
from ..sources import load_mesh

_ORDER = ("load", "group", "solve", "build")
LOCKED = "The flat mesh is locked. Unlock it to regenerate (your hand edits on it will be replaced)."
UNITS = ("auto", "cm", "mm")


class SessionError(Exception):
    """A step refused (bad input, conflicting counts). The message says what to change."""


class Cancelled(SessionError):
    """The artist pressed Cancel; nothing was changed."""


class TooDense(SessionError):
    """The build would make more quads than ui.quad_budget. Build with force=True to go ahead."""

    def __init__(self, estimate, target, suggested):
        self.estimate, self.target, self.suggested = estimate, target, suggested
        super().__init__(
            f"This would make about {estimate:,} quads at {target:g} cm per edge, which can take minutes. "
            f"An edge length of {suggested:g} cm gives about the usual density for a garment this size.")


def clean_prefix(name):
    """A Maya-safe name prefix from a file or node name ("|grp|ns:teddy_bear" -> "teddy_bear")."""
    name = os.path.splitext(os.path.basename(str(name)))[0]
    name = name.split("|")[-1].split(":")[-1]
    name = re.sub(r"[^A-Za-z0-9_]", "_", name).strip("_") or "garment"
    return "_" + name if name[0].isdigit() else name


def _scaled(mesh, k):
    if k == 1.0:
        return mesh
    return MeshData([tuple(c * k for c in p) for p in mesh.positions], mesh.uvs, mesh.faces, mesh.face_uvs,
                    mesh.source_id, dict(mesh.meta))


class GarmentSession:
    def __init__(self, cfg=None, progress=None):
        self.cfg = cfg or load_config("defaults")   # a private copy; the JSON files are never written
        self._progress = progress or (lambda step, frac: None)
        self.cancel_requested = False
        self.path = None
        self.prefix = "garment"
        self.scene_scale = 1.0     # scene units per internal cm
        self.source = self.mesh = self.topo = self.model = None
        self.similar, self.layouts, self.res = [], {}, None
        self.patches, self.output, self.face_piece = {}, None, None
        self.output_kind = None    # "auto" (built from the patches) or "flat" (from the flat stage)
        self.output_version = 0    # bumps whenever output changes
        self.flat = self.flat_layout = None
        self.locked = False
        self._flat_stale = False   # the scene's flat mesh may differ from self.flat (hand edits)
        self.user_counts = {}
        self.log = []              # (level, message) since the last call that cleared it
        self._done = set()

    # ---- settings -------------------------------------------------------
    @property
    def rings(self):
        return self.cfg["ring"]["auto_rings"]

    @property
    def ring_width(self):
        return self.cfg["ring"]["depth_ratio"]

    @property
    def target(self):
        return self.cfg["solver"]["target_edge_length"]

    def configure(self, target=None, similar_tol=None, rings=None, attach_inside=None, ring_width=None):
        """Change settings; later stages are redone on the next analyze/build/flatten.
        Returns True if anything changed."""
        changes = []
        if target is not None and target != self.target:
            if not target > 0:
                raise SessionError("Edge length must be above 0.")
            changes.append(("solver", "target_edge_length", float(target), "solve"))
        if similar_tol is not None and similar_tol != self.cfg["symmetry"]["uv_tolerance"]:
            if similar_tol < 0:
                raise SessionError("Similar-piece tolerance can't be negative.")
            changes.append(("symmetry", "uv_tolerance", float(similar_tol), "group"))
        if rings is not None and rings != self.rings:
            if not 0 <= rings <= self.cfg["ring"]["max_rings"]:
                raise SessionError(f"Border rings must be 0-{self.cfg['ring']['max_rings']}.")
            changes.append(("ring", "auto_rings", int(rings), "solve"))
        if ring_width is not None and ring_width != self.ring_width:
            if not ring_width > 0:
                raise SessionError("Ring width must be above 0.")
            changes.append(("ring", "depth_ratio", float(ring_width), "build"))
        if attach_inside is not None and attach_inside != self.cfg["attach"]["inside"]:
            if attach_inside not in ("keep", "delete"):
                raise SessionError("Faces under attaches must be 'keep' or 'delete'.")
            changes.append(("attach", "inside", attach_inside, "build"))
        if changes and self.locked:
            raise SessionError(LOCKED)
        for section, key, value, stage in changes:
            self.cfg[section][key] = value
            self._invalidate(stage)
        return bool(changes)

    def _invalidate(self, stage):
        self._done -= set(_ORDER[_ORDER.index(stage):])

    # ---- progress / cancel ---------------------------------------------
    def _step(self, label, frac):
        """Report progress; stop here if the artist pressed Cancel."""
        self._progress(label, frac)
        if self.cancel_requested:
            self.cancel_requested = False
            raise Cancelled("Cancelled. Nothing was changed.")

    # ---- stages ---------------------------------------------------------
    def load(self, path):
        """Read and check an MD OBJ export; finds pieces, seams and attach guides."""
        self.log = []
        self._step("Reading OBJ", 0.0)
        try:
            src = load_mesh("md", path)
        except (OSError, ValueError) as e:
            raise SessionError(f"Couldn't read {path}: {e}")
        self._ingest(src, src, path, clean_prefix(path), 1.0)

    def load_mesh_data(self, src, name, units="auto"):
        """Use a mesh that is already in the scene.

        units: "cm" (the scene is real size), "mm" (an MD OBJ imported as is, so 10x
        too big), or "auto": mm when the mesh is bigger than ui.mm_scene_size."""
        if units not in UNITS:
            raise SessionError(f"Units must be one of {', '.join(UNITS)}.")
        self.log = []
        self._step("Reading scene mesh", 0.0)
        pos = src.positions
        size = sum((max(p[i] for p in pos) - min(p[i] for p in pos)) ** 2 for i in range(3)) ** 0.5 if pos else 0.0
        if units == "auto":
            units = "mm" if size > self.cfg["ui"]["mm_scene_size"] else "cm"
            if units == "mm":
                self._note([f"{name} is {size:.0f} units across, too big for a garment in cm: treating it as an "
                            "MD OBJ imported in millimetres (edge length and thickness stay in real cm). "
                            "Set Scene units to cm if it really is that big."], "info")
        k = 0.1 if units == "mm" else 1.0
        self._ingest(_scaled(src, k), src, f"scene:{name}", clean_prefix(name), 1.0 / k)

    def _ingest(self, src, original, path, prefix, scene_scale):
        val = validate_mesh(src)
        self._note(val.warnings)
        if not val.ok:
            self._note(val.errors, "error")
            raise SessionError("; ".join(val.errors))
        self._step("Welding", 0.1)
        mesh, wrep = normalise_welding(src, self.cfg["weld"])
        self._step("Finding pieces and seams", 0.2)
        topo = build_topology(mesh)
        self._step("Segments and attach guides", 0.35)
        model, gwarn = build_model(mesh, topo, self.cfg)
        self.path, self.source, self.mesh, self.topo, self.model = path, original, mesh, topo, model
        self.prefix, self.scene_scale = prefix, scene_scale
        self.user_counts = {}
        self.patches, self.output, self.face_piece, self.output_kind = {}, None, None, None
        self.flat = self.flat_layout = None
        self.locked = self._flat_stale = False
        self._note(wrep.warnings + topo.warnings + gwarn)
        self._done = {"load"}
        self._progress("Loaded", 0.45)

    def analyze(self):
        """Steps up to the counts: similar pieces, layouts, counts. No mesh is built."""
        self._need_source()
        if "group" not in self._done:
            self._step("Matching similar pieces", 0.5)
            self.similar = similar_groups(self.mesh, self.topo, self.model, self.cfg["symmetry"])
            self._done.add("group")
            self._invalidate("solve")
        if "solve" not in self._done:
            self._step("Solving counts", 0.6)
            layouts, res = solve_garment(self.mesh, self.topo, self.model, self.similar,
                                         self.user_counts, self.cfg)
            self._note(res.warnings)
            if not res.ok:
                self._note(res.errors, "error")
                raise SessionError("; ".join(res.errors))
            self.layouts, self.res = layouts, res
            self._done.add("solve")
            self._invalidate("build")
        self._progress("Analyzed", 0.65)

    def estimate(self):
        """About how many quads a full build makes at the current edge length."""
        self._need_source()
        return int(estimate_quads(self.mesh, self.topo, self.target) * self.cfg["ui"]["quad_estimate_factor"])

    def suggested_target(self):
        """The edge length that brings the estimate down to a fifth of the budget."""
        ratio = self.estimate() / (self.cfg["ui"]["quad_budget"] / 5)
        return round(self.target * max(ratio, 1.0) ** 0.5, 2)

    def build(self, piece_ids=None, output=True, force=False):
        """Build patches (all, or only piece_ids, keeping the rest) and, unless
        output=False, the combined mesh on the drape. Returns (MeshData in scene
        units or None, face_piece). Raises TooDense before a very large build
        unless force=True."""
        self.analyze()
        if "build" not in self._done or piece_ids is not None:
            full = piece_ids is None or not self.patches
            ids = list(range(len(self.topo.pieces))) if full else sorted(piece_ids)
            if full and not force:
                n = self.estimate()
                if n > self.cfg["ui"]["quad_budget"]:
                    raise TooDense(n, self.target, self.suggested_target())
            specs = {p: RingSpec(self.rings) for p in range(len(self.topo.pieces))}

            def tick(pid, done, total):
                self._step(f"Filling piece {piece_label(pid)} ({done + 1}/{total})", 0.7 + 0.2 * done / total)
            new = build_pieces(ids, self.mesh, self.topo, self.model, self.res.counts, specs, self.cfg,
                               self.layouts, self.similar, progress=tick)
            self.patches.update(new)
            for p in new.values():
                self._note(p.warnings)
                self._note(p.errors, "error")
            self._set_output(None, None, None)
            self._done.add("build")
        if output and self.output_kind != "auto":
            self._step("Projecting and welding", 0.9)
            out, face_piece = build_output(self.patches, self.mesh, self.topo, self.cfg)
            self._set_output(out, face_piece, "auto")
        self._progress("Built", 1.0)
        return self.scene_output()

    def _set_output(self, out, face_piece, kind):
        self.output, self.face_piece, self.output_kind = out, face_piece, kind
        self.output_version += 1

    def scene_output(self):
        """The current result in scene units: (MeshData or None, face_piece)."""
        if self.output is None:
            return None, None
        return _scaled(self.output, self.scene_scale), self.face_piece

    # ---- counts ---------------------------------------------------------
    def set_count(self, seg_id, n):
        if self.locked:
            raise SessionError(LOCKED)
        if seg_id not in range(len(self.model.segments)):
            raise SessionError(f"No segment {seg_id}.")
        if not isinstance(n, int) or n < 1:
            raise SessionError(f"A count must be a whole number of at least 1, got {n!r}.")
        self.user_counts[seg_id] = n

    def clear_count(self, seg_id):
        if self.locked:
            raise SessionError(LOCKED)
        self.user_counts.pop(seg_id, None)

    def apply_counts(self, output=True):
        """Re-solve with your counts and rebuild only the pieces whose counts changed.

        If the solver refuses, the previous counts and mesh stay and SessionError says why."""
        self._need_source()
        if self.locked:
            raise SessionError(LOCKED)
        self.log = []
        old = self.res.counts if self.res else {}
        layouts, res = solve_garment(self.mesh, self.topo, self.model, self.similar, self.user_counts, self.cfg)
        self._note(res.warnings)
        if not res.ok:
            self._note(res.errors, "error")
            raise SessionError("; ".join(res.errors))
        changed = {self.model.segments[s].piece for s, c in res.counts.items() if old.get(s) != c}
        self.layouts, self.res = layouts, res
        self._done |= {"group", "solve"}
        if not self.patches:
            return set()
        if changed:
            self.build(changed, output=output)
        return changed

    # ---- flat stage (manual mode) --------------------------------------
    def flatten(self, force=False):
        """Lay every piece out flat beside the garment (scene units).

        Returns {"mesh": flat MeshData, "curves": {segment: polyline}, "colours":
        {segment: rgb}, "labels": [(segment, point, count)], "same_topology": True
        if only positions changed since the last flatten (update it in place)}."""
        if self.locked:
            raise SessionError(LOCKED)
        self.build(output=False, force=force)
        self._step("Laying pieces out flat", 0.95)
        flat, lay = flatten(self.patches, self.mesh, self.model, self.cfg)
        same = self.flat is not None and not self._flat_stale and self.flat.faces == flat.faces
        self.flat, self.flat_layout, self._flat_stale = flat, lay, False
        self._progress("Flat", 1.0)
        k = self.scene_scale
        sc = (lambda p: tuple(c * k for c in p)) if k != 1.0 else (lambda p: p)
        lift = self.cfg["flat"]["label_lift"]
        up = self.cfg["flat"]["curve_lift"]   # toward the camera, so the wireframe doesn't hide them
        return {"mesh": _scaled(flat, k),
                "curves": {sid: [sc((p[0], p[1], p[2] + up)) for p in poly] for sid, poly in lay.curves.items()},
                "colours": seam_colours(self.model, lay.curves),
                "labels": [(sid, sc(p), n) for sid, p, n in segment_labels(lay, self.res.counts, lift)],
                "same_topology": same}

    def lock(self, on=True):
        if on and self.flat is None:
            raise SessionError("Flatten first.")
        if self.locked and not on:
            self._flat_stale = True   # hand edits: the next flatten must replace the scene mesh
        self.locked = bool(on)

    def to_3d(self, edited=None):
        """Project the flat mesh (as edited in Maya, in scene units; or as generated)
        onto the drape. Returns (MeshData in scene units, face_piece)."""
        if self.flat is None:
            raise SessionError("Flatten first.")
        self.log = []
        self._step("Projecting the flat mesh onto the drape", 0.5)
        flat = self.flat if edited is None else _scaled(edited, 1.0 / self.scene_scale)
        out, face_piece, warnings = transfer_flat(flat, self.flat, self.flat_layout, self.mesh, self.topo, self.cfg)
        self._note(warnings)
        if not out.faces:
            raise SessionError("Nothing on the flat mesh lies over a pattern piece.")
        self._set_output(out, face_piece, "flat")
        self._progress("Built", 1.0)
        return self.scene_output()

    # ---- thickness ------------------------------------------------------
    def thickened(self, thickness, support=True):
        """The current result with a thickness in real cm (inward, rim with support
        loops), in scene units. Returns (MeshData, face_piece). Face sets: both
        shells keep their attach sets; the rim goes in "thickness_rim"."""
        if self.output is None:
            raise SessionError("Build the mesh (Auto) or press To 3D (Manual) first.")
        try:
            out, face_piece, rim = thicken(self.output, thickness, support,
                                           self.cfg["thickness"]["support_ratio"], self.face_piece,
                                           self.cfg["geometry"]["eps"])
        except ValueError as e:
            raise SessionError(str(e))
        n = len(self.output.faces)
        sets = {name: ids + [i + n for i in ids] for name, ids in self.output.meta.get("face_sets", {}).items()}
        sets["thickness_rim"] = rim
        out.meta["face_sets"] = sets
        return _scaled(out, self.scene_scale), face_piece

    # ---- table data -----------------------------------------------------
    def piece_rows(self):
        twin = {}
        for m in self.similar:
            twin[m.a], twin[m.b] = m.b, m.a
        rows = []
        for piece in self.topo.pieces:
            p = self.patches.get(piece.id)
            lay = self.layouts.get((piece.id, 0))
            notes = (p.errors + p.warnings) if p else []
            rows.append({
                "id": piece.id,
                "label": piece_label(piece.id),
                "layout": lay.kind if lay else "",
                "quads": len(p.quads) if p else None,
                "rings": p.rings if p else None,
                "poles": len(p.poles) if p else None,
                "twin": piece_label(twin[piece.id]) if piece.id in twin else "",
                "attach_sets": sorted(p.face_tags) if p else [],
                "ok": p.ok if p else None,
                "note": notes[0] if notes else "",
            })
        return rows

    def segment_rows(self, piece_id):
        """Border segments of the piece (loop by loop), then its attach guides. Lengths in real cm."""
        counts = self.res.counts if self.res else {}
        ids = [s for (pid, li), seg_ids in sorted(self.model.loops.items()) if pid == piece_id for s in seg_ids]
        ids += [s.id for s in self.model.segments if s.piece == piece_id and s.uv_points is not None]
        rows = []
        for sid in ids:
            s = self.model.segments[sid]
            kind = "attach guide" if s.uv_points is not None else \
                f"on piece {piece_label(s.attach_to)}" if s.attach_to >= 0 else "seam" if s.partners else "free edge"
            rows.append({"id": sid, "kind": kind, "length": s.length, "partners": piece_labels(s.partners),
                         "count": counts.get(sid), "yours": sid in self.user_counts})
        return rows

    def segment_of_edge(self, v0, v1):
        """Which segment a flat-mesh edge (two vertex ids) lies on, or None."""
        if self.flat_layout is None:
            return None
        return self.flat_layout.border_edges.get((min(v0, v1), max(v0, v1)))

    def flat_faces_of(self, pieces):
        """Face ids of these pieces on the generated flat mesh."""
        ids, start = [], 0
        for pid in sorted(self.flat_layout.quads) if self.flat_layout else ():
            n = len(self.flat_layout.quads[pid])
            if pid in pieces:
                ids += range(start, start + n)
            start += n
        return ids

    # ---- helpers --------------------------------------------------------
    def _need_source(self):
        if "load" not in self._done:
            raise SessionError("Load an MD OBJ export or a scene mesh first.")

    def _note(self, messages, level="warning"):
        self.log += [(level, m) for m in messages]
