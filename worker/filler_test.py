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

from worker.filler import fill_ir, fill_box, fill_cone, BRICKS
from worker.ir_schema import IR, SubAssembly
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


def test_fill_box_multi_layer_stacks_vertically() -> None:
    print("test_fill_box_multi_layer_stacks_vertically")
    sa = SubAssembly("pillar", "box", [0, 0, 0], [1, 3, 1], 4)
    bricks = fill_box(sa)
    _assert(len(bricks) == 3, f"1x3x1 box produces 3 stacked bricks (got {len(bricks)})")
    y_values = sorted({b.y_ldu for b in bricks})
    _assert(y_values == [BRICK_LDU, 2 * BRICK_LDU, 3 * BRICK_LDU], f"y_ldu values are 1/2/3 brick heights (got {y_values})")


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
        test_fill_box_1x1x1,
        test_fill_box_2x1x4_greedy_picks_2x4,
        test_fill_box_4x1x4_packs_two_2x4s,
        test_fill_box_multi_layer_stacks_vertically,
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
