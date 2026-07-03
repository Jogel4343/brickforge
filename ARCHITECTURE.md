# Brickforge architecture

> **STALE — describes an abandoned approach.** This file documents the
> LegoGPT-direct-generation pipeline (chunked generation, stitcher module,
> Gurobi stability pass on raw brick output), which was scrapped — see the
> "remove LegoGPT-era chunked-generation scaffolds" commit. Current
> architecture: Claude does semantic decomposition into an IR of shape
> primitives, deterministic Python (`worker/filler.py`) does spatial
> placement/legolization. See `CLAUDE.md` (canonical) and
> `docs/V2_1_ARCHITECTURE.md` (adopted 2026-07-01, the actual replacement for
> this document). Do not use this file to orient a new session.

## Overview

Three components, deployed independently:

1. **Web app** (Next.js on Vercel) — UI, auth, viewer, API routes.
2. **Database + storage** (Supabase) — design metadata, parts catalog, file storage.
3. **GPU worker** (Python on Modal) — runs LegoGPT, planning, stability checks.

The web app is the user-facing surface. Long-running generation is delegated to
the worker via a signed HTTP call; status is polled by the client. Output files
(`.ldr`, `.png`) are uploaded by the worker directly to Supabase Storage.

## Generation pipeline

### Default path (text-direct, available now via LegoGPT)

```
prompt
  └── /api/generate
        └── insert designs row, status=queued
              └── POST → Modal worker.http_generate
                    └── if grid_size <= 20:
                          legogpt_runner.generate(prompt, grid_size=N)
                       else:
                          chunked_planner.plan_chunks(prompt, grid_size)
                          parallel: legogpt_runner.generate(chunk) for each chunk
                          stitcher.stitch(chunk_results)
                    └── Gurobi stability + connectivity pass
                    └── color palette snap (LAB nearest-neighbor → LDraw codes)
                    └── step_planner.plan_steps(bricks)
                    └── upload .ldr + .png to Supabase Storage
                    └── update designs row with paths + stats
        └── return design_id (poll for status)
```

### Optional path (image-conditioned, v1.1)

Adds Meshy text-to-3D as a preprocessing stage when the user wants image
conditioning or higher fidelity than the text-direct path provides.

```
prompt or image
  └── Meshy API → 3D mesh
  └── trimesh voxelize → voxel grid
  └── LegoGPT.infer(voxels=...)  # voxel-conditioned mode
  ... rest same as default
```

## Chunked / "subagent" generation (large builds)

LegoGPT's published cap is ~20³. To support larger builds:

1. **Decomposition**. For a target grid of N (where N > 20):
   - Text-conditioned: an LLM (Claude) takes the prompt and emits a JSON chunk
     plan with semantic sub-parts (cockpit, wing-L, wing-R, body, engine), each
     with its own bounding box and sub-prompt.
   - Voxel-conditioned: split the voxel grid into overlapping 20³ subvolumes
     with 2-brick overlap regions at each face.
2. **Parallel inference**. Each chunk runs as an independent LegoGPT call. Modal's
   `function.map` runs them concurrently across worker replicas.
3. **Stitching**. The `stitcher` module:
   - Aligns chunk-local coordinate frames into a global frame.
   - Resolves brick collisions at chunk interfaces (prefer the larger brick,
     fall back to the chunk with higher generation confidence).
   - Enforces stud-aligned connections crossing every interface — if none exist,
     synthesize connector bricks.
   - Re-runs the Gurobi stability pass on the merged structure; backtracks
     (re-runs offending chunks with different seeds) if instability is local
     and tractable.
4. **Granularity tradeoff**. Smaller chunks = better local detail but more visible
   seams. Larger chunks = better cohesion but closer to LegoGPT's cap. Start
   at 16³ chunks with 4-brick overlap. Tune per niche.

This is documented in `worker/chunked_planner.py` and lights up Week 5–6.

## Step planning

The step planner turns an unordered brick list into a buildable instruction
sequence.

### Layer slicing (v1)

1. Sort bricks by Y (vertical).
2. Group bricks at the same Y into a "layer".
3. Within a layer, group by connected components → multiple steps per layer if
   parts are not connected.
4. Target 5–15 new bricks per step; merge small steps, split large ones.

### Subassembly detection (v1.5)

1. Build the brick-adjacency graph (edge if two bricks share studs).
2. Find articulation points whose removal disconnects a small subgraph (<25%
   of bricks).
3. Surface those subgraphs as standalone subassemblies — built first, then
   attached to the main model.

### Natural-language prose (v1.5)

For each step, send the step's bricks + the preceding scene state to Claude with
a fixed prompt template: "Place the red 2×4 plate on top of step 12's black
base, oriented horizontally." This costs ~$0.001–$0.005 per step. ~30 steps/
model → ~$0.10/model total. No fine-tuning required.

## Storage layout

Supabase Storage buckets:
- `designs/{design_id}/model.ldr` — canonical LDraw output
- `designs/{design_id}/preview.png` — hero render
- `designs/{design_id}/steps/step-{n}.png` — per-step renders (v1.5)
- `designs/{design_id}/instructions.pdf` — downloadable booklet (v1.5)

## Auth + RLS

- Anonymous users can browse public designs (`is_public = true`).
- Authenticated users can create designs and see their own private designs.
- Designers (profiles.is_designer = true) can publish listings.
- Marketplace purchases use Stripe Connect (Phase 4).

## Costs (per design, A10G GPU)

| Stage | Cost (USD) | Notes |
|-------|-----------|-------|
| LegoGPT inference (single chunk) | ~$0.02 | 30–90s on A10G |
| Chunked generation (5 chunks) | ~$0.10–$0.30 | parallel; includes stitch retries |
| LLM prose for steps (~30 steps) | ~$0.10 | Claude Haiku |
| BrickLink price lookup | $0 | free API |
| Storage + bandwidth | <$0.01 | Supabase free tier covers MVP |
| **Typical small design** | **~$0.05** | ✅ unit econ viable |
| **Typical large chunked design** | **~$0.50** | ✅ unit econ viable |

## Open questions tracked elsewhere

- **Gurobi commercial licensing** (`docs/COMMERCIAL_GURBI.md`)
- **Brick inventory model** (Phase 2)
- **Fulfillment partner choice** (Phase 5)
