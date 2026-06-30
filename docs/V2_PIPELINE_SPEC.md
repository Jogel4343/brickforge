# Brickforge v2 Pipeline Spec — LLM-Orchestrated LEGO Design

**Status**: Draft, pre-implementation
**Date**: 2026-06-30
**Owner**: Jack
**Estimated build time**: 6–10 weeks for v1
**Estimated cost to ship v1**: ~$3–5K (LLM API costs during development + compute)

---

## TL;DR

v1 (LegoGPT-only, what we shipped today) hit a hard ceiling: ~7-brick vocabulary,
"a small chair" comes out blocky and asymmetric, can't produce anything close
to professional MOC quality.

v2 replaces the model with a **frontier LLM (GPT-5/Claude) orchestrating a
toolchain**. The LLM has world knowledge of all 17,000 LDraw parts but lacks
spatial-coordinate precision. We supplement with:

- **Tool-calling** to a real LDraw/BrickLink catalog (LLM never invents a part)
- **Structured output** (LLM emits typed JSON, not free text)
- **Deterministic coordinate solver** (Python resolves rough sub-assembly layouts
  into exact x/y/z/rotation per brick, handling stud connections + collisions)
- **Gurobi stability check** (reuse existing LegoGPT integration — reject and
  regenerate failing structures)

The result: a pipeline that can produce models using **the full 17K part
library**, with **real physics validation**, at quality genuinely better than
stock LegoGPT. Realistic ceiling: "decent enthusiast MOC", not "BrickLink UCS
flagship." X-wing UCS quality still requires either dramatic improvements or
human-designer refinement on top.

---

## Goals

### Must-have (v1 ship criteria)
- User prompts in text OR uploads an image
- Output is a valid `.ldr` / `.io` file BrickLink Studio can open
- All bricks in output are real, orderable LEGO parts (validated against LDraw + BrickLink)
- Output passes basic physics check (no floating bricks, structurally connected)
- Output includes a buildable parts list with live BrickLink pricing
- End-to-end generation: <3 minutes per design
- Per-design cost: <$2 (LLM + compute combined)
- Quality: visibly better than LegoGPT for the same prompt (head-to-head test)

