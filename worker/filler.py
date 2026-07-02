"""Legolization filler — turn IR shape primitives into placed bricks.

v0.2 scope, deliberately tiny:
  - Two shapes supported: 'box' and 'cone'
  - ~10-part whitelisted vocabulary
  - Interlocking via seam-phase offset: odd layers (by absolute Y course)
    start each row with a 2-stud-long brick, so vertical seams land 2 studs
    away from the seams of even layers instead of stacking into full-height
    cracks.
  - No SNOT, no wedges, no aerodynamic surfaces
  - Rotation limited to 0°/90° about vertical

The whole point of this file is to be a KNOWN-GOOD FIXTURE. When Claude
starts emitting IRs in Push B.2, any pipeline failure is attributable to
Claude, not to this file. We polish it later once we know what real IRs
look like.

Algorithm for a 'box' shape of dims (W, H, D) in studs at position (px, py, pz):
  1. Determine layer plan: fill layer-by-layer from Y=py to Y=py+H
  2. Each layer is one brick tall (24 LDU) — no plates in v0.1
  3. For each layer, greedy-pack the (W x D) footprint with the largest
     bricks that fit. Row-major sweep: for each row, place bricks left-to-right.
  4. Order: outer rows first (X or Z edges), then inner. Not strictly needed
     for correctness, just makes the .ldr diff-friendly.

Algorithm for a 'cone' shape:
  Stack shrinking layers. v0.1 uses actual LDraw cone parts if the base
  width matches a supported size; otherwise falls back to layered plates
  with 2x2 slopes at the corners.
"""

from __future__ import annotations

from dataclasses import dataclass

from worker.ir_schema import IR, SubAssembly
from worker.ldr_writer import PlacedBrick, BRICK_LDU, PLATE_LDU


# ---------------------------------------------------------------------------
# Vocabulary — the whitelisted parts the filler can use.
#
# Every part_id here MUST exist in the LDraw catalog. These were hand-picked
# from the top-hits of Push 1's ranking tests, so they're all canonical
# plain-System parts, not Duplo/decorated/obsolete variants.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VocabBrick:
    part_id: str
    footprint: tuple[int, int]   # (size_x, size_z) in studs, NATIVE .dat orientation
    height_ldu: int              # 24 for brick, 8 for plate


# Bricks — sorted largest first so greedy-pack picks big pieces before small.
#
# Footprints are (size_x, size_z) of the part's NATIVE LDraw geometry, NOT
# the name order. "Brick 1 x 4" (3010) runs its long axis along X in
# 3010.dat, so its footprint is (4, 1). Getting this wrong renders every
# rotated brick 90° off from where the packer thinks it is. Any new vocab
# part must be checked against its .dat geometry, not its name.
BRICKS: list[VocabBrick] = [
    VocabBrick("3001", (4, 2), BRICK_LDU),   # Brick 2 x 4
    VocabBrick("3002", (3, 2), BRICK_LDU),   # Brick 2 x 3
    VocabBrick("3003", (2, 2), BRICK_LDU),   # Brick 2 x 2
    VocabBrick("3010", (4, 1), BRICK_LDU),   # Brick 1 x 4
    VocabBrick("3622", (3, 1), BRICK_LDU),   # Brick 1 x 3
    VocabBrick("3004", (2, 1), BRICK_LDU),   # Brick 1 x 2
    VocabBrick("3005", (1, 1), BRICK_LDU),   # Brick 1 x 1
]

# Plates (thin) — reserved for future mixed-height fills. Unused for now.
PLATES: list[VocabBrick] = [
    VocabBrick("3020", (4, 2), PLATE_LDU),   # Plate 2 x 4
    VocabBrick("3021", (3, 2), PLATE_LDU),   # Plate 2 x 3
    VocabBrick("3022", (2, 2), PLATE_LDU),   # Plate 2 x 2
    VocabBrick("3023", (2, 1), PLATE_LDU),   # Plate 1 x 2
    VocabBrick("3024", (1, 1), PLATE_LDU),   # Plate 1 x 1
]

