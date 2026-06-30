"""Step-by-step instruction planner.

Input: list of bricks with (ldraw_id, color, x, y, z, rotation) — typically from
LegoGPT or chunked_planner output.

Output: ordered list of "steps", each containing a small group of bricks. Steps
are bottom-up by Y coordinate, with subassembly detection so that natural
groupings (engines, wings, etc.) are built separately and attached.

Algorithms:
  1. Layer slicing — sort bricks by Y, group bricks at the same Y into the same
     layer; within a layer chunk by connected components.
  2. Subassembly detection — build the brick-adjacency graph; find articulation
     points whose removal splits the graph into a small isolated subgraph (these
     are natural subassemblies, e.g. a turret attached by one stud).
  3. Step granularity — target 5-15 new bricks per step; merge tiny steps,
     split huge ones along subassembly boundaries.

Scaffold for Week 6.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Brick:
    ldraw_id: str
    color: int
    x: int
    y: int
    z: int
    rot: int = 0


@dataclass
class Step:
    index: int
    bricks: list[Brick] = field(default_factory=list)
    label: str | None = None       # optional LLM-generated prose
    is_subassembly: bool = False
    parent_step: int | None = None


def plan_steps(bricks: list[Brick]) -> list[Step]:
    """Returns ordered build steps. Stub for now."""
    raise NotImplementedError("Wire up in Week 6.")
