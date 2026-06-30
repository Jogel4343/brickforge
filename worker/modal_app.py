"""Brickforge GPU worker — LegoGPT inference on Modal.

This Modal app exposes an HTTP endpoint the Next.js app calls to turn a text
prompt into a LEGO brick model. End-to-end pipeline (v1):

    prompt
      └── LegoGPT (CMU, fine-tuned Llama-3.2-1B-Instruct)
            └── outputs brick list (LDraw format) + .png preview
                  └── Gurobi stability check (built into LegoGPT)
                        └── return {ldr_text, brick_count, gpu_seconds, ...}

Deployment:

    python -m modal deploy worker/modal_app.py

Local smoke test:

    python -m modal run worker/modal_app.py::smoke

Secrets required (set up via the Modal dashboard or `python -m modal secret create`):

    huggingface:  HF_TOKEN          # your hf_... token
    gurobi:       GRB_LICENSE_FILE  # contents of your gurobi.lic (paste verbatim)

Cost reality:
    - First deploy builds a ~5 GB container image (~5-10 min, one-time).
    - Each generation runs on an A10G (~$1.10/hr). A typical small (~50 brick)
      model takes 60-120s = ~$0.02-$0.04 per generation including stability
      retries.
    - GPU sits idle billed at $0/hr; only pay for actual inference time.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import modal

# ---------------------------------------------------------------------------
# App + image
# ---------------------------------------------------------------------------

app = modal.App("brickforge-worker")

# Persistent volume for HF model cache so we don't re-download Llama weights
# every cold start (saves ~30s and bandwidth).
hf_cache = modal.Volume.from_name("brickforge-hf-cache", create_if_missing=True)

# We pin Python 3.11 (LegoGPT was tested on 3.10-3.12). PyTorch + LegoGPT
# install from the LegoGPT repo's own pyproject.toml.
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install(
        "git",
        "build-essential",
        "curl",
        # bpy (Blender Python module, used by LegoGPT for .png previews) needs
        # a bunch of system libs that are normally shipped with a full desktop
        # Debian install. debian-slim doesn't have them. Add the minimum set:
        "libxrender1",       # libXrender.so.1 (the one that bit us)
        "libxi6",            # X input ext
        "libxxf86vm1",       # X video mode ext
        "libxfixes3",        # X fixes ext
        "libxkbcommon0",     # keyboard
        "libsm6",            # session mgmt
        "libgl1",            # OpenGL
        "libegl1",           # EGL (Blender's GPU backend)
        "libglu1-mesa",      # OpenGL utility
    )
    .pip_install("uv", "gdown")  # LegoGPT uses uv for its own deps; gdown for Google Drive
    .run_commands(
        # Clone LegoGPT WITH submodules (ImportLDraw lives in a submodule and
        # the renderer fails without it).
        "git clone --recurse-submodules https://github.com/AvaLovelace1/LegoGPT.git /opt/legogpt",
        # Install LegoGPT's deps via uv into a project-local venv at /opt/legogpt/.venv
        "cd /opt/legogpt && uv sync --frozen || uv sync",
        # Download the background EXR file the renderer needs. It's on Google
        # Drive so we use gdown (regular curl/wget just gets an HTML page).
        "gdown 'https://drive.google.com/uc?id=1Yux0sEqWVpXGMT9Z5J094ISfvxhH-_5K' -O /opt/legogpt/ImportLDraw/loadldraw/background.exr",
        # Download the LDraw parts library (~80MB compressed, ~250MB unpacked)
        # into the home dir. LegoGPT's renderer looks for ~/ldraw by default,
        # or honors LDRAW_LIBRARY_PATH. The package layout extracts to ~/ldraw.
        "apt-get install -y unzip wget && "
        "cd /root && wget -q https://library.ldraw.org/library/updates/complete.zip && "
        "unzip -q complete.zip && rm complete.zip",
    )
    .env(
        {
            "LDRAW_LIBRARY_PATH": "/root/ldraw",
        }
    )
    .pip_install(
        # Extra deps for our wrapper (FastAPI, etc.)
        "fastapi>=0.110",
        "pydantic>=2.7",
        "httpx>=0.27",
    )
    .env(
        {
            "HF_HOME": "/root/hf_cache",
            "TRANSFORMERS_CACHE": "/root/hf_cache",
            "HF_HUB_CACHE": "/root/hf_cache",
        }
    )
)

# ---------------------------------------------------------------------------
# Generation function
# ---------------------------------------------------------------------------

GPU_TYPE = "A10G"  # ~$1.10/hr; good cost/perf for LegoGPT (small Llama backbone)


@app.function(
    image=image,
    gpu=GPU_TYPE,
    timeout=600,
    volumes={"/root/hf_cache": hf_cache},
    secrets=[
        modal.Secret.from_name("huggingface"),  # exposes HF_TOKEN env var
        modal.Secret.from_name("gurobi"),       # exposes GRB_LICENSE_FILE env var
    ],
)
def generate(prompt: str, max_bricks: int = 200, seed: int | None = None) -> dict:
    """Run LegoGPT for a single prompt. Returns {ldr_text, brick_count, ...}."""
    start = time.time()

    # Set up Gurobi license. Modal injects the secret value as an env var
    # STRING, but Gurobi tries to OPEN the value as a file path. When users
    # paste the .lic file contents into the Modal secret (which is what our
    # DEPLOY.md tells them to do), GRB_LICENSE_FILE ends up containing the
    # license TEXT, not a path. Detect this and materialize it to a real file.
    grb = os.environ.get("GRB_LICENSE_FILE", "")
    looks_like_file_contents = (
        "\n" in grb              # multi-line
        or "LICENSEID=" in grb   # Gurobi license fields
        or "TYPE=" in grb
        or grb.lstrip().startswith("#")  # comment header
    )
    if grb and (looks_like_file_contents or not Path(grb).exists()):
        lic_path = Path("/root/gurobi.lic")
        lic_path.write_text(grb)
        os.environ["GRB_LICENSE_FILE"] = str(lic_path)
        print(f"[brickforge] Wrote Gurobi license to {lic_path} ({len(grb)} chars)")

    # LegoGPT login to HF (for gated Llama weights). Token is in HF_TOKEN.
    hf_token = os.environ.get("HF_TOKEN")
    if not hf_token:
        return {"error": "HF_TOKEN secret not set"}
    # huggingface_hub auto-reads HF_TOKEN; nothing more to do.

    # Invoke LegoGPT's inference CLI. LegoGPT v1 exposes `uv run infer` which
    # reads a prompt from stdin and writes output.ldr, output.txt, output.png
    # into the cwd.
    workdir = Path("/tmp/brickforge-run")
    workdir.mkdir(parents=True, exist_ok=True)

    # LegoGPT's `uv run infer` is interactive and asks THREE questions:
    #   1. "Enter a prompt, or <Return> to exit: "
    #   2. "Enter a filename to save the output image (default=output.png): "
    #   3. "Enter a generation seed (default=42): "
    # We pipe all three via stdin. Empty lines 2 & 3 accept defaults; pass the
    # seed explicitly when caller specifies one.
    seed_input = f"{seed}\n" if seed is not None else "\n"
    cmd = ["uv", "run", "infer"]
    proc = subprocess.run(
        cmd,
        cwd="/opt/legogpt",
        input=f"{prompt}\n\n{seed_input}",  # prompt, default filename, seed (or default)
        text=True,
        capture_output=True,
        timeout=540,
        env={**os.environ},
    )

    if proc.returncode != 0:
        return {
            "error": f"LegoGPT exited {proc.returncode}",
            "stdout": proc.stdout[-2000:],
            "stderr": proc.stderr[-4000:],
            "gpu_seconds": round(time.time() - start, 2),
        }

    # Collect outputs from LegoGPT's working directory.
    out_dir = Path("/opt/legogpt")
    ldr_path = out_dir / "output.ldr"
    txt_path = out_dir / "output.txt"
    png_path = out_dir / "output.png"

    ldr_text = ldr_path.read_text() if ldr_path.exists() else None
    txt_text = txt_path.read_text() if txt_path.exists() else None
    png_bytes = png_path.read_bytes() if png_path.exists() else None

    # Parse a brick count from the .ldr (lines starting with "1 " are part references).
    brick_count = (
        sum(1 for line in (ldr_text or "").splitlines() if line.startswith("1 "))
        if ldr_text else 0
    )

    return {
        "ldr_text": ldr_text,
        "brick_list_txt": txt_text,
        "preview_png_b64": _b64(png_bytes) if png_bytes else None,
        "brick_count": brick_count,
        "gpu_seconds": round(time.time() - start, 2),
        "stdout_tail": proc.stdout[-2000:],
    }


def _b64(b: bytes) -> str:
    import base64

    return base64.b64encode(b).decode("ascii")


# ---------------------------------------------------------------------------
# HTTP endpoint called by Next.js /api/generate
# ---------------------------------------------------------------------------

@app.function(
    image=image,
    cpu=2.0,
    memory=2048,
    timeout=600,
    secrets=[modal.Secret.from_name("brickforge-worker-key")],  # optional shared secret
)
@modal.fastapi_endpoint(method="POST", label="brickforge-generate")
def http_generate(item: dict) -> dict:
    """POST {prompt: string, grid?: number, chunked?: boolean}."""
    # Auth (simple shared-secret header; tighten before public launch).
    # Modal injects the secret value as an env var; the client sends the same
    # value in 'x-brickforge-key'. For v1 we trust Modal's URL secrecy.
    prompt = item.get("prompt", "")
    if not prompt or len(prompt) < 3:
        return {"error": "prompt too short"}
    if len(prompt) > 600:
        return {"error": "prompt too long"}

    result = generate.remote(prompt=prompt)
    return result


# ---------------------------------------------------------------------------
# Smoke test entrypoint
# ---------------------------------------------------------------------------

@app.local_entrypoint()
def smoke(prompt: str = "a small chair"):
    """Run a single LegoGPT inference and print the result summary.

    Usage:
        python -m modal run worker/modal_app.py::smoke
        python -m modal run worker/modal_app.py::smoke --prompt "a small spaceship"
    """
    print(f"Brickforge smoke test — prompt: {prompt!r}")
    print("(first run will be slow: container build ~5min, model download ~30s)")
    result = generate.remote(prompt=prompt)
    if "error" in result:
        print("FAILED:")
        print(json.dumps(result, indent=2))
        sys.exit(1)

    print(f"\nSUCCESS — generated {result['brick_count']} bricks in {result['gpu_seconds']}s")
    if result.get("ldr_text"):
        # Save .ldr locally so the user can drop it into the viewer.
        Path("output.ldr").write_text(result["ldr_text"])
        print("LDraw model saved to ./output.ldr")
    if result.get("preview_png_b64"):
        import base64

        Path("output.png").write_bytes(base64.b64decode(result["preview_png_b64"]))
        print("Preview PNG saved to ./output.png")