# Cones — for roofs. LDraw part 3942c is a common cone-based roof.
# For v0.1 we use a very simple "layered shrinking plates + cone tip" approach.
CONE_TIP = VocabBrick("3062", (1, 1), BRICK_LDU)   # Round Brick 1x1 (stand-in tip)


# ---------------------------------------------------------------------------
# Box filler
# ---------------------------------------------------------------------------

def _best_fit(
    grid: list[list[bool]],
    width: int,
    depth: int,
    col: int,
    row: int,
    predicate=None,
) -> tuple[VocabBrick, int, int, int] | None:
    """Largest vocab brick (by area) that fits at (col, row) without
    overlap, trying both the native footprint and the 90°-rotated one.
    `predicate(w, d)` optionally restricts candidate oriented footprints.

    Returns (brick, oriented_w, oriented_d, rotation_deg) or None.
    """
    best: tuple[VocabBrick, int, int, int] | None = None
    best_area = 0
    for vb in BRICKS:
        for rot, (w, d) in ((0, vb.footprint), (90, (vb.footprint[1], vb.footprint[0]))):
            if predicate is not None and not predicate(w, d):
                continue
            if col + w > width or row + d > depth:
                continue
            if any(grid[row + dz][col + dx] for dz in range(d) for dx in range(w)):
                continue
            area = w * d
            if area > best_area:
                best_area = area
                best = (vb, w, d, rot)
    return best


def _fill_layer(
    width: int,
    depth: int,
    x_offset_stud: int,
    z_offset_stud: int,
    y_ldu: int,
    color_code: int,
    phase: int = 0,
) -> list[PlacedBrick]:
    """Greedy-fill a rectangular footprint (width x depth) at a given
    vertical position. Bricks placed left-to-right, front-to-back.

    `phase` staggers seams between vertically adjacent layers: on phase-1
    layers the first brick of each row is 2 studs long along X (falling
    back to 2 studs along Z at the very first cell when the footprint is
    1 stud wide), so seams land 2 studs off from phase-0 layers and walls
    interlock instead of forming full-height vertical cracks.

    Returns list of PlacedBrick.
    """
    placed: list[PlacedBrick] = []
    # Boolean grid: True = occupied. Shape (depth, width) to match sweep order.
    grid = [[False] * width for _ in range(depth)]

    for row in range(depth):
        col = 0
        while col < width:
            if grid[row][col]:
                col += 1
                continue

            fit: tuple[VocabBrick, int, int, int] | None = None
            if phase == 1 and col == 0:
                fit = _best_fit(grid, width, depth, col, row, predicate=lambda w, d: w == 2)
                if fit is None and row == 0:
                    fit = _best_fit(grid, width, depth, col, row, predicate=lambda w, d: w == 1 and d == 2)
            if fit is None:
                fit = _best_fit(grid, width, depth, col, row)

            if fit is None:
                # Fall back to 1x1 (should never fail — 1x1 fits anywhere)
                chosen = BRICKS[-1]  # 3005
                chosen_w, chosen_d, chosen_rot = 1, 1, 0
            else:
                chosen, chosen_w, chosen_d, chosen_rot = fit

            for dz in range(chosen_d):
                for dx in range(chosen_w):
                    grid[row + dz][col + dx] = True

            placed.append(PlacedBrick(
                part_id=chosen.part_id,
                color_code=color_code,
                x_stud=x_offset_stud + col,
                y_ldu=y_ldu,
                z_stud=z_offset_stud + row,
                footprint_studs=chosen.footprint,  # native footprint; rotation applied separately
                height_ldu=chosen.height_ldu,
                rotation_deg=chosen_rot,
            ))
            col += chosen_w
    return placed


