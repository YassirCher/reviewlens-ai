import { Suspense } from "react";
import { AdminKnowledge } from "@/components/admin-control-pages";
export default function Page() { return <Suspense fallback={<p role="status">Loading knowledge…</p>}><AdminKnowledge /></Suspense>; }
