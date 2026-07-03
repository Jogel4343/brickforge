"""Legolization filler — turn IR shape primitives into placed bricks.

v0.4 scope:
  - Four shapes supported: 'box', 'cone', 'wedge', 'tapered_slab'. 'wedge'
    and 'tapered_slab' are IR-level shape primitives filled with the same
    stepped regular-brick approach as 'cone' — NOT real LDraw slope/wedge
    PARTS, which remain deferred (see CLAUDE.md).
  - ~22-part whitelisted vocabulary (full canonical straight-brick range)
  - Interlocking via seam-phase offset: odd courses (absolute Y) start each
    row with a 2-stud-long brick, so vertical seams land 2 studs away from
    the seams of even courses instead of stacking into full-height cracks.
  - Union occupancy grid: sub-assemblies are NOT packed independently.
    Every sub-assembly is rasterized into absolute (course, x, z) -> color
    cells first, and all sub-assemblies sharing a course are merged into one
    cell set before packing. A brick can span across a sub-assembly boundary
    (same course, same color, adjacent cells), so a corner where two walls
    meet interlocks like real masonry instead of each wall independently
    packing right up to a seam that never staggers. This also makes
    brick-level collisions structurally impossible: each absolute cell is
    claimed by exactly one sub-assembly (first in build order wins a
    contested cell) before any brick is placed, so no cell is ever covered
    twice.
  - No SNOT, no real slope/wedge parts, no aerodynamic surfaces
  - Rotation limited to 0°/90° about vertical

The whole point of this file is to be a KNOWN-GOOD FIXTURE. When Claude
starts emitting IRs in Push B.2, any pipeline failure is attributable to
Claude, not to this file. We polish it later once we know what real IRs
look like.

Algorithm:
  1. Rasterize every sub-assembly into {absolute_course: {(x, z): color}}.
     'box' contributes the same footprint at every course from py to
     py+h-1. 'cone' shrinks that footprint on BOTH horizontal axes per
     course, ending in a single round tip part (never packed generically).
     'wedge' shrinks on only ONE horizontal axis per course (taper_axis),
     ending in a full-width 1-stud ridge row (no special part — a real
     ridge is just regular bricks). 'tapered_slab' has a CONSTANT footprint
     per course that is itself a symmetric trapezoid, narrowing along
     taper_axis from the wide end to taper_to_studs at the far end.
  2. Merge all sub-assemblies' cells per course (first-writer-wins on a
     contested cell) into one global occupancy grid.
  3. For each course, greedy-pack each color's cell set independently
     (a brick can't span two colors) with the largest vocab brick that fits
     — sparse-set membership, not a dense rectangular grid, so packing
     works over arbitrary (non-rectangular) merged footprints. Row-major
     sweep: front-to-back rows, left-to-right within a row.
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
# part must be checked against its .dat geometry, not its name — verified
# here against public/ldraw/parts/*.dat bounding boxes, not against memory.
#
# This is the full canonical range of standard rectangular System bricks
# (every size that ships as a single straight mould, 1x1 up to 12x24).
# Plates, tiles, and slopes are deliberately NOT added to this whitelist yet:
# plates need sub-brick-course height support in the IR schema (dims_studs[1]
# only counts whole brick courses today), and tiles/slopes need shape-aware
# placement the box-filler doesn't have (they'd render as a lump mid-wall
# instead of a proper roof/finish). Both are later roadmap items.
BRICKS: list[VocabBrick] = [
    VocabBrick("30072", (24, 12), BRICK_LDU),  # Brick 12 x 24
    VocabBrick("4204", (16, 8), BRICK_LDU),    # Brick 8 x 16
    VocabBrick("733", (10, 10), BRICK_LDU),    # Brick 10 x 10
    VocabBrick("4201", (8, 8), BRICK_LDU),     # Brick 8 x 8
    VocabBrick("4202", (12, 4), BRICK_LDU),    # Brick 4 x 12
    VocabBrick("6212", (10, 4), BRICK_LDU),    # Brick 4 x 10
    VocabBrick("2356", (6, 4), BRICK_LDU),     # Brick 4 x 6
    VocabBrick("3006", (10, 2), BRICK_LDU),    # Brick 2 x 10
    VocabBrick("2465", (16, 1), BRICK_LDU),    # Brick 1 x 16
    VocabBrick("3007", (8, 2), BRICK_LDU),     # Brick 2 x 8
    VocabBrick("6112", (12, 1), BRICK_LDU),    # Brick 1 x 12
    VocabBrick("2456", (6, 2), BRICK_LDU),     # Brick 2 x 6
    VocabBrick("6111", (10, 1), BRICK_LDU),    # Brick 1 x 10
    VocabBrick("3001", (4, 2), BRICK_LDU),     # Brick 2 x 4
    VocabBrick("3008", (8, 1), BRICK_LDU),     # Brick 1 x 8
    VocabBrick("3009", (6, 1), BRICK_LDU),     # Brick 1 x 6
    VocabBrick("3002", (3, 2), BRICK_LDU),     # Brick 2 x 3
    VocabBrick("3003", (2, 2), BRICK_LDU),     # Brick 2 x 2
    VocabBrick("3010", (4, 1), BRICK_LDU),     # Brick 1 x 4
    VocabBrick("3622", (3, 1), BRICK_LDU),     # Brick 1 x 3
    VocabBrick("3004", (2, 1), BRICK_LDU),     # Brick 1 x 2
    VocabBrick("3005", (1, 1), BRICK_LDU),     # Brick 1 x 1
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
# Rasterization — turn one sub-assembly into occupancy cells. This is the
# shared input to the union-grid packer below: sub-assemblies stop being
# packed independently the moment they're rasterized into the same absolute
# coordinate space.
# ---------------------------------------------------------------------------

CellGrid = dict[int, dict[tuple[int, int], int]]  # abs_course -> {(x, z): color_code}


@dataclass(frozen=True)
class _ConeTip:
    """A cone's apex is always a single round part, never generically
    packed — tracked separately from the cell grid so it can't be merged
    with a neighboring sub-assembly's cells."""
    course: int
    x: int
    z: int
    color: int


