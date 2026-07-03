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

- IR schema locked (`worker/ir_schema.py`): shapes `box`, `cone`, `wedge`,
  `tapered_slab`.
- Filler v0.x (`worker/filler.py`): 22-part structural brick vocabulary (full
  canonical straight range, 1x1 to 12x24, verified against
  `public/ldraw/parts/*.dat` bounding boxes), all 4 shapes, 0/90-degree
  rotation, seam-phase interlocking (odd courses start each row with a
  2-stud brick so vertical seams stagger instead of stacking into
  full-height cracks). Vocab footprints are `(size_x, size_z)` in TRUE native
  `.dat` orientation, NOT name order — "Brick 1 x 4" (3010) runs its long
  axis along X, so its footprint is `(4, 1)`. Any new vocab part must match
  its `.dat` geometry. Plates/tiles/slopes deliberately not added yet: plates
  need sub-brick-course height in the IR schema, tiles/slopes need
  shape-aware placement the box-filler doesn't have — both later roadmap
  items.
- **Union occupancy grid (2026-07-02, roadmap #2 done):** sub-assemblies are
  no longer packed independently. Every sub-assembly rasterizes into
  `{absolute_course: {(x, z): color}}` cells; `fill_ir` merges all
  sub-assemblies' cells per course (first-in-build-order wins a contested
  cell) before packing, so a brick can span across a sub-assembly boundary —
  a corner where two walls meet interlocks instead of each wall packing
  independently up to a static seam. Side effect: brick-level collisions are
  now structurally impossible (each cell is claimed by exactly one
  sub-assembly before any brick is placed), confirmed 0/0/0 across a
  re-run of the medieval-tower experiment that previously showed 6-8
  collisions per run.
- LDR writer works (`worker/ldr_writer.py`). LDraw gotchas in its docstring:
  Y is down, part origin is bottom-center, stud = 20 LDU, brick course = 24
  LDU, plate = 8 LDU.
