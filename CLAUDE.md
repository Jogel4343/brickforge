# CLAUDE.md — Brickforge

Project context for Claude Code. This file is canonical: when it conflicts
with `README.md` or the root `ARCHITECTURE.md`, this file and `docs/` win
(see "Stale docs" at the bottom).

## What Brickforge is

Text prompt -> an AI-designed, physically buildable LEGO model, output as an
open-format `.ldr` file (LDraw) and rendered in a Next.js + Three.js viewer.
Long-term this becomes a marketplace where designers sell AI-generated build
instructions, with optional "ship me the bricks" fulfillment. Today it is a
portfolio-grade tech proof, not the marketplace.

## The core architectural bet (do not re-litigate)

Split the work by what each side is good at: **Claude does semantic
reasoning, deterministic Python does spatial reasoning.**

LLMs have great semantic knowledge ("what does an X-wing look like, what are
its sub-parts and proportions") and terrible spatial knowledge ("does a 2x4
at (0,0,0) rotated 90 degrees collide with a 1x2 at (1,0,0)"). So Claude is
kept away from brick IDs and stud coordinates for structural work.
Legolization (voxel shape -> legal interlocking bricks) is a solved problem
in the literature; we reuse it rather than ask the LLM to fake it.

Full rationale in `docs/V2_1_ARCHITECTURE.md` (adopted 2026-07-01, supersedes
`docs/V2_PIPELINE_SPEC.md`).

## The pipeline

- **Stage 0 (optional):** photo -> text brief via Claude Vision. Never
  photo-to-mesh.
- **Stage 1 — decomposition:** prompt -> an **IR of shape primitives**
  (Claude), optionally seeded with few-shot exemplars retrieved from the OMR
  corpus of real designer models. The IR is a locked contract
  (`worker/ir_schema.py`): primitives, dims in studs, attachment plan — no
  parts.
- **Stage 2 — special parts:** Claude + `lookup_part`, but only for
  semantically-loaded parts (canopies, wheels, cannons, wings). Structural
  bricks are never named by Claude.
- **Stage 3 — legolization solver:** deterministic Python voxelizes each
  primitive and greedy-merges into a whitelisted brick vocabulary, enforcing
  interlocking (staggered seams) and stud connectivity. `worker/filler.py`
  is the current v0.x seed of this.
- **Stage 4 — merge, validate, write:** combine structural + special parts,
  run collision / connectivity / stability checks (Gurobi/HiGHS), write
  `.ldr`, generate a BrickLink parts list.

### The IR contract rules

- Bricks are NEVER in the IR. If a sub-assembly names a `part_id`, it is
  wrong.
- Shapes are in STUDS (LEGO's natural unit), not LDU. The filler converts.
- Y is up in the IR; the LDR writer flips to LDraw's Y-down on write.
- Claude never places coordinates for individual structural bricks.

## The "thousands of bricks" principle — two lanes, kept separate

The goal is models that can use any of the thousands of real LDraw parts.
That is achieved by two DIFFERENT mechanisms, not one:

1. **Structural lane — curated, bounded vocabulary.** Walls, slabs, volumes
   are filled from a small whitelist (~50 canonical parts long-term: every
   standard brick/plate/tile/slope size). This is deliberate. Feeding the
   greedy packer thousands of options makes packing slower and worse, not
   better — most of those parts are decorated, obsolete, or specialized
   variants that do not belong in a wall. Growing the whitelist is a curation
   task, not a "load the whole catalog" task.
2. **Special-parts lane — full LDraw catalog.** Semantically-loaded parts
   (canopies, wheels, wings, cannons, cheese slopes, curved aero panels,
   minifig accessories) are named by Claude by intent and resolved against
   the full catalog via `lookup_part` (`worker/tools.py` + `worker/catalog.py`,
   deployed on Modal). This is the lane where "the entire library" is used.

Anti-goal: do not make the structural filler draw from the full catalog.

## Deployment shape

- **Web app:** Next.js on Vercel — UI, viewer, API routes.
- **Database + storage:** Supabase — design metadata, parts catalog, files.
- **Worker:** Python on Modal — the solver + validation + part lookup.

Output files (`.ldr`, `.png`) land in Supabase Storage.

## Current state

- IR schema locked (`worker/ir_schema.py`): shapes `box` and `cone`.
- Filler v0.x (`worker/filler.py`): 7-part structural vocabulary, box + cone,
  0/90-degree rotation, seam-phase interlocking (odd courses start each row
  with a 2-stud brick so vertical seams stagger instead of stacking into
  full-height cracks). Vocab footprints are `(size_x, size_z)` in TRUE native
  `.dat` orientation, NOT name order — "Brick 1 x 4" (3010) runs its long
  axis along X, so its footprint is `(4, 1)`. Any new vocab part must match
  its `.dat` geometry.
- LDR writer works (`worker/ldr_writer.py`). LDraw gotchas in its docstring:
  Y is down, part origin is bottom-center, stud = 20 LDU, brick course = 24
  LDU, plate = 8 LDU.
- OMR retrieval built and tested (`worker/omr_ingest.py`), NOT yet wired into
  prompting.
- Part-lookup tools deployed on Modal (`worker/tools.py`, `worker/catalog.py`),
  de-prioritized until the special-parts stage.
- **Core thesis validated (2026-07-02):** Claude emits valid, buildable IRs
  from a text prompt. `scripts/claude_ir_gen.py` scored 10/10 on
  "medieval tower" and 10/10 on "simple house" (parse + schema + sanity +
  fill + ldr). Artifacts in `data/runs/<slug>/`.
- Tower fixture (`data/fixtures/tower.json`) verified post-fix: 24/24 wall
  courses closed rings, 24/24 mirror-symmetric, 0 identical adjacent layers,
  0 collisions, 233 bricks.

### Deferred on purpose (accepted limitations)

- Corner butt-joints between separate wall sub-assemblies every course. Right
  fix is a union occupancy grid per layer across sub-assemblies (roadmap #2),
  not packing each box independently.
- SNOT (studs-not-on-top / angled panels), wedges, plates, tapered slabs,
  angled attachments, non-90-degree rotation.

## Roadmap

Build out the solver (structural lane):
1. Grow the filler vocabulary from 7 to the full canonical ~50 (all standard
   brick/plate/tile/slope sizes, correct native footprints).
2. Union occupancy grid per layer across sub-assemblies — fixes the corner
   butt-joint limitation; the real general packing engine.
3. Expand IR shapes beyond box/cone: `tapered_slab`, `wedge`, plus the
   attachment plan for angled sub-assemblies.
4. Wire OMR retrieval into the decomposition prompt (built, tested, just not
   connected yet).

Unlock the full library (special-parts lane):
5. Realize Stage 2 end-to-end: Claude emits `special_parts`, `lookup_part`
   resolves them against the full catalog, solver places them per attachment
   specs.
6. Stage 4 validation: collision + connectivity + Gurobi/HiGHS stability,
   then the BrickLink parts list.

Then the product:
7. End-to-end web integration: prompt -> pipeline -> `.ldr` -> viewer,
   Supabase persistence, eventually the marketplace.

## How to run things

```bash
# Filler unit tests (9 tests)
python -m worker.filler_test

# Build the tower fixture to an .ldr
python -m worker.filler_test build tower

# The IR generation experiment (the core-thesis harness)
python -m scripts.claude_ir_gen "medieval tower" --runs 10
python -m scripts.claude_ir_gen "simple house" --runs 10
# add --workers 4 to parallelize; --transport api uses the anthropic SDK
# (needs ANTHROPIC_API_KEY), else it shells out to the `claude` CLI.
```

## Style

- No emojis.
- Do not use the words "scrape" or "crawl" — say "collect", "extract", "read".
- Be concrete and code-first.
- Push back on bad ideas with reasons.
- Experiment/validate before polishing. The recurring failure mode on this
  project is polishing comfortable deterministic components while the
  experiment that determines whether the product works stays "one push away."

## Stale docs (fix or ignore)

`README.md` and the root `ARCHITECTURE.md` still describe the ABANDONED
approach: LegoGPT generating bricks directly, chunked generation, a stitcher,
the Week 1-8 plan. That path is dead (see the "remove LegoGPT-era
chunked-generation scaffolds" commit). Trust this file and `docs/` over them
until they are rewritten.
