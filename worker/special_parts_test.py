"""Tests for worker/special_parts.py — the Stage 2 resolve-then-place wiring.

Uses a small hand-built Catalog (not the real 24K-part one) so tests are
fast and deterministic, and don't depend on public/ldraw being present.
Run:
    python -m worker.special_parts_test
"""

from __future__ import annotations

from worker.catalog import Catalog, Part, _tokenize
from worker.ir_schema import IR, SubAssembly, SpecialPart
from worker.ldr_writer import BRICK_LDU
from worker.special_parts import resolve_special_parts, SpecialPartResolutionError
from worker.tools import lookup_part


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)
    print(f"  ok — {msg}")


def _synthetic_catalog(*parts: tuple[str, str, str | None, int | None, int | None]) -> Catalog:
    """Build a tiny Catalog from (ldraw_id, name, category, width, length) tuples,
    indexed the same way load_catalog() indexes the real one."""
    parts_by_id: dict[str, Part] = {}
    parts_by_name_tokens: dict[str, set[str]] = {}
    for ldraw_id, name, category, width, length in parts:
        p = Part(ldraw_id=ldraw_id, name=name, category=category, width_studs=width, length_studs=length, is_official=True)
        parts_by_id[ldraw_id] = p
        for tok in _tokenize(name):
            parts_by_name_tokens.setdefault(tok, set()).add(ldraw_id)
    return Catalog(parts=parts_by_id, parts_by_name_tokens=parts_by_name_tokens, colors_by_code={}, colors_by_name={})


def test_resolve_special_parts_places_known_good_query() -> None:
    print("test_resolve_special_parts_places_known_good_query")
    cat = _synthetic_catalog(("3001", "Brick  2 x  4", "Brick", 2, 4))
    ir = IR(
        name="test",
        sub_assemblies=[SubAssembly("chassis", "box", [0, 0, 0], [4, 1, 8], 71)],
        special_parts=[SpecialPart("deco", "brick 2x4", attach_to="chassis", offset_studs=[1, 1, 1], color_code=4)],
    )
    placed = resolve_special_parts(ir, catalog=cat)
    _assert(len(placed) == 1, f"resolves to exactly 1 placed part (got {len(placed)})")
    p = placed[0]
    _assert(p.part_id == "3001", f"resolved to the catalog match (got {p.part_id})")
    # Real 3001.dat geometry (worker/part_geometry.py) is a 4x2-stud brick,
    # not the old nominal 1x1 placeholder — footprint reflects that, and
    # offset_studs is treated as the part's intended CENTER, so the
    # min-corner shifts by half the real footprint.
    _assert(p.footprint_studs == (4, 2), f"real 3001 geometry is a 4x2 brick (got {p.footprint_studs})")
    _assert((p.x_stud, p.z_stud) == (-1, 0), f"min-corner centers the real footprint on anchor+offset (got {(p.x_stud, p.z_stud)})")
    # bottom_offset_ldu for a standard full-height brick == BRICK_LDU, same
    # as the old hardcoded assumption — this case is unchanged in practice.
    _assert(p.y_ldu == 2 * BRICK_LDU, f"y_ldu uses course = anchor_y + offset_y (got {p.y_ldu})")
    _assert(p.color_code == 4, "keeps the special part's own color_code")


def test_resolve_special_parts_empty_list_is_a_noop() -> None:
    print("test_resolve_special_parts_empty_list_is_a_noop")
    ir = IR(name="test", sub_assemblies=[SubAssembly("chassis", "box", [0, 0, 0], [4, 1, 8])])
    # No catalog passed either — must not attempt load_catalog() for an IR
    # with no special_parts (would be slow/fail without a real LDraw root).
    placed = resolve_special_parts(ir)
    _assert(placed == [], "no special_parts means no work and no catalog load")


def test_resolve_special_parts_raises_on_unresolvable_query() -> None:
    print("test_resolve_special_parts_raises_on_unresolvable_query")
    cat = _synthetic_catalog(("3001", "Brick  2 x  4", "Brick", 2, 4))
    ir = IR(
        name="test",
        sub_assemblies=[SubAssembly("chassis", "box", [0, 0, 0], [4, 1, 8])],
        special_parts=[SpecialPart("mystery", "flux capacitor", attach_to="chassis")],
    )
    try:
        resolve_special_parts(ir, catalog=cat)
        raise AssertionError("expected SpecialPartResolutionError")
    except SpecialPartResolutionError:
        pass
    _assert(True, "raises rather than inventing a part_id when nothing matches")


def test_lookup_part_curated_alias_overrides_bad_wheel_ranking() -> None:
    print("test_lookup_part_curated_alias_overrides_bad_wheel_ranking")
    # Include a decoy that fuzzy ranking would otherwise pick — this is
    # what actually happened against the real catalog (see
    # docs/SPECIAL_PARTS_TODO.md): a steering-wheel prop outranked the
    # real wheel+tire assembly.
    cat = _synthetic_catalog(
        ("3482c01", "Wheel Rim  8 x 17.5 with Axlehole with Black Tyre  7/ 56 x 17 Offset Tread", "Wheel", 8, None),
        ("2741", "Technic Steering Wheel Large", "Technic", None, None),
    )
    # Both real Claude-generated queries observed this session — one with
    # "tire", one (run 3's "small car wheel 18mm") without it.
    for query in ["small wheel 8mm with tire", "small car wheel 18mm"]:
        hits = lookup_part(query, catalog=cat)
        _assert(len(hits) >= 1, f"{query!r} returns at least 1 hit")
        _assert(hits[0].ldraw_id == "3482c01", f"{query!r} resolves to the curated wheel+tire part (got {hits[0].ldraw_id})")


def test_lookup_part_curated_alias_overrides_bad_headlight_ranking() -> None:
    print("test_lookup_part_curated_alias_overrides_bad_headlight_ranking")
    cat = _synthetic_catalog(
        ("4070", "Brick  1 x  1 with Headlight", "Brick", 1, 1),
        ("u1852", "Roadsign Round Small without Base", None, None, None),
    )
    hits = lookup_part("small round headlight", catalog=cat)
    _assert(len(hits) >= 1, "returns at least 1 hit")
    _assert(hits[0].ldraw_id == "4070", f"resolves to the curated headlight brick (got {hits[0].ldraw_id})")


def test_lookup_part_fuzzy_path_unaffected_for_other_queries() -> None:
    print("test_lookup_part_fuzzy_path_unaffected_for_other_queries")
    cat = _synthetic_catalog(("3001", "Brick  2 x  4", "Brick", 2, 4))
    hits = lookup_part("brick 2x4", catalog=cat)
    _assert(len(hits) == 1 and hits[0].ldraw_id == "3001", "queries with no curated intent still use fuzzy ranking")


def run_all_tests() -> None:
    tests = [
        test_resolve_special_parts_places_known_good_query,
        test_resolve_special_parts_empty_list_is_a_noop,
        test_resolve_special_parts_raises_on_unresolvable_query,
        test_lookup_part_curated_alias_overrides_bad_wheel_ranking,
        test_lookup_part_curated_alias_overrides_bad_headlight_ranking,
        test_lookup_part_fuzzy_path_unaffected_for_other_queries,
    ]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} tests passed.")


if __name__ == "__main__":
    run_all_tests()
