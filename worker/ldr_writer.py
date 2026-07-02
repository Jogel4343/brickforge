"""Write a list of placed bricks to a valid LDraw .ldr file.

LDraw coordinate system (this is the confusing part):
  - X: horizontal, positive right
  - Y: vertical, positive DOWN. Yes — LDraw Y goes down.
  - Z: horizontal, positive forward (away from camera in default view)
  - Units: LDU (LDraw Units).
    - 1 stud horizontal = 20 LDU
    - 1 brick tall     = 24 LDU (= 3 plates)
    - 1 plate tall     = 8 LDU

The IR uses studs with Y-up (natural). This writer converts to LDU with Y-down.

Type-1 line format:
  1 <color> <x> <y> <z> <a> <b> <c> <d> <e> <f> <g> <h> <i> <file>
where a..i is a 3x3 rotation matrix in row-major order:
    | a b c |
    | d e f |
    | g h i |

Identity rotation = 1 0 0 0 1 0 0 0 1 (no rotation applied).

The origin of a brick in LDraw is the CENTER of its footprint at its BOTTOM.
Not the corner. Not the top. Bottom-center. This trips up everyone once.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


# LDraw unit conversions
STUD_LDU = 20    # 1 stud horizontal
BRICK_LDU = 24   # 1 brick tall (= 3 plates)
PLATE_LDU = 8    # 1 plate tall


@dataclass(frozen=True)
class PlacedBrick:
    """A single brick placed in the model. Positions and dimensions are in
    STUDS (the natural LEGO unit), converted to LDU on write.

    Position semantics: (x_stud, y_stud, z_stud) is the MIN-CORNER of the
    brick's footprint. y_stud is which vertical layer (0 = ground plate) —
    a "plate layer" if the vocab entry is a plate, a "brick layer" (= 3
    plate layers) if it's a brick.

    footprint_studs = (width_x, depth_z). rotation_deg is 0 or 90 only in
    v0.1 (rotates footprint around vertical axis).
    """
    part_id: str                  # LDraw part number, e.g. "3001"
    color_code: int               # LDraw color code
    x_stud: int
    y_ldu: int                    # exact vertical position in LDU (varies by brick vs plate)
    z_stud: int
    footprint_studs: tuple[int, int]  # (width_x, depth_z) BEFORE rotation
    height_ldu: int               # vertical size of this brick (24 for brick, 8 for plate)
    rotation_deg: int = 0         # 0 or 90


def _rotation_matrix(deg: int) -> tuple[int, ...]:
    """Row-major 3x3 rotation around vertical (Y) axis.

    We only support 0° and 90° in v0.1. 90° rotates footprint w,d -> d,w.
    """
    if deg == 0:
        return (1, 0, 0, 0, 1, 0, 0, 0, 1)
    if deg == 90:
        # 90° about Y (LDraw Y is down, but the algebra is the same):
        # x' = z, z' = -x. Matrix that does this to a column vector:
        return (0, 0, 1, 0, 1, 0, -1, 0, 0)
    raise ValueError(f"unsupported rotation {deg}° (only 0 and 90)")


def brick_to_ldr_line(b: PlacedBrick) -> str:
    """Convert one PlacedBrick to one LDraw type-1 line.

    LDraw brick origin = bottom-center of footprint. So we shift the position
    by half-footprint horizontally and add half-height vertically.
    """
    # Rotated footprint (for center calculation)
    w, d = b.footprint_studs
    if b.rotation_deg == 90:
        w, d = d, w

    # Center of footprint, in studs, then convert to LDU
    cx_stud = b.x_stud + w / 2.0
    cz_stud = b.z_stud + d / 2.0
    x_ldu = int(round(cx_stud * STUD_LDU))
    z_ldu = int(round(cz_stud * STUD_LDU))

    # LDraw Y goes down. Our y_ldu is measured from ground going up, so flip
    # sign. Origin of the brick is bottom-center → we shift down (i.e. more
    # positive LDraw Y) by height_ldu because "bottom" in world = higher
    # LDraw-Y than "top".
    # But actually LDraw parts define themselves such that (0,0,0) is bottom-
    # center-of-footprint at the base of the stud, with studs going in -Y.
    # So bottom of a brick at world-Y=y_ldu becomes LDraw y = -y_ldu.
    ldraw_y = -b.y_ldu

    r = _rotation_matrix(b.rotation_deg)
    matrix_str = " ".join(str(v) for v in r)
    return f"1 {b.color_code} {x_ldu} {ldraw_y} {z_ldu} {matrix_str} {b.part_id}.dat"


def write_ldr(
    bricks: list[PlacedBrick],
    path: str | Path,
    model_name: str = "brickforge_model",
    author: str = "Brickforge",
) -> str:
    """Write bricks to disk as a valid .ldr file. Returns the file contents."""
    lines: list[str] = [
        f"0 {model_name}",
        f"0 Name: {Path(path).name}",
        f"0 Author: {author}",
        "0 !LDRAW_ORG Unofficial_Model",
        "",
    ]
    for b in bricks:
        lines.append(brick_to_ldr_line(b))
    contents = "\n".join(lines) + "\n"
    Path(path).write_text(contents)
    return contents


def render_to_string(bricks: list[PlacedBrick], model_name: str = "brickforge_model") -> str:
    """In-memory render of the .ldr file, without writing to disk. Useful for API responses."""
    lines: list[str] = [
        f"0 {model_name}",
        "0 Name: model.ldr",
        "0 Author: Brickforge",
        "0 !LDRAW_ORG Unofficial_Model",
        "",
    ]
    for b in bricks:
        lines.append(brick_to_ldr_line(b))
    return "\n".join(lines) + "\n"
