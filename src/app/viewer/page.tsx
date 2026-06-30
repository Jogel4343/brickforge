"use client";

import { useState } from "react";
import dynamic from "next/dynamic";

// LdrawViewer touches `window` / WebGL so it must be client-only.
const LdrawViewer = dynamic(() => import("@/components/LdrawViewer"), { ssr: false });

export default function ViewerPage() {
  const [modelUrl, setModelUrl] = useState<string | undefined>(undefined);
  const [fileName, setFileName] = useState<string | null>(null);

  function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setFileName(file.name);
    // Convert the LDraw file into a blob URL the LDrawLoader can fetch.
    const blobUrl = URL.createObjectURL(file);
    setModelUrl(blobUrl);
  }

  function loadSample() {
    setFileName(null);
    setModelUrl(undefined); // falls back to the built-in sample
  }

  return (
    <main className="h-screen w-screen flex flex-col">
      <header className="px-6 py-3 border-b border-neutral-800 flex items-center justify-between gap-4 flex-wrap">
        <a href="/" className="font-semibold">Brickforge</a>
        <div className="flex items-center gap-3 text-xs">
          <label className="cursor-pointer rounded-md bg-brand-500 hover:bg-brand-600 px-3 py-1.5 font-medium text-white">
            Load .ldr / .mpd
            <input
              type="file"
              accept=".ldr,.mpd,.dat"
              onChange={onFile}
              className="hidden"
            />
          </label>
          <button
            onClick={loadSample}
            className="rounded-md border border-neutral-700 hover:bg-neutral-800 px-3 py-1.5"
          >
            Sample model
          </button>
          {fileName && (
            <span className="text-neutral-400">
              Loaded: <span className="font-mono">{fileName}</span>
            </span>
          )}
        </div>
        <div className="text-xs text-neutral-400">
          Drag to orbit · Scroll to zoom · Right-click drag to pan
        </div>
      </header>
      <div className="flex-1">
        <LdrawViewer modelUrl={modelUrl} />
      </div>
    </main>
  );
}
