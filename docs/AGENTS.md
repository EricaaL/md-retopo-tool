# AGENTS.md

Guidance for AI agents working in this repository. The root `CLAUDE.md` imports this file, so it loads automatically. Keep it current: update it after any structural change.

## Purpose

A Maya tool that shortens the retopology of a Marvelous Designer (MD) garment. For each pattern piece it builds the outer border ring of quads first, then fills the interior with quads, the way an artist would by hand.

- The artist sets the quad count on one side of a seam. The other side of the seam follows, so seams always weld cleanly. A large length mismatch across a seam is flagged as a warning, not blocked.
- The output is a neat, even, **quad-only** base mesh. Odd-shaped pieces get poles where needed; **no tris**. The artist subdivides afterwards (cloth sim, sculpt base, game asset). The tool does not subdivide.
- Internal lines and darts become edge loops. They come from MD's DXF pattern export, from curves drawn in Maya, or both.
- Similar pieces (alike in the UV editor within `symmetry.uv_tolerance`, the tolerance slider; mirrored or not) are detected automatically, share counts **and one topology** (the lowest-id piece is built, the others get a copy). Manual link/unlink is available.
- Starting counts come from a **target edge length** (`solver.target_edge_length`). After that, counts are set per seam. Tiny pieces get their own finer target (`detail`).
- Openings sewn onto the middle of another piece (arms on a torso, a neck on a head, pockets, appliqués) become guide lines on that base piece, with counts matched to the opening, and the base's topology is built around them. The faces under the opening are kept in a face set the artist can delete (`attach.inside`: `keep`, default, or `delete`). The attached piece stays its own piece.
- **Auto mode** (default) uses `ring.auto_rings` border rings (1), places the poles itself and builds the mesh on the drape in one go. **Manual mode** lays every piece out flat beside the garment (the flat stage):
  - Seam-colour curves show what sews to what, and count labels show the quads per segment.
  - Sliders (density, outline ring width) and count edits regenerate it live. Clicking a border edge picks its segment in the panel.
  - **Lock** stops regeneration so the artist can edit the flat mesh by hand. **To 3D** projects it, edits included, onto the drape.
  - Pole dragging and per-free-edge ring counts are planned.
- Count edits recompute only the pieces whose counts change (the piece, its seam partners, its similar twin) and redraw them.
- Output: one combined, welded, quad-only mesh with the MD pattern UVs, one face set per pattern piece and one attach set per attach. Every node and set carries a name prefix (the OBJ or scene mesh name): `<prefix>_retopo`, `<prefix>_piece_01`, `<prefix>_attach_inside_01_07`. The original MD mesh is kept and hidden.
- Optional final step: thickness (`core/thickness.py`). The shell grows inward, with a rim on every open border and two support loops in it.
- Packaging: pure Python with no third-party dependencies (numpy is used if Maya provides it, with a fallback). Installed as a Maya module (`.mod`) with a shelf button.
- Source: MD only, welded or unwelded, with pattern UVs. **OBJ** is read in pure Python (`sources/md.py`). A mesh already in the scene (for example an imported FBX) is read through `maya_adapter.scene.read_mesh`, in scene units with no unit scale. ZBrush is out of scope for now; the source registry leaves room for it.
- Target: Maya 2024+, so **Python 3.10 minimum**. Don't use 3.11+ syntax or stdlib (no `tomllib`, `ExceptionGroup`, `Self`, and so on). `maya.cmds` / OpenMaya 2, dockable PySide6 UI.

## Pipeline