def _rasterize_box(sa: SubAssembly) -> CellGrid:
    px, py, pz = sa.position_studs
    w, h, d = sa.dims_studs
    cells: CellGrid = {}
    for layer in range(h):
        course = py + layer
        layer_cells = cells.setdefault(course, {})
        for dx in range(w):
            for dz in range(d):
                layer_cells[(px + dx, pz + dz)] = sa.color_code
    return cells


def _rasterize_cone(sa: SubAssembly) -> tuple[CellGrid, list[_ConeTip]]:
    px, py, pz = sa.position_studs
    w, h, d = sa.dims_studs
    cells: CellGrid = {}
    tips: list[_ConeTip] = []
    for layer in range(h):
        course = py + layer
        shrink = layer
        layer_w = max(1, w - 2 * shrink)
        layer_d = max(1, d - 2 * shrink)
        layer_px = px + shrink
        layer_pz = pz + shrink
        if layer_w == 1 and layer_d == 1:
            tips.append(_ConeTip(course, layer_px, layer_pz, sa.color_code))
            break
        # Solid square, not hollow — a pyramid cone, not a ring, so the
        # roof doesn't have a hole in the middle of each course.
        layer_cells = cells.setdefault(course, {})
        for dx in range(layer_w):
            for dz in range(layer_d):
                layer_cells[(layer_px + dx, layer_pz + dz)] = sa.color_code
    return cells, tips


