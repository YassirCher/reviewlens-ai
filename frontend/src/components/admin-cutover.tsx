"use client";

import { useState } from "react";
import { adminPost, dateTime, money } from "@/lib/admin";
import { AdminState, ConfirmAction, Status, useAdminData } from "@/components/admin-ui";

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
  change_note: string;
  started_at: string;
  evaluated_at: string | null;
  ended_at: string | null;
  version: number;
};

type CutoverStatus = {
  root_mode: string;
  adapter_enabled: boolean;
  production_defaults: Record<string, number>;
  current: CutoverObservation | null;
};

function metricLabel(code: string): string {
  return code.replaceAll("_", " ");
}

export function CutoverEvidence({ observation }: { observation: CutoverObservation }) {
  const metrics = observation.result.metrics || [];
  const distributions = observation.result.distributions || {};
  return <section className="admin-panel" aria-labelledby="cutover-evidence-title">
    <div className="admin-panel-head">
      <div><h2 id="cutover-evidence-title">V2 stable window</h2><p className="admin-muted">Started {dateTime(observation.started_at)} in {observation.environment}.</p></div>
      <div className="admin-actions"><Status value={observation.status} />{observation.test_evidence && <span className="admin-eyebrow">TEST EVIDENCE</span>}</div>
    </div>
    {observation.result.blockers?.length ? <div className="admin-banner admin-banner-warning" role="status">Blocked by {observation.result.blockers.map(metricLabel).join(", ")}.</div> : observation.result.ready && <div className="admin-banner admin-banner-good" role="status">Every snapshotted threshold passed.</div>}
    <div className="admin-table-scroll"><table className="admin-table"><caption className="sr-only">Cutover stability thresholds</caption><thead><tr><th>Measure</th><th>Observed</th><th>Threshold</th><th>State</th></tr></thead><tbody>{metrics.map(metric => <tr key={metric.code}><td>{metricLabel(metric.code)}</td><td>{String(metric.observed_value ?? "unavailable")} {metric.unit}</td><td>{String(metric.threshold ?? "none")} {metric.unit}</td><td><Status value={metric.passed ? "passed" : "blocked"} /></td></tr>)}</tbody></table></div>
    <p className="admin-muted">Observed tokens {(distributions.total_tokens || 0).toLocaleString()} · p95 tokens {(distributions.p95_tokens_per_run || 0).toLocaleString()} against {observation.thresholds.public_run_token_cap?.toLocaleString() || "unavailable"} per run · observed cost {money(distributions.total_cost_microusd || 0)} · p95 cost {money(distributions.p95_cost_microusd_per_run)} against {money(observation.thresholds.public_run_cost_cap_microusd)} per run.</p>
  </section>;
}

export function AdminCutoverControls() {
  const cutover = useAdminData<CutoverStatus>("/cutover");
  const [message, setMessage] = useState("");
  async function mutate(path: string, body: object) {
    setMessage("");
    try {
      await adminPost(path, body);
      await cutover.reload();
      setMessage("Cutover record updated and audited.");
    } catch (value) {
      setMessage(value instanceof Error ? value.message : "The cutover record could not be updated.");
      throw value;
    }
  }
  const observation = cutover.data?.current;
  return <section aria-labelledby="cutover-controls-title">
    <AdminState loading={cutover.loading} error={cutover.error} empty={!cutover.data}>
      {cutover.data && <>
        <div className="admin-panel">
          <div className="admin-panel-head"><div><h2 id="cutover-controls-title">Public V2 cutover</h2><p className="admin-muted">Runtime root: {cutover.data.root_mode} · compatibility adapter: {cutover.data.adapter_enabled ? "enabled" : "disabled"}</p></div><div className="admin-actions">
            {observation?.status !== "observing" && <ConfirmAction label="Start observation" title="Start stable-window observation" description="Snapshot the configured thresholds and begin collecting production cutover evidence." confirmation="start cutover observation" onConfirm={() => mutate("/cutover/observations", { confirmation: "start cutover observation", change_note: "Phase 11 stable-window observation" })} />}
            {observation?.status === "observing" && <ConfirmAction label="Evaluate now" title="Evaluate stable-window evidence" description="Recompute every threshold from authoritative run, report, compatibility, usage, and budget records." confirmation="evaluate cutover observation" onConfirm={() => mutate(`/cutover/observations/${observation.id}/evaluate`, { confirmation: "evaluate cutover observation", expected_version: observation.version })} />}
            {observation?.status === "observing" && <ConfirmAction label="Record rollback" title="Record public rollback" description="The emergency kill switch must already be active. This closes the observation without changing run snapshots." confirmation="record cutover rollback" danger onConfirm={() => mutate(`/cutover/observations/${observation.id}/record-rollback`, { confirmation: "record cutover rollback", expected_version: observation.version })} />}
          </div></div>
          {message && <div className="admin-banner" role="status">{message}</div>}
          {!observation && <p className="admin-muted">No observation has been recorded for this environment.</p>}
        </div>
        {observation && <CutoverEvidence observation={observation} />}
      </>}
    </AdminState>
  </section>;
}
