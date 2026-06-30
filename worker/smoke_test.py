"""Local LegoGPT smoke test.

Verifies the LegoGPT repo runs end-to-end on YOUR machine (or a Modal GPU) before
we commit to integrating it. Run this in Week 1 — failure here means the whole
plan needs adjusting.

Usage:
  git clone https://github.com/AvaLovelace1/LegoGPT.git /opt/legogpt
  cd /opt/legogpt
  uv sync
  huggingface-cli login          # request access to meta-llama/Llama-3.2-1B-Instruct first
  # Gurobi: install + put gurobi.lic in ~/gurobi/
  cd -
  python smoke_test.py --prompt "a small chair"

What to expect:
  - ~30-90 seconds on an A10G
  - Output files: output.ldr, output.txt, output.png
  - Some rejections + regenerations are normal (stability checker doing its job)

If this works, you're cleared to integrate. If not, debug HERE, not in week 4
when 5 other moving parts are involved.
"""
from __future__ import annotations
import argparse
import subprocess
import sys
import os
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", default="a small chair")
    ap.add_argument("--legogpt-path", default=os.environ.get("LEGOGPT_PATH", "/opt/legogpt"))
    args = ap.parse_args()

    repo = Path(args.legogpt_path)
    if not repo.exists():
        sys.exit(
            f"LegoGPT not found at {repo}. Clone with:\n"
            f"  git clone https://github.com/AvaLovelace1/LegoGPT.git {repo}"
        )

    print(f"Running LegoGPT inference: prompt={args.prompt!r}")
    # Per the LegoGPT README, `uv run infer` is the entrypoint and prompts interactively.
    # We pipe the prompt via stdin.
    result = subprocess.run(
        ["uv", "run", "infer"],
        cwd=str(repo),
        input=args.prompt + "\n",
        text=True,
        capture_output=True,
        timeout=600,
    )
    print("---- stdout ----")
    print(result.stdout)
    print("---- stderr ----")
    print(result.stderr)
    if result.returncode != 0:
        sys.exit(f"LegoGPT exited with code {result.returncode}")

    for f in ("output.ldr", "output.txt", "output.png"):
        p = repo / f
        if p.exists():
            print(f"OK: {p} ({p.stat().st_size} bytes)")
        else:
            print(f"MISSING: {p}")


if __name__ == "__main__":
    main()
