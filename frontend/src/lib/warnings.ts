import type { RunStatus } from "./v2";

const LABELS: Record<string, string> = {
  quality_audit_warning: "Some findings were omitted because their evidence did not fully support them.",
  partial_source_coverage: "Some requested sources could not be analyzed.",
  single_source_no_consensus: "Only one source was available; agreement between reviewers is unconfirmed.",
  first_impressions_only: "The available reviews cover first impressions rather than longer-term use.",
  central_evidence_invalid: "Evidence for a central conclusion could not be verified.",
};

export function warningLabel(code: string): string {
  return LABELS[code] || code.replaceAll("_", " ");
}

export function partialNotice(run: Pick<RunStatus, "source_count_analyzed" | "source_count_requested" | "warnings">): string {
  const { source_count_analyzed: analyzed, source_count_requested: requested, warnings } = run;
  if (analyzed < requested) {
    return `Partial report: ${analyzed} of ${requested} requested sources could be analyzed. Valid evidence remains available.`;
  }
  return `All ${requested} requested sources were analyzed. ` + (warnings.includes("quality_audit_warning")
    ? "The report contains evidence warnings; some findings were omitted."
    : "The report was published with warnings; inspect the report for details.");
}
