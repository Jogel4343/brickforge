# Brickforge worker

Python GPU worker that wraps LegoGPT inference + chunked stitching, runs on
[Modal](https://modal.com/) (free $30/mo of compute on the starter plan).

## Stages

```
prompt
  ├── (text-direct, default path) ──► LegoGPT.infer(prompt)
  └── (image-conditioned, v1.1)   ──► Meshy → mesh → voxelize → LegoGPT.infer(voxels)
                                       │
                                       └── if grid > 20: ChunkedPlanner → many LegoGPT calls → stitch
                                                                          │
                                                                          ▼
                                          stability + connectivity check (Gurobi or HiGHS fallback)
                                                                          │
                                                                          ▼
                                          color snap → step planner (layer slicer + subassembly graph)
                                                                          │
                                                                          ▼
                                          outputs: .ldr, .txt brick list, .png render, step JSON
```

## Files

- `legogpt_runner.py` — thin wrapper around LegoGPT inference.
- `chunked_planner.py` — splits target into chunks; orchestrates parallel LegoGPT calls.
- `stitcher.py` — merges chunk outputs into a single connected, stable model.
- `step_planner.py` — layer slicer + subassembly detection → ordered build steps.
- `ingest_ldraw.py` — one-time: parse LDraw `parts.lst` + `LDConfig.ldr` into Supabase.
- `modal_app.py` — Modal entrypoint exposing an HTTP endpoint to Next.js.

## Setup

```bash
cd worker
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt

# Hugging Face token (LegoGPT depends on gated Llama-3.2-1B-Instruct)
huggingface-cli login

# Gurobi license (free academic via https://gurobi.com/academia)
# Put gurobi.lic in ~/gurobi/ or set GRB_LICENSE_FILE
```

## Pre-flight (Week 1, do this before Week 4)

```bash
python -m worker.smoke_test --prompt "a small chair"
```

Should produce `output.ldr` and `output.png` locally in <2 min on a T4 / A10G.
