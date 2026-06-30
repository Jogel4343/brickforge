import { NextResponse } from "next/server";

/**
 * GET /api/generate/[id]
 *
 * Polling endpoint for generation status. Wired to the Modal worker in
 * Week 4 — for now it just returns a placeholder so the UI doesn't 500.
 *
 * Final shape (Week 4):
 *   {
 *     id, status, progress,
 *     ldrUrl?: string,
 *     previewUrl?: string,
 *     stats?: { totalBricks, rejections, regenerations, gpuSeconds }
 *   }
 */
export async function GET(
  _req: Request,
  ctx: { params: { id: string } }
) {
  const id = ctx.params?.id;
  if (!id) {
    return NextResponse.json({ error: "task id required" }, { status: 400 });
  }
  return NextResponse.json({
    id,
    status: "NOT_READY",
    message: "LegoGPT worker not yet deployed.",
  });
}
