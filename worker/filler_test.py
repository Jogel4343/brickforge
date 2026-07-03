"""Tests + CLI for the filler.

Run tests:
    python -m worker.filler_test

Produce tower.ldr from the fixture:
    python -m worker.filler_test build tower
    # writes tower.ldr in the current directory
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from worker.filler import fill_ir, fill_box, fill_cone, fill_wedge, fill_tapered_slab, BRICKS
from worker.ir_schema import IR, SubAssembly, SpecialPart
from worker.ldr_writer import (
    PlacedBrick,
    brick_to_ldr_line,
    write_ldr,
    render_to_string,
    STUD_LDU,
    BRICK_LDU,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "data" / "fixtures"


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)
    print(f"  ok — {msg}")


def test_ir_schema_roundtrip() -> None:
    print("test_ir_schema_roundtrip")
    ir = IR(
        name="test",
        sub_assemblies=[
            SubAssembly("box1", "box", [0, 0, 0], [2, 3, 2], 4),
            SubAssembly("cone1", "cone", [0, 3, 0], [2, 2, 2], 14),
        ],
    )
    data = ir.to_dict()
    reloaded = IR.from_dict(data)
    _assert(reloaded.name == "test", "name roundtrips")
    _assert(len(reloaded.sub_assemblies) == 2, "2 sub_assemblies roundtrip")
    _assert(reloaded.sub_assemblies[0].shape == "box", "box shape roundtrips")
    _assert(reloaded.sub_assemblies[1].shape == "cone", "cone shape roundtrips")


def test_ir_schema_rejects_bad_input() -> None:
    print("test_ir_schema_rejects_bad_input")
    # Zero-dim
    try:
        SubAssembly("bad", "box", [0, 0, 0], [0, 3, 2])
        raise AssertionError("expected ValueError for zero-dim")
    except ValueError:
        pass
    _assert(True, "rejects zero-dim")

    # Unsupported shape
    try:
        SubAssembly("bad", "sphere", [0, 0, 0], [2, 3, 2])
        raise AssertionError("expected ValueError for sphere")
    except ValueError:
        pass
    _assert(True, "rejects unsupported shape")

    # Empty IR
    try:
        IR(name="empty", sub_assemblies=[])
        raise AssertionError("expected ValueError for empty IR")
    except ValueError:
        pass
    _assert(True, "rejects empty IR")

    # Duplicate names
    try:
        IR(name="dup", sub_assemblies=[
            SubAssembly("a", "box", [0, 0, 0], [1, 1, 1]),
            SubAssembly("a", "box", [1, 0, 0], [1, 1, 1]),
        ])
        raise AssertionError("expected ValueError for duplicate name")
    except ValueError:
        pass
    _assert(True, "rejects duplicate sub-assembly names")


def test_ir_schema_rejects_bad_taper_fields() -> None:
    print("test_ir_schema_rejects_bad_taper_fields")
    try:
        SubAssembly("bad", "tapered_slab", [0, 0, 0], [8, 1, 5], taper_axis="z")
        raise AssertionError("expected ValueError for missing taper_to_studs")
    except ValueError:
        pass
    _assert(True, "rejects tapered_slab without taper_to_studs")

    try:
        SubAssembly("bad", "tapered_slab", [0, 0, 0], [8, 1, 5], taper_axis="z", taper_to_studs=20)
        raise AssertionError("expected ValueError for taper_to_studs exceeding the tapered dimension")
    except ValueError:
        pass
    _assert(True, "rejects taper_to_studs larger than the tapered dimension")

    try:
        SubAssembly("bad", "wedge", [0, 0, 0], [4, 2, 3], taper_axis="y")
        raise AssertionError("expected ValueError for invalid taper_axis")
    except ValueError:
        pass
    _assert(True, "rejects invalid taper_axis")


def test_ir_schema_rejects_bad_special_parts() -> None:
    print("test_ir_schema_rejects_bad_special_parts")

    # attach_to must name a real sub_assembly
    try:
        IR(name="bad", sub_assemblies=[SubAssembly("chassis", "box", [0, 0, 0], [4, 1, 8])],
           special_parts=[SpecialPart("wheel", "wheel 30mm", attach_to="nonexistent")])
        raise AssertionError("expected ValueError for unknown attach_to")
    except ValueError:
        pass
    _assert(True, "rejects special_part with unknown attach_to")

    # names share one namespace with sub_assemblies
    try:
        IR(name="bad", sub_assemblies=[SubAssembly("chassis", "box", [0, 0, 0], [4, 1, 8])],
           special_parts=[SpecialPart("chassis", "wheel 30mm", attach_to="chassis")])
        raise AssertionError("expected ValueError for name collision with a sub_assembly")
    except ValueError:
        pass
    _assert(True, "rejects special_part name colliding with a sub_assembly name")

    # rotation_deg must be 0 or 90
    try:
        SpecialPart("wheel", "wheel 30mm", attach_to="chassis", rotation_deg=45)
        raise AssertionError("expected ValueError for rotation_deg=45")
    except ValueError:
        pass
    _assert(True, "rejects rotation_deg other than 0/90")

    # offset_studs must be length 3
    try:
        SpecialPart("wheel", "wheel 30mm", attach_to="chassis", offset_studs=[0, 0])
        raise AssertionError("expected ValueError for short offset_studs")
    except ValueError:
        pass
    _assert(True, "rejects offset_studs with wrong length")


def test_ir_normalize_positions_shifts_special_part_anchor() -> None:
    print("test_ir_normalize_positions_shifts_special_part_anchor")
    ir = IR(
        name="car",
        sub_assemblies=[SubAssembly("chassis", "box", [-2, 0, 0], [4, 1, 8], 4)],
        special_parts=[SpecialPart("wheel", "wheel 30mm", attach_to="chassis", offset_studs=[0, 0, 1])],
    )
    ir.normalize_positions()
    # chassis shifted from x=-2 to x=0; the special part's offset is untouched
    # because it's relative to attach_to's (now-shifted) position, so its
    # effective world position shifts consistently along with everything else.
    _assert(ir.sub_assemblies[0].position_studs == [0, 0, 0], "chassis shifted to non-negative x")
    _assert(ir.special_parts[0].offset_studs == [0, 0, 1], "special part offset is untouched (relative, not absolute)")


def test_ir_normalize_positions_shifts_negative_axis() -> None:
    print("test_ir_normalize_positions_shifts_negative_axis")
    ir = IR(
        name="plane",
        sub_assemblies=[
            SubAssembly("fuselage", "box", [0, 0, 0], [2, 2, 6], 4),
            SubAssembly("wing_left", "box", [-5, 2, 2], [5, 1, 2], 4),
        ],
    )
    ir.normalize_positions()
    _assert(ir.sub_assemblies[1].position_studs == [0, 2, 2], "negative X shifted to 0")
    _assert(ir.sub_assemblies[0].position_studs == [5, 0, 0], "other sub-assembly shifted by the same amount")


def test_ir_normalize_positions_noop_when_already_valid() -> None:
    print("test_ir_normalize_positions_noop_when_already_valid")
    ir = IR.from_json_file(FIXTURES / "tower.json")
    before = [list(sa.position_studs) for sa in ir.sub_assemblies]
    ir.normalize_positions()
    after = [list(sa.position_studs) for sa in ir.sub_assemblies]
    _assert(before == after, "already-valid IR positions are untouched")


def test_fill_box_1x1x1() -> None:
    print("test_fill_box_1x1x1")
    sa = SubAssembly("solo", "box", [0, 0, 0], [1, 1, 1], 4)
    bricks = fill_box(sa)
    _assert(len(bricks) == 1, "1x1x1 box produces exactly 1 brick")
    _assert(bricks[0].part_id == "3005", "single brick is 1x1 (3005)")
    _assert(bricks[0].footprint_studs == (1, 1), "footprint is (1, 1)")


def test_fill_box_2x1x4_greedy_picks_2x4() -> None:
    print("test_fill_box_2x1x4_greedy_picks_2x4")
    sa = SubAssembly("row", "box", [0, 0, 0], [2, 1, 4], 4)
    bricks = fill_box(sa)
    _assert(len(bricks) == 1, "2x1x4 box packs into 1 brick")
    _assert(bricks[0].part_id == "3001", "greedy picks 2x4 (3001)")


def test_fill_box_4x1x4_packs_two_2x4s() -> None:
    print("test_fill_box_4x1x4_packs_two_2x4s")
    sa = SubAssembly("floor", "box", [0, 0, 0], [4, 1, 4], 4)
    bricks = fill_box(sa)
    _assert(len(bricks) == 2, f"4x1x4 packs into 2 bricks (got {len(bricks)})")
    _assert(all(b.part_id == "3001" for b in bricks), "both bricks are 2x4")


def test_fill_box_8x1x16_picks_single_8x16() -> None:
    print("test_fill_box_8x1x16_picks_single_8x16")
    sa = SubAssembly("slab", "box", [0, 0, 0], [16, 1, 8], 4)
    bricks = fill_box(sa)
    _assert(len(bricks) == 1, f"16x8 footprint packs into 1 brick (got {len(bricks)})")
    _assert(bricks[0].part_id == "4204", f"greedy picks 8x16 (4204) (got {bricks[0].part_id})")


def test_fill_box_1x1x6_picks_single_1x6() -> None:
    print("test_fill_box_1x1x6_picks_single_1x6")
    sa = SubAssembly("row", "box", [0, 0, 0], [6, 1, 1], 4)
    bricks = fill_box(sa)
    _assert(len(bricks) == 1, f"1x6 footprint packs into 1 brick (got {len(bricks)})")
    _assert(bricks[0].part_id == "3009", f"greedy picks 1x6 (3009) (got {bricks[0].part_id})")


def test_fill_box_multi_layer_stacks_vertically() -> None:
    print("test_fill_box_multi_layer_stacks_vertically")
    sa = SubAssembly("pillar", "box", [0, 0, 0], [1, 3, 1], 4)
    bricks = fill_box(sa)
    _assert(len(bricks) == 3, f"1x3x1 box produces 3 stacked bricks (got {len(bricks)})")
    y_values = sorted({b.y_ldu for b in bricks})
    _assert(y_values == [BRICK_LDU, 2 * BRICK_LDU, 3 * BRICK_LDU], f"y_ldu values are 1/2/3 brick heights (got {y_values})")


def test_fill_ir_merges_adjacent_sub_assemblies_of_same_color() -> None:
    print("test_fill_ir_merges_adjacent_sub_assemblies_of_same_color")
    ir = IR(
        name="corner",
        sub_assemblies=[
            SubAssembly("a", "box", [0, 0, 0], [1, 1, 1], 4),
            SubAssembly("b", "box", [1, 0, 0], [1, 1, 1], 4),
        ],
    )
    # In isolation each sub-assembly is its own 1x1 brick.
    _assert(len(fill_box(ir.sub_assemblies[0])) == 1, "sub-assembly a alone is 1 brick")
    _assert(len(fill_box(ir.sub_assemblies[1])) == 1, "sub-assembly b alone is 1 brick")
    # Through fill_ir, the union grid sees one contiguous 2-cell row and
    # merges them into a single 1x2 brick spanning the sub-assembly boundary.
    bricks = fill_ir(ir)
    _assert(len(bricks) == 1, f"union grid merges into 1 brick (got {len(bricks)})")
    _assert(bricks[0].part_id == "3004", f"merged brick is 1x2 (3004) (got {bricks[0].part_id})")


def test_fill_ir_does_not_merge_different_colors() -> None:
    print("test_fill_ir_does_not_merge_different_colors")
    ir = IR(
        name="corner_two_colors",
        sub_assemblies=[
            SubAssembly("a", "box", [0, 0, 0], [1, 1, 1], 4),
            SubAssembly("b", "box", [1, 0, 0], [1, 1, 1], 14),
        ],
    )
    bricks = fill_ir(ir)
    _assert(len(bricks) == 2, f"different colors stay separate bricks (got {len(bricks)})")
    _assert({b.color_code for b in bricks} == {4, 14}, "each brick keeps its own color")


def test_fill_ir_overlap_produces_no_double_claimed_cell() -> None:
    print("test_fill_ir_overlap_produces_no_double_claimed_cell")
    ir = IR(
        name="overlap",
        sub_assemblies=[
            SubAssembly("first", "box", [0, 0, 0], [2, 1, 2], 4),
            SubAssembly("second", "box", [1, 0, 1], [2, 1, 2], 14),
        ],
    )
    bricks = fill_ir(ir)
    occupied: set[tuple[int, int]] = set()
    for b in bricks:
        w, d = b.footprint_studs
        if b.rotation_deg == 90:
            w, d = d, w
        for dx in range(w):
            for dz in range(d):
                cell = (b.x_stud + dx, b.z_stud + dz)
                _assert(cell not in occupied, f"cell {cell} claimed by only one brick")
                occupied.add(cell)


def test_fill_wedge_shrinks_one_axis_no_tip() -> None:
    print("test_fill_wedge_shrinks_one_axis_no_tip")
    sa = SubAssembly("roof", "wedge", [0, 0, 0], [4, 2, 3], 14, taper_axis="z")
    bricks = fill_wedge(sa)
    by_course: dict[int, list[PlacedBrick]] = {}
    for b in bricks:
        by_course.setdefault(b.y_ldu, []).append(b)
    _assert(len(by_course) == 2, f"wedge produces 2 courses (got {len(by_course)})")
    base, top = (by_course[c] for c in sorted(by_course))

    def extent(course_bricks, axis):
        lo, hi = [], []
        for b in course_bricks:
            w, d = b.footprint_studs
            if b.rotation_deg == 90:
                w, d = d, w
            start, size = (b.x_stud, w) if axis == "x" else (b.z_stud, d)
            lo.append(start)
            hi.append(start + size - 1)
        return min(lo), max(hi)

    _assert(extent(base, "x") == (0, 3), "base course spans full width (0-3)")
    _assert(extent(top, "x") == (0, 3), "top course keeps full width (taper_axis=z leaves X unchanged)")
    _assert(extent(base, "z") == (0, 2), "base course spans full depth (0-2)")
    _assert(extent(top, "z") == (1, 1), "top course shrinks to a single-Z ridge row")
    _assert(all(b.part_id != "3062" for b in bricks), "wedge never places the cone tip part")


def test_fill_tapered_slab_narrows_along_axis() -> None:
    print("test_fill_tapered_slab_narrows_along_axis")
    sa = SubAssembly("hull", "tapered_slab", [0, 0, 0], [8, 1, 5], 71, taper_axis="z", taper_to_studs=2)
    bricks = fill_tapered_slab(sa)
    cells: set[tuple[int, int]] = set()
    for b in bricks:
        w, d = b.footprint_studs
        if b.rotation_deg == 90:
            w, d = d, w
        for dx in range(w):
            for dz in range(d):
                cells.add((b.x_stud + dx, b.z_stud + dz))

    def width_at(z: int) -> int:
        return sum(1 for (_, zz) in cells if zz == z)

    _assert(width_at(0) == 8, f"wide end (z=0) is full width 8 (got {width_at(0)})")
    _assert(width_at(4) == 2, f"narrow end (z=4) is taper_to_studs 2 (got {width_at(4)})")
    widths = [width_at(z) for z in range(5)]
    _assert(widths == sorted(widths, reverse=True), f"width is non-increasing along the taper (got {widths})")


def test_fill_cone_shrinks_to_tip() -> None:
    print("test_fill_cone_shrinks_to_tip")
    sa = SubAssembly("cone", "cone", [0, 0, 0], [3, 2, 3], 14)
    bricks = fill_cone(sa)
    _assert(len(bricks) >= 2, "cone produces multiple bricks")
    # Top brick should be at highest y
    top = max(bricks, key=lambda b: b.y_ldu)
    _assert(top.footprint_studs == (1, 1), "top brick is 1x1 (tip)")


def test_ldr_line_format() -> None:
    print("test_ldr_line_format")
    b = PlacedBrick(
        part_id="3001",
        color_code=4,
        x_stud=0,
        y_ldu=BRICK_LDU,
        z_stud=0,
        footprint_studs=(2, 4),
        height_ldu=BRICK_LDU,
        rotation_deg=0,
    )
    line = brick_to_ldr_line(b)
    # LDraw line: "1 <color> <x> <y> <z> <9 rotation> <file>"
    parts = line.split()
    _assert(parts[0] == "1", "line type is 1")
    _assert(parts[1] == "4", f"color is 4 (got {parts[1]})")
    _assert(parts[-1] == "3001.dat", f"file is 3001.dat (got {parts[-1]!r})")
    _assert(len(parts) == 15, f"line has 15 tokens (got {len(parts)})")
    # Position: 2x4 brick centered → x = 2/2*20 = 20 LDU, z = 4/2*20 = 40 LDU
    _assert(parts[2] == "20", f"x is 20 LDU (got {parts[2]})")
    _assert(parts[3] == f"-{BRICK_LDU}", f"y is -24 LDU (LDraw Y-down) (got {parts[3]})")
    _assert(parts[4] == "40", f"z is 40 LDU (got {parts[4]})")
    # Rotation is identity
    _assert(parts[5:14] == ["1", "0", "0", "0", "1", "0", "0", "0", "1"], f"identity rotation (got {parts[5:14]})")


def test_tower_end_to_end() -> None:
    print("test_tower_end_to_end")
    ir = IR.from_json_file(FIXTURES / "tower.json")
    _assert(ir.name == "medieval_tower", "tower IR loads")
    _assert(len(ir.sub_assemblies) == 5, f"tower has 5 sub_assemblies (got {len(ir.sub_assemblies)})")

    bricks = fill_ir(ir)
    _assert(len(bricks) > 0, f"tower produces bricks (got {len(bricks)})")

    ldr = render_to_string(bricks, model_name="medieval_tower")
    _assert(ldr.startswith("0 medieval_tower"), "ldr starts with header")
    _assert(ldr.count("\n1 ") == len(bricks), "one type-1 line per brick")
    _assert(".dat" in ldr, "references .dat parts")

    # Sanity: every part in output is in our vocab whitelist (except CONE_TIP)
    vocab_ids = {v.part_id for v in BRICKS} | {"3062"}  # 3062 = cone tip
    used_ids = {b.part_id for b in bricks}
    unknown = used_ids - vocab_ids
    _assert(not unknown, f"all parts are in vocab whitelist (unknown: {unknown})")

    print(f"  info: tower packs into {len(bricks)} bricks")


def build_tower_ldr(out_path: str) -> None:
    """CLI entry: read fixtures/tower.json, run the filler, write .ldr to disk."""
    ir = IR.from_json_file(FIXTURES / "tower.json")
    bricks = fill_ir(ir)
    write_ldr(bricks, out_path, model_name=ir.name)
    print(f"wrote {out_path}: {len(bricks)} bricks, IR '{ir.name}' with {len(ir.sub_assemblies)} sub-assemblies")


def run_all_tests() -> None:
    tests = [
        test_ir_schema_roundtrip,
        test_ir_schema_rejects_bad_input,
        test_ir_schema_rejects_bad_taper_fields,
        test_ir_schema_rejects_bad_special_parts,
        test_ir_normalize_positions_shifts_negative_axis,
        test_ir_normalize_positions_noop_when_already_valid,
        test_ir_normalize_positions_shifts_special_part_anchor,
        test_fill_box_1x1x1,
        test_fill_box_2x1x4_greedy_picks_2x4,
        test_fill_box_4x1x4_packs_two_2x4s,
        test_fill_box_8x1x16_picks_single_8x16,
        test_fill_box_1x1x6_picks_single_1x6,
        test_fill_box_multi_layer_stacks_vertically,
        test_fill_ir_merges_adjacent_sub_assemblies_of_same_color,
        test_fill_ir_does_not_merge_different_colors,
        test_fill_ir_overlap_produces_no_double_claimed_cell,
        test_fill_wedge_shrinks_one_axis_no_tip,
        test_fill_tapered_slab_narrows_along_axis,
        test_fill_cone_shrinks_to_tip,
        test_ldr_line_format,
        test_tower_end_to_end,
    ]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} tests passed.")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "build" and sys.argv[2] == "tower":
        out = sys.argv[3] if len(sys.argv) >= 4 else "tower.ldr"
        build_tower_ldr(out)
    else:
        run_all_tests()
