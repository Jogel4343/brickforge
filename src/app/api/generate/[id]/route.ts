import { NextResponse } from "next/server";
import { getTextTo3DTask } from "@/lib/meshy";

/**
 * GET /api/generate/[id]
 *
 * Polls a Meshy text-to-3D task by id. Returns:
 *   - status: PENDING | IN_PROGRESS | SUCCEEDED | FAILED | EXPIRED
 *   - progress: 0..100 (when in progress)
 *   - modelGlbUrl: the generated .glb (when SUCCEEDED)
 *   - thumbnailUrl: a quick render preview (when SUCCEEDED)
 *
 * Week 4 update: when SUCCEEDED, this route will trigger the Modal worker
 * to convert the .glb → voxels → LegoGPT brick decomposition.
 */
export async function GET(
  _req: Request,
  ctx: { params: { id: string } }
) {
  const taskId = ctx.params?.id;
  if (!taskId) {
    return NextResponse.json({ error: "task id required" }, { status: 400 });
  }
  if (!process.env.MESHY_API_KEY) {
    return NextResponse.json(
      { error: "MESHY_API_KEY not configured" },
      { status: 500 }
    );
  }
  try {
    const t = await getTextTo3DTask(taskId);
    return NextResponse.json({
      taskId,
      status: t.status,
      progress: t.progress,
      modelGlbUrl: t.model_urls?.glb,
      modelObjUrl: t.model_urls?.obj,
      thumbnailUrl: t.thumbnail_url,
      error: t.task_error?.message,
    });
  } catch (err: any) {
    return NextResponse.json(
      { error: err?.message ?? String(err) },
      { status: 500 }
    );
  }
}