1. Load and validate the MD mesh (UVs required).
2. Normalise welding so welded and unwelded exports take one path.
3. Pieces (UV shells), seams (UV-border edges shared in 3D, including same-piece darts) and surface contacts (borders lying on another piece's faces).
4. Guides: attach guides from surface contacts (DXF, manual curves planned), and similar-piece detection.
5. Split border loops into segments at corners (plus corners where an attach chord meets a border; `pipeline.build_model` runs 3–5 and the guides). Then plan each loop's layout (`layout.py`: quad, npole, loop or free).
6. Solve quad counts (seam, similar-piece and guide links, parity per loop and per guide region, minimum counts, then balancing toward the layout).
7. Sample each segment at its count, then build the border ring(s).
8. Fill the interior from the FILL template registry, tidy away poles that local edits can cancel (`tidy.py`), then relax in 2D. Manual pole positions are pinned constraints.
9. Project the flat result back onto the 3D drape by barycentric lookup.
10. Build one combined mesh, weld the seams, split ear-style fins into sheets, create face sets, and optionally add thickness.

Manual mode inserts the flat stage between 8 and 9: `pipeline.flatten` lays the patches out (`core/flat.py`); the artist adjusts and may edit it; `pipeline.transfer_flat` splits the (edited) flat mesh back into per-piece patches and runs 9–10 on them.

## Layout

`docs/` holds documentation only. All code lives in `retopo_tool/` and `tests/` at the project root. *(planned)* marks parts that don't exist yet.

```
CLAUDE.md                   # imports docs/AGENTS.md
install_retopo_tool.py      # drag into a Maya viewport: adds the ReTopo shelf button, opens the window
.claude/agents/             # explore.md, explore-deep.md: read-only search agents
docs/                       # documentation only
retopo_tool/
  config/                   # defaults.json, sources.json: all thresholds and profiles
  core/                     # pure Python, no maya import
    mesh.py                 #   all shared dataclasses (the contracts)
    config.py               #   reads config/*.json, checks the version
    geom.py                 #   shared 2D/3D geometry helpers
    validate.py             #   step 1, plus check_patch for steps 7-8
    weld.py                 #   step 2
    topology.py             #   step 3: pieces, seams, near seams, surface contacts
    guides.py               #   step 4: attach guides (loops and chords) -> SegmentModel.regions
    symmetry.py             #   step 4: similar pieces in UV (similar_groups); DXF/curve guides planned
    segments.py             #   step 5: corners, segments, seam links, attach_to
    layout.py               #   step 5b: layout per loop, minimum counts (rings, guide regions)
    solver.py               #   step 6: counts, GF(2) parity, balancing, side-rule registry
    ring.py                 #   step 7: segment sampling, border rings
    fill/                   #   step 8: FILL registry (__init__) + templates
      grid.py npole.py annulus.py loop.py split.py
    tidy.py                 #   step 8: doublet merges and pole-cancelling edge flips
    relax.py                #   step 8: 2D Laplacian relax
    flat.py                 #   manual flat stage: layout beside the garment, seam colours, labels, split back
    project.py              #   step 9: flat patch -> 3D drape (barycentric in the MD faces)
    assemble.py             #   step 10: combine, weld seams, split fins, keep UVs
    thickness.py            #   step 10 (optional): inward shell, rim with support loops
    pipeline.py             #   wires the steps: build_model, solve_garment, build_pieces (+ topology copy), build_output
  sources/                  # SOURCE registry: md.py; dxf_aama.py (planned)
  maya_adapter/             # the only place that imports maya
    scene.py                #   MeshData -> Maya mesh (OpenMaya 2), face sets per piece and per attach
    host.py                 #   for the UI: Qt binding, docking, replace_mesh, import_original, select_sets
    batch.py                #   headless run: mayapy -m retopo_tool.maya_adapter.batch <obj> <out.ma>
                            #     [--target cm] [--similar-tol t] [--attach-inside keep|delete]
  ui/
    session.py              #   GarmentSession: load/analyze/build/count edits; plain Python, tested
    window.py               #   dockable Qt window (PySide6, PySide2 fallback); Maya only via host.py
tests/                      # pytest on synthetic pieces
```

## Rules

These apply `docs/implementation-guide.md` to this project. Only these sections of the guide apply:
- 1–4
- 6 and 7
- 9 (validation only)
- 10 (input is untrusted)
- 12 (iteration and recursion caps)
- 14 and 15

These parts are not relevant: events (5), lifecycle loops (8), tenancy, SSRF, LLM and cost rules (11, 12-cost).

- **Data over code.** Tolerances, angles, ratios, weights and source profiles live in `retopo_tool/config/*.json`, not in algorithms. `geometry.eps` is the one numeric epsilon. Every config file carries `version`; `load_config` rejects an unknown version.
- **Registries, not branching.** Sources, solver side rules and fill templates are added by registering a function. No if/elif chains on type names. Fill templates are tried in the order of `fill.templates`.
- **Pure core.** `retopo_tool/core/` never imports `maya`. Maya I/O lives in `maya_adapter/`. Siblings in `core/` talk through the dataclasses in `mesh.py`.
- **Import boundaries** (enforced by `tests/test_boundaries.py`):
  - A `core/` step module imports only the stdlib, `.mesh`, `.config` and `.geom`. `pipeline.py` is the only exception; it wires the steps together.
  - A `core/fill/` template imports only `..mesh`, `..config`, `..geom` and its registry `.`. Templates never import each other; they recurse through `fill_region`.
  - `sources/` imports only `core.mesh` and `core.config`.
- **One owner per fact.** Each vocabulary or contract lives in one file. Config is read, not copied.
- **Input is untrusted.** Validate meshes, UVs, DXF files and user counts. Fail with a clear, actionable message. A bad guide or DXF warns and is skipped; it never blocks the run.
- **Never silently change a user-set value.** The count solver may adjust only counts the user hasn't set. If constraints can't be met it names the segments involved and suggests a fix. The fill never changes counts: if it can't fill a piece, it reports the piece's segment counts.
- **Quads only.** Every output face has 4 vertices. A step that would produce a tri is a bug. `check_patch` verifies every flat patch.
- **Contracts are additive.** Don't rename existing fields; add new ones with defaults. Saved garment JSON (counts, corners, mirror links, guides, ring counts, pole positions) carries a version.
- **Preview stays incremental.** A count or pole edit must not recompute the whole garment, only `affected_pieces`.

## Conventions

- **Units.** `sources.json` `unit_scale` converts MD units to Maya scene units. MD OBJ exports are in **mm** (confirmed on real exports), so the MD profile uses 0.1 to get Maya cm. All lengths in `defaults.json` (weld tolerance, `target_edge_length`) are in scene units (cm) after that scaling. The real exports had UVs roughly in a 0–2 range, not 0–1. Nothing depends on the UV scale: `seam_uv_length_ratio_warn` is a ratio, and the fill works relative to the piece's own edge length.
- **Counts.** A count is the number of quad edges along a segment. Initial count = `round(3D length / target)`, at least `min_count`. The target is `solver.target_edge_length`, except on a piece whose 3D span is under `detail.size_ratio` targets: it gets `span / detail.edges_across` (`pipeline.detail_targets`), so eyes and pupils get a real outline. Linked segments average their starting counts.
- **Parity.** Each border loop (outer and every hole), and each cycle of a guide region, is made even (`SegmentModel.cycles()`). The math only needs each piece's total to be even. The stricter rule is on purpose: it keeps every ring, hole and region fillable on its own.
- **Layouts and balancing** (`layout.py`, `solver._balance`). Each outer loop gets a layout from its UV corner turns (measured over `layout.window` of each segment):
  - `quad`: 4+ candidate corners (turn ≥ `corner_min_deg`); the best 4 by total turn minus `side_balance_weight` × side-length imbalance. The scarf body (a square with two clipped corners) becomes one grid this way.
  - `npole`: 3 corners. `loop`: fewer than 3. `free`: any reflex corner, or a hole loop.

  After parity, a bounded local search (±1, ±2 and pair moves, `solver.balance_max_steps`) nudges counts the user hasn't set so opposite quad sides match and every N-pole spoke is ≥ 1 (`geom.npole_spokes`), weighted by `solver.balance_weights` against drift from the length-based count. It rolls back if a loop would end odd. Anything left unbalanced is named in a warning. The 4-segment side rule only applies without layouts. The fill takes the layout corners as its top-level corners.
- **Loop layouts get grid corners from the samples** (`layout.loop_corners`). A `loop` outline has no corners between segments, but it can still turn sharply inside one (the teddy's tapered leg and ear pieces). After sampling, 4 border vertices with side counts (a, b, a, b) are picked by their turn over `layout.loop_corner_window` samples either side. Every one must turn at least `loop_corner_min_deg`, and `loop_corner_mean_ratio` times what a circle with that many samples turns, so a disc keeps the fill's virtual corners (4 poles on the ring is right for a disc). The ring then turns at those vertices and the inside is one pole-free grid.
- **The fill turns where the ring turned.** Without layout corners, the fill's corners are its angle corners (`fill.corner_angle_deg`) plus every inner-loop vertex where the border ring made a corner quad or a reflex fan (`fill_piece(ring_corners=...)`). A fill corner anywhere else leaves a pole on the ring.
- **Minimum counts.** A segment needs `1 + rings × (convex layout corners at its ends)` edges (turn ≥ `layout.ring_corner_deg`), so the ring keeps an inner edge on every side. Guide regions use the same rule per cycle (no ring under an opening) and need ≥ 4 edges per cycle (`layout.region_minimums`). A user count below the minimum warns.
- **Segment sampling.** Segments are sampled at equal **3D** arc length, then mapped to UV at the same edge parameter. Both sides of a seam therefore get identical 3D points (`FlatPatch.fixed3d`), so step 10 welds seams by index.
- **Winding.** Every loop has its interior on the left: outer CCW, holes CW in UV. `FlatPatch` quads are CCW in UV. `FlatPatch.uv_flipped` marks pieces whose MD faces are CW in UV (a mirrored pattern piece). Step 9/10 must flip those quads to keep the 3D normals.
- **Ring corners.** A convex corner gets one corner quad (each inner side loses 1 edge). A reflex corner gets a fan of 3 quads (each inner side gains 1). A plain corner gets a straight spoke. With a quad/npole layout only the layout corners are convex. If a side would drop below 1 edge, the piece gets fewer rings and a warning.
- **Ring depth** is `ring.depth_ratio` × edge, cut locally to `ring.clearance_ratio` × the room along the inward bisector (ray cast to the first border edge, skipping `ring.clearance_skip` edges either side of the vertex). A narrow strip gets a shallow ring instead of none; the global halving stays as a last resort.
- **Fill order** (`fill.templates`):
  1. `grid`: 4 sides, opposite sides equal; a Coons patch.
  2. `npole`: 3/5/6 sides, every spoke ≥ 1 and inside the region (it needn't be convex); one pole.
  3. `annulus`: one hole with the same edge count; bridged.
  4. `loop`: fewer than 4 corners; virtual corners, then a grid.
  5. `split`: cut holes or split along chords, then recurse.

  Recursion is capped by `fill.max_depth` and `fill.max_attempts`. A large region needs many levels of cuts: with a depth of 12 the bodice at 0.7 × its unit edge (~1,300 quads) used up all 4,000 attempts in about 2 minutes and failed; 30 fills it in under 1 s. `loop` also takes small leftovers (at most `fill.loop_any_max` edges) that have 4+ corners, so deep branches don't fail.
- **How `split` picks chords.** A quad mesh wants every region corner near 90° or 180°, so each chord end is scored by how far its two angles are from those values. A chord square to a straight side, or one extending a side of a reflex corner, costs 0. A chord into a convex corner, or along a reflex bisector, costs about one pole.
  1. Every pair is ranked with that cheap cost.
  2. Only the best visible `split_pair_pool` pairs get the full score: chord counts within `chord_count_slack` of the length, and whether each part fills directly (grid 0, N-pole `npole`, small leftover `leftover`). Short cut-offs that can't shrink either part also take places in the pool; when they take all of it, the pool stretches until one usable chord turns up, instead of leaving the region with none. A `chord_count_slack` of 0.75 saved 16 poles on the teddy, but on the pupils (whose UV outline crosses itself) it made quads whose corners collapse in 3D and opened 18 edges, so it stays 0.5.
  3. Branching narrows with depth: `max_split_candidates // depth`, at least `min_split_candidates`.

  All weights are in `fill.split_weights`.
- **Install and UI.** Dragging `install_retopo_tool.py` into a viewport runs `onMayaDroppedPythonFile`. It puts a **ReTopo** button on the current shelf (replacing an earlier one) and saves the shelves. The button's command adds the project folder to `sys.path`, so the tool runs from where the folder is and no `.mod` is needed (`.mod` paths with spaces are unreliable). Moving the folder means dragging the installer in again. Each click shuts down the open window and its scriptJob, drops the `retopo_tool` modules from `sys.modules` and imports them fresh, so code changes apply without restarting Maya.
  - Panel order: **1 Source** (OBJ or selected mesh, scene units, prefix and Rename), **2 Settings**, **3 Retopo** (Auto and Manual tabs; Manual steps are numbered 1 Flatten, 2 Adjust, 3 Lock, 4 To 3D), **4 Finish** (thickness). Below: progress with **Cancel**, then the *Pieces & counts* and *Log* tabs (the Log tab counts unread warnings). The controls scroll, so a short dock still works.
  - One action at a time: while one runs, the action buttons are off and Cancel is on. Progress calls `processEvents` (per piece during the fill, through `build_pieces(progress=...)`), and `session.cancel_requested` stops at the next step with `Cancelled`, leaving the previous result as it was.
  - **Size guard:** before a full fill the session estimates the quads (3D area / edge², × `ui.quad_estimate_factor`). Above `ui.quad_budget` it raises `TooDense`, and the window offers a lighter edge length, Build anyway, or Cancel. Live slider updates only log it.
  - The window drives a `GarmentSession`, which caches its stages (load, group, solve, build). A setting change invalidates only later stages: target and rings re-solve, tolerance regroups, ring width and attach mode rebuild.
  - Count edits go to `user_counts`. **Apply counts** re-solves and rebuilds only the pieces whose counts changed. If the solver refuses, the previous result stays.
  - The UI copies the config and never writes JSON. Building replaces `<prefix>_retopo` and every set under `<prefix>_retopo_sets`. The OpenMaya mesh creation is not undoable. **Rename** renames all of them (`host.rename_prefix`).
  - Sources: an OBJ file, or **Use selected mesh** (world space, its UVs required). The source mesh is hidden after a build, unless that option is off.
  - **Scene units.** An MD OBJ imported into Maya as is comes in 10× too big (mm read as cm). That makes 100× the quads, and the cm thresholds (seam and attach gaps) go wrong. Seen: the teddy gave 101,000 quads in 105 s. **Scene units** Detect treats a mesh whose bounding-box diagonal exceeds `ui.mm_scene_size` as mm. The session then works in real cm and hands every mesh back at scene size (`scene_scale`); edge length and thickness stay in real cm.
  - Live updates are debounced by `ui.live_delay_ms`. When only points moved (`flatten()['same_topology']`, or a thickness change), the scene mesh is updated in place with `MFnMesh.setPoints`; otherwise it is replaced. On the teddy a slider step takes about 1–1.5 s, a count edit about 1 s, a thickness change about 0.05 s.
  - Viewport clicks reach the panel through a `SelectionChanged` scriptJob, killed when the window closes. The curves and labels are drawn as reference (not selectable), so clicks land on the flat mesh's edges.
- **Names shown to the artist** count from 01: piece tables, log messages and set names all go through `mesh.piece_label` (attach sets through `attach_set_name` / `attach_set_pieces`). Ids stay 0-based inside the code.
- **Flat stage** (`core/flat.py`):
  - Patches are scaled to real size (the median segment 3D length / UV length) and stood up in the XY plane, facing the front camera (+Z) and upright as in the UV editor: from the garment's floor height, `flat.beside_gap` past its +x side, through the middle of its depth. Seam curves sit `flat.curve_lift` and labels `flat.label_lift` toward the camera, so the wireframe doesn't hide them.
  - Pattern positions are kept, but a piece overlapping one already placed (MD's mirrored UV overlays) slides right, with `flat.gap` between pieces.
  - `seam_colours` unions `Segment.links` (seams and attach guides), giving each group one colour and each free segment its own. Hues step by the golden ratio.
  - `split_flat` gives each face of the (hand-edited) flat mesh to the piece of the original quad under its centroid, keeping that quad's attach set. Vertices still on a generated border position keep their exact 3D point; new border vertices are projected, then welded by proximity. Non-quad faces made by hand are reported, not dropped: the quads-only rule covers what the tool makes.
- **Thickness** (`core/thickness.py`): area-weighted vertex normals, shared by copies of one point (split fins) so the shell doesn't crack. The inner shell is offset by -normal × thickness, with its faces reversed. Each open border edge gets a rim 1 quad high, or 3 with support loops at `thickness.support_ratio` from each surface. Both shells keep the piece and attach sets; the rim goes in `<prefix>_thickness_rim`.
- **Saved JSON (when built).** Key segments by a stable key (piece id plus start and end UV ids), never by list index. Segment ids change when corners change.
- **Similar pieces** (`symmetry.similar_groups`). Two single-loop pieces match when they have the same segment count, a cyclic correspondence (reversal allowed) whose UV lengths agree within `symmetry.uv_tolerance`, and a 2D similarity fit (rotation, uniform scale, translation, optional reflection, `samples_per_segment` points per segment) whose RMS error is within the tolerance × the piece's radius. Each piece is matched against the first piece of every group. `MirrorPair` carries `uv_transform`, `reflected` and `reversed`; `detect_mirrors` is a wrapper. Pieces with holes are not grouped yet. It can pair unrelated identical pieces, so a manual unlink must exist in the UI.
- **Topology copy** (`pipeline.transfer_patch`). The member samples its own border (and guides), the canonical patch's border vertices map onto it segment by segment, interior vertices go through `uv_transform`, quads are copied (reversed when reflected), then relaxed and checked. Guide segments are matched through the attached pieces' own similar pairs, aligned by direction and (for loops) starting point. If counts, guides or the check disagree, the piece is built on its own with a warning.
- **Attach guides** (`topology.surface_contacts`, `guides.py`):
  - A free border edge is on piece B when both ends and its midpoint lie within `seam_gap_max` of B's faces; with layered parts the nearest piece wins. Runs shorter than `seam_min_run_edges` are dropped (touching cloth isn't sewing), and `Segment.attach_to` records the base.
  - Attached segments are chained by 3D ends into runs per base. A run whose median gap is above `attach_gap_median` is skipped: sewing pulls a border to about 1 mm, while layers resting on each other sit at about 2 mm (the scarf).
  - `plan_guides` also returns the border edges of the runs it accepts (`mesh.segment_edges` keys). If any contact was rejected, `build_model` rebuilds the segments with only those edges allowed to attach (`build_segments(sewn=...)`). Otherwise the rejected contacts, flickering between pieces along a seam, cut it into many bits: the scarf strip's self-seam was 42 segments, now 4.
  - A closed run becomes a guide loop: a hole of the region around it, and a region of its own. An open run becomes a chord between two base border vertices (nearest in 3D). `plan_guides` puts corners there and segments are rebuilt. Up to `guide_trim_segments` end segments that lie along the base border are trimmed first.
  - Guide segments are virtual (`uv_points`, `points3d`; the attached piece's own 3D polyline, so the weld closes them) and linked to the attached segments. A guide that would cross the border or another guide is skipped with a warning, and that opening stays open.
  - Each region is ringed and filled on its own (`sample_regions`). The region under an opening gets no ring; it is kept and tagged `attach_inside_<base>_<attached>`, or left out in delete mode. A `pinched` opening (two attached pieces sewn to each other round the rim, like a puffy eye) is closed on its own, so its faces are always kept.
  - In keep mode the guide edges have 3 faces (base outside, base inside, attached piece) until the set is deleted.
- **Fins** (`assemble._split_fins`). An edge with an even number (4+) of faces is split into manifold sheets: faces are sorted by angle around the edge and paired with their neighbour after the widest gap. This covers ear halves pinched onto a head seam and pinched attach rims. Odd counts are left alone.
- **Weld collapse.** If the proximity weld would merge two corners of one quad (tiny pieces), that corner is kept apart instead.
- **Real MD data** (learned from the scarf and teddy exports):
  - **Mirrored UV overlays.** MD lays a mirrored pair of pieces on top of each other in UV, with opposite winding. The weld only merges UVs inside one input UV shell (same 3D vertices, same UV coordinates, same winding), so the pair never fuses.
  - **Multi-way seams.** A 3D edge shared by 3+ faces is valid when it is a pattern border for every face, e.g. a plush ear whose two halves are sewn onto one head line. `SeamEdge` then has 3+ faces and `Segment.partners` lists every partner. The linked counts are shared. Step 10 splits 4-face fins into sheets; a 3-way seam (the scarf's triangle tips meeting the body) stays non-manifold.
  - **Proximity partners must run, sit tight and be mutual** (`segments._drop_short_runs`, `_mutual_partners`):
    - A proximity-only partner is kept only where it holds for `seam_min_run_edges` edges in a row; a knot or fold that merely touches another piece would otherwise cut corners into the outline.
    - A proximity-only run (no exact edge in it) whose median midpoint gap (`Topology.near_gaps`) is above `seam_gap_median` is dropped: the scarf's tips fold onto their own edge at 2–3 mm, while sewn seams sit at 0–1.5 mm.
    - Each side is filtered on its own, so a proximity-only edge is then kept only if the partner has an edge sewn back to it within `seam_gap_max` plus half of both edge lengths (on a self-seam, not within `seam_min_run_edges` along the same loop). Otherwise one side keeps a seam the other dropped, and it can't link.
  - **Fold seams.** A border sewn back onto itself (a cone closure) folds where its previous and next border vertices are the same 3D vertex. That point is a segment corner, so the two halves link and the fold vertex is always a sample point.
  - **MD seams don't conform, welded export or not.** One side can have vertices the other lacks, and the cloth sim leaves gaps of about 1 mm. MD's welded export only merges what lines up exactly. Handling:
    - Step 3 finds sewn edges by proximity (`Topology.near_seams`). Both ends and the midpoint must lie within `seam_gap_ratio` × the edge length, capped at `seam_gap_max`, of another piece's border, and the edge must run within `seam_parallel_cos` of parallel. It also runs on edges that weld exactly, so a multi-way seam where only some pieces weld (a tab between head and body) has every piece see every partner.
    - Step 5 unions exact and proximity partners. It fills free runs of up to `seam_gap_fill_edges` between runs with the same partners, and carries seam corners across to partner pieces (`corner_propagation_passes`). Segments that don't share identical edges link by mutual coverage (`seam_link_coverage`).
    - Step 10 welds border vertices closer than `weld.seam_snap_ratio` × their local sample spacing, to their average position.
  - **Appliqué onto a surface.** MD often sews a piece's border onto the middle of another piece, where that piece has no border. The teddy's arms, legs, neck and eyes are built this way; attach guides handle them (see above). The pupils sit at about 1.8 mm, above `attach_gap_median`, so they stay open edges.
  - **Asymmetric sewing.** MD twins aren't always sewn alike: leg 15's line on body 6 runs 5 edges further than leg 17's on body 5. Their guides then differ, and the bodies are built separately (with matching pole counts).

## Status

Steps 1–3 and 5–9 are implemented in `core/` and `sources/md.py` and covered by `tests/`. Step 4 has attach guides and similar-piece detection. Step 10 is minimal: it combines pieces, welds seams, splits fins, keeps UVs and records the piece per face and the attach sets, with optional thickness. `maya_adapter/batch.py` runs everything headless in Maya 2026 (`D:/Autodesk/Maya2026/bin/mayapy.exe`) and saves a `.ma` plus a JSON report. The ReTopo window has been driven headless in Maya 2026 on the teddy: Auto build, and the Manual flow of Flatten, sliders, edge click to segment, count edit, Lock, a hand edit (vertex move plus `polySplitRing`), To 3D (all quads), thickness and its slider, Rename, and remeshing a scene mesh. In the Maya window itself (driven by a startup script, see Testing): Auto and Manual on both garments, a scene mesh imported with Maya's OBJ importer (mm detected), Cancel during a build, and the size guard. Drag-and-drop install, docking, real mouse dragging and how the curves and labels read on screen have still not been checked by hand.

Real exports (`D:/maya saves/Test/Scarf.obj` and `teddy.obj`, default settings):
- **Scarf**: 4 pieces, no similar pieces, no attaches (its layers rest at about 2 mm). 29 segments (the strip has its 4 sides; it had 128 segments before resting contacts and resting proximity seams were dropped). 1,893 faces, all quads, every piece ringed, **2 poles** in total (it had about 32 before layouts). The body and the strip are pole-free grids. 4 non-manifold edges sit on the 3-way seam where the triangle tips meet the body. 162 open edges, all free borders. Four 1-edge sewn segments at the tip pinch points stay unlinked. About 8 s in Python, about 12 s for an Auto build in the Maya window (including bringing in the 29k-face MD mesh). Most of it is step 3 surface contacts (about 5 s); `segments._link_seams` uses a grid per seam line (`_Line`) and takes under 1 s.
- **Teddy** (welded export): 31 pieces, all fill, about 4 s for an Auto build in the Maya window. 13 similar pairs. Heads 0/1 and 3/4 copy one topology; bodies 5/6 and 7/8 are built separately (see Asymmetric sewing). Attach guides on 8 bases: neck chords on both head halves, eyes, arms, legs.
  - Keep mode: 2,333 faces, all quads, **131 poles** (154 before loop-layout corners, ring corners in the fill and tidy). Pieces 16/18 (tapered) and 19–22 are now pole-free grids, the head halves have 12 (was 15). 44 non-manifold edges, which are the kept attach loops (3 faces each).
  - Delete mode: 2,257 faces, 127 poles, 2 non-manifold edges (self-folds inside the tiny pupil pieces).
  - Most remaining poles sit around attach openings (heads, bodies: `_fill_regions` ignores the piece's layout) and on the cross-shaped and self-overlapping eye pieces 25/29 and 26/30.
  - Both modes: 94 open edges, at the ear-base ends (6 per ear piece) and around the unattached pupils.
  - Pieces 15/17 (small curled leg pieces) and 26/30 (pupil parts) still get no border ring.

Not started:
- DXF, manual-curve and pocket guides (step 4); similar groups for pieces with holes
- manual pole pinning
- per-free-edge ring counts
- saved-garment JSON; in the UI: pole dragging, manual similar-piece unlink

Step 8 (`tidy.py`): an interior doublet (2 edges) merges its two quads; an interior edge flips to either other diagonal of its hexagon when that lowers Σ(valence − 4)² over the four vertices it changes and both new quads are convex. Neither touches the piece border, attach lines, or an edge between two ring vertices, and neither merges quads from different face sets. On the real exports most poles aren't one flip from cancelling, so it rarely fires (the teddy's one doublet); it is a safety net, not the main tool.

Step 8 quality on the synthetic shapes:
- Rectangles, L-shapes and panels with a hole fill as clean grids (no poles) at every tested density, except where unequal counts force a pole pair.
- The bodice fixture gets 5–6 poles, clustered at the neckline/shoulder junction.
- The slowest case is the dense bodice: about 0.4 s for ~730 quads.

These are heuristics tuned on synthetic shapes. Re-check them on a real MD export before tuning further. To view a patch, render `FlatPatch.points`/`quads` with poles highlighted.

## Testing

- `pytest tests/` runs outside Maya on synthetic pieces (`tests/fixtures.py`):
  - rectangle, 2-piece tube, small and large L-shape
  - pentagon, triangle, disc, half-disc
  - mirrored pair, panel with a hole, UV-flipped rectangle, bodice outline
  - pinched insert (3-way seam), mirrored UV overlay, folded cone (fold seam)
  - clipped square (scarf-like), narrow strip, big piece with a tiny one, twin panels (similar, stretched or mirrored)
  - ear on a seam (4-way fin), tube on a panel (attach loop), tube across a seam (chords), puff on a panel (pinched rim)
- Tests that depend on counts pass `fixtures.solver_cfg()`, whose `target_edge_length` of 1.0 matches the fixtures' unit edges (or set it in a loaded config).
- Every rule above needs a test or a check behind it.
- Maya check on a real export: `mayapy -m retopo_tool.maya_adapter.batch <garment.obj> <out.ma>`, run from the project root. It writes `<out>_report.json`, with per-piece layout, rings, poles and attach sets, plus Maya's own face-side, open-edge, non-manifold and bounding-box checks. Maya 2026 runs Python 3.11, so it does not verify 3.10 compatibility.
- **Same result on every Python.** Python 3.12's `sum()` of floats rounds differently from 3.11's. That once changed the teddy's head fill between Maya (3.11) and desktop Python (3.12). Two rules prevent it: `fill/split.py` rounds every score to `fill.score_digits` before ranking (ties then fall back to index order), and code that feeds geometric decisions writes short sums out instead of calling `sum()` (`guides._Surface.closest`). Check with `mayapy` and desktop Python giving the same face counts.
- Check in the real Maya window: `maya.exe -noAutoloadPlugins -script <driver>.mel`, where the MEL line is `python("exec(open(r\"<driver>.py\").read())");`. The driver uses `maya.utils.executeDeferred` to open the window and run handlers, logs a timestamp per step to a file (plus a heartbeat thread) so a hang shows where it stopped, then `cmds.quit(force=True)`. Passing Python through `-command` loses its quoting.
- Window check without the Maya GUI: in `mayapy`, create a `QtWidgets.QApplication` **before** `maya.standalone.initialize()`. Standalone Maya otherwise starts a `QGuiApplication`, and the first widget kills the process with exit 127 and no message. Then call `window.show()` and its handlers. Drag-and-drop and docking only exist in the Maya window itself.
- If a check could not be run, report it as **blocked**, never as passed. No Python 3.10 interpreter is installed on the dev machine yet, so 3.10 compatibility is unverified.
