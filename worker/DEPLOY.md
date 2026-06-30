# Deploy the LegoGPT worker

One-time setup of secrets, deploy, smoke test, and wiring up the web app.

## 1. Create Modal secrets (one-time)

The worker needs your HF token and Gurobi license, uploaded as Modal secrets
so they're never in code or chat.

### A. Hugging Face token

In PowerShell (anywhere — doesn't need to be in the project dir):

```powershell
python -m modal secret create huggingface HF_TOKEN=hf_yourTokenHere
```

Replace `hf_yourTokenHere` with the actual token from your notes.

You can also create this in the Modal web dashboard:
[modal.com/settings/secrets](https://modal.com/settings/secrets) → New secret →
Name: `huggingface`, Key: `HF_TOKEN`, Value: your `hf_...` token.

### B. Gurobi license

The Gurobi license is a multi-line file. Easiest via the dashboard:

1. Open the file in Notepad:
   ```powershell
   notepad C:\Users\Jackv\gurobi.lic
   ```
2. Select all (Ctrl+A) and copy (Ctrl+C).
3. Go to [modal.com/settings/secrets](https://modal.com/settings/secrets) →
   New secret → Name: `gurobi`, Key: `GRB_LICENSE_FILE`, Value: paste the
   file contents.

The worker detects this and writes the contents to a real file inside the
container at runtime.

### C. Worker shared key (optional but recommended)

For the `/api/generate` → worker auth:

```powershell
python -m modal secret create brickforge-worker-key WORKER_API_KEY=pick-any-long-random-string
```

You can generate one with PowerShell:
```powershell
-join ((48..57) + (65..90) + (97..122) | Get-Random -Count 48 | ForEach-Object {[char]$_})
```

Save the same string in `.env.local` as `WORKER_API_KEY` (we'll wire this up
after the smoke test).

## 2. Deploy the worker

From the brickforge directory:

```powershell
cd C:\Users\Jackv\Projects\brickforge
python -m modal deploy worker/modal_app.py
```

**First deploy takes 5-10 minutes.** Modal is building the Docker image:
- Installing apt packages
- Cloning LegoGPT
- Running `uv sync` to install PyTorch + Transformers + ~2 GB of deps

Subsequent deploys are 10-30s (only changed code re-uploads).

After successful deploy you'll see a URL like:
```
✓ Created web endpoint: https://jogel4343--brickforge-generate.modal.run
```

**Copy that URL.** That's your `WORKER_URL`.

## 3. Smoke test

Before wiring up the web app, run a direct test:

```powershell
python -m modal run worker/modal_app.py::smoke
```

This generates a LEGO model from the default prompt "a small chair" and saves:
- `./output.ldr` — the LDraw model file
- `./output.png` — LegoGPT's rendered preview

**Expected first-run time: 90-180 seconds** (cold start: model weights download
from HF cache the first time, then inference + stability retries).

**Subsequent runs**: 30-90 seconds.

If it succeeds, you have proof of concept: text → real LEGO bricks. Open
`output.ldr` in [Stud.io](https://www.bricklink.com/v3/studio/download.page)
or import it via the brickforge viewer to see the result.

If it fails, paste the JSON error output. Common issues:
- `HF_TOKEN secret not set` → secret name typo
- Gurobi license error → check that the .lic file contents are in the secret
  exactly as in the file (including comment lines)
- `gated repo` error → HF token doesn't have the right scope; recreate

## 4. Wire the web app to the worker

Add to `.env.local`:

```
WORKER_URL=https://jogel4343--brickforge-generate.modal.run
WORKER_API_KEY=the-string-you-generated-in-step-1C
```

Restart `npm run dev`. Now hitting [http://localhost:3000/design](http://localhost:3000/design)
and clicking Generate will call the live Modal worker.

## 5. Monitor cost + usage

[modal.com/usage](https://modal.com/usage) shows your GPU spend in real time.
The free tier is $30/month; a single generation costs ~$0.02-$0.04.
Containers spin down to $0/hr when idle.

## Troubleshooting

**"Image build failed: uv sync error"** — LegoGPT's pyproject.toml may have
moved or pinned an incompatible torch version. Check the LegoGPT repo for
recent changes and update the `git clone` line in `modal_app.py`.

**"OutOfMemoryError"** — LegoGPT + Llama 1B should fit in A10G's 24GB. If you
hit this, the prompt may be producing a huge grid; lower `max_bricks` or
switch to L4 GPU.

**Stuck at "loading model weights"** — first run downloads ~3 GB of Llama
weights from HuggingFace. With HF_TOKEN set this should "just work"; if it
hangs >5 min, check the HF token has read access to gated repos.
