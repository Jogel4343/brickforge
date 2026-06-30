"use client";

import { useState } from "react";

type Status = "idle" | "submitting" | "queued" | "in_progress" | "succeeded" | "failed" | "not_ready";

export default function DesignPage() {
  const [prompt, setPrompt] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [debug, setDebug] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setStatus("submitting");
    setMessage(null);
    setDebug(null);
    try {
      const r = await fetch("/api/generate", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ prompt }),
      });
      const j = await r.json();
      setDebug(JSON.stringify(j, null, 2));
      if (j.status === "not_ready") {
        setStatus("not_ready");
        setMessage(j.message);
        return;
      }
      if (j.error) {
        setStatus("failed");
        setMessage(j.error);
        return;
      }
      // Once the worker is live this will switch to polling /api/generate/[id].
      setStatus("succeeded");
    } catch (err) {
      setStatus("failed");
      setMessage(String(err));
    }
  }

  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      <a href="/" className="text-sm text-neutral-400 hover:text-white">
        ← Back
      </a>
      <h1 className="text-3xl font-bold mt-4 mb-2">Design a model</h1>
      <p className="text-neutral-400 mb-8">
        Describe what you want. Brickforge will generate a buildable LEGO model
        with step-by-step instructions and a priced parts list.
      </p>

      <form onSubmit={onSubmit} className="space-y-4">
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="e.g. a small fighter spaceship with swept-back wings"
          className="w-full h-28 rounded-md bg-neutral-900 border border-neutral-700 p-3 text-sm"
        />
        <button
          type="submit"
          disabled={!prompt || status === "submitting"}
          className="rounded-lg bg-brand-500 hover:bg-brand-600 disabled:bg-neutral-700 px-5 py-3 font-medium"
        >
          {status === "submitting" ? "Submitting…" : "Generate"}
        </button>
      </form>

      {status === "not_ready" && (
        <div className="mt-8 rounded-md bg-amber-900/30 border border-amber-700/50 p-4 text-sm">
          <strong className="block mb-2">LegoGPT worker not deployed yet.</strong>
          <p className="text-amber-100/80 mb-3">{message}</p>
          <ol className="list-decimal list-inside space-y-1 text-amber-100/70 text-xs">
            <li>
              Request access to{" "}
              <a
                className="underline"
                href="https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct"
                target="_blank"
                rel="noreferrer"
              >
                Llama-3.2-1B-Instruct
              </a>{" "}
              on Hugging Face
            </li>
            <li>
              Get a{" "}
              <a
                className="underline"
                href="https://www.gurobi.com/academia/academic-program-and-licenses/"
                target="_blank"
                rel="noreferrer"
              >
                Gurobi academic license
              </a>{" "}
              (free for students)
            </li>
            <li>
              Sign up for{" "}
              <a className="underline" href="https://modal.com/" target="_blank" rel="noreferrer">
                Modal
              </a>{" "}
              and run <code className="bg-black/40 px-1 rounded">modal token new</code>
            </li>
            <li>
              Deploy <code className="bg-black/40 px-1 rounded">worker/modal_app.py</code> →
              copy the URL into <code className="bg-black/40 px-1 rounded">.env.local</code> as{" "}
              <code className="bg-black/40 px-1 rounded">WORKER_URL</code>
            </li>
          </ol>
        </div>
      )}

      {message && status !== "not_ready" && (
        <div className="mt-6 rounded-md bg-neutral-900 border border-neutral-800 p-4 text-sm">
          {message}
        </div>
      )}

      {debug && (
        <pre className="mt-6 bg-neutral-900 border border-neutral-800 rounded-md p-4 text-xs overflow-auto max-h-64">
          {debug}
        </pre>
      )}

      <section className="mt-12 border-t border-neutral-800 pt-8">
        <h2 className="text-lg font-semibold mb-3">Pipeline (v1)</h2>
        <ol className="space-y-2 text-sm text-neutral-300 list-decimal list-inside">
          <li>You type a prompt</li>
          <li>LegoGPT (on a Modal GPU) generates a stable LEGO brick model</li>
          <li>
            For larger builds: chunked / "subagent" generation splits the prompt into
            sub-parts (cockpit, wings, hull) generated in parallel and stitched
          </li>
          <li>Layer-slicing step planner produces buildable instructions</li>
          <li>
            Parts list maps LDraw → BrickLink IDs with live pricing from the BrickLink
            API
          </li>
          <li>Output: a sharable design page with .ldr download + parts CSV</li>
        </ol>
        <p className="text-xs text-neutral-500 mt-4">
          Previous prototype used Meshy as a text → 3D mesh first stage. We removed
          it: LegoGPT accepts text prompts directly, so the mesh stage is unnecessary
          for v1. Mesh stage will return in v1.5 for photo-upload conditioning.
        </p>
      </section>
    </main>
  );
}
