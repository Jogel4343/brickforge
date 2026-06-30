import dynamic from "next/dynamic";

// LdrawViewer touches `window` / WebGL so it must be client-only.
const LdrawViewer = dynamic(() => import("@/components/LdrawViewer"), { ssr: false });

export default function ViewerPage() {
  return (
    <main className="h-screen w-screen flex flex-col">
      <header className="px-6 py-3 border-b border-neutral-800 flex items-center justify-between">
        <a href="/" className="font-semibold">Brickforge</a>
        <div className="text-xs text-neutral-400">
          Drag to orbit · Scroll to zoom · Right-click drag to pan
        </div>
      </header>
      <div className="flex-1">
        {/* No modelUrl prop = loads the Three.js sample car. Drop /public/ldraw/ in to
            render real models from your own pipeline output. */}
        <LdrawViewer />
      </div>
    </main>
  );
}