def fill_box(sa: SubAssembly) -> list[PlacedBrick]:
    """Fill a 'box' sub-assembly with bricks.

    Interprets dims_studs[1] (height) as the number of brick layers.
    Y-position of layer N (0-indexed) in LDU = (py + N) * BRICK_LDU + BRICK_LDU
    (the extra BRICK_LDU accounts for LDraw's bottom-center origin: the
    brick occupies [y_ldu - BRICK_LDU, y_ldu]).
    """
    if sa.shape != "box":
        raise ValueError(f"fill_box called on non-box shape {sa.shape}")

    px, py, pz = sa.position_studs
    w, h, d = sa.dims_studs
    color = sa.color_code

    placed: list[PlacedBrick] = []
    for layer in range(h):
        # y_ldu is the LDU coordinate of the TOP of this brick's footprint
        # (LDraw brick origin is bottom-center, and we pass y_ldu as the
        # "world-up" measurement of the brick's bottom — ldr_writer flips
        # the sign for LDraw's Y-down convention).
        y_ldu = (py + layer) * BRICK_LDU + BRICK_LDU
        placed.extend(_fill_layer(
            width=w,
            depth=d,
            x_offset_stud=px,
            z_offset_stud=pz,
            y_ldu=y_ldu,
            color_code=color,
            phase=(py + layer) % 2,
        ))
    return placed


# ---------------------------------------------------------------------------
# Cone filler
# ---------------------------------------------------------------------------

def fill_cone(sa: SubAssembly) -> list[PlacedBrick]:
    """Fill a 'cone' sub-assembly by stacking shrinking box layers.

    v0.1 uses the dumbest possible approach: each vertical layer is a square
    frame of 1x1 bricks that shrinks by 1 stud per side per layer. The top
    layer is a single 1x1 round brick as a stand-in for a proper cone piece.

    This will look chunky, not smooth. That's fine for the ugly-slice test.
    """
    if sa.shape != "cone":
        raise ValueError(f"fill_cone called on non-cone shape {sa.shape}")

    px, py, pz = sa.position_studs
    w, h, d = sa.dims_studs
    color = sa.color_code

    # Cone height = number of layers to shrink over
    placed: list[PlacedBrick] = []
    for layer in range(h):
        # Shrink amount grows linearly with layer index
        shrink = layer  # 0, 1, 2, ...
        layer_w = max(1, w - 2 * shrink)
        layer_d = max(1, d - 2 * shrink)
        layer_px = px + shrink
        layer_pz = pz + shrink
        y_ldu = (py + layer) * BRICK_LDU + BRICK_LDU

        # If we've shrunk to 1x1, place a single tip brick
        if layer_w == 1 and layer_d == 1:
            placed.append(PlacedBrick(
                part_id=CONE_TIP.part_id,
                color_code=color,
                x_stud=layer_px,
                y_ldu=y_ldu,
                z_stud=layer_pz,
                footprint_studs=CONE_TIP.footprint,
                height_ldu=CONE_TIP.height_ldu,
                rotation_deg=0,
            ))
            break

        # Otherwise fill the layer as a solid square (not hollow — we're
        # making a solid pyramid cone, not a ring, so the roof doesn't have
        # a hole in the middle of each layer)
        placed.extend(_fill_layer(
            width=layer_w,
            depth=layer_d,
            x_offset_stud=layer_px,
            z_offset_stud=layer_pz,
            y_ldu=y_ldu,
            color_code=color,
            phase=(py + layer) % 2,
        ))
    return placed


# ---------------------------------------------------------------------------
# Top-level entrypoint
# ---------------------------------------------------------------------------

def fill_ir(ir: IR) -> list[PlacedBrick]:
    """Convert a full IR into a flat list of placed bricks."""
    all_bricks: list[PlacedBrick] = []
    for sa in ir.sub_assemblies:
        if sa.shape == "box":
            all_bricks.extend(fill_box(sa))
        elif sa.shape == "cone":
            all_bricks.extend(fill_cone(sa))
        else:
            raise ValueError(f"filler v0.1 doesn't support shape {sa.shape!r}")
    return all_bricks
