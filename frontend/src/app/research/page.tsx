import type { Metadata } from "next";
import { V2Intake } from "@/components/v2-intake";
import { V2Shell } from "@/components/v2-shell";

export const metadata: Metadata = { title: "Research a product · ReviewLens", robots: { index: false, follow: false } };

export default function ResearchPage() { return <V2Shell><V2Intake /></V2Shell>; }