def _rasterize_wedge(sa: SubAssembly) -> CellGrid:
    """A ridge shape: like _rasterize_cone but shrinks toward a LINE instead
    of a point — only one horizontal axis (sa.taper_axis) narrows per
    course, the other stays constant. No special tip: the final course is a
    full-width 1-stud-wide row, filled by the ordinary packer (a real ridge
    line is just a row of regular bricks, unlike a pointed spire)."""
    px, py, pz = sa.position_studs
    w, h, d = sa.dims_studs
    cells: CellGrid = {}
    for layer in range(h):
        course = py + layer
        shrink = layer
        if sa.taper_axis == "z":
            layer_w, layer_d = w, max(1, d - 2 * shrink)
            layer_px, layer_pz = px, pz + shrink
        else:
            layer_w, layer_d = max(1, w - 2 * shrink), d
            layer_px, layer_pz = px + shrink, pz
        layer_cells = cells.setdefault(course, {})
        for dx in range(layer_w):
            for dz in range(layer_d):
                layer_cells[(layer_px + dx, layer_pz + dz)] = sa.color_code
    return cells


def _rasterize_tapered_slab(sa: SubAssembly) -> CellGrid:
    """A trapezoid footprint (viewed from above), extruded to constant
    height. Unlike wedge/cone, the taper is a function of position ALONG
    the taper axis, not of course — every course gets the identical
    trapezoid. Interpolates from dims_studs' cross-width at the near end
    (position_studs) to taper_to_studs at the far end; taper_to_studs may be
    smaller (narrows — a tapered tower, a hull) or larger (widens — a
    flared fender) than the near end.

    Centered on max(near, far), NOT always on `near`: centering on `near`
    would make a widening taper's inset go negative (cells to the left of
    position_studs — outside the sub-assembly's own declared bounding box,
    and invisible to sanity_check's position_studs-only bounds check).
    Centering on the wider of the two ends keeps inset >= 0 for both
    directions, and is provably identical to the old narrowing-only formula
    when far < near (max(near, far) == near in that case) — verified by
    hand against the original inset-based version before replacing it."""
    px, py, pz = sa.position_studs
    w, h, d = sa.dims_studs
    far = sa.taper_to_studs
    near, length = (w, d) if sa.taper_axis == "z" else (d, w)
    full_wide = max(near, far)
    cells: CellGrid = {}
    for layer in range(h):
        course = py + layer
        layer_cells = cells.setdefault(course, {})
        for i in range(length):
            cur_wide = near + ((far - near) * i) // (length - 1) if length > 1 else near
            inset = (full_wide - cur_wide) // 2
            for j in range(inset, inset + cur_wide):
                cell = (px + j, pz + i) if sa.taper_axis == "z" else (px + i, pz + j)
                layer_cells[cell] = sa.color_code
    return cells


def _merge_cells(dst: CellGrid, src: CellGrid) -> None:
    """Merge src into dst. First writer wins on a contested cell: a real
    overlap between sub-assemblies is a Claude mistake (the system prompt
    says primitives should touch, not overlap), and degrading to
    'earliest-declared sub-assembly owns the cell' is safer than crashing
    the whole generation or double-placing a brick there."""
    for course, layer_cells in src.items():
        dst_layer = dst.setdefault(course, {})
        for cell, color in layer_cells.items():
            dst_layer.setdefault(cell, color)


# ---------------------------------------------------------------------------
# Union-grid packer — greedy-fills an arbitrary (possibly non-rectangular)
# set of absolute stud cells. Because packing runs on the MERGED cell set,
# a brick can span across a sub-assembly boundary: a corner where two wall
# sub-assemblies meet is just a bigger cell set to this function, so seams
# stagger across the boundary the same way they stagger within one wall.
# ---------------------------------------------------------------------------

