"""Tests for worker/part_geometry.py — real per-part bbox computation.

Runs against the real public/ldraw parts library (needed to parse real
geometry, unlike special_parts_test.py's synthetic catalog). Values below
were verified by hand against the real .dat files before being hardcoded
here — see worker/part_geometry.py's docstring.

Run:
    python -m worker.part_geometry_test
"""

from __future__ import annotations

from worker.ldr_writer import BRICK_LDU, PLATE_LDU
from worker.part_geometry import get_part_bbox


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)
    print(f"  ok — {msg}")


def test_standard_brick_matches_known_brick_ldu() -> None:
    print("test_standard_brick_matches_known_brick_ldu")
    bbox = get_part_bbox("3005")  # Brick 1 x 1
    _assert(bbox.footprint_studs == (1, 1), f"1x1 brick footprint (got {bbox.footprint_studs})")
    _assert(not bbox.is_center_mounted, "a brick rests on its bottom, not its center")
    _assert(bbox.mount_offset_ldu == BRICK_LDU, f"origin sits BRICK_LDU above the true bottom (got {bbox.mount_offset_ldu})")


def test_wide_brick_footprint_matches_its_name() -> None:
    print("test_wide_brick_footprint_matches_its_name")
    bbox = get_part_bbox("3001")  # Brick 2 x 4
    _assert(bbox.footprint_studs == (4, 2), f"2x4 brick is 4 wide (X) x 2 deep (Z) (got {bbox.footprint_studs})")
    _assert(bbox.mount_offset_ldu == BRICK_LDU, f"still a full-height brick (got {bbox.mount_offset_ldu})")


def test_plate_bottom_offset_is_plate_height_not_brick_height() -> None:
    print("test_plate_bottom_offset_is_plate_height_not_brick_height")
    bbox = get_part_bbox("3623")  # Plate 1 x 3
    _assert(not bbox.is_center_mounted, "a plate rests on its bottom too")
    _assert(bbox.mount_offset_ldu == PLATE_LDU, f"a plate's origin sits only PLATE_LDU above its bottom (got {bbox.mount_offset_ldu})")


def test_wheel_is_center_mounted_not_bottom_mounted() -> None:
    print("test_wheel_is_center_mounted_not_bottom_mounted")
    # The real reason this module exists: a wheel+tyre assembly is modeled
    # hub-centered, NOT bottom-center like a brick. Confirmed to matter in
    # practice, not just in theory: a live "80's 911 targa" generation had
    # its wheels' true bottom edge sink 24 LDU (a full course) below the
    # chassis's own bottom edge under the old bottom-mount-only assumption,
    # because the wheel's radius (~31 LDU) exceeds a full course (24 LDU).
    bbox = get_part_bbox("3482c01")  # Wheel Rim 8x17.5 with Black Tyre
    _assert(bbox.footprint_studs[0] >= 3, f"a real wheel+tyre is multiple studs across, not 1 (got {bbox.footprint_studs})")
    _assert(bbox.is_center_mounted, "a wheel's origin is at its geometric center, not its bottom")
    _assert(bbox.mount_offset_ldu != BRICK_LDU, f"a wheel's mount offset must NOT equal the brick assumption (got {bbox.mount_offset_ldu})")


def test_unknown_part_falls_back_to_nominal_placeholder() -> None:
    print("test_unknown_part_falls_back_to_nominal_placeholder")
    bbox = get_part_bbox("this_part_id_does_not_exist_999999")
    _assert(bbox.footprint_studs == (1, 1), "falls back to the old nominal footprint")
    _assert(not bbox.is_center_mounted, "falls back to bottom-mount, not center-mount")
    _assert(bbox.mount_offset_ldu == BRICK_LDU, "falls back to the old nominal bottom offset")


def run_all_tests() -> None:
    tests = [
        test_standard_brick_matches_known_brick_ldu,
        test_wide_brick_footprint_matches_its_name,
        test_plate_bottom_offset_is_plate_height_not_brick_height,
        test_wheel_is_center_mounted_not_bottom_mounted,
        test_unknown_part_falls_back_to_nominal_placeholder,
    ]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} tests passed.")


if __name__ == "__main__":
    run_all_tests()
