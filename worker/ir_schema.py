"""IR (Intermediate Representation) — the contract between Claude and the filler.

This is a LOCKED interface. Claude's decomposition output must conform to
this schema; the filler reads only this schema. Anything Claude wants to
express about a model must be expressible in this schema, and anything the
filler needs to know must be readable from this schema.

Design principles:
  - Bricks are NEVER in sub_assemblies. If one names a part_id, it's wrong.
  - Shapes are in STUDS (LEGO's natural unit), not LDU. The filler converts.
  - Y is vertical (positive = up). X and Z are horizontal (LDraw convention:
    LDraw actually uses Y as vertical-negative, we flip in the writer).
  - Positions are the min-corner of the shape's bounding box.
  - v0.2 supports four shapes: 'box', 'cone', 'wedge', 'tapered_slab'.
    Everything else — sphere, SNOT, non-90-degree rotation — is future work.
    Note 'wedge' here is an IR SHAPE (a ridge that tapers toward a line,
    filled with regular stepped bricks — the same "ugly slice" approach as
    'cone'), not a real LDraw slope/wedge PART. Real angled parts are a
    separate, still-deferred piece of work (see CLAUDE.md).
  - v0.3 adds special_parts (optional): semantically-loaded parts named by
    INTENT (free-text query), never by part ID — Claude still never names a
    part directly. worker/special_parts.py resolves the query against the
    real catalog via lookup_part. Positioned relative to a sub_assembly
    (attach_to + offset_studs), not by absolute world coordinates.

Why hand-write this before Claude sees it:
  If we let Claude emit its own JSON shape and then reverse-engineer the schema,
  Claude is defining the contract. We want Claude conforming to a spec we
  designed for the filler. Direction of authority matters.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


# Supported shapes for v0.2. Expand deliberately; every addition means work
# in filler.py.
ShapeType = Literal["box", "cone", "wedge", "tapered_slab"]
TaperAxis = Literal["x", "z"]


@dataclass
class SubAssembly:
    """One shape primitive in a model. Bricks are NOT specified here — the
    filler picks bricks from its whitelisted vocabulary to fill this shape.

    Coordinates and dimensions are in studs. Position is the min-corner of
    the shape's axis-aligned bounding box.
    """
    name: str                         # human-readable label, e.g. "north_wall"
    shape: ShapeType                  # 'box', 'cone', 'wedge', or 'tapered_slab'
    position_studs: list[int]         # [x, y, z] min-corner
    dims_studs: list[int]             # [width_x, height_y, depth_z]
    color_code: int = 4               # LDraw color code (4 = red; 14 = yellow; 71 = light bluish grey)
    taper_axis: TaperAxis = "z"       # 'wedge'/'tapered_slab' only: which horizontal axis tapers
    taper_to_studs: int | None = None  # 'tapered_slab' only (required): width at the far end —
                                        # smaller than dims_studs' wide dimension to narrow (a
                                        # tapered tower, a hull), larger to widen (a flared fender)

    def __post_init__(self) -> None:
        if len(self.position_studs) != 3:
            raise ValueError(f"{self.name}: position_studs must be [x, y, z], got {self.position_studs}")
        if len(self.dims_studs) != 3:
            raise ValueError(f"{self.name}: dims_studs must be [w, h, d], got {self.dims_studs}")
        if any(d <= 0 for d in self.dims_studs):
            raise ValueError(f"{self.name}: dims_studs must all be positive, got {self.dims_studs}")
        if self.shape not in ("box", "cone", "wedge", "tapered_slab"):
            raise ValueError(f"{self.name}: unsupported shape {self.shape!r}")
        if self.taper_axis not in ("x", "z"):
            raise ValueError(f"{self.name}: taper_axis must be 'x' or 'z', got {self.taper_axis!r}")
        if self.shape == "tapered_slab":
            if self.taper_to_studs is None or self.taper_to_studs < 1:
                raise ValueError(f"{self.name}: tapered_slab requires taper_to_studs >= 1")
            # No upper bound here: taper_to_studs may be smaller than the
            # base (narrowing — a tapered tower, a boat hull) OR larger
            # (widening — a flared fender). Both are structurally valid;
            # only the overall footprint has a sane cap, and that belongs
            # to the generation pipeline's sanity_check (which already owns
            # MAX_FOOTPRINT_STUDS), not this schema module.


@dataclass
class SpecialPart:
    """A semantically-loaded part named by INTENT, never by part ID — the
    hallucination defense in worker/tools.py. `query` is free text (e.g.
    "wheel 30mm", "minifig head"); a separate deterministic step
    (worker/special_parts.py) resolves it against the real catalog via
    lookup_part. Positioned relative to a sub-assembly, not by absolute
    world coordinates: attach_to + offset_studs is added to that
    sub-assembly's position_studs to get the placement anchor.
    """
    name: str                          # unique label, e.g. "front_left_wheel"
    query: str                         # free-text intent, e.g. "wheel 30mm" — NEVER a part ID
    attach_to: str                     # must name an existing sub_assembly
    offset_studs: list[int] = field(default_factory=lambda: [0, 0, 0])  # [dx, dy, dz] from attach_to's position_studs
    rotation_deg: int = 0              # 0 or 90, same convention as structural bricks
    color_code: int = 4                # LDraw color code

    def __post_init__(self) -> None:
        if len(self.offset_studs) != 3:
            raise ValueError(f"{self.name}: offset_studs must be [dx, dy, dz], got {self.offset_studs}")
        if self.rotation_deg not in (0, 90):
            raise ValueError(f"{self.name}: rotation_deg must be 0 or 90, got {self.rotation_deg}")
        if not self.attach_to:
            raise ValueError(f"{self.name}: attach_to is required")


@dataclass
class IR:
    """Complete decomposition of one model — the output of Stage 1 (Claude)
    and the input to Stage 3 (filler)."""
    name: str                             # e.g. "medieval_tower"
    sub_assemblies: list[SubAssembly]     # order = build order (bottom-up)
    schema_version: str = "0.1"           # bump when incompatible changes ship
    special_parts: list[SpecialPart] = field(default_factory=list)  # optional Stage 2 parts

    def __post_init__(self) -> None:
        if not self.sub_assemblies:
            raise ValueError(f"{self.name}: IR has no sub_assemblies")
        seen_names: set[str] = set()
        for sa in self.sub_assemblies:
            if sa.name in seen_names:
                raise ValueError(f"{self.name}: duplicate sub_assembly name {sa.name!r}")
            seen_names.add(sa.name)
        sub_assembly_names = seen_names
        for sp in self.special_parts:
            if sp.name in seen_names:
                raise ValueError(f"{self.name}: duplicate name {sp.name!r} (sub_assemblies and special_parts share one namespace)")
            seen_names.add(sp.name)
            if sp.attach_to not in sub_assembly_names:
                raise ValueError(f"{self.name}: special_part {sp.name!r} has attach_to {sp.attach_to!r}, which is not a sub_assembly name")

    def normalize_positions(self) -> None:
        """Translate every sub-assembly so no coordinate is negative.

        Claude sometimes emits a negative X (or Z) for a naturally symmetric
        part — e.g. "wing_left" positioned relative to a centered design —
        even though the schema requires non-negative positions. Since every
        sub-assembly shifts by the same per-axis amount, relative geometry
        is unchanged; this is a lossless repair, not a guess. Axes that are
        already non-negative are left untouched (no-op for valid IRs).
        """
        mins = [min(sa.position_studs[axis] for sa in self.sub_assemblies) for axis in range(3)]
        shift = [-m if m < 0 else 0 for m in mins]
        if not any(shift):
            return
        for sa in self.sub_assemblies:
            sa.position_studs = [sa.position_studs[i] + shift[i] for i in range(3)]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IR":
        subs = [SubAssembly(**sa) for sa in data.get("sub_assemblies", [])]
        special = [SpecialPart(**sp) for sp in data.get("special_parts", [])]
        return cls(
            name=data["name"],
            sub_assemblies=subs,
            schema_version=data.get("schema_version", "0.1"),
            special_parts=special,
        )

    @classmethod
    def from_json_file(cls, path: str | Path) -> "IR":
        return cls.from_dict(json.loads(Path(path).read_text()))


# JSON Schema — the machine-readable version we'll eventually send to Claude
# as part of the system prompt. Kept next to the dataclass so they can't drift.
JSON_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["name", "sub_assemblies"],
    "properties": {
        "name": {"type": "string", "description": "Human-readable model name, e.g. 'medieval_tower'."},
        "schema_version": {"type": "string", "const": "0.1"},
        "sub_assemblies": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["name", "shape", "position_studs", "dims_studs"],
                "properties": {
                    "name": {"type": "string"},
                    "shape": {"type": "string", "enum": ["box", "cone", "wedge", "tapered_slab"]},
                    "position_studs": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "minItems": 3,
                        "maxItems": 3,
                        "description": "[x, y, z] min-corner in studs. Y is up.",
                    },
                    "dims_studs": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 1},
                        "minItems": 3,
                        "maxItems": 3,
                        "description": "[width_x, height_y, depth_z] in studs. Y is up.",
                    },
                    "color_code": {
                        "type": "integer",
                        "description": "LDraw color code. Common: 4 red, 14 yellow, 71 light bluish grey, 72 dark bluish grey, 0 black.",
                        "default": 4,
                    },
                    "taper_axis": {
                        "type": "string",
                        "enum": ["x", "z"],
                        "default": "z",
                        "description": (
                            "'wedge' and 'tapered_slab' ONLY. Which horizontal axis the taper "
                            "runs along. 'z': the shape narrows as Z increases, so the ridge "
                            "(wedge) or narrow end (tapered_slab) runs along X. 'x': narrows as "
                            "X increases, ridge/narrow end runs along Z."
                        ),
                    },
                    "taper_to_studs": {
                        "type": "integer",
                        "minimum": 1,
                        "description": (
                            "'tapered_slab' ONLY, required. Width in studs at the far end. "
                            "The near/base end is the dims_studs component perpendicular to "
                            "taper_axis (width_x if taper_axis is 'z', depth_z if 'x'). Smaller "
                            "than the base to NARROW (a tapered tower, a hull); larger to WIDEN "
                            "(a flared fender, a trumpet bell)."
                        ),
                    },
                },
            },
        },
        "special_parts": {
            "type": "array",
            "description": (
                "OPTIONAL. Semantically-loaded parts a generic box/cone/wedge/tapered_slab "
                "primitive can't represent — wheels, canopies/windscreens, cannons, minifig "
                "accessories. Do NOT use this for plain structural elements (walls, floors, "
                "roofs); those stay as sub_assemblies."
            ),
            "items": {
                "type": "object",
                "required": ["name", "query", "attach_to"],
                "properties": {
                    "name": {"type": "string", "description": "Unique label, shares a namespace with sub_assembly names."},
                    "query": {
                        "type": "string",
                        "description": (
                            "Free-text INTENT for the part, e.g. 'wheel 30mm', 'minifig head', "
                            "'canopy windscreen'. NEVER a specific LDraw part ID or number — a "
                            "separate deterministic step resolves this against the real catalog."
                        ),
                    },
                    "attach_to": {"type": "string", "description": "Name of an existing sub_assembly this part is positioned relative to."},
                    "offset_studs": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "minItems": 3,
                        "maxItems": 3,
                        "default": [0, 0, 0],
                        "description": "[dx, dy, dz] added to attach_to's position_studs to get the placement anchor.",
                    },
                    "rotation_deg": {"type": "integer", "enum": [0, 90], "default": 0},
                    "color_code": {
                        "type": "integer",
                        "description": "LDraw color code. Common: 0 black (tires/wheels), 4 red, 71 light bluish grey.",
                        "default": 4,
                    },
                },
            },
        },
    },
}
