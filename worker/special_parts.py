"""Stage 2 — special-parts resolution.

Claude names semantically-loaded parts by INTENT (a free-text query in
IR.special_parts), never by part_id — the hallucination defense described
in worker/tools.py. This module resolves those intents against the real
catalog via lookup_part and places the resolved part at its attachment
point. Structural bricks are never touched here — that's worker/filler.py.

v0.2 placement model:
  - A special part's absolute anchor = the sub-assembly it's attached to
    (attach_to) plus offset_studs.
  - footprint_studs and the vertical bottom-offset now come from the
    resolved part's REAL geometry (worker/part_geometry.py), not a nominal
    (1, 1) placeholder — see that module's docstring for how the placement
    formula was derived and what it does and doesn't get right.
  - offset_studs is treated as the part's intended CENTER (Claude has no
    visibility into a part's real size when it writes offset_studs), so the
    min-corner is derived by centering the real footprint on that point.
  - Still no collision checking against the structural union grid — that
    remains Stage 4 (roadmap #6) territory; real geometry only fixes WHERE
    a part sits, not whether it overlaps something else.

Known follow-ups (lookup_part ranking gaps on broad queries; load_catalog()
cold-load cost) are tracked in docs/SPECIAL_PARTS_TODO.md, deliberately not
fixed here — see that file for why.
"""

from __future__ import annotations

from worker.catalog import Catalog, load_catalog
from worker.ir_schema import IR
from worker.ldr_writer import BRICK_LDU, PlacedBrick
from worker.part_geometry import get_part_bbox
from worker.tools import lookup_part


class SpecialPartResolutionError(ValueError):
    """A special part's query had no catalog match, or its attach_to didn't
    name a real sub-assembly. Never silently invents a part_id — that's
    exactly what lookup_part exists to prevent."""


def resolve_special_parts(ir: IR, catalog: Catalog | None = None) -> list[PlacedBrick]:
    """Resolve every IR.special_parts entry to a real part and place it.

    Returns an empty list without touching the catalog if the IR has no
    special_parts — most models won't use this stage, and load_catalog()
    is not cheap on a cold process (see docs/SPECIAL_PARTS_TODO.md).
    """
    if not ir.special_parts:
        return []

    cat = catalog or load_catalog()
    by_name = {sa.name: sa for sa in ir.sub_assemblies}

    placed: list[PlacedBrick] = []
    for sp in ir.special_parts:
        anchor = by_name.get(sp.attach_to)
        if anchor is None:
            # IR.__post_init__ already validates this, but a caller could
            # hand-construct an IR bypassing that check.
            raise SpecialPartResolutionError(
                f"{sp.name}: attach_to {sp.attach_to!r} does not name a sub_assembly"
            )

        hits = lookup_part(sp.query, limit=1, catalog=cat)
        if not hits:
            raise SpecialPartResolutionError(f"{sp.name}: no catalog match for query {sp.query!r}")
        part_id = hits[0].ldraw_id
        bbox = get_part_bbox(part_id)
        fw, fd = bbox.footprint_studs

        center_x = anchor.position_studs[0] + sp.offset_studs[0]
        course = anchor.position_studs[1] + sp.offset_studs[1]
        center_z = anchor.position_studs[2] + sp.offset_studs[2]
        x = center_x - fw // 2
        z = center_z - fd // 2
        y_ldu = course * BRICK_LDU + bbox.mount_offset_ldu

        placed.append(PlacedBrick(
            part_id=part_id,
            color_code=sp.color_code,
            x_stud=x,
            y_ldu=y_ldu,
            z_stud=z,
            footprint_studs=bbox.footprint_studs,
            height_ldu=BRICK_LDU,
            rotation_deg=sp.rotation_deg,
        ))
    return placed
