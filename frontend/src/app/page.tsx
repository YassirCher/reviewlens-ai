import type { Metadata } from "next";
import { V2Intake } from "@/components/v2-intake";
import { V2Shell } from "@/components/v2-shell";

export const metadata: Metadata = {
  title: "Evidence-backed product research · ReviewLens",
  description: "Compare YouTube product reviews through timestamped evidence and a durable research report.",
};

export default function Home() {
  return <V2Shell><V2Intake /></V2Shell>;
}
