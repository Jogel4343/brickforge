"""Local entrypoint to smoke-test the catalog + tools on the Modal worker.

Run:
    python -m modal run worker/tools_test.py::test

Should print a handful of part-search results and validate the catalog loaded
correctly. Quickest way to confirm Week 1 Push 1 is working end-to-end.
"""

from __future__ import annotations

import json

import modal

from worker.modal_app import app, image


@app.function(image=image, timeout=120)
def run_tool_checks() -> dict:
    """Smoke-test the catalog + tool functions inside the Modal container."""
    from worker.catalog import load_catalog
    from worker.tools import lookup_part, find_similar_parts, check_assembly_validity

    cat = load_catalog()

    summary = {
        "catalog_size": {
            "parts": len(cat.parts),
            "colors": len(cat.colors_by_code),
        },
        "sample_lookup_brick_2x4": [
            hit.to_dict() for hit in lookup_part("brick 2x4", limit=3)
        ],
        "sample_lookup_plate_2x2": [
            hit.to_dict() for hit in lookup_part("plate 2x2", limit=3)
        ],
        "sample_lookup_wedge_plate": [
            hit.to_dict() for hit in lookup_part("wedge plate", limit=3)
        ],
        "sample_lookup_slope_45_2x2": [
            hit.to_dict() for hit in lookup_part("slope 45 2x2", limit=3)
        ],
        "sample_lookup_tile_1x4": [
            hit.to_dict() for hit in lookup_part("tile 1x4", limit=3)
        ],
        "sample_lookup_technic_pin": [
            hit.to_dict() for hit in lookup_part("technic pin", limit=3)
        ],
        "sample_similar_to_3001": [
            hit.to_dict() for hit in find_similar_parts("3001", n=3)
        ],
        "sample_validation_pass": check_assembly_validity(
            [
                {"ldraw_id": "3001", "color_code": 4, "x": 0, "y": 0, "z": 0},
                {"ldraw_id": "3001", "color_code": 4, "x": 2, "y": 0, "z": 0},
            ]
        ).to_dict(),
        "sample_validation_fail_unknown_part": check_assembly_validity(
            [
                {"ldraw_id": "fake-part-9999", "color_code": 4, "x": 0, "y": 0, "z": 0},
            ]
        ).to_dict(),
        "sample_validation_fail_collision": check_assembly_validity(
            [
                {"ldraw_id": "3001", "color_code": 4, "x": 0, "y": 0, "z": 0},
                {"ldraw_id": "3001", "color_code": 4, "x": 0, "y": 0, "z": 0},
            ]
        ).to_dict(),
    }
    return summary


@app.local_entrypoint()
def test():
    print("Running catalog + tools smoke test on Modal worker...")
    result = run_tool_checks.remote()
    print(json.dumps(result, indent=2))
