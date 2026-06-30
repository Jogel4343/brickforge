"""Chunked / subagent-style generation for large LEGO builds.

Concept (matches user's "split into subagents, each builds a piece"):

  1. Decompose the target into semantic sub-parts (cockpit, wing-L, wing-R, hull, engine).
     For text-conditioned generation we can use an LLM to propose the decomposition
     given the prompt; for voxel-conditioned generation we use volumetric chunking.
  2. Each sub-part is generated independently by a LegoGPT "subagent" (one Modal
     invocation per chunk, run in parallel via modal.starmap).
  3. Stitcher merges chunks: aligns interfaces, enforces stud-connection across seams,
     and runs a global stability pass.

This file is a scaffold — full implementation lands Week 5/6 after stock LegoGPT
integration is proven in Week 4.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Chunk:
    name: str                              # e.g. "cockpit", "left_wing"
    prompt: str                            # sub-prompt for LegoGPT
    bounds: tuple[int, int, int, int, int, int]  # (xmin,ymin,zmin,xmax,ymax,zmax) in brick coords
    interfaces: list["Interface"] = field(default_factory=list)


@dataclass
class Interface:
    """A shared boundary between two chunks where bricks must connect."""
    chunk_a: str
    chunk_b: str
    plane: str                             # "x=10" or "y=5" etc
    required_stud_alignment: bool = True


def plan_chunks(prompt: str, target_grid: int) -> list[Chunk]:
    """Use an LLM (Claude) to decompose the prompt into semantic chunks.

    Stub for now — returns a single chunk equal to the full target. Replace with
    an LLM call that emits a JSON chunk plan.
    """
    return [
        Chunk(
            name="root",
            prompt=prompt,
            bounds=(0, 0, 0, target_grid, target_grid, target_grid),
        )
    ]


def stitch(chunk_results: list[dict], chunks: list[Chunk]) -> dict:
    """Merge per-chunk brick lists into a single model. Enforces:
      - No spatial collisions across chunk boundaries
      - At least one stud-aligned connection per interface
      - Global stability (connectivity graph + Gurobi physics pass)
    """
    raise NotImplementedError("Wire up in Week 5/6.")
