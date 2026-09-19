import { ConfigurationDetail } from "@/components/admin-configurations";
export default async function Page({ params }: { params: Promise<{ agent_id: string }> }) { const { agent_id } = await params; return <ConfigurationDetail kind="agents" id={agent_id} />; }
