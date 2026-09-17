import Link from "next/link";
import { V2Shell } from "@/components/v2-shell";

export default function ReportNotFound() {
  return <V2Shell><section className="v2-empty"><h1>Report not found</h1><p>This link is unavailable. Check that you copied the complete address, or ask the person who shared it for a new link.</p><Link href="/research" className="v2-button">Start new research</Link></section></V2Shell>;
}
