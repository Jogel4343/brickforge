"""The experiment that the whole project hinges on: can Claude emit valid,
buildable IRs from a text prompt?

For each run we send the same system prompt (schema + rules + one worked
example) and the user's text prompt to Claude, then push the response
through the real pipeline stages:

    parse           response text contains one JSON object
    schema          JSON validates as an IR (worker.ir_schema)
    sanity          dimensions are within buildable bounds (no 500-stud walls)
    fill            the deterministic filler produces placed structural bricks
    special_parts   any special_parts entries resolve against the real
                    catalog (worker.special_parts) and place successfully
    ldr             bricks render to an .ldr file

A run is a SUCCESS if it reaches `ldr`. The summary reports N/runs plus a
failure breakdown by stage, so "Claude emitted invalid JSON" (parser or
prompt fix) is distinguishable from "valid but unbuildable" (schema or
guidance fix).

Usage:
    python -m scripts.claude_ir_gen "medieval tower" --runs 10
    python -m scripts.claude_ir_gen "simple house" --runs 10 --workers 4

Transport:
    --transport api   uses the anthropic SDK (needs ANTHROPIC_API_KEY)
    --transport cli   shells out to `claude -p` with a replaced system
                      prompt and all tools disabled (works inside Claude
                      Code environments where no raw API key is available)
    --transport auto  (default) api if a key is present, else cli

Artifacts land in data/runs/<slug>/:
    run_XX_raw.txt   raw response text (always; diagnose failures from this)
    run_XX_ir.json   extracted IR JSON (when parse succeeded)
    run_XX.ldr       rendered model (when the run succeeded)
    summary.json     per-run stages + N/runs + failure breakdown
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from worker.filler import fill_ir
from worker.ir_schema import IR, JSON_SCHEMA
from worker.ldr_writer import write_ldr
from worker.special_parts import resolve_special_parts

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "data" / "fixtures"
RUNS_ROOT = REPO_ROOT / "data" / "runs"

DEFAULT_MODEL = "claude-sonnet-5"

# Buildability bounds enforced at the sanity stage. Generous, but they keep
# a hallucinated 500-stud wall from grinding the filler.
MAX_FOOTPRINT_STUDS = 64
MAX_HEIGHT_COURSES = 64
MAX_SUB_ASSEMBLIES = 32


def build_system_prompt() -> str:
    schema = json.dumps(JSON_SCHEMA, indent=2)
    exemplar = (FIXTURES / "tower.json").read_text().strip()
    return f"""You are the decomposition stage of Brickforge, a system that turns a short \
text prompt into a buildable LEGO model. Your job is ONLY semantic \
decomposition: break the requested object into axis-aligned shape primitives. \
A separate deterministic system converts your primitives into real bricks — \
you never pick bricks.

Hard output rules:
- Respond with exactly one JSON object and nothing else. No markdown fences, \
no commentary before or after.
- Never mention a specific LEGO part number and never place individual \
structural bricks. The one exception is special_parts' "query" field, which \
names a part by INTENT in free text (e.g. "wheel 30mm", "minifig head") — \
never a part ID or number there either. A separate deterministic step \
resolves that query against the real parts catalog.
- The JSON must conform to this schema:

{schema}

Geometry rules:
- Units are studs. Y is up. position_studs is the MIN corner [x, y, z] of the \
primitive's bounding box. dims_studs is [width_x, height_y, depth_z]; \
height_y counts brick courses.
- Supported shapes:
  - "box": solid rectangular volume.
  - "cone": square pyramid that shrinks about 1 stud per side per course on \
BOTH horizontal axes, ending in a point — hip roofs, spires, turrets.
  - "wedge": like "cone" but shrinks on only ONE horizontal axis (set \
taper_axis to "x" or "z"), ending in a ridge line instead of a point — \
gable/ridge roofs. The other axis stays full width the whole height.
  - "tapered_slab": constant height; the FOOTPRINT itself is a trapezoid \
