"use client";

import { useState } from "react";

export default function DesignPage() {
  const [prompt, setPrompt] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setResult(null);
    try {
      const r = await fetch("/api/generate", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ prompt }),
      });
      const json = await r.json();
      setResult(JSON.stringify(json, null, 2));
    } catch (err) {
      setResult(String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="mx-auto max-w-3xl px-6 py-16">
      <a href="/" className="text-sm text-neutral-400 hover:text-white">← Back</a>
      <h1 className="text-3xl font-bold mt-4 mb-2">Design a model</h1>
      <p className="text-neutral-400 mb-8">
        Describe what you want. Generation runs on a GPU worker (LegoGPT) and
        usually takes 30–120 seconds. Larger designs are split into chunks and
        stitched.
      </p>

      <form onSubmit={onSubmit} className="space-y-4">
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="e.g. A small fighter spaceship with swept-back wings"
          className="w-full h-32 rounded-md bg-neutral-900 border border-neutral-700 p-3 text-sm"
        />
        <button
          type="submit"
          disabled={!prompt || submitting}
          className="rounded-lg bg-brand-500 hover:bg-brand-600 disabled:bg-neutral-700 px-5 py-3 font-medium"
        >
          {submitting ? "Designing…" : "Generate"}
        </button>
      </form>

      {result && (
        <pre className="mt-8 bg-neutral-900 border border-neutral-800 rounded-md p-4 text-xs overflow-auto">
          {result}
        </pre>
      )}
    </main>
  );
}
