"""Single-shot prompt -> .ldr generation, callable synchronously from the
Next.js API route (src/app/api/generate/route.ts).

This is the same Claude call + evaluation pipeline as claude_ir_gen.py's
experiment harness (parse -> schema -> sanity -> fill -> special_parts ->
ldr), minus the N-runs loop and on-disk artifacts — reused directly rather
than reimplemented, so the web path and the experiment harness can't drift
apart.

It's an interim local-dev bridge: a subprocess call, not the deployed
Modal worker described in CLAUDE.md's "Deployment shape". Swap this for a
real HTTP call to the worker once that's deployed; nothing about the IR
pipeline itself changes.

Usage:
    python -m scripts.generate_one "a small dog"
    # prints one JSON object to stdout: {ok, name, bricks, ldr} or
    # {ok: false, error}
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

from scripts.claude_ir_gen import (
    build_system_prompt,
    call_api,
    call_cli,
    extract_json,
    sanity_check,
)
from worker.filler import fill_ir
from worker.ir_schema import IR
from worker.ldr_writer import render_to_string
from worker.special_parts import resolve_special_parts

DEFAULT_MODEL = "claude-sonnet-5"


def generate_one(prompt: str, model: str = DEFAULT_MODEL) -> dict:
    system_prompt = build_system_prompt()
    transport = "api" if os.environ.get("ANTHROPIC_API_KEY") else "cli"

    try:
        if transport == "api":
            raw = call_api(system_prompt, prompt, model)
        else:
            if shutil.which("claude") is None:
                return {"ok": False, "error": "no ANTHROPIC_API_KEY and no `claude` CLI on PATH"}
            neutral_cwd = tempfile.mkdtemp(prefix="brickforge_gen_")
            raw = call_cli(system_prompt, prompt, model, cwd=neutral_cwd)
    except Exception as exc:  # noqa: BLE001 — surfaced to the caller as JSON
        return {"ok": False, "error": f"transport failure: {type(exc).__name__}: {exc}"}

    try:
        data = extract_json(raw)
        ir = IR.from_dict(data)
        ir.normalize_positions()
        sanity_check(ir)
        bricks = fill_ir(ir) + resolve_special_parts(ir)
        ldr = render_to_string(bricks, model_name=ir.name)
        return {"ok": True, "name": ir.name, "bricks": len(bricks), "ldr": ldr}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "raw": raw[:2000]}


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "usage: generate_one.py <prompt>"}))
        return 2
    result = generate_one(sys.argv[1])
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
