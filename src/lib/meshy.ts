/**
 * Meshy AI text-to-3D wrapper.
 *
 * Used as the first stage of the generation pipeline when we want
 * image-conditioned input to LegoGPT (rather than text-direct).
 *
 * Flow:
 *   1. POST /openapi/v2/text-to-3d → returns a task id
 *   2. Poll GET /openapi/v2/text-to-3d/{task_id} until status === "SUCCEEDED"
 *   3. The succeeded task carries `model_urls.glb` etc.
 *
 * Docs: https://docs.meshy.ai/api-text-to-3d
 */

const MESHY_API_BASE = "https://api.meshy.ai";

export type MeshyMode = "preview" | "refine";

export interface MeshyTextTo3DOptions {
  prompt: string;
  mode?: MeshyMode;             // "preview" is fast + free credits; "refine" is higher quality
  // Meshy 6 defaults are sensible; only pass these if you need to override.
  targetPolycount?: number;     // 100..300_000; default 30_000
  modelType?: "standard" | "lowpoly";
  // Polling tunables
  pollEveryMs?: number;
  timeoutMs?: number;
}

export interface MeshyTextTo3DResult {
  taskId: string;
  status: "SUCCEEDED" | "FAILED" | "EXPIRED";
  modelGlbUrl?: string;
  modelObjUrl?: string;
  thumbnailUrl?: string;
  progress?: number;
  error?: string;
  raw?: unknown;
}

function requireApiKey(): string {
  const k = process.env.MESHY_API_KEY;
  if (!k) {
    throw new Error(
      "MESHY_API_KEY missing. Get one at https://www.meshy.ai/ and add it to .env.local"
    );
  }
  return k;
}

/**
 * Submit a text-to-3D job. Returns the task id; caller can poll or call
 * runTextTo3D() for an all-in-one flow.
 */
export async function createTextTo3DTask(
  opts: MeshyTextTo3DOptions
): Promise<string> {
  const apiKey = requireApiKey();
  // Meshy-6 defaults are good; send only the minimum required fields. We used
  // to send `art_style` and `negative_prompt` here but Meshy docs explicitly
  // mark both as deprecated for Meshy-6, and sending `art_style` can cause
  // the new model to stall or error.
  const body: Record<string, unknown> = {
    mode: opts.mode ?? "preview",
    prompt: opts.prompt,
  };
  if (opts.targetPolycount) body.target_polycount = opts.targetPolycount;
  if (opts.modelType) body.model_type = opts.modelType;

  const res = await fetch(`${MESHY_API_BASE}/openapi/v2/text-to-3d`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Meshy create failed: ${res.status} ${text}`);
  }

  const json = (await res.json()) as { result?: string };
  if (!json.result) {
    throw new Error(`Meshy returned no task id: ${JSON.stringify(json)}`);
  }
  return json.result;
}

export async function getTextTo3DTask(taskId: string): Promise<{
  status: string;
  progress?: number;
  model_urls?: Record<string, string>;
  thumbnail_url?: string;
  task_error?: { message?: string };
  raw: unknown;
}> {
  const apiKey = requireApiKey();
  const res = await fetch(
    `${MESHY_API_BASE}/openapi/v2/text-to-3d/${taskId}`,
    { headers: { Authorization: `Bearer ${apiKey}` } }
  );
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Meshy get failed: ${res.status} ${text}`);
  }
  const json = await res.json();
  return { ...json, raw: json };
}

/**
 * One-shot: create + poll until terminal. Use in API routes when you want a
 * synchronous-ish call (the client polls separately for status, so don't use
 * this for long preview runs > 60s — refactor to background job).
 */
export async function runTextTo3D(
  opts: MeshyTextTo3DOptions
): Promise<MeshyTextTo3DResult> {
  const taskId = await createTextTo3DTask(opts);
  const pollEvery = opts.pollEveryMs ?? 4000;
  const timeoutMs = opts.timeoutMs ?? 180_000; // 3 min default for preview
  const start = Date.now();

  while (true) {
    const t = await getTextTo3DTask(taskId);
    if (t.status === "SUCCEEDED") {
      return {
        taskId,
        status: "SUCCEEDED",
        modelGlbUrl: t.model_urls?.glb,
        modelObjUrl: t.model_urls?.obj,
        thumbnailUrl: t.thumbnail_url,
        progress: t.progress,
        raw: t.raw,
      };
    }
    if (t.status === "FAILED" || t.status === "EXPIRED") {
      return {
        taskId,
        status: t.status as "FAILED" | "EXPIRED",
        error: t.task_error?.message ?? "unknown error",
        raw: t.raw,
      };
    }
    if (Date.now() - start > timeoutMs) {
      throw new Error(
        `Meshy timeout after ${timeoutMs}ms (task ${taskId} still ${t.status})`
      );
    }
    await new Promise((r) => setTimeout(r, pollEvery));
  }
}
