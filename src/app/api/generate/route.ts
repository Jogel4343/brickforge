import { NextResponse } from "next/server";
import { createTextTo3DTask } from "@/lib/meshy";

/**
 * POST /api/generate
 *
 * Stage 1 of the LEGO generation pipeline.
 *
 * Today (Week 2): kick off a Meshy text-to-3D job. Returns the Meshy task id
 *   so the client can poll /api/generate/[id] for progress.
 *
 * Soon (Week 4): when Meshy SUCCEEDED, hand the mesh off to the Modal worker
 *   running LegoGPT for the brick decomposition + step planning + parts list.
 *
 * For now this route lets us verify Meshy works end-to-end. A successful call
 * proves: prompt → 3D mesh URL, which is the input we'll feed into LegoGPT.
 */
export async function POST(req: Request) {
  try {
    const { prompt, mode } = (await req.json()) as {
      prompt?: string;
      mode?: "preview" | "refine";
    };
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

    if (!process.env.MESHY_API_KEY) {
      return NextResponse.json(
        {
          status: "stub",
          message:
            "MESHY_API_KEY not configured. Set it in .env.local (https://www.meshy.ai/) and restart `npm run dev`.",
          received: { prompt, mode: mode ?? "preview" },
        },
        { status: 200 }
      );
    }

    const taskId = await createTextTo3DTask({
      prompt,
      mode: mode ?? "preview",
      // Lower polycount = faster generation and easier to voxelize for LegoGPT.
      // Meshy's default 30k is overkill for our LEGO use case.
      targetPolycount: 5000,
    });

    return NextResponse.json({
      status: "queued",
      taskId,
      message:
        "Meshy text-to-3D job submitted. Poll /api/generate/" +
        taskId +
        " for status. Typically completes in 30-90 seconds.",
    });
  } catch (err: any) {
    console.error("/api/generate error", err);
    return NextResponse.json(
      { error: err?.message ?? String(err) },
      { status: 500 }
    );
  }
}
