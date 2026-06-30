"use client";

import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";

const MeshPreview = dynamic(() => import("@/components/MeshPreview"), { ssr: false });

type Status =
  | "idle"
  | "submitting"
  | "queued"
  | "in_progress"
  | "succeeded"
  | "failed"
  | "stub";

export default function DesignPage() {
  const [prompt, setPrompt] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [progress, setProgress] = useState(0);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [glbUrl, setGlbUrl] = useState<string | null>(null);
  const [thumb, setThumb] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [debug, setDebug] = useState<string | null>(null);
  const [pollCount, setPollCount] = useState(0);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Poll Meshy task status when we have a taskId.
  useEffect(() => {
    if (!taskId) return;
    // Belt-and-suspenders: clear any prior interval before starting a new one.
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    setPollCount(0);
    setStartedAt(Date.now());

    let stopped = false;
    const stop = () => {
      stopped = true;
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };

    const tick = async () => {
      if (stopped) return;
      setPollCount((n) => n + 1);
      try {
        const r = await fetch(`/api/generate/${taskId}`);
        const j = await r.json();
        setDebug(JSON.stringify(j, null, 2));
        if (j.error) {
          setStatus("failed");
          setMessage(j.error);
          stop();
          return;
        }
        setProgress(j.progress ?? 0);
        if (j.status === "SUCCEEDED") {
          setStatus("succeeded");
          setGlbUrl(j.modelGlbUrl ?? null);
          setThumb(j.thumbnailUrl ?? null);
          stop();
        } else if (j.status === "FAILED" || j.status === "EXPIRED") {
          setStatus("failed");
          setMessage(j.error ?? j.status);
          stop();
        } else {
          setStatus("in_progress");
        }
      } catch (err) {
        // Transient — keep polling.
      }
    };

    tick();
    pollRef.current = setInterval(tick, 4000);
    return stop;
  }, [taskId]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setStatus("submitting");
    setMessage(null);
    setGlbUrl(null);
    setThumb(null);
    setProgress(0);
    try {
      const r = await fetch("/api/generate", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ prompt }),
      });
      const j = await r.json();
      if (j.status === "stub") {
        setStatus("stub");
        setMessage(j.message);
        return;
      }
      if (j.taskId) {
        setTaskId(j.taskId);
        setStatus("queued");
      } else {
        setStatus("failed");
        setMessage(j.error ?? "Unknown error");
      }
    } catch (err) {
      setStatus("failed");
      setMessage(String(err));
    }
  }

  return (
    <main className="mx-auto max-w-5xl px-6 py-12">
      <a href="/" className="text-sm text-neutral-400 hover:text-white">
        ← Back
      </a>
      <h1 className="text-3xl font-bold mt-4 mb-2">Design a model</h1>
      <p className="text-neutral-400 mb-8">
        Describe what you want. Stage 1 generates a 3D mesh from your prompt
        (~30–90 s). Stage 2 (coming Week 4) converts it to a buildable LEGO
        model with step-by-step instructions and a priced parts list.
      </p>

      <form onSubmit={onSubmit} className="space-y-4">
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="e.g. A small fighter spaceship with swept-back wings"
          className="w-full h-28 rounded-md bg-neutral-900 border border-neutral-700 p-3 text-sm"
        />
        <button
          type="submit"
          disabled={
            !prompt ||
            status === "submitting" ||
            status === "queued" ||
            status === "in_progress"
          }
          className="rounded-lg bg-brand-500 hover:bg-brand-600 disabled:bg-neutral-700 px-5 py-3 font-medium"
        >
          {status === "submitting" && "Submitting…"}
          {status === "queued" && "Queued…"}
          {status === "in_progress" && `Generating… ${progress}%`}
          {(status === "idle" || status === "succeeded" || status === "failed" || status === "stub") &&
            "Generate"}
        </button>
      </form>

      {message && (
        <div className="mt-6 rounded-md bg-neutral-900 border border-neutral-800 p-4 text-sm">
          {message}
        </div>
      )}

      {(status === "in_progress" || status === "queued" || status === "succeeded" || status === "failed") && taskId && (
        <div className="mt-6 rounded-md bg-neutral-900 border border-neutral-800 p-4">
          <div className="flex items-center justify-between text-xs text-neutral-400 mb-2">
            <span>
              Task <span className="font-mono">{taskId.slice(0, 8)}…</span> ·
              {" "}poll #{pollCount}{" "}
              {startedAt && (
                <>· {Math.floor((Date.now() - startedAt) / 1000)}s elapsed</>
              )}
            </span>
            <span className="uppercase text-[10px] tracking-wider">{status}</span>
          </div>
          {debug && (
            <pre className="bg-black/40 rounded p-3 text-[11px] overflow-auto max-h-64 text-neutral-300">
              {debug}
            </pre>
          )}
        </div>
      )}

      {status === "succeeded" && glbUrl && (
        <section className="mt-8 grid md:grid-cols-2 gap-6">
          <div>
            <h2 className="text-lg font-semibold mb-2">3D mesh</h2>
            <div className="aspect-square rounded-md overflow-hidden border border-neutral-800">
              <MeshPreview glbUrl={glbUrl} />
            </div>
            <p className="text-xs text-neutral-500 mt-2">
              Drag to rotate · Scroll to zoom
            </p>
          </div>
          <div>
            <h2 className="text-lg font-semibold mb-2">Next steps</h2>
            <ol className="list-decimal list-inside space-y-2 text-sm text-neutral-300">
              <li>✅ Stage 1: Text → 3D mesh (you're here)</li>
              <li className="text-neutral-500">
                ⏳ Stage 2: Mesh → voxel grid (worker, Week 3)
              </li>
              <li className="text-neutral-500">
                ⏳ Stage 3: Voxels → LegoGPT brick decomposition (Week 4)
              </li>
              <li className="text-neutral-500">
                ⏳ Stage 4: Stability + color + step planning (Week 5–6)
              </li>
              <li className="text-neutral-500">
                ⏳ Stage 5: Priced parts list + LDraw export (Week 7)
              </li>
            </ol>
            {thumb && (
              <>
                <h3 className="text-sm font-semibold mt-6 mb-2">
                  Meshy preview render
                </h3>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={thumb}
                  alt="Meshy preview"
                  className="rounded-md border border-neutral-800"
                />
              </>
            )}
          </div>
        </section>
      )}
    </main>
  );
}
