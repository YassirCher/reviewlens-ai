"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, GitCompareArrows, RotateCcw, Save, ShieldCheck } from "lucide-react";
import { adminGet, adminPost, adminPut, newJobKey, type AdminJob, type Definition, type Page, type Version } from "@/lib/admin";
import { AdminHeading, AdminState, ConfirmAction, Status, useAdminData } from "@/components/admin-ui";
import { WorkflowGraph } from "@/components/admin-graph";

export type ConfigKind = "agents" | "workflows" | "model-policies" | "embedding-policies" | "budget-policies";
const labels: Record<ConfigKind, string> = { agents: "Agents", workflows: "Workflows", "model-policies": "Model policies", "embedding-policies": "Embedding policies", "budget-policies": "Budget policies" };

export function ConfigurationList({ kind, onSelect }: { kind: ConfigKind; onSelect?: (id: string) => void }) {
  const { data, loading, error, reload } = useAdminData<Page<Definition>>(`/configuration/${kind}`);
  const [selected, setSelected] = useState<string | null>(null);
  const open = onSelect || setSelected;
  if (selected) return <ConfigurationDetail kind={kind} id={selected} onBack={() => { setSelected(null); void reload(); }} />;
  return <section className="admin-panel"><div className="admin-panel-head"><h2>{labels[kind]}</h2><button className="admin-secondary" onClick={() => void reload()}>Refresh</button></div><AdminState loading={loading} error={error} empty={data?.items.length === 0}>{data?.items.map(item => <article className="admin-definition" key={item.id}><h3>{item.name}</h3><p>{item.description}</p><div className="admin-version-stack"><span className="admin-mono">{item.key}</span>{item.versions.slice(0, 4).map(version => <span key={version.id}><Status value={version.lifecycle} /> {version.active && <span className="admin-eyebrow">ACTIVE</span>} <span className="admin-muted">v{version.number}</span></span>)}<button onClick={() => open(item.id)}>Open versions</button></div></article>)}</AdminState></section>;
}