- OMR retrieval built and tested (`worker/omr_ingest.py`), NOT yet wired into
  prompting — deliberately skipped ahead of #5 this session (see roadmap #4):
  no real corpus exists (only synthetic test MPDs), corpus acquisition from
  omr.ldraw.org is a separate undecided project (`docs/PARSER_TODO.md` #4),
  and the schema-design urgency that originally motivated "do OMR first" is
  gone now that the IR schema is locked and validated at 10/10.
- Part-lookup tools (`worker/tools.py`, `worker/catalog.py`) verified
  reachable LOCALLY (2026-07-02) — not just "deployed on Modal" as previously
  assumed. `public/ldraw` (24,297 real parts) added to
  `catalog.py`'s root-candidate list; `load_catalog()` works without Modal or
  env vars. Cold load is ~143s (walking every `.dat`); OS-cache-warm reload
  is ~2.5s. No on-disk cached index yet — fine for dev, needed before this
  is live-request-safe (`docs/SPECIAL_PARTS_TODO.md`).
- **Core thesis validated (2026-07-02):** Claude emits valid, buildable IRs
  from a text prompt. `scripts/claude_ir_gen.py` scored 10/10 on
  "medieval tower" and 10/10 on "simple house" (parse + schema + sanity +
  fill + ldr). Artifacts in `data/runs/<slug>/`.
- Tower fixture (`data/fixtures/tower.json`) verified post-fix (ring-closure
  and mirror-symmetry checks predate items 1-2 below, not re-run since):
  24/24 wall courses closed rings, 24/24 mirror-symmetric, 0 identical
  adjacent layers, 0 collisions. Brick count at time of that check was 233;
  now 127 after the vocab growth (item 1) and union-grid merge (item 2).
- **`wedge` and `tapered_slab` shapes (2026-07-02, roadmap #3 done):** same
  rasterize-then-union-pack pipeline as box/cone — `wedge` shrinks the
  footprint on only ONE horizontal axis per course (`taper_axis`), ending in
  a full-width ridge row instead of a point (gable/ridge roofs);
  `tapered_slab` has a constant-height, constant-per-course footprint that's
  itself a symmetric trapezoid narrowing to `taper_to_studs` (angled
  facades, tapered towers, hulls). Both are filled with the same stepped
  regular-brick approach as `cone` — **not** real LDraw slope/wedge PARTS,
  which remain deferred (see below). Live-validated: Claude correctly used
  `wedge` for gable roofs AND gable-end triangle walls, and for a boat's
  triangular sail; `tapered_slab` for boat hulls — including uses not in
  any worked example, just from the schema description + inline examples in
  the system prompt. 0 collisions across all test runs.
- **Special-parts lane wired end-to-end (2026-07-02, roadmap #5 done):**
  `IR.special_parts` (name, `query` free-text intent, `attach_to` + relative
  `offset_studs`, `rotation_deg`, `color_code`) → `worker/special_parts.py`
  resolves each query via `lookup_part` against the real catalog and places
  it at `attach_to`'s position + offset, using the same y_ldu convention as
  the structural filler. Placed with a nominal `footprint_studs=(1, 1)` —
  real per-part geometry (a minifig head's origin isn't bottom-center like
  a brick's) is Stage 4 territory (#6), not this wiring step; same for
  collision-checking special parts against the structural grid. Live-
  validated end-to-end: `python -m scripts.claude_ir_gen "a small car"`
  produced well-formed `special_parts` with correct `attach_to`/offsets on
  every run. First pass surfaced a real ranking bug (wheel/headlight
  queries resolved to a steering wheel / a roadsign) — fixed with a
  `_CURATED_INTENT_ALIASES` table in `worker/tools.py`, bounded to exactly
  the two intents confirmed to fail for real, not general ranking tuning.
  Re-verified live after the fix: both resolve correctly now. Full details
  in `docs/SPECIAL_PARTS_TODO.md`.
- **Second thesis validated: the full loop renders in a browser
  (2026-07-02, roadmap #7 core experiment done).** Inventory ahead of
  building #7 found `/api/generate` (`src/app/api/generate/route.ts`)
  already shells out to `python -m scripts.generate_one <prompt>`, which
  runs the real pipeline (Claude decomposition → `fill_ir` →
  `resolve_special_parts` → `render_to_string`), and `/design`
  (`src/app/design/page.tsx`) already calls it, blob-URLs the returned
  `.ldr`, and feeds it into `LdrawViewer` — prompt to rendered model in one
  browser session, no manual steps. First thesis was "Claude emits valid
  IRs" (10/10 x2); this is "a person can go prompt-to-rendered-model,"
  proven, not just plausible.
  **Architectural landmine this surfaces:** `/api/generate` works via a
  local Python subprocess — that is a local-dev-only bridge and will NOT
  run on Vercel (serverless functions can't spawn a long-running Python
  process with the anthropic SDK + LDraw library). Going live requires
  porting the pipeline into the Modal worker and having the route call it
  over HTTP — `worker/modal_app.py` is currently still the abandoned
  LegoGPT-era code (GPU inference, HF token, pre-2026-07-01 pivot) and a
  deployed-but-dead Modal app (`brickforge-worker`, created 2026-06-30)
  reflects that old code, not today's pipeline. That port is real,
  non-trivial future work, not a detail to discover mid-deploy.
  Also confirmed at the time: Supabase was fully unprovisioned — see the
  persistence entry below for what changed.
- **Persistence slice done (2026-07-02, roadmap #7 persistence seam
  closed):** Supabase provisioned; `supabase/schema.sql` cleaned of
  LegoGPT-era fields (`grid_size`, `chunked`, `brick_rejections`) and
  applied, with a public `designs` Storage bucket + public-read RLS policy
  added. `/api/generate` (`src/app/api/generate/route.ts`) now saves every
  successful generation's `.ldr` to Storage and a `designs` row via
  `persistDesign()` — fails soft, so a Supabase outage doesn't break
  generation, it just means that run isn't shareable. New `/d/[id]`
  (`src/app/d/[id]/page.tsx`) server-renders a saved design from Supabase
  by id — a real shareable link, not a transient blob URL. Live-verified
  against the real project: `POST /api/generate` → row + Storage object
  confirmed in Postgres/Storage, public Storage URL returns 200, `/d/[id]`
  server-renders the real prompt. One real bug found and fixed along the
  way: CLI-transport generation (`ANTHROPIC_API_KEY` unset, shells out
  through the `claude` CLI) took 121-186s, past the route's old 120s
  subprocess timeout — bumped `GENERATE_TIMEOUT_MS` to 240s. The other #7
  seam (porting the pipeline off a local subprocess into the Modal worker,
  required before this can run on Vercel) is still open.
- **Real per-part geometry for special-parts placement (2026-07-02, partial
  roadmap #6 — see "Deferred on purpose" and `docs/SPECIAL_PARTS_TODO.md`
  #2 for what this does and doesn't cover):** `worker/part_geometry.py`
  recursively parses a resolved special part's actual `.dat` geometry
  (transform-chain-aware, resolving every sub-file/primitive reference)
  into a true bounding box, replacing the placeholder
  `footprint_studs=(1, 1)` + hardcoded `BRICK_LDU` bottom-offset that
  `worker/special_parts.py` used before. Verified against real geometry
  first, not assumed: standard bricks/plates confirmed their origin sits
  exactly `BRICK_LDU`/`PLATE_LDU` above their true bottom (matching what
  `worker/filler.py` already assumed for its whitelisted vocabulary); a
  wheel+tyre assembly (3482c01) came back fully symmetric about its own
  origin in all three axes — a hub-centered convention, proof the old
  hardcoded brick-shaped assumption was silently wrong for anything that
  isn't a brick. `worker/special_parts.py` now uses the real footprint and
  `course * BRICK_LDU + bottom_offset_ldu`, and treats `offset_studs` as
  the part's intended center (deriving the min-corner from the real
  footprint), since Claude can't see a part's real size when it writes
  that field. Live-verified: a fresh `"a small car"` generation placed
  wheels at their real geometric offset, not the old flat assumption.
  Does NOT touch collision-checking special parts against the structural
  grid, connectivity, or Gurobi/HiGHS stability — those remain deferred.

### Deferred on purpose (accepted limitations)

- Real LDraw slope/wedge PARTS (as opposed to the `wedge` IR *shape*, which
  exists now — see "Current state" above), SNOT (studs-not-on-top / angled
  panels), non-90-degree rotation, and the "attachment plan for angled
  sub-assemblies" (attaching a special part to a sloped face) — that needs
  the special-parts lane (roadmap #5) to exist first, since there's nothing
  to attach yet.

## Roadmap

Build out the solver (structural lane):
1. ~~Grow the filler vocabulary from 7 to the full canonical ~50 (all
   standard brick/plate/tile/slope sizes, correct native footprints).~~
   Bricks done (2026-07-02): 22-part canonical straight-brick range, 1x1 to
   12x24. Plates/tiles/slopes intentionally deferred — plates need sub-brick
   height in the IR schema (item 3 territory), tiles/slopes need shape-aware
   placement the box-filler doesn't have; adding their part IDs to today's
   flat-rectangle packer would place them like a plain brick, which is wrong.
2. ~~Union occupancy grid per layer across sub-assemblies — fixes the corner
   butt-joint limitation; the real general packing engine.~~ Done
   (2026-07-02): see "Current state" above. `fill_box`/`fill_cone` still work
   standalone (rasterize + pack one sub-assembly) for unit testing; `fill_ir`
   rasterizes all sub-assemblies and merges before packing.
3. ~~Expand IR shapes beyond box/cone: `tapered_slab`, `wedge`~~, plus the
   attachment plan for angled sub-assemblies. Shapes done (2026-07-02): see
   "Current state" above. Attachment plan intentionally NOT done — deferred
   to roadmap #5 (special-parts lane), since there's no special part yet to
   attach to a sloped face.
4. Wire OMR retrieval into the decomposition prompt (built, tested, just not
   connected yet). Deliberately SKIPPED past this session in favor of #5 —
   see "Current state" above for why. Still on the roadmap; do it once
   there's an actual corpus-acquisition plan (`docs/PARSER_TODO.md` #4).

Unlock the full library (special-parts lane):
5. ~~Realize Stage 2 end-to-end: Claude emits `special_parts`, `lookup_part`
   resolves them against the full catalog, solver places them per attachment
   specs.~~ Done (2026-07-02): see "Current state" above and
   `docs/SPECIAL_PARTS_TODO.md`.
6. Stage 4 validation: collision + connectivity + Gurobi/HiGHS stability,
   then the BrickLink parts list. **Partially started (2026-07-02):** real
   per-part bounding-box geometry for special-parts placement is done
   (`worker/part_geometry.py`) — see "Current state" below and
   `docs/SPECIAL_PARTS_TODO.md` #2. Collision-checking special parts against
   the structural grid, connectivity, and the Gurobi/HiGHS stability solve
   are all still NOT done.

Then the product:
7. End-to-end web integration: prompt -> pipeline -> `.ldr` -> viewer,
   Supabase persistence, eventually the marketplace. **Core experiment
   (prompt-to-rendered-model in a browser) done 2026-07-02 — see "Current
   state" above.** Remaining work is hardening, not thesis-proving, and
   splits into two independently-shippable seams:
   - Persistence: provision Supabase, strip LegoGPT-era fields from
     `supabase/schema.sql`, save each generated `.ldr` to Storage + a
     `designs` row, add a `/d/[id]` page so designs are saved and
     shareable instead of a transient blob URL. No Modal port needed.
   - Deployed worker: port the pipeline (currently a local subprocess,
     `scripts/generate_one.py`) into the Modal worker and have
     `/api/generate` call it over HTTP instead of shelling out — required
     before this can run on Vercel; `worker/modal_app.py` is still the old
     LegoGPT code and needs replacing, not just redeploying. Bigger lift,
     deliberately scoped separately.

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

`README.md` and the root `ARCHITECTURE.md` describe the ABANDONED approach:
LegoGPT generating bricks directly, chunked generation, a stitcher, the
Week 1-8 plan. That path is dead (see the "remove LegoGPT-era
chunked-generation scaffolds" commit). Both files now carry a stale-doc
banner at the top pointing here and to `docs/V2_1_ARCHITECTURE.md`. Trust
this file and `docs/` over them until they are rewritten or deleted.
