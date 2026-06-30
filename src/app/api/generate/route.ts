import { NextResponse } from "next/server";

/**
 * POST /api/generate
 *
 * v1 path (text-direct): prompt → Modal worker (LegoGPT) → bricks.
 *
 * Wired up to the Modal worker in Week 4. Until WORKER_URL is set, this
 * route returns a "not ready" stub so the /design page can demo the UI flow
 * without erroring out.
 *
 * Notes on the design choice:
 *   - We previously had a Meshy text-to-3D stage in front of LegoGPT. We
 *     pulled it out because LegoGPT accepts text prompts directly (per the
 *     CMU paper / repo: `uv run infer` takes a text prompt). Mesh-stage is
 *     a v1.5 addition for photo-upload conditioning. See src/lib/meshy.ts —
 *     code kept for later, not wired up.
 */

interface GenerateRequest {
  prompt?: string;
  /** Override grid size; default 20 = LegoGPT native max single-pass. */
  grid?: number;
  /** Force chunked path for builds > LegoGPT cap. */
  chunked?: boolean;
}

export async function POST(req: Request) {
  try {
    const body = (await req.json()) as GenerateRequest;
    const { prompt, grid = 20, chunked = false } = body;

    if (!prompt || typeof prompt !== "string" || prompt.length < 3) {
      return NextResponse.json(
        { error: "Provide a prompt of at least 3 characters." },
        { status: 400 }
      );
    }
    if (prompt.length > 600) {
      return NextResponse.json(
        { error: "Prompt too long (max 600 chars)." },
        { status: 400 }
      );
    }

    const workerUrl = process.env.WORKER_URL;
    const workerKey = process.env.WORKER_API_KEY;

    if (!workerUrl) {
      return NextResponse.json({
        status: "not_ready",
        message:
          "LegoGPT worker not yet deployed. See README.md and worker/README.md " +
          "for the setup steps: (1) Request HF access to Llama-3.2-1B-Instruct, " +
          "(2) Get a Gurobi academic license, (3) Deploy worker/modal_app.py to " +
          "Modal, (4) Set WORKER_URL in .env.local.",
        prompt,
      });
    }

    // Real call (Week 4). The worker is responsible for:
    //   - Running LegoGPT inference (text -> bricks)
    //   - Chunked / 'subagent' generation if grid > LegoGPT cap
    //   - Stability check via Gurobi
    //   - Color palette snap
    //   - Step planner (layer slice + subassembly detection)
    //   - Returning {bricks, ldr, preview_png_b64, stats}
    const r = await fetch(workerUrl, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        ...(workerKey ? { "x-brickforge-key": workerKey } : {}),
      },
      body: JSON.stringify({ prompt, grid, chunked }),
    });
    const j = await r.json();
    if (!r.ok) {
      return NextResponse.json(
        { error: j.error ?? `worker ${r.status}` },
        { status: 502 }
      );
    }
    return NextResponse.json(j);
  } catch (err: any) {
    console.error("/api/generate error", err);
    return NextResponse.json(
      { error: err?.message ?? String(err) },
      { status: 500 }
    );
  }
}
