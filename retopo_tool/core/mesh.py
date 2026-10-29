"""Plain data contracts for a loaded garment mesh. No maya import."""
from dataclasses import dataclass, field


@dataclass
class MeshData:
    """Polygon mesh with per-face-corner UVs.

    faces[i] lists position indices; face_uvs[i] lists UV indices, same length.
    """
    positions: list
    uvs: list
    faces: list
    face_uvs: list
    source_id: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class WeldReport:
    """What normalisation changed. position_map: old position index -> new."""
    position_map: list = field(default_factory=list)
    dropped_faces: list = field(default_factory=list)  # original face indices
    kept_faces: list = field(default_factory=list)     # original index per output face
    warnings: list = field(default_factory=list)


@dataclass
class ValidationReport:
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.errors


@dataclass
class SeamEdge:
    """A 3D edge whose faces have different UV edges (a sewn seam or dart).

    Usually two faces. Three or more means a multi-way seam: a piece sewn into
    another seam, or an opening pinched flat onto a line (a plush ear's base).
    """
    key: tuple      # (min vertex, max vertex)
    faces: tuple
    pieces: tuple   # one entry per face; equal entries mean a dart inside one piece


@dataclass
class Piece:
    """A UV shell. loops are ordered UV-id rings; loops[0] is the outer border (CCW in UV)."""
    id: int
    faces: list
    loops: list


@dataclass
class Topology:
    pieces: list
    seam_edges: dict   # edge key -> SeamEdge
    uv_vertex: list    # UV id -> welded 3D vertex id (-1 if unused)
    face_piece: list
    warnings: list = field(default_factory=list)
    # UV border edge (min uv, max uv) -> partner piece ids found by proximity: seams
    # sewn across a gap or with mismatched vertices. Added to any exact seam_edges
    # partners of the same edge. See topology.near_seams.
    near_seams: dict = field(default_factory=dict)
    # UV border edge (min uv, max uv) -> pieces whose surface (not border) it sits
    # on: an opening sewn onto the middle of another piece. See topology.surface_contacts.
    surface_contacts: dict = field(default_factory=dict)
    # (UV border edge, partner piece) -> 3D gap at the edge's midpoint, for every
    # near_seams entry. Tells sewing (pulled to about 1 mm) from cloth resting.
    near_gaps: dict = field(default_factory=dict)


@dataclass
class Segment:
    """A run of border edges between two corners."""
    id: int
    piece: int
    loop: int
    index: int          # order within its loop
    uv_ids: list        # polyline of UV ids, first == last for a closed single segment
    edge_keys: list     # 3D edge keys along the segment
    length: float       # 3D length
    uv_length: float
    tag: int            # first partner piece id, -1 for a free (unsewn) border
    links: list = field(default_factory=list)   # seam partner segment ids
    partners: tuple = ()  # every partner piece id (more than one on a multi-way seam)
    attach_to: int = -1   # piece whose surface this (free) segment is sewn onto, -1 if none
    uv_points: list = None   # guide segments (no MD border): UV polyline on their piece
    points3d: list = None    # guide segments: matching 3D polyline
    ends: tuple = None       # guide chord segments: (start uv id, end uv id) on the base border, None if free

    @property
    def mesh_edges(self):
        return len(self.edge_keys)


@dataclass
class SegmentModel:
    segments: list
    loops: dict         # (piece id, loop index) -> segment ids in order
    warnings: list = field(default_factory=list)
    # Pieces split by attach guides (guides.attach_guides): piece id -> regions,
    # each {"cycles": [[(segment id, reversed)], ...] (outer first, then holes),
    #       "inside": attached piece ids if this region lies under an attached opening, else None,
    #       "pinched": the opening's rim is two attached pieces sewn to each other (never left out)}
    regions: dict = field(default_factory=dict)

    def cycles(self):
        """Every closed run of segments that must have an even count: piece loops, then regions."""
        out = dict(self.loops)
        for pid, regs in self.regions.items():
            for k, reg in enumerate(regs):
                for c, cyc in enumerate(reg["cycles"]):
                    out[(pid, f"region{k}.{c}")] = [s for s, _ in cyc]
        return out


