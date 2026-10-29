"""Marvelous Designer OBJ loader (positions + pattern UVs). Welded or unwelded."""
from ..core.config import load_config
from ..core.mesh import MeshData
from . import register


def _index(token, count, line_no, what):
    try:
        i = int(token)
    except ValueError:
        raise ValueError(f"Line {line_no}: bad {what} index '{token}'.")
    i = i - 1 if i > 0 else count + i  # OBJ is 1-based; negatives are relative
    return i


@register("md")
def load_md_obj(path):
    profile = load_config("sources")["sources"]["md"]
    if not any(str(path).lower().endswith(e) for e in profile["extensions"]):
        raise ValueError(f"MD source expects one of {profile['extensions']}: {path}")

    scale = profile["unit_scale"]
    positions, uvs, faces, face_uvs = [], [], [], []
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except OSError as e:
        raise ValueError(f"Cannot open '{path}': {e}")
    with f:
        for n, line in enumerate(f, 1):
            parts = line.split()
            if not parts:
                continue
            tag = parts[0]
            try:
                if tag == "v":
                    positions.append(tuple(float(x) * scale for x in parts[1:4]))
                elif tag == "vt":
                    uvs.append((float(parts[1]), float(parts[2])))
                elif tag == "f":
                    vs, ts = [], []
                    for tok in parts[1:]:
                        sub = tok.split("/")
                        vs.append(_index(sub[0], len(positions), n, "vertex"))
                        if len(sub) > 1 and sub[1]:
                            ts.append(_index(sub[1], len(uvs), n, "UV"))
                    faces.append(vs)
                    face_uvs.append(ts)
            except (IndexError, ValueError) as e:
                raise ValueError(f"{path}: line {n}: {e}")
    return MeshData(positions, uvs, faces, face_uvs, source_id="md")
