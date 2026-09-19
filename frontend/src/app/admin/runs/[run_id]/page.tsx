import { AdminRunDetail } from "@/components/admin-observation";
export default async function Page({ params }: { params: Promise<{ run_id: string }> }) { const { run_id } = await params; return <AdminRunDetail id={run_id} />; }
