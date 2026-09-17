import type { Metadata } from "next";
import { V2Report } from "@/components/v2-report";
import { V2Shell } from "@/components/v2-shell";
import { loadPublicReport } from "@/lib/v2-server";

export const metadata: Metadata = { title: "Research report · ReviewLens", robots: { index: false, follow: false, nocache: true } };

export default async function ReportPage({ params }: { params: Promise<{ public_token: string }> }) {
  const { public_token } = await params;
  const report = await loadPublicReport(public_token);
  return <V2Shell section="Research report"><V2Report report={report} token={public_token} /></V2Shell>;
}
