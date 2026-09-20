import type { Metadata } from "next";
import { connection } from "next/server";
import { AnalysisApp } from "@/components/analysis-app";
import { V2Intake } from "@/components/v2-intake";
import { V2Shell } from "@/components/v2-shell";

export const metadata: Metadata = {
  title: "Evidence-backed product research · ReviewLens",
  description: "Compare YouTube product reviews through timestamped evidence and a durable research report.",
};

export default async function Home() {
  await connection();
  if (process.env.PUBLIC_ROOT_EXPERIENCE === "v1") return <AnalysisApp />;
  return <V2Shell><V2Intake /></V2Shell>;
}
