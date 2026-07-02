# OMR Parser TODO

Deferred issues in `worker/omr_ingest.py` — filed here so they don't get lost,
but not blocking the ugly-slice thesis test. Fix these once the Claude → IR
→ filler loop is proven end-to-end.

## Real bugs

1. **`walk()` cycle protection.** Real OMR MPDs are hand-authored and
   occasionally malformed. A cycle in submodel refs (A refs B refs A) will
   stack-overflow with an opaque `RecursionError`. Fix: pass a `visited` set
   through the recursion, skip and warn on cycles. Alternative: depth cap of
   ~50 with a warning at the limit. Two lines.

2. **`s\` and `48\` sub-part references shouldn't count as parts.** They're
   LDraw library internals (subparts and hi-res primitives), not orderable
   parts. Currently normalized to a bare name and counted in
   `aggregate_part_counts`, which pollutes few-shot prompts with noise.
   Skip them instead. The unit test `test_normalize_edge_cases` enshrines
   the wrong behavior — flip its assertion.

3. **`to_index_entry` doesn't truncate part counts.** A UCS-scale MPD has
   hundreds of distinct parts. Across thousands of sets the index JSON
   balloons and we only ever want the top ~30 for prompt injection. Change to
   `Counter(...).most_common(30)` at index-build time.

4. **No corpus acquisition step.** Push 2 was tested against synthetic MPDs;
   there's no script that downloads real OMR files from omr.ldraw.org.
   Options: manual curation of a small seed corpus (~50 sets), or a
   respectful mirror script (respect robots.txt and rate limits).
   Decide alongside the retrieval strategy.

## Nice-to-haves

5. **Capture `0 !LICENSE` header while parsing.** Already read headers for
   theme/author; extending to license is trivial. Doesn't matter for
   internal few-shot use; matters if index contents ever ship to users.

6. **Weight theme-token matches higher than name-token matches in
   `retrieve()`.** Currently all tokens count equally. Theme is a stronger
   semantic signal (a "Star Wars" prompt should prefer any Star Wars set
   over a same-name-word set in another theme).

## Non-issues (deliberate design choices)

- Keyword-based retrieval instead of vector search: fine for v1. Upgrade
  when we have real usage data suggesting keywords miss the mark.
- Storing full `search_text` in the index: cheap and lets us change the
  retrieval algorithm without re-parsing MPDs.
