"use client";

import dynamic from "next/dynamic";

const LdrawViewer = dynamic(() => import("@/components/LdrawViewer"), { ssr: false });

export default function DesignViewerClient({ modelUrl }: { modelUrl: string }) {
  return <LdrawViewer modelUrl={modelUrl} />;
}
