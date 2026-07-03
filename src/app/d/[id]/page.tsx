import { notFound } from "next/navigation";
import { getSupabaseAdminClient } from "@/lib/supabase/server";
import DesignViewerClient from "./DesignViewerClient";

/**
 * GET /d/[id] — a saved design's shareable page (roadmap #7 persistence
 * slice). Reads via the admin client server-side rather than the anon key,
 * so this works even before any client-facing RLS policy exists beyond the
 * public SELECT one in supabase/schema.sql.
 */
export default async function DesignPage({ params }: { params: { id: string } }) {
  const supabase = getSupabaseAdminClient();
  const { data: design } = await supabase
    .from("designs")
    .select("id, prompt, status, total_bricks, ldr_path, created_at")
    .eq("id", params.id)
    .single();

  if (!design || !design.ldr_path) {
    notFound();
  }

  const { data: publicUrl } = supabase.storage.from("designs").getPublicUrl(design.ldr_path);

  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      <a href="/" className="text-sm text-neutral-400 hover:text-white">
        ← Back
      </a>
      <h1 className="text-2xl font-bold mt-4 mb-1">{design.prompt}</h1>
      <p className="text-sm text-neutral-400 mb-8">
        {design.total_bricks} bricks · generated{" "}
        {new Date(design.created_at).toLocaleString()}
      </p>
      <div className="h-[480px] rounded-md overflow-hidden border border-neutral-800">
        <DesignViewerClient modelUrl={publicUrl.publicUrl} />
      </div>
    </main>
  );
}
