"use client";

import { useState } from "react";
import dynamic from "next/dynamic";

const LdrawViewer = dynamic(() => import("@/components/LdrawViewer"), { ssr: false });

type Status = "idle" | "submitting" | "succeeded" | "failed";

export default function DesignPage() {
  const [prompt, setPrompt] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [modelUrl, setModelUrl] = useState<string | null>(null);
  const [modelName, setModelName] = useState<string | null>(null);
  const [brickCount, setBrickCount] = useState<number | null>(null);
  const [designId, setDesignId] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setStatus("submitting");
    setMessage(null);
    if (modelUrl) URL.revokeObjectURL(modelUrl);
    setModelUrl(null);
    try {
      const r = await fetch("/api/generate", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ prompt }),
      });
      const j = await r.json();
      if (j.error) {
        setStatus("failed");
        setMessage(j.error);
        return;
      }
      const blob = new Blob([j.ldr], { type: "text/plain" });
      setModelUrl(URL.createObjectURL(blob));
      setModelName(j.name);
      setBrickCount(j.bricks);
      setDesignId(j.id ?? null);
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
        Describe what you want. Brickforge will generate a buildable LEGO
        model as a real .ldr file (takes 5-40s). Step-by-step instructions
        and a priced parts list are future work.
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
          {status === "submitting" ? "Generating… (5-40s)" : "Generate"}
        </button>
      </form>

      {status === "failed" && message && (
        <div className="mt-6 rounded-md bg-red-900/30 border border-red-700/50 p-4 text-sm text-red-100/90">
          {message}
        </div>
      )}

      {status === "succeeded" && modelUrl && (
        <div className="mt-8">
          <div className="flex items-center justify-between mb-2 text-sm">
            <span>
              <strong>{modelName}</strong> — {brickCount} bricks
            </span>
            <div className="flex items-center gap-4">
              {designId && (
                <a href={`/d/${designId}`} className="text-brand-400 hover:underline">
                  Shareable link
                </a>
              )}
              <a
                href={modelUrl}
                download={`${modelName ?? "brickforge_model"}.ldr`}
                className="text-brand-400 hover:underline"
              >
                Download .ldr
              </a>
            </div>
          </div>
          <div className="h-[480px] rounded-md overflow-hidden border border-neutral-800">
            <LdrawViewer modelUrl={modelUrl} />
          </div>
        </div>
      )}

      <section className="mt-12 border-t border-neutral-800 pt-8">
        <h2 className="text-lg font-semibold mb-3">Pipeline (current)</h2>
        <ol className="space-y-2 text-sm text-neutral-300 list-decimal list-inside">
          <li>
            Claude decomposes your prompt into an IR of axis-aligned shape
            primitives (walls, roofs, towers) — it never picks individual
            bricks
          </li>
          <li>
            A deterministic Python solver ("legolization") fills each
            primitive with real bricks from a whitelisted vocabulary,
            interlocking seams between courses
          </li>
          <li>The result is written out as a real .ldr file, rendered above</li>
        </ol>
        <p className="text-xs text-neutral-500 mt-4">
          This replaces an earlier LegoGPT-direct-generation prototype
          (see CLAUDE.md) — Claude does semantic reasoning, deterministic code
          does spatial reasoning. Step-by-step instructions and a priced
          parts list are not wired up yet.
        </p>
      </section>
    </main>
  );
}