### Should-have (v1 stretch)
- Render-stage preview before committing to full generation (so user can reject bad shape interpretations cheaply)
- Iterative refinement ("make the wings bigger") that doesn't re-run the whole pipeline
- Multi-resolution: simple prompts produce ~200-piece models; "detailed" prompts produce 500–2000 piece models
- Multi-color support (LLM picks from LDraw's 50+ colors)

### Out-of-scope for v1
- Marketplace / designer refinement (Phase 4)
- Physical fulfillment (Phase 5)
- Mobile app
- AR view
- Real-time collaborative design
- Auto-generated PDF instruction booklets (we'll output a viewer URL + a CSV parts list for v1; full PDF in v1.5)

---

## Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│ USER INPUT                                                         │
│  • Text prompt: "small fighter spaceship with swept-back wings"    │
│  • OR uploaded image                                               │
└────────────────────────┬───────────────────────────────────────────┘
                         │
        ┌────────────────▼────────────────┐
        │ STAGE 1: SCENE UNDERSTANDING    │
        │  GPT-5 Vision (if image)        │
        │  OR GPT-5 text comprehension    │
        │  Output: structured scene desc  │
        │    { primary_object, scale,     │
        │      key_features, style,       │
        │      sub_assemblies[] }         │
        └────────────────┬────────────────┘
                         │
        ┌────────────────▼────────────────┐
        │ STAGE 2: ASSEMBLY PLAN          │
        │  LLM with tool-calling          │
        │  Tools available:               │
        │    - lookup_part(query)         │
        │    - check_parts_compatible(a,b)│
        │    - get_color_palette(theme)   │
        │  Output: structured JSON plan   │
        │    [{ sub_assembly: "cockpit",  │
        │       parts: [...],             │
        │       rough_position: {x,y,z}}] │
        └────────────────┬────────────────┘
                         │
        ┌────────────────▼────────────────┐
        │ STAGE 3: COORDINATE SOLVER       │
        │  Python (deterministic)         │
        │  Takes rough placements,        │
        │  resolves to exact (x,y,z,rot)  │
        │  per brick, snapping to studs,  │
        │  resolving collisions, building │
        │  proper LDraw transform matrices│
        └────────────────┬────────────────┘
                         │
        ┌────────────────▼────────────────┐
        │ STAGE 4: STABILITY CHECK         │
        │  Gurobi (reuse from LegoGPT)    │
        │  • Connectivity graph           │
        │  • Stud-alignment verification  │
        │  • Center-of-gravity stability  │
        │  IF FAIL → ask LLM to fix       │
        │   the specific failing sub-asm  │
        └────────────────┬────────────────┘
                         │
        ┌────────────────▼────────────────┐
        │ STAGE 5: OUTPUT                  │
        │  • Write .ldr / .io file        │
        │  • Query BrickLink for prices   │
        │  • Generate parts list CSV      │
        │  • (v1.5) Auto-instructions     │
        └────────────────┬────────────────┘
                         │
        ┌────────────────▼────────────────┐
        │ USER OUTPUT                      │
        │  • 3D viewer (existing)         │
        │  • Download .ldr                │
        │  • Download parts CSV           │
        │  • "Buy on BrickLink" links     │
        └─────────────────────────────────┘
```

---

## Stage-by-stage details

### Stage 1: Scene Understanding

**Goal**: convert vague user input into structured scene description.

**Implementation**:
- Single LLM call (GPT-5 or Claude Sonnet, vision-enabled)
- System prompt establishes BL expert persona + LDraw vocabulary awareness
- Output schema (validated with Pydantic):
  ```python
  class SceneDescription(BaseModel):
      primary_object: str           # "fighter spaceship"
      scale: Literal["micro", "small", "medium", "large"]
      key_features: list[str]       # ["swept-back wings", "cockpit canopy"]
      style: str                    # "sleek modern", "battle-damaged retro"
      sub_assemblies: list[SubAssembly]
      estimated_piece_count: int
      dominant_colors: list[str]
  ```

**Cost**: ~$0.05–$0.20 per call (depending on image presence).

**Risk**: LLM may produce too vague or too detailed descriptions. Mitigation:
add few-shot examples in the system prompt.

---

### Stage 2: Assembly Plan

**Goal**: turn the scene into a concrete brick-level plan.

**Implementation**:
- LLM call with tool-calling enabled. Tools:
  - `lookup_part(description: str) -> Part`: query our LDraw catalog (in
    Supabase, seeded from LDraw library), returns real part_id, dimensions,
    color options.
  - `find_similar_parts(part_id: str) -> list[Part]`: alternatives if LLM
    wants a 2x4 plate but it's expensive; suggest 2x2 + 2x2.
  - `check_assembly_validity(parts: list) -> ValidationResult`: pre-validate
    a proposed sub-assembly before adding it to the plan.

- LLM produces JSON of sub-assemblies. Each sub-assembly has:
  - Rough position in model space (not exact coords yet)
  - List of bricks with part_id + color + relative orientation
  - Connection points to other sub-assemblies

**Cost**: ~$0.30–$1.00 per call (long context, multiple tool calls).

**Critical detail**: LLM is NOT asked to place exact coordinates. Just
"the cockpit is at the front, ~30% up the body length." Stage 3 resolves
exact positions deterministically.

**Risk**: LLM may produce sub-assemblies that can't fit together. Mitigation:
the validity tool checks compatibility before each addition; failures fed
back to the LLM for revision.

---

### Stage 3: Coordinate Solver

**Goal**: turn rough sub-assembly plan into exact LDraw coordinates.

**Implementation** (pure Python, deterministic):
1. For each sub-assembly:
   - Treat as a local 3D coordinate frame
   - Lay out bricks using stud-snapping logic (every brick aligned to LDraw's
     20-unit grid in X/Z and 8-unit plate height in Y)
   - Resolve internal collisions (two bricks at same coord → reject + repropose)
2. Position sub-assemblies in model space using "rough_position" hints
3. Verify connection points match between adjacent sub-assemblies (a hinge
   plate on cockpit must align with a hinge plate on body)
4. Output: list of bricks with global (x, y, z, rotation, color) tuples

**Stack**: Custom Python module `worker/coordinate_solver.py`. Uses `numpy`
for transforms, `shapely` (or `pyclipper`) for 2D collision detection at
stud level.

**Cost**: $0 (CPU-only, runs on Modal worker).

**Risk**: hand-rolling a CAD-grade solver is real work. Mitigation: start
with simple grid-aligned axis-aligned brick placement (works for 80% of
cases). Punt rotation/hinges to v1.5.

---

### Stage 4: Stability Check

**Goal**: verify the proposed model actually stands up.

**Implementation**:
- Reuse the Gurobi-based stability checker we already integrated for LegoGPT
- It models each brick as a node, each stud-connection as an edge, runs
  a structural-stability solve
- Returns: stable / unstable + which bricks are weak

**Iteration loop**:
- If unstable: identify failing sub-assembly, feed back to LLM with the
  specific failure ("the cockpit's back-left brick has no support; fix
  this section") → LLM proposes amendment → Stage 3 re-resolves → Stage 4
  re-checks
- Cap at 3 retry iterations to bound cost
- If still unstable: return best-effort output + warning

**Cost**: $0 per check (Gurobi license already paid for via WLS).

**Risk**: Gurobi solves on small grids fast, but a 2000-brick model with
fine-grained connection graph may be slow. Mitigation: solve at sub-assembly
level first (each independently stable), then check inter-assembly
connections separately.

---

### Stage 5: Output

**Goal**: produce shippable artifacts.

**Implementation**:
1. **`.ldr` file writer** (~200 lines of Python)
   - LDraw format spec is simple: header + one line per brick
   - `1 <color> <x> <y> <z> <transform matrix> <part_id>.dat`
   - Reference LDraw spec: https://www.ldraw.org/article/218.html

2. **`.io` (Stud.io native format) writer** (~stretch, v1.5)
   - Stud.io's format is a renamed zip with XML inside
   - Not strictly needed for v1; `.ldr` opens in Stud.io fine

3. **BrickLink parts list**
   - Map LDraw part_id → BrickLink part_id (mapping table from Rebrickable)
   - Query BrickLink API for live pricing + availability
   - Output CSV with columns: part_id, name, color, quantity, avg_price, link

4. **Auto-instructions** (v1.5)
   - Layer-slicing + subassembly detection (already scaffolded in
     `worker/step_planner.py`)
   - Output: ordered JSON of build steps, renderable in our existing viewer

---

## Cost model

Per-generation cost breakdown for a 500-piece MOC:

| Stage | Cost per call | Calls per generation | Subtotal |
|---|---|---|---|
| Scene understanding | $0.10 | 1 | $0.10 |
| Assembly plan (with tool calls) | $0.50 | 1 | $0.50 |
| Stability retries (avg) | $0.30 | 1.5 | $0.45 |
| GPU compute (Gurobi + parsing) | $0.02 | 1 | $0.02 |
| BrickLink API | $0.00 | 1 | $0.00 |
| **TOTAL** | | | **~$1.07** |

vs. LegoGPT alone: ~$0.02.

**At $50–150 retail per kit, $1 cost is fine.** At a free-tier hobby
demo, $1 per generation adds up — gate generation behind auth + limit
to 3/month for free users.

---

## Risks (real, ranked)

### High risk
1. **LLM spatial coordinate errors.** Even with structured output and a
   solver downstream, LLMs are notoriously bad at consistent 3D reasoning.
   The solver may have to do a lot of "interpret this loosely and fix it"
   work. *Mitigation*: keep LLM output at the sub-assembly level (not
   per-brick coords); solver handles all exact positioning.

2. **Hallucinated parts.** Even with tool-calling, LLM may reference a
   part incorrectly ("the 1x4 hinge plate" when no such part exists).
   *Mitigation*: every part lookup must succeed; failed lookups force
   LLM to revise.

3. **Coordinate solver complexity.** Implementing a real LEGO-aware CAD
   layout engine from scratch is the hardest part of this. *Mitigation*:
   start with axis-aligned, no-rotation, no-hinge layouts. Cover 80% of
   prompts. Tackle the hard stuff in v1.5.

### Medium risk
4. **Per-generation cost spirals.** If users prompt for "a Star Destroyer"
   the LLM may want to make 10K-piece models with huge context windows.
   Cost per call could 10x. *Mitigation*: cap piece count at 2000 for v1.

5. **Stability check iteration loop runs forever.** If the LLM keeps
   producing unstable designs, we burn $$ on retries. *Mitigation*: cap
   at 3 iterations, return best-effort with warning.

### Low risk
6. **BrickLink API rate limits.** Free tier has limits. *Mitigation*:
   cache part metadata locally; only query for prices on user demand.

7. **Stud.io file format changes.** Format is reasonably stable. *Mitigation*:
   we output LDraw `.ldr` which is the open standard; Stud.io reads it.

---

## Milestones

### Week 1: Foundations
- [ ] Seed LDraw parts catalog into Supabase (we have the LDraw library;
      `worker/ingest_ldraw.py` already exists, needs to be run)
- [ ] Build `lookup_part`, `find_similar_parts`, `check_assembly_validity`
      tools as Modal functions
- [ ] Wire OpenAI/Anthropic API to Modal (new secret, simple client)
- [ ] Successful end-to-end LLM call with tool-calling, no LEGO output yet

**Deliverable**: a Python script that calls GPT-5 with the LDraw tools
and gets back a structured JSON plan for "a small chair."

### Week 2: MVP pipeline (head-to-head with LegoGPT)
- [ ] Naive coordinate solver: axis-aligned, no rotation, no hinges
- [ ] `.ldr` file writer
- [ ] End-to-end: prompt → LLM plan → solver → `.ldr` → load in viewer
- [ ] Side-by-side comparison: same prompt to LegoGPT and to v2 pipeline

**Deliverable**: a `python -m modal run worker/modal_app.py::compare --prompt "a small chair"` command that produces both outputs and tells us which is visibly better.

**DECISION POINT**: if v2 output is not visibly better than LegoGPT, abort
and revisit the plan. If it is better, continue.

### Weeks 3–4: Quality + stability
- [ ] Integrate Gurobi stability check into v2 pipeline
- [ ] Iteration loop: LLM revises failing sub-assemblies
- [ ] Multi-color support
- [ ] Test battery: 20 diverse prompts, document quality range

### Weeks 5–6: Production-ready pipeline
- [ ] Rotation + simple hinge support in solver
- [ ] BrickLink parts list with live prices
- [ ] Web `/design` page wired to v2 pipeline (replacing the current LegoGPT-only flow)
- [ ] Cost monitoring + per-user generation limits
- [ ] User can choose: "fast" (skip stability iteration) or "stable" (full pipeline)

### Weeks 7–8: Polish + launch prep
- [ ] Auto-instructions (layer-slicing + step viewer in existing viewer)
- [ ] Share URLs for designs
- [ ] Improved viewer UX (per-step highlighting, etc.)
- [ ] Real user testing with 5–10 prompts from non-technical friends

### Weeks 9–10: Hardening
- [ ] Edge cases from real testing
- [ ] Performance tuning (cold-start, parallel sub-assembly generation)
- [ ] First public demo / launch

---

## Decision gates

These are the moments to pause and re-evaluate:

| Gate | When | Pass criteria | What "fail" means |
|---|---|---|---|
| Tool-calling works | End of Week 1 | LLM returns valid JSON plan using real LDraw parts | Pipeline isn't viable; abort |
| Quality > LegoGPT | End of Week 2 | Head-to-head: 5/5 prompts visibly better with v2 | Architecture problem; rethink |
| Stable outputs | End of Week 4 | 80% of generations pass stability without manual fix | Iteration loop tuning needed |
| Sellable quality | End of Week 6 | A non-technical user calls it "cool" without prompting | Position toward niches, not general |
| Cost economics | End of Week 6 | Per-generation cost < $2.00 sustained | Need to throttle LLM context or change provider |
| Ready to launch | End of Week 10 | All milestones complete + 10+ test prompts work | Push launch to week 12 |

---

## What's NOT in this spec (intentionally)

- **Marketplace / designer flywheel** (Phase 4) — covered in `ARCHITECTURE.md`
- **Physical fulfillment** (Phase 5) — covered in earlier conversations
- **Fine-tuning custom models** — this entire spec is about NOT training our own model
- **Mobile app** — web-only for v1
- **Multiple LLM providers** — we'll start with whoever has the best
  tool-calling (probably Anthropic Claude or OpenAI GPT-5); add fallback in v1.5

---

## Open questions (resolve before Week 1)

1. **LLM provider**: GPT-5 or Claude? Both have strong tool-calling; need to
   benchmark on a few sample prompts before committing.
2. **Catalog source**: LDraw has 17K parts; BrickLink has 80K parts including
   variants. Do we expose all variants to the LLM, or just LDraw's set?
   Recommendation: start with LDraw only (smaller search space, simpler).
3. **Hosting**: GPT-5/Claude on Modal via API, or directly from Next.js
   server? Pro of Modal: same place as Gurobi + coordinate solver. Pro of
   Next.js: lower latency.
4. **Cost guardrails**: hard cap on per-generation spend? Per-user monthly
   budget? Free tier limits?

---

## Why this is the right plan

I want to make the case in plain language:

1. **It solves the vocabulary problem.** LLMs already know all 17K parts.
   No retraining needed.
2. **It plays to each tool's strength.** LLM does creative reasoning;
   Python does precise math; Gurobi does optimization. No single piece
   has to be superhuman.
3. **It's incrementally testable.** Week 2 has a clear go/no-go decision.
   We don't sink 10 weeks before knowing if it works.
4. **It produces real, buildable output.** Every part is a real LEGO
   part. Every placement passes physics. Users can actually build it.
5. **It positions us for the marketplace.** AI-generated designs become
   the supply side; human designers refine top sellers; you take a cut.
   The marketplace was always the real business; v2 is the AI engine
   that makes it viable.

It's also the most under-explored idea I've seen in this space. CMU did
the academic version (LegoGPT). BrickLink did the manual version (Stud.io).
No one has built the LLM-orchestrated version because it requires
serious engineering — but the engineering is mostly known patterns
(tool-calling, structured output, deterministic solvers, Gurobi). The
risk is in the combination, not the individual pieces.

---

## What we DON'T know yet

Honest list of unknowns we'll resolve as we build:

1. How well does GPT-5/Claude actually reason about LEGO at the
   sub-assembly level? We'll find out in Week 1.
2. How big does the coordinate solver need to be? Could be 200 lines
   or 2000 depending on how much LEGO-specific logic we encode.
3. How often does the stability check fail and require iteration? If
   it's 90% of the time, costs blow up. If it's 10%, we're fine.
4. Will users prefer "fast + sometimes ugly" or "slow + better"? Probably
   need both modes.
5. What's the actual quality ceiling? We'll know by Week 6.

These are real questions we'd answer by building, not by more planning.

---

## Next concrete actions

When we resume work on this:

1. **Push the OpenAI/Anthropic API key infrastructure** to Modal (new secret)
2. **Run `worker/ingest_ldraw.py`** to seed the parts catalog in Supabase
3. **Write the first tool functions** (`lookup_part`, `find_similar_parts`)
4. **Make the first GPT-5/Claude call** with tool-calling, dump the structured
   output for inspection

That gets us through Week 1's primary deliverable. Then Week 2 starts the
MVP pipeline.
