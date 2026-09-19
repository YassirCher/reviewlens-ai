import { Suspense } from "react";
import { AdminRuns } from "@/components/admin-observation";
export default function Page() { return <Suspense fallback={<p role="status">Loading runs…</p>}><AdminRuns /></Suspense>; }
