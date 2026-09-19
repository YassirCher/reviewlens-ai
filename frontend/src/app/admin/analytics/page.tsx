import { Suspense } from "react";
import { AdminAnalytics } from "@/components/admin-observation";
export default function Page() { return <Suspense fallback={<p role="status">Loading analytics…</p>}><AdminAnalytics /></Suspense>; }