along taper_axis, the same at every course. dims_studs' cross-axis width is \
the NEAR end (at position_studs); taper_to_studs (required field) is the \
FAR end — smaller to NARROW (angled facades, tapered towers, hulls, wings) \
or LARGER to WIDEN (a flared fender, a bell shape). Unlike wedge/cone, the \
taper is NOT a function of height, and there's no upper limit tying \
taper_to_studs to dims_studs — it can be bigger.
- taper_axis ("x" or "z", default "z") and taper_to_studs apply ONLY to \
"wedge" and "tapered_slab". Example (partial objects, not full IRs):
  {{"name": "gable_roof", "shape": "wedge", "position_studs": [0, 10, 0], \
"dims_studs": [8, 4, 6], "taper_axis": "z", "color_code": 4}}
  -> 8 wide (X), ridge runs along X; depth shrinks from 6 at the base to a \
1-stud ridge over 4 courses.
  {{"name": "hull_side", "shape": "tapered_slab", "position_studs": [0, 0, 0], \
"dims_studs": [8, 3, 10], "taper_axis": "z", "taper_to_studs": 3, \
"color_code": 71}}
  -> 8 wide (X) at z=0, narrowing to 3 wide at z=9, constant 3-course height.
  {{"name": "rear_fender_flare", "shape": "tapered_slab", "position_studs": \
[0, 0, 0], "dims_studs": [6, 4, 8], "taper_axis": "z", "taper_to_studs": 10, \
"color_code": 4}}
  -> 6 wide (X) at z=0 (near the car's mid-body), WIDENING to 10 wide at \
z=7 (the flared rear) — taper_to_studs (10) is larger than dims_studs' 6, \
which is allowed and means "widen," not an error.
- All coordinates and dimensions are positive-or-zero integers; dims are >= 1.
- List sub_assemblies bottom-up in build order, with the ground at y=0.
- Adjacent primitives should touch, not overlap. Stack by starting a \
primitive's y at the top of what it rests on.
- Prefer hollow construction for large enclosed volumes: four thin wall \
boxes instead of one solid block. Small or structural elements can be solid.
- Decompose into every semantically distinct part the object actually has — \
separate primitives for each wing, engine, wheel, tower, roof section, window \
bay, etc. Do not fuse distinct features into one box to save on count: more \
sub_assemblies generally makes the model more recognizable and detailed, not \
just bigger.
- Stay within bounds: footprint within {MAX_FOOTPRINT_STUDS} x {MAX_FOOTPRINT_STUDS} studs, \
height within {MAX_HEIGHT_COURSES} courses, at most {MAX_SUB_ASSEMBLIES} sub_assemblies.
- Sub-assembly names are unique snake_case labels.
- color_code is an LDraw color: 0 black, 1 blue, 2 green, 4 red, 14 yellow, \
15 white, 19 tan, 28 dark tan, 70 reddish brown, 71 light bluish grey, \
72 dark bluish grey.

Special parts (optional):
- Use special_parts ONLY for parts a generic box/cone/wedge/tapered_slab \
can't represent: wheels, canopies/windscreens, cannons, minifig accessories. \
Do NOT use it for plain structural elements (walls, floors, roofs) — those \
stay as sub_assemblies.
- Each special_part is positioned RELATIVE to a sub_assembly: attach_to \
names that sub_assembly, and offset_studs [dx, dy, dz] is added to its \
position_studs to get the placement anchor. Not an absolute world position.
- query is free text describing what you want by intent, e.g. "wheel 30mm" \
or "minifig head" — qualify with a size or descriptor when you have one, \
it improves the match. Never a part ID.
- Example (partial object): {{"name": "front_left_wheel", "query": "wheel \
30mm", "attach_to": "chassis", "offset_studs": [0, 0, 0], "color_code": 0}}

Worked example.
Prompt: "medieval tower"
Output:
{exemplar}"""


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------

def call_api(system_prompt: str, user_prompt: str, model: str) -> str:
    import anthropic  # imported lazily so the cli transport works without it

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        # Extended thinking is on by default for this model and can consume
        # the ENTIRE max_tokens budget on a prompt it finds complex, leaving
        # zero tokens for the actual JSON response (observed live: "80's
        # 911 targa" hit stop_reason=max_tokens with a single 4096-token
        # thinking block and no text block at all). This stage is
        # deterministic structured output, not something that benefits from
        # exposed reasoning, so thinking is switched off rather than just
        # raising max_tokens (which would only push the failure to a more
        # complex prompt, not fix it).
        thinking={"type": "disabled"},
    )
    return "".join(b.text for b in resp.content if b.type == "text")


def call_cli(system_prompt: str, user_prompt: str, model: str, cwd: str) -> str:
    """One-shot `claude -p` with the default system prompt replaced by ours
    and every tool disabled. Run from a neutral directory so no project
    memory (CLAUDE.md) leaks into the experiment."""
    cmd = [
        "claude", "-p",
        "--model", model,
        "--system-prompt", system_prompt,
        "--tools", "",
    ]
    # Prompt goes on stdin: --tools is variadic and would swallow a trailing
    # positional argument.
    proc = subprocess.run(
        cmd, input=user_prompt, capture_output=True, text=True, timeout=300, cwd=cwd,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI exited {proc.returncode}: {proc.stderr.strip()[:500]}")
    return proc.stdout


# ---------------------------------------------------------------------------
# Evaluation pipeline
# ---------------------------------------------------------------------------

def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a response, tolerating markdown
    fences and stray prose."""
    stripped = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```\s*$", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1)
    start = stripped.find("{")
    if start == -1:
        raise ValueError("no JSON object found in response")
    obj, _ = json.JSONDecoder().raw_decode(stripped[start:])
    if not isinstance(obj, dict):
        raise ValueError(f"top-level JSON is {type(obj).__name__}, expected object")
    return obj


def sanity_check(ir: IR) -> None:
    if len(ir.sub_assemblies) > MAX_SUB_ASSEMBLIES:
        raise ValueError(f"{len(ir.sub_assemblies)} sub_assemblies exceeds cap {MAX_SUB_ASSEMBLIES}")
    for sa in ir.sub_assemblies:
        w, h, d = sa.dims_studs
        x, y, z = sa.position_studs
        if w > MAX_FOOTPRINT_STUDS or d > MAX_FOOTPRINT_STUDS:
            raise ValueError(f"{sa.name}: footprint {w}x{d} exceeds cap {MAX_FOOTPRINT_STUDS}")
        if y + h > MAX_HEIGHT_COURSES:
            raise ValueError(f"{sa.name}: top at {y + h} courses exceeds cap {MAX_HEIGHT_COURSES}")
        if min(x, y, z) < 0:
            raise ValueError(f"{sa.name}: negative position {sa.position_studs}")
        # tapered_slab's far end (taper_to_studs) isn't part of dims_studs
        # and can legitimately exceed it (a flare widens past the near
        # end) — checked separately since the w/d check above only covers
        # the near end's declared bounding box.
        if sa.shape == "tapered_slab" and sa.taper_to_studs is not None:
            if sa.taper_to_studs > MAX_FOOTPRINT_STUDS:
                raise ValueError(
                    f"{sa.name}: taper_to_studs {sa.taper_to_studs} exceeds cap {MAX_FOOTPRINT_STUDS}"
                )


def count_collisions(bricks) -> int:
    """Occupied-cell double-claims across the whole model. Diagnostic only:
    overlapping primitives in the IR show up here."""
    seen: set[tuple[int, int, int]] = set()
    collisions = 0
    for b in bricks:
        w, d = b.footprint_studs
        if b.rotation_deg == 90:
            w, d = d, w
        for dx in range(w):
            for dz in range(d):
                cell = (b.x_stud + dx, b.y_ldu, b.z_stud + dz)
                if cell in seen:
                    collisions += 1
                seen.add(cell)
    return collisions


def evaluate(raw_text: str, out_dir: Path, run_idx: int) -> dict:
    """Run one raw response through parse -> schema -> sanity -> fill ->
    special_parts -> ldr. Returns the per-run summary record."""
    rec: dict = {
        "run": run_idx,
        "ok": False,
        "stage_reached": None,
        "error": None,
        "raw_file": f"run_{run_idx:02d}_raw.txt",
    }
    try:
        data = extract_json(raw_text)
        rec["stage_reached"] = "parse"
        (out_dir / f"run_{run_idx:02d}_ir.json").write_text(json.dumps(data, indent=2) + "\n")

        ir = IR.from_dict(data)
        ir.normalize_positions()
        rec["stage_reached"] = "schema"
        rec["sub_assemblies"] = len(ir.sub_assemblies)
        rec["special_parts"] = len(ir.special_parts)

        sanity_check(ir)
        rec["stage_reached"] = "sanity"

        bricks = fill_ir(ir)
        rec["stage_reached"] = "fill"
        rec["bricks"] = len(bricks)
        rec["collisions"] = count_collisions(bricks)

        special_bricks = resolve_special_parts(ir)
        rec["stage_reached"] = "special_parts"
        rec["special_parts_resolved"] = [b.part_id for b in special_bricks]
        bricks = bricks + special_bricks

        ldr_path = out_dir / f"run_{run_idx:02d}.ldr"
        write_ldr(bricks, ldr_path, model_name=ir.name)
        rec["stage_reached"] = "ldr"
        rec["ldr_file"] = ldr_path.name
        rec["ok"] = True
    except Exception as exc:  # noqa: BLE001 — every failure is data here
        rec["error"] = f"{type(exc).__name__}: {exc}"
    return rec


FAILURE_STAGE_AFTER = {
    None: "parse",               # died before parse completed
    "parse": "schema",           # parsed, died validating
    "schema": "sanity",
    "sanity": "fill",
    "fill": "special_parts",
    "special_parts": "ldr",
}


def slugify(prompt: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", prompt.lower()).strip("_") or "unnamed"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("prompt", help="text prompt, e.g. 'medieval tower'")
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--transport", choices=["auto", "api", "cli"], default="auto")
    ap.add_argument("--workers", type=int, default=1)
    args = ap.parse_args()

    transport = args.transport
    if transport == "auto":
        transport = "api" if os.environ.get("ANTHROPIC_API_KEY") else "cli"
    if transport == "cli" and shutil.which("claude") is None:
        print("error: no ANTHROPIC_API_KEY and no `claude` CLI on PATH", file=sys.stderr)
        return 2

    slug = slugify(args.prompt)
    out_dir = RUNS_ROOT / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    system_prompt = build_system_prompt()
    neutral_cwd = tempfile.mkdtemp(prefix="brickforge_ir_")

    print(f"prompt: {args.prompt!r}  runs: {args.runs}  model: {args.model}  transport: {transport}")
    print(f"artifacts: {out_dir}")

    def one_run(idx: int) -> dict:
        t0 = time.time()
        try:
            if transport == "api":
                raw = call_api(system_prompt, args.prompt, args.model)
            else:
                raw = call_cli(system_prompt, args.prompt, args.model, cwd=neutral_cwd)
        except Exception as exc:  # noqa: BLE001
            raw = ""
            rec = {
                "run": idx, "ok": False, "stage_reached": None,
                "error": f"transport failure — {type(exc).__name__}: {exc}",
                "raw_file": f"run_{idx:02d}_raw.txt",
            }
            (out_dir / rec["raw_file"]).write_text(raw)
            rec["seconds"] = round(time.time() - t0, 1)
            return rec
        (out_dir / f"run_{idx:02d}_raw.txt").write_text(raw)
        rec = evaluate(raw, out_dir, idx)
        rec["seconds"] = round(time.time() - t0, 1)
        return rec

    indices = list(range(1, args.runs + 1))
    if args.workers > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            records = sorted(pool.map(one_run, indices), key=lambda r: r["run"])
    else:
        records = [one_run(i) for i in indices]

    for rec in records:
        status = "OK " if rec["ok"] else "FAIL"
        detail = f"{rec.get('bricks', '-')} bricks" if rec["ok"] else rec["error"]
        print(f"  run {rec['run']:02d} [{status}] reached={rec['stage_reached']} {detail} ({rec['seconds']}s)")

    n_success = sum(1 for r in records if r["ok"])
    failures: dict[str, int] = {}
    for r in records:
        if not r["ok"]:
            stage = FAILURE_STAGE_AFTER.get(r["stage_reached"], "unknown")
            if r["error"] and r["error"].startswith("transport failure"):
                stage = "transport"
            failures[stage] = failures.get(stage, 0) + 1

    summary = {
        "prompt": args.prompt,
        "slug": slug,
        "model": args.model,
        "transport": transport,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_runs": args.runs,
        "n_success": n_success,
        "failures_by_stage": failures,
        "runs": records,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    print(f"\n{n_success}/{args.runs} runs succeeded (parse + schema + sanity + fill + special_parts + ldr)")
    if failures:
        print(f"failures by stage: {failures}")
    print(f"summary: {out_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
