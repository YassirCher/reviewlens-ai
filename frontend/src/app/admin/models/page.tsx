import { Suspense } from "react";
import { AdminModels } from "@/components/admin-control-pages";
export default function Page() { return <Suspense fallback={<p role="status">Loading models…</p>}><AdminModels /></Suspense>; }
