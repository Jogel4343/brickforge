"""IR (Intermediate Representation) — the contract between Claude and the filler.

This is a LOCKED interface. Claude's decomposition output must conform to
this schema; the filler reads only this schema. Anything Claude wants to
express about a model must be expressible in this schema, and anything the
filler needs to know must be readable from this schema.

Design principles:
  - Bricks are NEVER in the IR. If it names a part_id, it's wrong.
  - Shapes are in STUDS (LEGO's natural unit), not LDU. The filler converts.
  - Y is vertical (positive = up). X and Z are horizontal (LDraw convention:
    LDraw actually uses Y as vertical-negative, we flip in the writer).
  - Positions are the min-corner of the shape's bounding box.
  - v0.1 supports only two shapes: 'box' and 'cone'. Enough for a tower.
    Everything else — tapered_slab, wedge, sphere, SNOT — is future work.

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


# Supported shapes for v0.1. Expand deliberately; every addition means work
# in filler.py.
ShapeType = Literal["box", "cone"]


@dataclass
class SubAssembly:
    """One shape primitive in a model. Bricks are NOT specified here — the
    filler picks bricks from its whitelisted vocabulary to fill this shape.

    Coordinates and dimensions are in studs. Position is the min-corner of
    the shape's axis-aligned bounding box.
    """
    name: str                         # human-readable label, e.g. "north_wall"
    shape: ShapeType                  # 'box' or 'cone'
    position_studs: list[int]         # [x, y, z] min-corner
    dims_studs: list[int]             # [width_x, height_y, depth_z]
    color_code: int = 4               # LDraw color code (4 = red; 14 = yellow; 71 = light bluish grey)

    def __post_init__(self) -> None:
        if len(self.position_studs) != 3:
            raise ValueError(f"{self.name}: position_studs must be [x, y, z], got {self.position_studs}")
        if len(self.dims_studs) != 3:
            raise ValueError(f"{self.name}: dims_studs must be [w, h, d], got {self.dims_studs}")
        if any(d <= 0 for d in self.dims_studs):
            raise ValueError(f"{self.name}: dims_studs must all be positive, got {self.dims_studs}")
        if self.shape not in ("box", "cone"):
            raise ValueError(f"{self.name}: unsupported shape {self.shape!r}")


@dataclass
class IR:
    """Complete decomposition of one model — the output of Stage 1 (Claude)
    and the input to Stage 3 (filler)."""
    name: str                             # e.g. "medieval_tower"
    sub_assemblies: list[SubAssembly]     # order = build order (bottom-up)
    schema_version: str = "0.1"           # bump when incompatible changes ship

    def __post_init__(self) -> None:
        if not self.sub_assemblies:
            raise ValueError(f"{self.name}: IR has no sub_assemblies")
        seen_names: set[str] = set()
        for sa in self.sub_assemblies:
            if sa.name in seen_names:
                raise ValueError(f"{self.name}: duplicate sub_assembly name {sa.name!r}")
            seen_names.add(sa.name)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IR":
        subs = [SubAssembly(**sa) for sa in data.get("sub_assemblies", [])]
        return cls(
            name=data["name"],
            sub_assemblies=subs,
            schema_version=data.get("schema_version", "0.1"),
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
                    "shape": {"type": "string", "enum": ["box", "cone"]},
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
                },
            },
        },
    },
}