type Detail = { id: string; key: string; name: string; description: string; versions: Version[]; active_versions: Record<string, string | null> };
export function ConfigurationDetail({ kind, id, onBack }: { kind: ConfigKind; id: string; onBack?: () => void }) {
  const { data, loading, error, reload } = useAdminData<Detail>(`/configuration/${kind}/${id}`);
  const [selected, setSelected] = useState<string | null>(null);
  const [editorOverride, setEditorOverride] = useState<{ id: string; value: string } | null>(null);
  const [noteOverride, setNoteOverride] = useState<{ id: string; value: string } | null>(null);
  const [message, setMessage] = useState(""); const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<AdminJob | null>(null);
  const version = useMemo(() => data?.versions.find(value => value.id === selected) || data?.versions[0], [data, selected]);
  const published = useMemo(() => data?.versions.find(value => value.lifecycle === "published"), [data]);
  const editor = editorOverride && editorOverride.id === version?.id ? editorOverride.value : JSON.stringify(version?.payload || {}, null, 2);
  const note = noteOverride && noteOverride.id === version?.id ? noteOverride.value : version?.change_note || "";
  const setEditor = (value: string) => setEditorOverride({ id: version?.id || "", value });
  const setNote = (value: string) => setNoteOverride({ id: version?.id || "", value });
  useEffect(() => {
    if (!job || !["queued", "running"].includes(job.status)) return;
    const timer = setInterval(() => { adminGet<AdminJob>(`/jobs/${job.id}`).then(next => { setJob(next); if (!["queued", "running"].includes(next.status)) void reload(); }).catch(() => undefined); }, 2500);
    return () => clearInterval(timer);
  }, [job, reload]);
  const base = version ? `/configuration/${kind}/${id}/versions/${version.id}` : "";
  async function action(work: () => Promise<unknown>, success: string) {
    setBusy(true); setMessage("");
    try { await work(); setMessage(success); setEditorOverride(null); setNoteOverride(null); await reload(); } catch (value) { setMessage(value instanceof Error ? value.message : "Action failed."); }
    finally { setBusy(false); }
  }
  async function makeDraft(source: Version) {
    await action(async () => { const created = await adminPost<Version>(`/configuration/${kind}/${id}/drafts`, { source_version_id: source.id, change_note: `Rollback or fork from v${source.version_number}` }); setSelected(created.id); }, "New draft created. Review and validate it before publication.");
  }
  return <><AdminHeading eyebrow={`CONFIGURATION / ${labels[kind].toUpperCase()}`} title={data?.name || labels[kind]} description={data?.description || "Inspect and govern versioned configuration."} actions={onBack ? <button className="admin-secondary" onClick={onBack}><ArrowLeft size={16} /> Back</button> : <Link className="admin-secondary" href={`/admin/${kind === "agents" ? "agents" : kind === "workflows" ? "workflows" : "models"}`}><ArrowLeft size={16} /> Back</Link>} /><AdminState loading={loading} error={error} empty={!data}><div className="admin-panel"><div className="admin-panel-head"><h2>Versions</h2><span className="admin-mono admin-muted">{data?.key}</span></div><div className="admin-version-stack">{data?.versions.map(item => <button key={item.id} aria-pressed={version?.id === item.id} onClick={() => setSelected(item.id)}>v{item.version_number} · {item.lifecycle} {item.active ? "· ACTIVE" : ""}</button>)}</div>{version && <><div className="admin-banner">Selected v{version.version_number} <Status value={version.lifecycle} /> · hash <code>{version.content_hash.slice(0, 16)}…</code>{version.active && " · Active for new runs"}</div><div className="admin-actions"><button className="admin-secondary" disabled={busy} onClick={() => void makeDraft(version)}><RotateCcw size={16} /> {version.lifecycle === "published" ? "Rollback as draft" : "Fork draft"}</button>{version.lifecycle === "draft" && <><button className="admin-secondary" disabled={busy} onClick={() => void action(async () => { const payload = JSON.parse(editor); await adminPut(base, { expected_version: version.version, payload, change_note: note || "Updated draft" }); }, "Draft saved.")}><Save size={16} /> Save draft</button><button className="admin-secondary" disabled={busy} onClick={() => void action(async () => { const result = await adminPost<{ valid: boolean; errors?: string[] }>(`${base}/validate`, { acknowledge_stale: false }); setMessage(result.valid ? "Validation passed." : `Validation failed: ${result.errors?.join(", ")}`); }, "Validation finished.")}><ShieldCheck size={16} /> Validate</button>{kind === "agents" && <ConfirmAction label="Run evaluation" title="Run live golden evaluation" description="This uses the published OpenRouter model policy and consumes the shared evaluation budget. The default total cap is $0.50 and 300,000 tokens." confirmation="run evaluation" onConfirm={async () => { const queued = await adminPost<{ job_id: string }>(`${base}/evaluate`, { confirmation: "run evaluation" }, newJobKey()); setJob({ id: queued.job_id, kind: "agent_evaluation", status: "queued", safe_result: {}, error_code: null }); }} />}<ConfirmAction label="Publish" title="Publish draft" description="A published row becomes immutable. Publishing alone will not change the active workflow." confirmation={data?.key || "publish"} onConfirm={async () => { await adminPost(base + "/publish", { acknowledge_stale: false }); await reload(); }} /></>}{version.lifecycle === "published" && ["workflows", "budget-policies", "embedding-policies"].includes(kind) && <ConfirmAction label="Activate" title="Activate published version" description="New runs will use this version. Existing runs retain their snapshots." confirmation={data?.key || "activate"} onConfirm={async () => { await adminPost(base + "/activate", { confirmation: data?.key, expected_active_version_id: data?.active_versions[kind] || null }); await reload(); }} />}</div>{message && <div className="admin-banner" role="status">{message}</div>}{job && <div className="admin-banner" role="status">Evaluation job: {job.status}{job.safe_result?.status ? ` · ${job.safe_result.status}` : ""}{job.error_code ? ` · ${job.error_code}` : ""}</div>}<div className="admin-two-col"><div><h3>Version payload {version.lifecycle === "draft" ? "· editable" : "· read only"}</h3>{version.lifecycle === "draft" ? <><label className="admin-field">Change note<input value={note} onChange={event => setNote(event.target.value)} /></label><label className="admin-field">Configuration JSON<textarea value={editor} onChange={event => setEditor(event.target.value)} spellCheck={false} /></label></> : <pre className="admin-code">{JSON.stringify(version.payload, null, 2)}</pre>}</div><div><h3><GitCompareArrows size={17} aria-hidden="true" /> Published comparison</h3>{published && published.id !== version.id ? <pre className="admin-code">{JSON.stringify(published.payload, null, 2)}</pre> : <p className="admin-muted">This is the latest published version.</p>}{version.evaluation && <><h3>Evaluation</h3><pre className="admin-code">{JSON.stringify(version.evaluation, null, 2)}</pre></>}</div></div></>}</div></AdminState>{kind === "workflows" && version?.payload.dag && <section className="admin-panel"><h2>Workflow DAG</h2><WorkflowGraph dag={version.payload.dag as Record<string, unknown>} /></section>}</>;
}
