"""Self-contained tests for worker/omr_ingest.py.

Doesn't need real OMR files — synthesizes tiny MPDs in a temp dir and
verifies the parser + index + retrieval end-to-end.

Run:
    python -m worker.omr_ingest_test
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from worker.omr_ingest import parse_mpd, build_index, retrieve


# A tiny hand-written MPD that looks like OMR: a "starfighter" with 3 submodels.
SAMPLE_MPD_STARFIGHTER = """\
0 FILE 12345-1-starfighter.ldr
0 12345-1 Rebel Starfighter
0 Name: 12345-1-starfighter.ldr
0 Author: Test Author
0 !THEME Star Wars
0 !LICENSE Redistributable

1 4 0 0 0 1 0 0 0 1 0 0 0 1 fuselage.ldr
1 4 -60 0 0 1 0 0 0 1 0 0 0 1 left_wing.ldr
1 4 60 0 0 1 0 0 0 1 0 0 0 1 right_wing.ldr

0 FILE fuselage.ldr
0 Fuselage
0 Name: fuselage.ldr

1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat
1 4 40 0 0 1 0 0 0 1 0 0 0 1 3001.dat
1 4 -40 0 0 1 0 0 0 1 0 0 0 1 3001.dat
1 4 0 -24 0 1 0 0 0 1 0 0 0 1 3022.dat

0 FILE left_wing.ldr
0 Left Wing
0 Name: left_wing.ldr

1 4 0 0 0 1 0 0 0 1 0 0 0 1 3022.dat
1 4 20 0 0 1 0 0 0 1 0 0 0 1 3022.dat
1 4 -20 0 0 1 0 0 0 1 0 0 0 1 3039.dat

0 FILE right_wing.ldr
0 Right Wing
0 Name: right_wing.ldr

1 4 0 0 0 1 0 0 0 1 0 0 0 1 3022.dat
1 4 20 0 0 1 0 0 0 1 0 0 0 1 3022.dat
1 4 -20 0 0 1 0 0 0 1 0 0 0 1 3039.dat
"""

# A second sample: a "castle tower" with different theme, to prove retrieval discriminates.
SAMPLE_MPD_CASTLE = """\
0 FILE 22222-1-castle-tower.ldr
0 22222-1 Medieval Castle Tower
0 Name: 22222-1-castle-tower.ldr
0 Author: Another Author
0 !THEME Castle

1 14 0 0 0 1 0 0 0 1 0 0 0 1 wall.ldr
1 14 0 24 0 1 0 0 0 1 0 0 0 1 wall.ldr
1 14 0 48 0 1 0 0 0 1 0 0 0 1 roof.ldr

0 FILE wall.ldr
0 Wall Section
0 Name: wall.ldr

1 14 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat
1 14 40 0 0 1 0 0 0 1 0 0 0 1 3001.dat
1 14 -40 0 0 1 0 0 0 1 0 0 0 1 3001.dat

0 FILE roof.ldr
0 Roof Cone
0 Name: roof.ldr

1 14 0 0 0 1 0 0 0 1 0 0 0 1 3039.dat
1 14 0 8 0 1 0 0 0 1 0 0 0 1 3040.dat
"""


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)
    print(f"  ok — {msg}")


def test_parse_starfighter() -> None:
    print("test_parse_starfighter")
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "starfighter.mpd"
        path.write_text(SAMPLE_MPD_STARFIGHTER)

        parsed = parse_mpd(path)

    _assert(parsed.set_number == "12345-1", f"set_number == 12345-1 (got {parsed.set_number!r})")
    _assert("Starfighter" in parsed.set_name, f"set_name mentions Starfighter (got {parsed.set_name!r})")
    _assert(parsed.theme == "Star Wars", f"theme == 'Star Wars' (got {parsed.theme!r})")
    _assert(parsed.author == "Test Author", f"author == 'Test Author' (got {parsed.author!r})")
    _assert(parsed.submodel_count == 4, f"submodel_count == 4 (got {parsed.submodel_count})")

    top = parsed.submodels[0]
    _assert(top.name.endswith("starfighter.ldr"), f"top submodel name (got {top.name!r})")
    _assert(top.part_counts == {}, f"top has no direct base parts (got {top.part_counts})")
    _assert(sum(top.submodel_refs.values()) == 3, f"top refs 3 submodels (got {top.submodel_refs})")

    # Aggregate: 3x 3001 in fuselage + 1x 3022 in fuselage
    #          + 2x 3022 + 1x 3039 in left_wing
    #          + 2x 3022 + 1x 3039 in right_wing
    # Totals: 3001=3, 3022=5, 3039=2
    _assert(parsed.aggregate_part_counts.get("3001") == 3, f"aggregate 3001 == 3 (got {parsed.aggregate_part_counts.get('3001')})")
    _assert(parsed.aggregate_part_counts.get("3022") == 5, f"aggregate 3022 == 5 (got {parsed.aggregate_part_counts.get('3022')})")
    _assert(parsed.aggregate_part_counts.get("3039") == 2, f"aggregate 3039 == 2 (got {parsed.aggregate_part_counts.get('3039')})")


def test_build_index_and_retrieve() -> None:
    print("test_build_index_and_retrieve")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        (td_path / "starfighter.mpd").write_text(SAMPLE_MPD_STARFIGHTER)
        (td_path / "castle.mpd").write_text(SAMPLE_MPD_CASTLE)

        index_path = td_path / "index.json"
        summary = build_index(td_path, index_path)

        _assert(summary["parsed"] == 2, f"parsed 2 MPDs (got {summary['parsed']})")
        _assert(summary["failed"] == [], f"no failures (got {summary['failed']})")

        index = json.loads(index_path.read_text())
        _assert(len(index["entries"]) == 2, f"index has 2 entries (got {len(index['entries'])})")

        # Retrieval: "star wars" should surface the starfighter first
        hits = retrieve("star wars spaceship", index_path, n=2)
        _assert(len(hits) >= 1, f"got at least 1 hit for 'star wars spaceship' (got {len(hits)})")
        _assert("Starfighter" in hits[0]["set_name"], f"top hit is Starfighter (got {hits[0]['set_name']!r})")

        # Retrieval: "medieval castle" should surface the castle first
        hits = retrieve("medieval castle", index_path, n=2)
        _assert(len(hits) >= 1, f"got at least 1 hit for 'medieval castle' (got {len(hits)})")
        _assert("Castle" in hits[0]["set_name"], f"top hit is Castle (got {hits[0]['set_name']!r})")


def test_normalize_edge_cases() -> None:
    print("test_normalize_edge_cases")
    # An MPD with an "s\subpart.dat" reference should still be treated as a base part
    # (not a submodel), and its path prefix should be stripped.
    mpd = """\
0 FILE test.ldr
0 Test
1 4 0 0 0 1 0 0 0 1 0 0 0 1 s\\subthing.dat
1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat
"""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "test.mpd"
        path.write_text(mpd)
        parsed = parse_mpd(path)

    _assert(parsed.submodel_count == 1, f"single submodel (got {parsed.submodel_count})")
    parts = parsed.aggregate_part_counts
    _assert(parts.get("subthing") == 1, f"s\\subthing.dat -> 'subthing' (got {parts})")
    _assert(parts.get("3001") == 1, f"3001 counted (got {parts})")


if __name__ == "__main__":
    test_parse_starfighter()
    test_build_index_and_retrieve()
    test_normalize_edge_cases()
    print("\nAll tests passed.")
