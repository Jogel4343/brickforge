"""Real per-part bounding-box geometry for special-parts placement.

Stage 4 (roadmap #6) territory, but scoped narrowly: this is NOT the
Gurobi/HiGHS stability solver and does NOT do collision-checking against the
structural grid (both still deferred). It answers one question — "how big is
this resolved part, and where does its own geometry sit relative to its
LDraw origin" — by recursively parsing the part's real .dat geometry (and
every sub-file/primitive it references) into a true axis-aligned bounding
box, instead of worker/special_parts.py's previous nominal
footprint_studs=(1, 1) placeholder.

Why this needed real geometry, not a lookup table: LDraw doesn't publish
per-part origin conventions as metadata. The convention has to be inferred
from where the geometry actually falls relative to (0, 0, 0). Verified
against real parts before writing the placement formula (see
docs/SPECIAL_PARTS_TODO.md #4):
  - Standard bricks/plates (3005, 3001, 3623, 4070): origin sits exactly
    BRICK_LDU (24) or PLATE_LDU (8) above the part's true bottom — this is
    what worker/filler.py already assumed for its whitelisted vocabulary,
    now confirmed against real geometry rather than taken on faith.
  - A wheel+tyre assembly (3482c01) is modeled fully symmetric about its own
    origin in all three axes (a hub/axle convention, not "bottom-center") —
    proof a single hardcoded BRICK_LDU offset is wrong for non-brick parts,
    and that computing the real bbox per part is necessary, not optional.

Placement rule used by resolve_special_parts: align the part's true
geometric BOTTOM with the bottom of its target course. This exactly
reproduces the existing brick/plate convention (bottom_offset_ldu ==
BRICK_LDU or PLATE_LDU for those parts) and generalizes correctly to
everything else. It's an approximation for parts meant to be mounted by a
different reference point (a wheel's hub, say) rather than rested on a
surface — no per-category "this part mounts by its center" semantics exist
yet. That's a real limitation, not silently papered over: real slope/wedge
PARTS and SNOT remain deferred (CLAUDE.md), and this doesn't change that.

Horizontal (X/Z) placement still assumes the part's footprint is centered on
its own origin, matching worker/ldr_writer.py's brick_to_ldr_line. True for
every part checked here (straight bricks, plates, the wheel, a minifig
head) — NOT true for genuinely asymmetric parts (slopes/wedges), which is
exactly why those remain out of scope for special_parts today.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from worker.catalog import find_ldraw_root
from worker.ldr_writer import BRICK_LDU, STUD_LDU

# Fallback for parts whose geometry can't be parsed (missing file, malformed
# data) — reproduces the old nominal placeholder exactly, so a parse failure
# degrades to previous behavior rather than crashing generation.
_FALLBACK_FOOTPRINT_STUDS = (1, 1)
_FALLBACK_BOTTOM_OFFSET_LDU = BRICK_LDU

_SEARCH_SUBDIRS = ("parts", "parts/s", "p", "p/48")

Point = tuple[float, float, float]
Matrix = tuple[float, ...]  # 16 values, row-major 4x4


@dataclass(frozen=True)
class PartBBox:
    """A resolved part's real geometry, in placement-ready units."""
    footprint_studs: tuple[int, int]  # (width_x, depth_z), rounded, min 1 each
    bottom_offset_ldu: int            # local LDraw-Y (down-positive) of the part's lowest point


def _make_matrix(x: float, y: float, z: float, a: float, b: float, c: float,
                  d: float, e: float, f: float, g: float, h: float, i: float) -> Matrix:
    return (a, b, c, x, d, e, f, y, g, h, i, z, 0, 0, 0, 1)


def _apply(m: Matrix, pt: Point) -> Point:
    x, y, z = pt
    return (
        m[0] * x + m[1] * y + m[2] * z + m[3],
        m[4] * x + m[5] * y + m[6] * z + m[7],
        m[8] * x + m[9] * y + m[10] * z + m[11],
    )


@lru_cache(maxsize=None)
def _search_dirs() -> tuple[Path, ...]:
    root = find_ldraw_root()
    return tuple(root / sub for sub in _SEARCH_SUBDIRS)


@lru_cache(maxsize=None)
def _resolve_file(name: str) -> Path | None:
    base = name.replace("\\", "/").split("/")[-1]
    for d in _search_dirs():
        p = d / base
        if p.exists():
            return p
    return None


@lru_cache(maxsize=None)
def _local_bbox(path_str: str) -> tuple[Point, Point] | None:
    """Recursively computes ((minx,miny,minz), (maxx,maxy,maxz)) for one
    .dat file in its OWN local coordinate frame — memoized per file so
    heavily-shared primitives (studs, etc.) are only parsed once."""
    path = Path(path_str)
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return None

    pts: list[Point] = []
    for line in lines:
        tokens = line.split()
        if not tokens:
            continue
        t = tokens[0]
        try:
            if t == "1" and len(tokens) >= 15:
                vals = [float(v) for v in tokens[2:14]]
                sub_path = _resolve_file(tokens[14])
                if sub_path is None:
                    continue
                sub_bbox = _local_bbox(str(sub_path))
                if sub_bbox is None:
                    continue
                m = _make_matrix(*vals)
                (minx, miny, minz), (maxx, maxy, maxz) = sub_bbox
                for cx in (minx, maxx):
                    for cy in (miny, maxy):
                        for cz in (minz, maxz):
                            pts.append(_apply(m, (cx, cy, cz)))
            elif t in ("2", "5") and len(tokens) >= 8:
                vals = [float(v) for v in tokens[2:8]]
                pts.append((vals[0], vals[1], vals[2]))
                pts.append((vals[3], vals[4], vals[5]))
            elif t == "3" and len(tokens) >= 11:
                vals = [float(v) for v in tokens[2:11]]
                pts.extend((vals[k * 3], vals[k * 3 + 1], vals[k * 3 + 2]) for k in range(3))
            elif t == "4" and len(tokens) >= 14:
                vals = [float(v) for v in tokens[2:14]]
                pts.extend((vals[k * 3], vals[k * 3 + 1], vals[k * 3 + 2]) for k in range(4))
        except ValueError:
            continue  # malformed numeric field — skip this line, not the whole file

    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    zs = [p[2] for p in pts]
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def get_part_bbox(part_id: str) -> PartBBox:
    """Real footprint + bottom-offset for a resolved part_id, computed from
    its actual LDraw geometry. Falls back to the old nominal placeholder
    (1, 1) footprint / BRICK_LDU offset if the part can't be found or
    parsed — never raises, since a geometry miss shouldn't fail generation."""
    path = _resolve_file(f"{part_id}.dat")
    bbox = _local_bbox(str(path)) if path else None
    if bbox is None:
        return PartBBox(_FALLBACK_FOOTPRINT_STUDS, _FALLBACK_BOTTOM_OFFSET_LDU)

    (minx, miny, minz), (maxx, maxy, maxz) = bbox
    width_studs = max(1, round((maxx - minx) / STUD_LDU))
    depth_studs = max(1, round((maxz - minz) / STUD_LDU))
    bottom_offset_ldu = round(maxy)  # LDraw Y is down-positive: max = lowest point
    return PartBBox((width_studs, depth_studs), bottom_offset_ldu)