def _best_fit_sparse(
    remaining: set[tuple[int, int]],
    x: int,
    z: int,
    predicate=None,
) -> tuple[VocabBrick, int, int, int] | None:
    """Largest vocab brick (by area) that fits with its min-corner at (x, z),
    trying both the native footprint and the 90°-rotated one. A candidate
    fits iff every cell it would cover is still in `remaining` — sparse-set
    membership, so this works over any occupied-cell shape, not just a
    rectangle. `predicate(w, d)` optionally restricts candidate footprints.

    Returns (brick, oriented_w, oriented_d, rotation_deg) or None.
    """
    best: tuple[VocabBrick, int, int, int] | None = None
    best_area = 0
    for vb in BRICKS:
        for rot, (w, d) in ((0, vb.footprint), (90, (vb.footprint[1], vb.footprint[0]))):
            if predicate is not None and not predicate(w, d):
                continue
            if any((x + dx, z + dz) not in remaining for dx in range(w) for dz in range(d)):
                continue
            area = w * d
            if area > best_area:
                best_area = area
                best = (vb, w, d, rot)
    return best


def _fill_cells(cells: dict[tuple[int, int], int], y_ldu: int, phase: int) -> list[PlacedBrick]:
    """Greedy-fill one course's occupied cells, one color at a time (a
    single brick can't span two colors). Row-major sweep: front-to-back
    rows, left-to-right within a row.

    `phase` staggers seams between vertically adjacent courses: on phase-1
    courses the first brick of each row is 2 studs long along X (falling
    back to 2 studs along Z at the very first cell of a 1-stud-wide row),
    so seams land 2 studs off from phase-0 courses instead of stacking into
    full-height vertical cracks.
    """
    by_color: dict[int, set[tuple[int, int]]] = {}
    for cell, color in cells.items():
        by_color.setdefault(color, set()).add(cell)

    placed: list[PlacedBrick] = []
    for color, remaining in by_color.items():
        min_x = min(c[0] for c in remaining)
        max_x = max(c[0] for c in remaining)
        min_z = min(c[1] for c in remaining)
        max_z = max(c[1] for c in remaining)

        for z in range(min_z, max_z + 1):
            x = min_x
            while x <= max_x:
                if (x, z) not in remaining:
                    x += 1
                    continue

                fit: tuple[VocabBrick, int, int, int] | None = None
                if phase == 1 and x == min_x:
                    fit = _best_fit_sparse(remaining, x, z, predicate=lambda w, d: w == 2)
                    if fit is None and z == min_z:
                        fit = _best_fit_sparse(remaining, x, z, predicate=lambda w, d: w == 1 and d == 2)
                if fit is None:
                    fit = _best_fit_sparse(remaining, x, z)

                if fit is None:
                    # Fall back to 1x1 (should never fail — 1x1 fits anywhere)
                    chosen, chosen_w, chosen_d, chosen_rot = BRICKS[-1], 1, 1, 0
                else:
                    chosen, chosen_w, chosen_d, chosen_rot = fit

                for dz in range(chosen_d):
                    for dx in range(chosen_w):
                        remaining.discard((x + dx, z + dz))

                placed.append(PlacedBrick(
                    part_id=chosen.part_id,
                    color_code=color,
                    x_stud=x,
                    y_ldu=y_ldu,
                    z_stud=z,
                    footprint_studs=chosen.footprint,  # native footprint; rotation applied separately
                    height_ldu=chosen.height_ldu,
                    rotation_deg=chosen_rot,
                ))
                x += chosen_w
    return placed


def _tip_brick(tip: _ConeTip) -> PlacedBrick:
    y_ldu = tip.course * BRICK_LDU + BRICK_LDU
    return PlacedBrick(
        part_id=CONE_TIP.part_id,
        color_code=tip.color,
        x_stud=tip.x,
        y_ldu=y_ldu,
        z_stud=tip.z,
        footprint_studs=CONE_TIP.footprint,
        height_ldu=CONE_TIP.height_ldu,
        rotation_deg=0,
    )


