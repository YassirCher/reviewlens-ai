import type { Metadata } from "next";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { V2Graph } from "@/components/v2-graph";
import { V2Shell } from "@/components/v2-shell";
import { loadPublicReport } from "@/lib/v2-server";

export const metadata: Metadata = { title: "Evidence map · ReviewLens", robots: { index: false, follow: false, nocache: true } };

export default async function EvidencePage({ params }: { params: Promise<{ public_token: string }> }) {
  const { public_token } = await params;
  const report = await loadPublicReport(public_token);
  return <V2Shell section="Evidence map"><div className="v2-page-heading"><Link href={`/r/${public_token}`} className="v2-back"><ArrowLeft size={16} aria-hidden="true" /> Back to report</Link><p className="v2-eyebrow">SOURCE RELATIONSHIPS</p><h1>How the evidence connects</h1><p>Explore the findings and their sources for {report.product_name}. The list contains the same information as the map.</p></div><V2Graph report={report} token={public_token} full /></V2Shell>;
}
