# MD ReTopo Tool

A Maya tool that speeds up the retopology of a Marvelous Designer (MD) garment. For each pattern piece it builds the outer border ring of quads first, then fills the interior with quads, the way an artist would by hand.

## Features

- **Quad-only output.** A neat, even base mesh with no tris. Odd-shaped pieces get poles where needed. Subdivide it afterwards for cloth sim, sculpting or a game asset.
- **Seams weld cleanly.** Set the quad count on one side of a seam and the other side follows. A big length mismatch across a seam is flagged as a warning.
- **Similar pieces share one topology.** Pieces that look alike in the UV editor are detected automatically and get the same counts and the same mesh.
- **Internal lines and darts become edge loops**, from MD's DXF pattern export, curves drawn in Maya, or both.
- **Attached pieces** (arms on a torso, pockets, appliqués) become guide lines on the base piece, and the base topology is built around them.
- **Auto and manual modes.** Auto builds the mesh on the drape in one go. Manual lays each piece out flat so you can adjust counts and edit by hand, then projects back to 3D.
- **Optional thickness.** Grows the shell inward with a rim and support loops on open borders.
- **One combined output mesh** with the MD pattern UVs and one face set per pattern piece.

## Requirements

- Maya 2024 or newer (Python 3.10+)
- An MD garment exported with pattern UVs, as an OBJ file or a mesh already in the Maya scene
- No third-party dependencies (numpy is used if Maya provides it)

## Install

1. Download or clone this repo and keep the folder where it is.
2. Drag `install_retopo_tool.py` into a Maya viewport.
3. A **ReTopo** button is added to your current shelf and the window opens.

The shelf button points at this folder, so edits to the code are picked up the next time Maya starts. If you move the folder, drag the installer in again.

## Development

The core logic is pure Python with no `maya` import, so it runs under pytest:

```
python -m pytest tests
```

See [`docs/AGENTS.md`](docs/AGENTS.md) for the pipeline, project layout and design rules.
