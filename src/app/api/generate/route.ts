import { NextResponse } from "next/server";
import { execFile } from "node:child_process";
import { getSupabaseAdminClient } from "@/lib/supabase/server";

/**
 * POST /api/generate
 *
 * Current pipeline (see CLAUDE.md): Claude decomposes the prompt into an IR
 * of shape primitives, then deterministic Python (worker/filler.py) fills
 * that IR with real bricks and writes .ldr. scripts/generate_one.py runs
 * that pipeline for one prompt and prints {ok, name, bricks, ldr} as JSON.
 *
 * This route shells out to that script as a subprocess. That's an interim
 * local-dev bridge, not the deployed architecture in CLAUDE.md's
 * "Deployment shape" (Python worker on Modal, called over HTTP) — swap the
 * execFile call for a fetch() to WORKER_URL once that's deployed. Nothing
 * about the IR pipeline itself changes either way.
 *
 * Persistence (roadmap #7): after a successful generation, the .ldr is
 * saved to the Supabase "designs" Storage bucket and a public.designs row
 * is written, so /d/[id] has something to load. Persistence failures don't
 * fail the request — the viewer still gets the generated .ldr either way,
 * it just won't have a shareable link.
 */

interface GenerateRequest {
  prompt?: string;
}

// CLI transport (no ANTHROPIC_API_KEY set) shells out to the `claude` CLI on
// top of the Python subprocess, which is measurably slower than direct API
// transport — observed a live run take 121s, just over the old 120s cap.
const GENERATE_TIMEOUT_MS = 240_000;

function runGenerateOne(prompt: string): Promise<{ ok: boolean; [key: string]: any }> {
  return new Promise((resolve, reject) => {
    execFile(
      "python",
      ["-m", "scripts.generate_one", prompt],
      { cwd: process.cwd(), timeout: GENERATE_TIMEOUT_MS, maxBuffer: 16 * 1024 * 1024 },
      (err, stdout, stderr) => {
        if (stdout) {
          try {
            resolve(JSON.parse(stdout));
            return;
          } catch {
            // fall through to error handling below
          }
        }
        reject(err ?? new Error(stderr || "scripts.generate_one produced no output"));
      }
    );
  });
}

/**
 * Save the generated .ldr to Storage and record a designs row. Returns the
 * design id on success, or null if persistence fails — a Supabase outage
 * shouldn't take down generation, it should just mean this run isn't
 * shareable.
 */
async function persistDesign(
  prompt: string,
  name: string,
  bricks: number,
  ldr: string
): Promise<string | null> {
  if (!process.env.NEXT_PUBLIC_SUPABASE_URL || !process.env.SUPABASE_SERVICE_ROLE_KEY) {
    return null;
  }
  try {
    const supabase = getSupabaseAdminClient();
    const { data: design, error: insertError } = await supabase
      .from("designs")
      .insert({ prompt, status: "succeeded", total_bricks: bricks })
      .select("id")
      .single();
    if (insertError || !design) throw insertError ?? new Error("insert returned no row");

    const ldrPath = `${design.id}.ldr`;
    const { error: uploadError } = await supabase.storage
      .from("designs")
      .upload(ldrPath, ldr, { contentType: "text/plain", upsert: true });
    if (uploadError) throw uploadError;

    const { error: updateError } = await supabase
      .from("designs")
      .update({ ldr_path: ldrPath })
      .eq("id", design.id);
    if (updateError) throw updateError;

    return design.id as string;
  } catch (err) {
    console.error(`persistDesign failed for prompt ${JSON.stringify(prompt)}:`, err);
    return null;
  }
}

export async function POST(req: Request) {
  try {
    const body = (await req.json()) as GenerateRequest;
    const { prompt } = body;

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

    const result = await runGenerateOne(prompt);
    if (!result.ok) {
      console.error(`/api/generate failed for prompt ${JSON.stringify(prompt)}:`, result.error);
      return NextResponse.json({ error: result.error ?? "generation failed" }, { status: 502 });
    }

    const id = await persistDesign(prompt, result.name, result.bricks, result.ldr);

    return NextResponse.json({
      status: "succeeded",
      id,
      name: result.name,
      bricks: result.bricks,
      ldr: result.ldr,
    });
  } catch (err: any) {
    console.error("/api/generate error", err);
    return NextResponse.json(
      { error: err?.message ?? String(err) },
      { status: 500 }
    );
  }
}
