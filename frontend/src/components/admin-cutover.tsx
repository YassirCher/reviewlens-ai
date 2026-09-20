"use client";

import { dateTime, money } from "@/lib/admin";
import { AdminState, Status, useAdminData } from "@/components/admin-ui";

export type CutoverMetric = {
  code: string;
  observed_value: string | number | boolean | null;
  threshold: string | number | boolean | null;
  passed: boolean;
  unit: string;
};

export type CutoverObservation = {
  id: string;
  environment: string;
  test_evidence: boolean;
  status: string;
  root_mode: string;
  thresholds: Record<string, number>;
  result: {
    observed_at?: string;
    ready?: boolean;
    blockers?: string[];
    metrics?: CutoverMetric[];
    samples?: Record<string, number>;
    distributions?: Record<string, number | null>;
  };
  started_at: string;
  evaluated_at: string | null;
  ended_at: string | null;
  version: number;
};

type CutoverHistory = {
  phase: "retired";
  retirement_authorized: boolean;
  observation: CutoverObservation | null;
};

function metricLabel(code: string): string {
  return code.replaceAll("_", " ");
}

export function CutoverEvidence({ observation }: { observation: CutoverObservation }) {
  const metrics = observation.result.metrics || [];
  const distributions = observation.result.distributions || {};
  return <section className="admin-panel" aria-labelledby="cutover-evidence-title">
    <div className="admin-panel-head">
      <div><h2 id="cutover-evidence-title">V2 retirement evidence</h2><p className="admin-muted">Passed production window started {dateTime(observation.started_at)} in {observation.environment}.</p></div>
      <div className="admin-actions"><Status value={observation.status} /></div>
    </div>
    <div className="admin-banner admin-banner-good" role="status">Legacy runtime retirement was authorized by this stored observation.</div>
    <div className="admin-table-scroll"><table className="admin-table"><caption className="sr-only">Retirement authorization thresholds</caption><thead><tr><th>Measure</th><th>Observed</th><th>Threshold</th><th>State</th></tr></thead><tbody>{metrics.map(metric => <tr key={metric.code}><td>{metricLabel(metric.code)}</td><td>{String(metric.observed_value ?? "unavailable")} {metric.unit}</td><td>{String(metric.threshold ?? "none")} {metric.unit}</td><td><Status value={metric.passed ? "passed" : "blocked"} /></td></tr>)}</tbody></table></div>
    <p className="admin-muted">Observed tokens {(distributions.total_tokens || 0).toLocaleString()} · p95 tokens {(distributions.p95_tokens_per_run || 0).toLocaleString()} against {observation.thresholds.public_run_token_cap?.toLocaleString() || "unavailable"} per run · observed cost {money(distributions.total_cost_microusd || 0)} · p95 cost {money(distributions.p95_cost_microusd_per_run)} against {money(observation.thresholds.public_run_cost_cap_microusd)} per run.</p>
  </section>;
}

export function AdminCutoverHistory() {
  const cutover = useAdminData<CutoverHistory>("/cutover");
  return <section aria-labelledby="cutover-history-title">
    <AdminState loading={cutover.loading} error={cutover.error} empty={!cutover.data}>
      {cutover.data && <>
        <div className="admin-panel">
          <div className="admin-panel-head"><div><h2 id="cutover-history-title">V2 retirement</h2><p className="admin-muted">The compatibility adapter and presentation rollback have been retired. This view is historical and read-only.</p></div><Status value={cutover.data.retirement_authorized ? "authorized" : "evidence unavailable"} /></div>
          {!cutover.data.observation && <p className="admin-muted">No passed production observation is available in this database.</p>}
        </div>
        {cutover.data.observation && <CutoverEvidence observation={cutover.data.observation} />}
      </>}
    </AdminState>
  </section>;
}
