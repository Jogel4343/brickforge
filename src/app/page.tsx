import Link from "next/link";

export default function HomePage() {
  return (
    <main className="mx-auto max-w-5xl px-6 py-20">
      <div className="space-y-6">
        <h1 className="text-5xl font-bold tracking-tight">
          Brickforge
        </h1>
        <p className="text-xl text-neutral-300 max-w-2xl">
          Describe what you want. We design a buildable LEGO model, give you step-by-step
          instructions, and price the parts list against live BrickLink data.
        </p>
        <div className="flex gap-3 pt-4">
          <Link
            href="/viewer"
            className="rounded-lg bg-brand-500 hover:bg-brand-600 px-5 py-3 font-medium"
          >
            Open viewer demo
          </Link>
          <Link
            href="/design"
            className="rounded-lg border border-neutral-700 hover:bg-neutral-800 px-5 py-3 font-medium"
          >
            Design a model
          </Link>
        </div>
      </div>

      <section className="mt-20 grid md:grid-cols-3 gap-6">
        <Card title="3D viewer" body="Orbit, pan, zoom, explode-view, AR. Built on Three.js + LDraw." />
        <Card title="Step-by-step instructions" body="Layer-sliced, subassembly-aware, with optional natural-language prose." />
        <Card title="Priced parts list" body="Mapped to BrickLink IDs with live pricing. Export as CSV or order-ready link." />
      </section>
    </main>
  );
}

function Card({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-xl border border-neutral-800 bg-neutral-900/40 p-5">
      <h3 className="font-semibold mb-2">{title}</h3>
      <p className="text-sm text-neutral-400">{body}</p>
    </div>
  );
}
