import type { Metadata } from "next";
import { V2Intake } from "@/components/v2-intake";
import { productError } from "@/lib/v2";
import { V2Shell } from "@/components/v2-shell";

export const metadata: Metadata = {
  title: "Evidence-backed product research · ReviewLens",
  description: "Compare YouTube product reviews through timestamped evidence and a durable research report.",
};

export default async function Home({ searchParams }: { searchParams: Promise<{ product?: string | string[] }> }) {
  const query = await searchParams;
  const product = typeof query.product === "string" && !productError(query.product) ? query.product : "";
  return <V2Shell><V2Intake initialProduct={product} /></V2Shell>;
}
