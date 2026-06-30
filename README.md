# Brickforge

AI-designed LEGO models with step-by-step instructions and priced parts lists.

> **Status: scaffolding (Week 1 of 8).** The viewer renders LDraw files; the
> generation pipeline is stubbed and lights up in Week 4.

## What it does (when complete)

1. User types a prompt or uploads a photo
2. [LegoGPT](https://github.com/AvaLovelace1/LegoGPT) generates a physically
   stable LEGO brick model
3. Three.js viewer renders it in 3D — orbit, pan, zoom, explode-view, AR
4. Step planner generates layered build instructions with subassembly detection
5. Parts list is mapped to BrickLink IDs with live pricing
6. Output: a shareable design page + `.ldr` download + parts list CSV
7. (Phase 2) Marketplace for designers to sell instructions, with optional
   fulfillment ("ship me the bricks")

## Architecture at a glance

```
┌──────────────────────────────────────────┐
│ Next.js app (Vercel)                     │
│  /         landing                       │
│  /viewer   Three.js + LDrawLoader demo   │
│  /design   prompt → /api/generate        │
│  /d/[id]   shareable design page         │
└─────────────────┬────────────────────────┘
                  │
        ┌─────────┴─────────┐
        ▼                   ▼
┌──────────────┐   ┌────────────────────────┐
│ Supabase     │   │ Modal worker (Python)  │
│ - Auth       │   │ legogpt_runner         │
│ - Postgres   │   │ chunked_planner        │
│   designs    │   │ stitcher               │
│   parts      │   │ step_planner           │
│   colors     │   │ Gurobi stability pass  │
│ - Storage    │   └────────────────────────┘
└──────────────┘
```

See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for details.

## Local dev

```bash
# 1. Install
npm install

# 2. Env
cp .env.local.example .env.local
# Fill in Supabase URL/keys (free tier OK)

# 3. Run
npm run dev
# open http://localhost:3000
```

Visit `/viewer` — by default it loads a Three.js-hosted sample LDraw car so the
viewer is demoable with zero LDraw library setup.

## Build plan

| Week | Deliverable |
|------|-------------|
| 1 | LDraw library + viewer + repo scaffold (THIS WEEK) |
| 2 | Optional Meshy text-to-3D path (image conditioning) |
| 3 | Voxelization + naive brick rendering |
| 4 | **LegoGPT integrated on Modal** — text → bricks end-to-end |
| 5 | Stability + color palette + chunked / subagent scaffold lit up |
| 6 | Step planner + interactive step-through in viewer |
| 7 | Parts list with BrickLink prices + LDraw export polish |
| 8 | Share URLs, screenshot/AR, demo video, launch |

## Stack

- **Frontend**: Next.js 14, React, TypeScript, Tailwind, Three.js + LDrawLoader
- **Backend**: Next.js API routes + Supabase (Postgres, Auth, Storage)
- **AI worker**: Python on Modal, GPU (A10G/A100), LegoGPT, Gurobi
- **LLM helpers**: Claude/GPT-4 for prose, names, descriptions (no fine-tune needed)
- **Hosting**: Vercel (web) + Modal (worker)

## License

Source: All Rights Reserved (for now). Will revisit once monetization paths solidify.

LegoGPT (third-party) is MIT-licensed; commercial use is permitted but it depends on:
- Llama-3.2-1B-Instruct (Meta gated, free with attribution)
- Gurobi for stability analysis (free academic license; commercial requires paid license — see [docs/COMMERCIAL_GURBI.md](./docs/COMMERCIAL_GURBI.md))
