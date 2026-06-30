import { NextResponse } from "next/server";

/**
 * POST /api/generate
 *
 * Stub for the generation pipeline. In production this will:
 *   1. Validate prompt + auth
 *   2. Insert a row in `generations` (Supabase) with status=queued
 *   3. Call the Modal worker (WORKER_URL) which runs LegoGPT (+ chunked stitching
 *      for large requests) and returns brick list + .ldr + .png
 *   4. Store the .ldr + render in Supabase Storage
 *   5. Compute parts list, map to BrickLink IDs, attach live prices
 *   6. Return design ID for the client to poll/redirect
 *
 * For now: validates the request shape and returns a placeholder so the UI
 * can be developed in parallel with the worker.
 */
export async function POST(req: Request) {
  try {
    const { prompt } = (await req.json()) as { prompt?: string };
    if (!prompt || typeof prompt !== "string" || prompt.length < 3) {
      return NextResponse.json({ error: "Provide a prompt of at least 3 characters." }, { status: 400 });
    }
    if (prompt.length > 1000) {
      return NextResponse.json({ error: "Prompt too long (max 1000 chars)." }, { status: 400 });
    }

    // TODO: replace with real worker call. Wired up in Week 4.
    return NextResponse.json({
      status: "stub",
      message:
        "Worker not yet wired. This endpoint will call the Modal LegoGPT worker. See worker/ for the Python pipeline scaffold.",
      received: { prompt },
    });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}
