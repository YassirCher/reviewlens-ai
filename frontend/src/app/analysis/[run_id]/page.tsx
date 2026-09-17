import type { Metadata } from "next";
import { V2Progress } from "@/components/v2-progress";
import { V2Shell } from "@/components/v2-shell";

export const metadata: Metadata = { title: "Analysis progress · ReviewLens", robots: { index: false, follow: false } };

export default async function AnalysisPage({ params }: { params: Promise<{ run_id: string }> }) {
  const { run_id } = await params;
  return <V2Shell section="Live analysis"><V2Progress runId={run_id} /></V2Shell>;
}
