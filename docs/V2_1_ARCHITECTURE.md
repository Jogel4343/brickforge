# Brickforge v2.1 Architecture — Revised Division of Labor

**Status**: Adopted 2026-07-01. Supersedes stage-level details in `V2_PIPELINE_SPEC.md`.

## Why we revised

v2.0 had Claude selecting *every* part, including structural bricks (2x4 bricks, 1x2 plates, tiles). That's the geometry-blind part of an LLM doing geometry. It produces plausible-sounding, geometrically broken assemblies where `check_assembly_validity` catches collisions but not "this doesn't look like a wing."

Two things changed our mind:

1. **LLMs have great semantic knowledge and terrible spatial knowledge.** Claude knows what an X-wing looks like. Claude does not know that a 2x4 brick at (0,0,0) rotated 90° collides with a 1x2 plate at (1,0,0). Asking it to reason about stud coordinates plays to its weakness.
2. **Legolization is a solved problem.** The academic literature (Gower et al. and follow-ups) has 20 years of algorithms for "given a voxelized shape, greedy-merge into legal interlocking bricks." We don't need to invent it; we need to reuse it.

## New division of labor

| Layer | Who | What it does |
|-------|-----|--------------|
| Semantic understanding | Claude | "What is an X-wing? What are its sub-parts and rough proportions?" |
| Decomposition | Claude | Emit an IR of shape primitives + attachment plan |
| Special parts | Claude + `lookup_part` | Canopies, cannons, wheels, minifigs — parts that need world knowledge |
| Legolization | Python solver | Voxelize each primitive, greedy-merge into whitelisted brick vocabulary, enforce interlocking |
| Corpus knowledge | OMR retrieval | Few-shot exemplars from real designer MPDs |
| Validation | Python + Gurobi | Collision, connectivity, stability |

## The 5 stages

### Stage 0 — Photo → text brief (optional)
Unchanged from v2.0. Claude Vision on any user-uploaded photo produces a text brief. Never photo-to-mesh.

### Stage 1 — OMR retrieval + decomposition

1. Given a prompt, retrieve top-3 OMR MPDs semantically similar to it
2. Extract submodel names + part-frequency histograms from those MPDs
3. Inject as few-shot exemplars into Claude's decomposition prompt
4. Claude emits an **Intermediate Representation (IR)** — a list of shape primitives, NOT parts

Example IR for "starfighter":

```json
{
  "sub_assemblies": [
    {
      "name": "fuselage",
      "primitive": "tapered_slab",
      "dims_studs": [6, 24, 6],
      "taper": {"axis": "y", "from": 6, "to": 3},
      "attach": null
    },
    {
      "name": "left_upper_wing",
      "primitive": "tapered_slab",
      "dims_studs": [12, 4, 1],
      "taper": {"axis": "x", "from": 4, "to": 2},
      "attach": {"to": "fuselage", "face": "-x", "angle_deg": 15}
    },
    {
      "name": "cockpit",
      "primitive": "wedge_with_canopy",
      "dims_studs": [4, 4, 3],
      "attach": {"to": "fuselage", "face": "+y_top"},
      "special_parts": ["canopy_2x4"]
    }
  ],
  "assembly_order": ["fuselage", "cockpit", "left_upper_wing", "right_upper_wing", "left_lower_wing", "right_lower_wing"]
}
```

Claude is genuinely good at this level of reasoning — it's language-adjacent. Proportions, symmetry, attachment logic are the same skills as writing a build guide.

### Stage 2 — Special parts (narrowed)

Claude uses `lookup_part` ONLY for parts inside `special_parts` arrays. Canopies, cannons, wheels, minifigs, exhaust cones — things that carry semantic meaning geometry can't invent. Structural bricks (plain bricks, plates, tiles, slopes) are NEVER named by Claude.

### Stage 3 — Legolization solver

Deterministic Python. For each primitive in the IR:

1. **Voxelize** into LDraw grid units (20 LDU per stud horizontal, 8 LDU per plate height)
2. **Greedy-merge** voxels into the largest legal bricks from a **whitelisted vocabulary (~50 parts)**:
   - Bricks: 1x1, 1x2, 1x3, 1x4, 1x6, 1x8, 2x2, 2x3, 2x4, 2x6, 2x8
   - Plates: 1x1, 1x2, 1x4, 1x6, 1x8, 2x2, 2x4, 2x6, 2x8, 4x4, 4x6
   - Tiles: 1x1, 1x2, 1x4, 2x2, 2x4
   - Slopes: 45° 2x1, 45° 2x2, 45° 2x4, 33° 3x1, 33° 3x2, 33° 6x1, 65° 2x1, 65° 2x2
   - Wedge plates: 3x3, 3x6, 4x4, 4x6, 6x6
   - Curved slopes for aerodynamic surfaces
3. **Enforce interlocking** — no vertical seam alignment across layers (like real brickwork)
4. **Connectivity graph check** — every brick must connect to at least one other via studs
5. **Attach sub-assemblies** — apply the IR's attachment specs, with hinge plates for angled attachments

For SNOT surfaces (angled aerodynamic panels), use bracket parts from a small SNOT vocabulary layered on top.

### Stage 4 — Merge + validate + write

1. Combine solver-produced structural bricks + Claude-selected special parts
2. Run `check_assembly_validity` (already built) — collision + unknown-ID + color checks
3. Run Gurobi/HiGHS stability check (existing integration from LegoGPT deployment)
4. Write `.ldr`
5. Generate BrickLink XML wanted-list

## Roadmap update

Old Push 2-5 sequence is dead. New order:

1. **Push 2 — OMR ingest.** Parse MPDs, index by submodel name + part frequency. `worker/omr_ingest.py`. Bounded, high leverage, unblocks IR schema design. Do this now.
2. **Push 3 — IR schema + Claude decomposition prompt.** Once we've seen what real MPDs contain, define the IR JSON schema and system prompt. Wire Claude API.
3. **Push 4 — Legolization solver.** Voxelize + greedy-merge with the whitelisted vocabulary. Start with straight walls, add slopes, add SNOT.
4. **Push 5 — End-to-end demo.** Prompt → OMR retrieval → Claude IR → solver → `.ldr` → viewer.

## What this deprecates from v2.0

- Stage 2 in `V2_PIPELINE_SPEC.md` (Claude selecting all parts) — dead
- The idea that `check_assembly_validity` alone was sufficient hallucination defense — insufficient
- Ranking algorithm being on the critical path — de-prioritized (structural brick selection moves to solver, so ranking only needs to be good enough for special parts)

## Open questions

- **SNOT limits.** Classic legolization algorithms (Gower et al.) were built for architectural voxel models. Angled aerodynamic surfaces need SNOT (studs-not-on-top) which those algorithms don't handle. Push 4 will need custom logic.
- **OMR corpus size vs quality.** Rebrickable's OMR has thousands of MPDs but coverage per theme varies. Star Wars is well-covered; Bionicle isn't. May need to gate the "aspirational" prompts by corpus coverage.
- **Retrieval method.** Simple keyword match on OMR set names + descriptions is probably fine for v1. Vector search on submodel names is a v2 improvement if we need it.