@dataclass
class MirrorPair:
    """Piece b looks like piece a in UV (a is the group's canonical piece)."""
    a: int
    b: int
    seg_map: dict       # segment id in piece a -> segment id in piece b
    uv_transform: tuple = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)  # a's UV -> b's UV: u'=a0 u+a1 v+a2, v'=a3 u+a4 v+a5
    reflected: bool = False   # the transform mirrors (quads must be reversed to stay CCW)
    reversed: bool = False    # b's segments run the other way round from a's


@dataclass
class SolveResult:
    counts: dict = field(default_factory=dict)    # segment id -> quad count
    initial: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.errors


@dataclass
class RingSpec:
    """Border rings for one piece. rings=None means auto mode (config ring.auto_rings)."""
    rings: int = None


@dataclass
class FlatPatch:
    """Quad mesh of one piece in UV space (steps 7-8).

    points are 2D; quads are CCW vertex-id lists of length 4. boundary maps a
    segment id to its vertex ids in segment direction. fixed3d holds the exact
    3D position of each piece-border vertex (seam partners get identical points).
    """
    piece: int
    points: list = field(default_factory=list)
    quads: list = field(default_factory=list)
    boundary: dict = field(default_factory=dict)
    fixed3d: dict = field(default_factory=dict)
    ring_vertices: set = field(default_factory=set)  # piece border and ring vertices
    rings: int = 0
    uv_flipped: bool = False                         # source faces are CW in UV
    poles: dict = field(default_factory=dict)        # interior vertex id -> valence (!= 4)
    face_tags: dict = field(default_factory=dict)    # face set name -> quad indices (attach insides)
    inner_segments: set = field(default_factory=set)  # boundary segments with faces on both sides
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.errors


@dataclass
class Layout:
    """How one border loop is laid out for the fill (steps 6-8).

    kind: "quad" (4 sides, a grid when opposite sides match), "npole" (3/5/6
    sides, one pole), "loop" (fewer than 3 corners), or "free" (concave or a
    hole: the fill splits it). sides: segment ids per side, in loop order;
    each side starts at a layout corner. side_min: least edge count per side so
    the border ring keeps at least one inner edge.
    """
    piece: int
    loop: int
    kind: str
    sides: list = field(default_factory=list)
    side_min: list = field(default_factory=list)
    segment_min: dict = field(default_factory=dict)  # free/loop kinds: segment id -> least count


@dataclass
class FlatLayout:
    """Where every piece's flat patch sits in the flat stage (core.flat).

    A patch point (u, v) of piece p sits at x = ox + scale*u, z = -(oz + scale*v)
    (y = the layout's height, in scene units). pieces: piece id -> {"offset": (ox, oz), "start":
    first global vertex id, "count": vertex count, "uv_flipped": bool}.
    quads: piece id -> list of quads in global vertex ids (to find which piece and
    quad a hand-edited face came from). fixed: global vertex id -> 3D drape point
    for piece-border vertices. tags: piece id -> {quad index: face set name}.
    curves: segment id -> flat polyline; labels: [(segment id, point, count)].
    """
    scale: float
    pieces: dict
    quads: dict
    fixed: dict
    tags: dict = field(default_factory=dict)
    border_edges: dict = field(default_factory=dict)   # (min v, max v) global -> segment id
    curves: dict = field(default_factory=dict)
    labels: list = field(default_factory=list)


def segment_edges(seg):
    """A border segment's edges as sorted UV-id pairs (the loop edge keys topology uses)."""
    ids = seg.uv_ids
    return [(min(a, b), max(a, b)) for a, b in zip(ids, ids[1:])]


def piece_label(pid):
    """How a piece is shown to the artist: 1-based, two digits (piece 0 -> "01")."""
    return f"{pid + 1:02d}"


def piece_labels(pids):
    return ", ".join(piece_label(p) for p in pids)


def attach_set_name(base, attached):
    """Face set for the faces of piece `base` under the opening of pieces `attached`."""
    return "attach_inside_" + "_".join(piece_label(p) for p in [base] + sorted(attached))


def attach_set_pieces(name):
    """attach_set_name's inverse: (base, [attached]) as 0-based piece ids."""
    ids = [int(x) - 1 for x in name[len("attach_inside_"):].split("_")]
    return ids[0], ids[1:]
