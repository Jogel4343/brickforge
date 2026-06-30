"""Modal entrypoint for Brickforge generation.

Exposes an authenticated HTTP endpoint that Next.js's /api/generate calls.

This is a SCAFFOLD — wired up for completion in Week 4. The function signatures
match the eventual production interface so the frontend can be developed against
the stub today.
"""
from __future__ import annotations
import os
import modal

app = modal.App("brickforge-worker")

# Image: CUDA + LegoGPT deps. LegoGPT is pulled from GitHub at build time.
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "build-essential")
    .pip_install(
        "torch>=2.3",
        "transformers>=4.43",
        "accelerate>=0.31",
        "trl>=0.9",
        "numpy",
        "pillow",
        "networkx",
        "trimesh",
        "gurobipy",
        "fastapi",
        "pydantic>=2",
    )
    .run_commands(
        # Pin to a known good LegoGPT commit. Update after pre-flight smoke test.
        "git clone https://github.com/AvaLovelace1/LegoGPT.git /opt/legogpt",
    )
)

GPU_TYPE = "A10G"  # ~$1.10/hr — good cost/perf for LegoGPT inference


@app.function(
    image=image,
    gpu=GPU_TYPE,
    timeout=600,
    secrets=[
        modal.Secret.from_name("hf-token"),       # Hugging Face token for gated Llama weights
        modal.Secret.from_name("gurobi-license"), # Gurobi WLS / academic license
    ],
)
def generate(prompt: str, grid_size: int = 20, seed: int | None = None) -> dict:
    """Run LegoGPT for a single (sub)volume.

    Returns:
      {
        "bricks": [{"ldraw_id": "3001", "color": 4, "x": 0, "y": 0, "z": 0, "rot": 0}, ...],
        "ldr": "<ldraw text>",
        "preview_png_b64": "<base64>",
        "stats": {"total_bricks": N, "rejections": M, "regenerations": K, "gpu_seconds": S}
      }
    """
    raise NotImplementedError("Wire up in Week 4 — see worker/legogpt_runner.py")


@app.function(image=image, gpu=GPU_TYPE, timeout=1800)
def generate_chunked(prompt: str, target_grid: int = 40) -> dict:
    """Chunked / "subagent" generation for builds larger than LegoGPT's native cap.

    Splits the target volume into 20-cube subvolumes, generates each in parallel,
    then stitches via chunked_planner.stitch.

    Returns the same shape as `generate` but for the full assembled model.
    """
    raise NotImplementedError("Wire up in Week 5/6 — see ARCHITECTURE.md for the design.")


@app.function(image=image, cpu=2.0, memory=2048, timeout=120)
@modal.web_endpoint(method="POST", label="brickforge-generate")
def http_generate(item: dict) -> dict:
    """HTTP entrypoint called by Next.js /api/generate.

    Auth: simple shared secret in `x-brickforge-key` header, validated upstream
    by Modal's request handling; replace with signed JWT before public launch.
    """
    prompt = item.get("prompt", "")
    if not prompt:
        return {"error": "prompt required"}
    chunked = bool(item.get("chunked", False))
    # Dispatch — these are stubs today; the wiring is here ready for Week 4.
    fn = generate_chunked if chunked else generate
    return fn.remote(prompt=prompt, **({"target_grid": item.get("grid", 40)} if chunked else {"grid_size": item.get("grid", 20)}))