def _pack_grid(cells: CellGrid) -> list[PlacedBrick]:
    placed: list[PlacedBrick] = []
    for course, layer_cells in cells.items():
        if not layer_cells:
            continue
        # y_ldu is the LDU coordinate of the TOP of this brick's footprint
        # (LDraw brick origin is bottom-center, and we pass y_ldu as the
        # "world-up" measurement of the brick's bottom — ldr_writer flips
        # the sign for LDraw's Y-down convention).
        y_ldu = course * BRICK_LDU + BRICK_LDU
        placed.extend(_fill_cells(layer_cells, y_ldu, phase=course % 2))
    return placed


# ---------------------------------------------------------------------------
# Per-shape entrypoints — each rasterizes ONE sub-assembly and packs it in
# isolation. Used directly by unit tests; fill_ir (below) does the same
# rasterize step but merges ALL sub-assemblies before packing, which is
# what actually lets bricks span sub-assembly boundaries.
# ---------------------------------------------------------------------------

def fill_box(sa: SubAssembly) -> list[PlacedBrick]:
    """Fill a single 'box' sub-assembly with bricks, in isolation."""
    if sa.shape != "box":
        raise ValueError(f"fill_box called on non-box shape {sa.shape}")
    return _pack_grid(_rasterize_box(sa))


def fill_cone(sa: SubAssembly) -> list[PlacedBrick]:
    """Fill a single 'cone' sub-assembly with bricks, in isolation."""
    if sa.shape != "cone":
        raise ValueError(f"fill_cone called on non-cone shape {sa.shape}")
    cells, tips = _rasterize_cone(sa)
    return _pack_grid(cells) + [_tip_brick(t) for t in tips]


def fill_wedge(sa: SubAssembly) -> list[PlacedBrick]:
    """Fill a single 'wedge' sub-assembly with bricks, in isolation."""
    if sa.shape != "wedge":
        raise ValueError(f"fill_wedge called on non-wedge shape {sa.shape}")
    return _pack_grid(_rasterize_wedge(sa))


def fill_tapered_slab(sa: SubAssembly) -> list[PlacedBrick]:
    """Fill a single 'tapered_slab' sub-assembly with bricks, in isolation."""
    if sa.shape != "tapered_slab":
        raise ValueError(f"fill_tapered_slab called on non-tapered_slab shape {sa.shape}")
    return _pack_grid(_rasterize_tapered_slab(sa))


# ---------------------------------------------------------------------------
# Top-level entrypoint
# ---------------------------------------------------------------------------

def fill_ir(ir: IR) -> list[PlacedBrick]:
    """Convert a full IR into a flat list of placed bricks.

    Rasterizes every sub-assembly and merges them into one occupancy grid
    BEFORE packing — this is the union-grid step. A brick can span across
    a sub-assembly boundary (same course, same color, adjacent cells), and
    no absolute cell can end up covered by more than one brick, since every
    cell is claimed by exactly one sub-assembly before packing runs.
    """
    merged: CellGrid = {}
    tips: list[_ConeTip] = []
    for sa in ir.sub_assemblies:
        if sa.shape == "box":
            _merge_cells(merged, _rasterize_box(sa))
        elif sa.shape == "cone":
            cone_cells, cone_tips = _rasterize_cone(sa)
            _merge_cells(merged, cone_cells)
            tips.extend(cone_tips)
        elif sa.shape == "wedge":
            _merge_cells(merged, _rasterize_wedge(sa))
        elif sa.shape == "tapered_slab":
            _merge_cells(merged, _rasterize_tapered_slab(sa))
        else:
            raise ValueError(f"filler v0.2 doesn't support shape {sa.shape!r}")

    # A cone tip is a single specific point; don't let a generic brick from
    # another sub-assembly land on the same cell underneath it.
    for tip in tips:
        layer_cells = merged.get(tip.course)
        if layer_cells is not None:
            layer_cells.pop((tip.x, tip.z), None)

    return _pack_grid(merged) + [_tip_brick(t) for t in tips]
