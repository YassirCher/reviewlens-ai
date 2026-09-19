import { AdminHeading } from "@/components/admin-ui";
import { ConfigurationList } from "@/components/admin-configurations";
export default function Page() { return <><AdminHeading eyebrow="CONFIGURATION / AGENTS" title="Agents" description="Edit role drafts, run golden evaluations, and publish immutable versions. The workflow stays active until you explicitly activate a published workflow." /><ConfigurationList kind="agents" /></>; }
