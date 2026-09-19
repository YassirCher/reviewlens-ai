import { Suspense } from "react";
import { AdminAudit } from "@/components/admin-control-pages";
export default function Page() { return <Suspense fallback={<p role="status">Loading audit…</p>}><AdminAudit /></Suspense>; }
