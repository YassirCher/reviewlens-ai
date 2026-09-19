import { AdminHeading } from "@/components/admin-ui";
import { ConfigurationList } from "@/components/admin-configurations";
export default function Page() { return <><AdminHeading eyebrow="CONFIGURATION / WORKFLOWS" title="Workflows" description="Review task DAGs and activate published versions for new runs. Existing runs keep their configuration snapshots." /><ConfigurationList kind="workflows" /></>; }
