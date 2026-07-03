# Special-Parts Lane TODO

Deferred issues found while wiring roadmap #5 (2026-07-02). Neither blocks
the wiring itself — `worker/special_parts.py` resolves and places correctly
end-to-end; these are catalog/ranking-quality issues one layer down.

## Fixed

1. **`lookup_part` ranking was wrong for vehicle wheels and lights —
   CONFIRMED against real Claude output, then fixed with a curated alias
   table (2026-07-02).** Ran `python -m scripts.claude_ir_gen "a small car"
   --runs 3`: the wiring worked (well-formed `special_parts`, correct
   `attach_to`/`offset_studs`, no crashes), but every wheel/headlight query
   resolved to the wrong part, even with size qualifiers:

   | Claude's query | Resolved to | Actual part |
   |---|---|---|
   | "small wheel 8mm with tire" | `52395` | Boat Ship's Wheel (steering-wheel prop) |
   | "small wheel with tire 12mm" | `52395` | same — boat wheel again |
   | "small car wheel 18mm" | `30663` | Car Steering Wheel 2L (dashboard part) |
   | "small round headlight" | `u1852` | Roadsign Round Small (a street sign) |

   3/3 wheel queries and 1/1 headlight query wrong — 100% failure rate on
   the most common vehicle special-part category, despite Claude reliably
   including size qualifiers. Stronger, more concrete signal than the
   earlier isolated `"wheel"` test (which at least surfaced a
   Technic/steering wheel, not a boat prop).

   Root cause: `_DECORATION_PENALTY_TOKENS` in `worker/tools.py` penalizes
   stickers/prints/Duplo/hinges/etc., but nothing penalizes
   "steering"/novelty-prop tokens or rewards wheel-plus-tire assemblies
   specifically, so a "wheel"-named prop with token overlap outranks the
   actual round rubber-tire wheel parts (whose LDraw names likely don't
   contain the literal word "wheel") or ranks them equally.

   **Fix**: `_CURATED_INTENT_ALIASES` in `worker/tools.py` — a small
   intent→verified-part_id table checked BEFORE fuzzy ranking, bounded to
   exactly the two intents that failed for real (`"wheel"` → `3482c01`,
   Wheel Rim 8 x 17.5 with Black Tyre; `"headlight"` → `4070`, Brick 1x1
   with Headlight). Did **not** touch general ranking — `tools.py` has
   already been iterated on multiple times (see git log: "smarter
   ranking", "penalize Duplo, specialization, aliases, obsolete parts"),
   and tuning it further was exactly the kind of comfortable deterministic
   component CLAUDE.md warns against polishing. Re-ran the live pipeline
   after the fix: both intents now resolve correctly in fresh generations.
   Dimension-qualified non-vehicle queries were never affected —
   `"slope 45 2x2"` correctly top-ranks `3039`, `"brick 2x4"` correctly
   top-ranks `3001`, before and after.

   **Grow this table only when a new intent is observed to fail for real**
   (not speculatively) — see `_CURATED_INTENT_ALIASES`'s docstring.

2. **Special parts were placed with a nominal `footprint_studs=(1, 1)` and a
   hardcoded `BRICK_LDU` bottom offset, regardless of the resolved part's
   real size or origin convention (2026-07-02).** Pulled forward out of
   Stage 4 (roadmap #6) deliberately narrow: real per-part geometry for
   placement, NOT the Gurobi/HiGHS stability solver and NOT collision-
   checking against the structural grid (both still deferred — see #4
   below).

   `worker/part_geometry.py` recursively parses a resolved part's actual
   `.dat` geometry (and every sub-file/primitive it references, applying
   the full transform chain) into a true bounding box, replacing the
   placeholder. Verified against real parts before trusting the placement
   formula: standard bricks/plates (3005, 3001, 3623, 4070) confirmed their
   origin sits exactly `BRICK_LDU` (24) or `PLATE_LDU` (8) above the part's
   true bottom — matching what `worker/filler.py` already assumed for its
   whitelisted vocabulary, now confirmed rather than assumed. A wheel+tyre
   assembly (3482c01) came back fully symmetric about its own origin in all
   three axes (X[-31,31] Y[-31,31] Z[-10,10]) — a hub-centered convention,
   not bottom-center — proof the old hardcoded `BRICK_LDU` offset was
   silently wrong for anything that isn't a brick, and that per-part
   geometry is genuinely necessary, not a nice-to-have.

   `worker/special_parts.py` now: uses the real footprint instead of
   `(1, 1)`; treats `offset_studs` as the part's intended CENTER (Claude
   can't see real part size when it writes that field, so centering,
   not min-cornering, is the least-surprising interpretation) and derives
   the min-corner from the real footprint; uses `course * BRICK_LDU +
   bottom_offset_ldu` instead of always `+ BRICK_LDU`. Live-verified: a
   fresh `"a small car"` generation through `/api/generate` placed wheels
   (3482c01) at LDraw Y = -31, matching the real geometry, not the old
   flat brick-course assumption.

   **What this does NOT fix**, on purpose: horizontal (X/Z) placement still
   assumes the footprint is centered on the part's own origin — true for
   every part checked (bricks, plates, the wheel, a minifig head), NOT true
   for genuinely asymmetric parts (slopes/wedges — confirmed 3039's Z range
   is `[-30, 10]`, not symmetric). Real slope/wedge PARTS remain deferred
   (CLAUDE.md) for exactly this reason. And a part is still placed as if it
   rests on the bottom of its target course — correct for anything meant to
   sit on a surface, an approximation for anything meant to mount by a
   different reference point (a wheel's hub, say). No per-category "this
   part mounts by its center" semantics exist; that's future work if it
   turns out to matter in practice, not built speculatively now.

## Real bugs

3. **`load_catalog()` cold-load cost.** First-ever load (walking 24,297
   `.dat` files, reading each): ~143s. A second load in a fresh process
   dropped to ~2.5s, but that's the OS page cache, not anything in the
   code — `@lru_cache` is per-process, and each live generation request
   (`scripts/generate_one.py`) is a fresh subprocess. On a cold container
   this tax lands on every single request.

   **Pre-production concern, not a wiring blocker.** During development,
   load once per process and iterate against the warm object. Needs an
   on-disk cached index (same shape as `worker/omr_ingest.py`'s
   `build_index` → JSON) before live generation requests can absorb this —
   do that when going to production, not now. If the 143s makes local dev
   painful, keep one process warm rather than building cache
   serialization/invalidation infra for a problem that isn't live yet.

4. **Collision-freedom is conflated with physical soundness — no grounding/
   support check exists anywhere in the pipeline (found 2026-07-02, while
   scoping roadmap #7).** The union occupancy grid (`worker/filler.py`)
   guarantees no two sub-assemblies claim the same cell, and
   `scripts/claude_ir_gen.py`'s `count_collisions` + "sanity" stage only
   check dimension bounds and cell overlap. Nothing checks whether a
   sub-assembly's footprint has material (or the ground) in the course
   directly beneath it.

   Confirmed against a real generated run
   (`data/runs/simple_house/run_01_ir.json`): `chimney` is a `box` at
   `[2, 10, 1]` sitting on top of `roof`, a `cone` at `[0, 6, 0]` dims
   `[12, 4, 8]`. `cone` shrinks its footprint on both horizontal axes every
   course going up (`worker/filler.py`), so by the top course the footprint
   has shrunk well inward from the 12x8 base — there's no guarantee
   `(x=2, z=1)` still has cone material directly beneath the chimney's
   column. The filler packs the chimney anyway; the run reports 0
   collisions and passes every existing check. (Door/window "lintels" in
   the same runs looked like a similar risk at first glance but aren't —
   `front_door`/`front_door_lintel` etc. are solid boxes with a distinct
   `color_code`, not voids, so there's real brick underneath them
   floor-to-ceiling.)

   **Not fixed now — logged as a known issue.** Roadmap **#6 (Gurobi/HiGHS
   stability check) becomes load-bearing, not optional polish, the moment
   overhang/angled shapes ship**: real voids (actual door/window openings
   instead of solid-but-recolored boxes), SNOT/angled sub-assemblies, or
   non-90-degree rotation all make "sits on a solid box below" no longer a
   safe default assumption. Until then, Claude's tendency to stack things
   on solid ground is a convention, not an enforced property.

   Optional lightweight mitigation if a floating chimney would visibly hurt
   a demo: a grounding check (does each sub-assembly's footprint have
   material or ground in the course beneath it?) is roughly an afternoon of
   work — much narrower than a full Gurobi/HiGHS stability solve. Not built
   as part of this pass; do it only if it's about to be seen.

## Non-issues (deliberate design choices)

- `public/ldraw` added to `catalog.py`'s `_LDRAW_ROOT_CANDIDATES` so local
  dev doesn't need `LDRAW_LIBRARY_PATH` set manually — this is a path-
  discovery fix, not the disk-cached index described in #2.
- No collision checking between special parts and the structural union
  grid — that's Stage 4 (roadmap #6) territory, not this wiring step. Real
  per-part geometry (#2 above) fixes WHERE a part sits; it doesn't check
  whether that placement overlaps something else.
