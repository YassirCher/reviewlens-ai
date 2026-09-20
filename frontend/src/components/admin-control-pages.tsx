"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { Download, RefreshCw } from "lucide-react";
import { adminGet, adminPost, adminPut, dateTime, money, newJobKey, type AdminJob, type Page } from "@/lib/admin";
import { AdminHeading, AdminState, ConfirmAction, DataTable, Status, useAdminData } from "@/components/admin-ui";
import { ConfigurationList } from "@/components/admin-configurations";
import { AdminGraph } from "@/components/admin-graph";
import { AdminCutoverControls } from "@/components/admin-cutover";

type CatalogModel = { slug: string; name: string; author: string; context_length: number | null; pricing: Record<string, string>; available: boolean; supported_parameters: string[]; output_modalities: string[] };
type CatalogPage = Page<CatalogModel> & { status: string; stale: boolean; fetched_at: string | null; last_error_category: string | null; total_count: number };
type Endpoint = { provider_slug: string; provider_name: string; context_length: number | null; pricing: Record<string, string>; supported_parameters: string[]; privacy: Record<string, unknown>; status: string; eligible?: boolean | null; eligibility_reasons?: string[] };
type Provider = { slug: string; name: string; privacy: Record<string, unknown>; status: string | null };
type ProviderPage = { status: string; stale: boolean; fetched_at: string | null; last_error_category: string | null; items: Provider[] };
type ProviderDetail = Provider & { metadata: Record<string, unknown>; fetched_at: string };
export function AdminModels() {
  const search = useSearchParams(); const router = useRouter(); const q = search.get("q") || "";
  const modality = search.get("modality") || "chat"; const [query, setQuery] = useState(q);
  const [selected, setSelected] = useState<string | null>(null); const [selectedProvider, setSelectedProvider] = useState<string | null>(null);
  const [job, setJob] = useState<AdminJob | null>(null);
  const [message, setMessage] = useState(""); const [cursor, setCursor] = useState<string | null>(null);
  const params = new URLSearchParams({ modality, limit: "25" }); if (q) params.set("q", q); if (cursor) params.set("cursor", cursor);
  const filterNames = ["author", "provider", "capability", "min_context", "max_price", "availability", "sort"] as const;
  for (const name of filterNames) if (search.get(name)) params.set(name, search.get(name)!);
  const modelFilters = Object.fromEntries(["modality", ...filterNames].map(name => [name, search.get(name) || ""])) as Record<string, string>;
  const { data, loading, error, reload } = useAdminData<CatalogPage>(`/models?${params}`);
  const { data: endpointData, loading: endpointLoading, error: endpointError } = useAdminData<{ status: string; stale: boolean; endpoints: Endpoint[] }>(selected ? `/models/${selected}/endpoints` : null);
  const providers = useAdminData<ProviderPage>("/providers");
  const provider = useAdminData<ProviderDetail>(selectedProvider ? `/providers/${selectedProvider}` : null);
  useEffect(() => { if (!job || !["queued", "running"].includes(job.status)) return; const timer = setInterval(() => adminGet<AdminJob>(`/jobs/${job.id}`).then(value => { setJob(value); if (!["queued", "running"].includes(value.status)) void reload(); }).catch(() => undefined), 2500); return () => clearInterval(timer); }, [job, reload]);
  useEffect(() => { if (query === q) return; const timer = setTimeout(() => { const next = new URLSearchParams(search.toString()); if (query) next.set("q", query); else next.delete("q"); setCursor(null); router.replace(`/admin/models?${next}`); }, 250); return () => clearTimeout(timer); }, [query, q, router, search]);
  function update(name: string, value: string) { const next = new URLSearchParams(search.toString()); if (value) next.set(name, value); else next.delete(name); setCursor(null); router.replace(`/admin/models?${next}`); }
  const columns: ColumnDef<CatalogModel, unknown>[] = [
    { accessorKey: "name", header: "Model", cell: cell => <button onClick={() => setSelected(cell.row.original.slug)}>{cell.row.original.name}<br /><small>{cell.row.original.slug}</small></button> },
    { accessorKey: "author", header: "Author" }, { accessorKey: "context_length", header: "Context", cell: cell => cell.row.original.context_length?.toLocaleString() || "—" },
    { id: "price", header: "Prompt / 1M", cell: cell => cell.row.original.pricing?.prompt ? `$${(Number(cell.row.original.pricing.prompt) * 1_000_000).toFixed(2)}` : "—" },
    { accessorKey: "available", header: "Available", cell: cell => cell.row.original.available ? "Yes" : "No" },
  ];
  return <><AdminHeading eyebrow="ROUTING / CATALOG" title="Models and providers" description="Search live catalog snapshots. Browsing does not change production routing." actions={<button className="admin-secondary" onClick={async () => { try { const value = await adminPost<{ job_id: string }>("/models/refresh", {}, newJobKey()); setJob({ id: value.job_id, kind: "catalog_refresh", status: "queued", safe_result: {}, error_code: null }); } catch (value) { setMessage(value instanceof Error ? value.message : "Refresh failed."); } }}><RefreshCw size={15} /> Refresh catalog</button>} />{data?.stale && <div className="admin-banner admin-banner-warning" role="status">Catalog is stale. {data.last_error_category && `Last refresh: ${data.last_error_category}.`}</div>}{job && <div className="admin-banner" role="status">Refresh job: {job.status}{job.error_code ? ` · ${job.error_code}` : ""}</div>}{message && <div className="admin-banner admin-banner-error" role="alert">{message}</div>}<section className="admin-panel"><ModelFilterBar query={query} onQuery={setQuery} filters={modelFilters} onUpdate={update} count={data?.total_count ?? 0} /><AdminState loading={loading} error={error} empty={data?.items.length === 0}><DataTable data={data?.items || []} columns={columns} caption="OpenRouter model catalog" /><div className="admin-pager"><button disabled={!cursor} onClick={() => setCursor(null)}>First page</button><button disabled={!data?.next_cursor} onClick={() => setCursor(data?.next_cursor || null)}>Next page</button></div></AdminState></section>{selected && <section className="admin-panel"><div className="admin-panel-head"><h2>{selected} endpoints</h2><button className="admin-secondary" onClick={() => setSelected(null)}>Close</button></div><AdminState loading={endpointLoading} error={endpointError} empty={endpointData?.endpoints.length === 0}>{endpointData?.stale && <div className="admin-banner admin-banner-warning">Endpoint data is stale.</div>}{endpointData?.endpoints.map((item, index) => <article className="admin-definition" key={`${item.provider_slug}-${index}`}><h3><button onClick={() => setSelectedProvider(item.provider_slug)}>{item.provider_name || item.provider_slug}</button> <Status value={item.status || "unknown"} /> {item.eligible === true ? <span className="admin-eyebrow"> ELIGIBLE</span> : <span className="admin-muted"> {item.eligibility_reasons?.join(", ") || "Eligibility unknown"}</span>}</h3><p>Context {item.context_length?.toLocaleString() || "—"} · parameters {item.supported_parameters?.join(", ") || "—"}</p><pre className="admin-code">{JSON.stringify({ pricing: item.pricing, privacy: item.privacy }, null, 2)}</pre></article>)}</AdminState></section>}<section className="admin-panel"><div className="admin-panel-head"><h2>Providers</h2><span className="admin-muted">Snapshot {providers.data?.status || "unavailable"}</span></div>{providers.data?.stale && <div className="admin-banner admin-banner-warning" role="status">Provider catalog is stale. {providers.data.last_error_category || "Refresh required."}</div>}<AdminState loading={providers.loading} error={providers.error} empty={providers.data?.items.length === 0}><ul className="admin-list">{providers.data?.items.map(item => <li key={item.slug}><div><strong>{item.name}</strong><br /><span className="admin-muted">{item.slug}</span></div><div className="admin-actions"><Status value={item.status || "unknown"} /><button className="admin-secondary" onClick={() => setSelectedProvider(item.slug)}>Details</button></div></li>)}</ul></AdminState>{selectedProvider && <div className="admin-definition"><div className="admin-panel-head"><h3>{provider.data?.name || selectedProvider}</h3><button className="admin-secondary" onClick={() => setSelectedProvider(null)}>Close</button></div><AdminState loading={provider.loading} error={provider.error} empty={!provider.data}>{provider.data && <pre className="admin-code">{JSON.stringify({ privacy: provider.data.privacy, metadata: provider.data.metadata, fetched_at: provider.data.fetched_at }, null, 2)}</pre>}</AdminState></div>}</section><ConfigurationList kind="model-policies" /><ConfigurationList kind="embedding-policies" /><ConfigurationList kind="budget-policies" /></>;
}

function ModelFilterBar({ query, onQuery, filters, onUpdate, count }: {
  query: string; onQuery: (value: string) => void; filters: Record<string, string>;
  onUpdate: (name: string, value: string) => void; count: number;
}) {
  return <form className="admin-filter" onSubmit={event => { event.preventDefault(); onUpdate("q", query); }}>
    <label>Search<input value={query} onChange={event => onQuery(event.target.value)} placeholder="Name or slug" /></label>
    <label>Type<select value={filters.modality || "chat"} onChange={event => onUpdate("modality", event.target.value)}><option value="chat">Chat</option><option value="embedding">Embedding</option></select></label>
    <label>Author<input value={filters.author || ""} onChange={event => onUpdate("author", event.target.value)} placeholder="e.g. deepseek" /></label>
    <label>Provider<input value={filters.provider || ""} onChange={event => onUpdate("provider", event.target.value)} placeholder="Provider slug" /></label>
    <label>Capability<select value={filters.capability || ""} onChange={event => onUpdate("capability", event.target.value)}><option value="">Any</option><option value="response_format">Structured output</option><option value="tools">Tools</option></select></label>
    <label>Minimum context<input type="number" min="0" value={filters.min_context || ""} onChange={event => onUpdate("min_context", event.target.value)} placeholder="Tokens" /></label>
    <label>Max prompt $ / 1M<input type="number" min="0" step="0.01" value={filters.max_price || ""} onChange={event => onUpdate("max_price", event.target.value)} placeholder="USD" /></label>
    <label>Availability<select value={filters.availability || "available"} onChange={event => onUpdate("availability", event.target.value)}><option value="available">Available</option><option value="all">All snapshots</option></select></label>
    <label>Sort<select value={filters.sort || "name"} onChange={event => onUpdate("sort", event.target.value)}><option value="name">Name</option><option value="context">Context</option><option value="prompt_price">Prompt price</option></select></label>
    <button className="admin-secondary">Apply</button>
    <span className="admin-muted" role="status" aria-live="polite">{count} models match</span>
  </form>;
}

type Tool = { id: string; key: string; name: string; versions: { id: string; number: number; semantic_version: string; lifecycle: string; capability_metadata: Record<string, unknown> }[] };
type Invocation = { id: string; run_id: string; tool_key: string; status: string; error_code: string | null; duration_ms: number | null; created_at: string; input_hash: string; output_hash: string | null };
export function AdminTools() {
  const tools = useAdminData<{ items: Tool[] }>("/tools");
  const audits = useAdminData<Page<Invocation>>("/tool-invocations");
  const columns: ColumnDef<Invocation, unknown>[] = [
    { accessorKey: "tool_key", header: "Tool" }, { accessorKey: "status", header: "Status", cell: cell => <Status value={cell.row.original.status} /> },
    { accessorKey: "run_id", header: "Run", cell: cell => <Link href={`/admin/runs/${cell.row.original.run_id}`}>{cell.row.original.run_id.slice(0, 8)}…</Link> },
    { accessorKey: "duration_ms", header: "Latency", cell: cell => `${cell.row.original.duration_ms ?? "—"} ms` },
    { accessorKey: "error_code", header: "Error" }, { accessorKey: "created_at", header: "Created", cell: cell => dateTime(cell.row.original.created_at) },
  ];
  return <><AdminHeading eyebrow="CAPABILITIES / TOOLS" title="Tools and audits" description="Published tool capabilities and recorded invocations. Raw tool input stays out of the admin list." /><section className="admin-panel"><h2>Tool registry</h2><AdminState loading={tools.loading} error={tools.error} empty={tools.data?.items.length === 0}>{tools.data?.items.map(item => <article className="admin-definition" key={item.id}><h3>{item.name} <span className="admin-mono admin-muted">{item.key}</span></h3>{item.versions.map(version => <div key={version.id} className="admin-version-stack"><Status value={version.lifecycle} /><span>v{version.number} · {version.semantic_version}</span><span className="admin-muted">Roles: {(version.capability_metadata.allowed_roles as string[] || []).join(", ")}</span></div>)}</article>)}</AdminState></section><section className="admin-panel"><div className="admin-panel-head"><h2>Recent invocations</h2><button className="admin-secondary" onClick={() => void audits.reload()}>Refresh</button></div><AdminState loading={audits.loading} error={audits.error} empty={audits.data?.items.length === 0}><DataTable data={audits.data?.items || []} columns={columns} caption="Tool invocation audits" /></AdminState></section></>;
}

type Workspace = { id: string; run_id: string; status: string; neo4j_status: string; qdrant_status: string; projection_error_code: string | null; created_at: string };
type GraphNode = { id: string; node_type: string; title: string; current_version_id: string; status: string; body_hash: string; trust_level: string; created_at: string };
type GraphEdge = { id: string; source_version_id: string; target_version_id: string; relation_type: string; confidence: number; status: string };
export function AdminKnowledge() {
  const search = useSearchParams(); const router = useRouter();
  const selected = search.get("workspace"); const [nodeId, setNodeId] = useState<string | null>(null);
  const [message, setMessage] = useState(""); const [job, setJob] = useState<AdminJob | null>(null);
  const workspaces = useAdminData<Page<Workspace>>("/workspaces");
  const nodes = useAdminData<Page<GraphNode>>(selected ? `/workspaces/${selected}/nodes?limit=100` : "/workspaces");
  const edges = useAdminData<Page<GraphEdge>>(selected ? `/workspaces/${selected}/edges?limit=100` : "/workspaces");
  const node = useAdminData<{ id: string; versions: { id: string; title: string; body_hash: string }[] }>(selected && nodeId ? `/workspaces/${selected}/nodes/${nodeId}` : "/workspaces");
  const [body, setBody] = useState("");
  useEffect(() => { if (!selected || !node.data || !("versions" in node.data)) return; const current = node.data.versions[0]; if (current) adminGet<{ body: string }>(`/workspaces/${selected}/nodes/${nodeId}/versions/${current.id}/body`).then(value => setBody(value.body)).catch(() => setBody("Body unavailable.")); }, [selected, nodeId, node.data]);
  useEffect(() => { if (!job || !["queued", "running"].includes(job.status)) return; const timer = setInterval(() => adminGet<AdminJob>(`/jobs/${job.id}`).then(setJob).catch(() => undefined), 2500); return () => clearInterval(timer); }, [job]);
  const versionToNode = new Map((nodes.data?.items || []).map(item => [item.current_version_id, item.id]));
  const links = (edges.data?.items || []).map(item => ({ source: versionToNode.get(item.source_version_id) || "", target: versionToNode.get(item.target_version_id) || "", label: item.relation_type }));
  function selectWorkspace(id: string) { setNodeId(null); router.replace(`/admin/knowledge?workspace=${id}`); }
  async function startJob(action: string) { if (!selected) return; try { const result = await adminPost<{ job_id: string }>(`/workspaces/${selected}/${action}`, {}, newJobKey()); setJob({ id: result.job_id, kind: action, status: "queued", safe_result: {}, error_code: null }); } catch (value) { setMessage(value instanceof Error ? value.message : "Action failed."); } }
  return <><AdminHeading eyebrow="EVIDENCE / GRAPH" title="Knowledge workspaces" description="Inspect PostgreSQL canonical nodes and relations. Projection health is shown per workspace." actions={selected ? <div className="admin-actions"><ConfirmAction label="Rebuild graph projection" title="Rebuild Neo4j projection" description="Recreate the graph projection from canonical workspace records." confirmation="rebuild graph" onConfirm={() => startJob("rebuild-neo4j")} /><button className="admin-secondary" onClick={() => void startJob("export")}><Download size={15} /> Export workspace</button></div> : undefined} />{job && <div className="admin-banner" role="status">{job.kind}: {job.status}{job.status === "succeeded" && job.kind === "export" && <a href={`${process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000"}/api/v2/admin/jobs/${job.id}/download`}> Download ZIP</a>}{job.error_code && ` · ${job.error_code}`}</div>}{message && <div className="admin-banner admin-banner-error" role="alert">{message}</div>}<section className="admin-panel"><h2>Workspaces</h2><AdminState loading={workspaces.loading} error={workspaces.error} empty={workspaces.data?.items.length === 0}><ul className="admin-list">{workspaces.data?.items.map(item => <li key={item.id}><div><button className="admin-secondary" onClick={() => selectWorkspace(item.id)}>{item.id.slice(0, 8)}…</button> <Link href={`/admin/runs/${item.run_id}`}>Run {item.run_id.slice(0, 8)}…</Link></div><span className="admin-muted">Neo4j: {item.neo4j_status} · Qdrant: {item.qdrant_status}</span></li>)}</ul></AdminState></section>{selected && <><section className="admin-panel"><div className="admin-panel-head"><h2>Workspace graph</h2><span className="admin-muted">{selected}</span></div><AdminState loading={nodes.loading || edges.loading} error={nodes.error || edges.error} empty={nodes.data?.items.length === 0}><AdminGraph title="workspace graph" items={(nodes.data?.items || []).map(item => ({ id: item.id, label: item.title, kind: item.node_type }))} links={links} onSelect={setNodeId} /></AdminState></section>{nodeId && <section className="admin-panel"><h2>Node inspector</h2><AdminState loading={node.loading} error={node.error} empty={!node.data}>{node.data && "versions" in node.data && <><p className="admin-muted">{nodeId} · {node.data.versions.length} versions</p><pre className="admin-code">{body}</pre><p className="admin-muted">This content is untrusted source material. Inspect provenance before using it.</p></>}</AdminState></section>}</>}</>;
}

type SettingsVersion = { id: string; number: number; version: number; lifecycle: string; catalog_refresh_minutes: number; raw_content_retention: boolean; raw_content_ttl_hours: number; change_note: string };
type SettingsData = { active_version_id: string | null; kill_switch: boolean; public_analysis_enabled: boolean; evaluation_budget: { token_limit: number; cost_limit_microusd: number; reserved_tokens: number; consumed_tokens: number; reserved_cost_microusd: number; consumed_cost_microusd: number }; versions: SettingsVersion[] };
type Session = { id: string; created_at: string; last_seen_at: string; expires_at: string; revoked_at: string | null; current: boolean };
function SettingsDraftControls({ draft, onDone }: { draft: SettingsVersion; onDone: () => Promise<unknown> }) {
  const [interval, setInterval] = useState(String(draft.catalog_refresh_minutes));
  const [retention, setRetention] = useState(draft.raw_content_retention);
  const [note, setNote] = useState(draft.change_note || "Settings draft edit");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true); setMessage("");
    try {
      await adminPut(`/settings/versions/${draft.id}`, { expected_version: draft.version,
        catalog_refresh_minutes: Number(interval), raw_content_retention: retention,
        raw_content_ttl_hours: 24, change_note: note });
      await onDone(); setMessage("Draft saved.");
    } catch (value) { setMessage(value instanceof Error ? value.message : "Draft could not be saved."); }
    finally { setBusy(false); }
  }
  return <div className="admin-settings-draft"><label>Catalog interval (minutes)<input type="number" min="1" max="1440" value={interval} onChange={event => setInterval(event.target.value)} /></label><label>Change note<input value={note} onChange={event => setNote(event.target.value)} /></label><label><input type="checkbox" checked={retention} onChange={event => setRetention(event.target.checked)} /> Retain encrypted raw content</label><button className="admin-secondary" disabled={busy} onClick={() => void save()}>Save draft</button>{message && <span role="status">{message}</span>}</div>;
}
export function AdminSettings() {
  const settings = useAdminData<SettingsData>("/settings"); const sessions = useAdminData<Page<Session>>("/sessions");
  const [tokensOverride, setTokensOverride] = useState<string | null>(null);
  const [costOverride, setCostOverride] = useState<string | null>(null);
  const [intervalOverride, setIntervalOverride] = useState<string | null>(null);
  const [retentionOverride, setRetentionOverride] = useState<boolean | null>(null);
  const [message, setMessage] = useState("");
  const current = settings.data?.versions.find(item => item.id === settings.data?.active_version_id);
  const tokens = tokensOverride ?? String(settings.data?.evaluation_budget.token_limit ?? 300000);
  const cost = costOverride ?? String((settings.data?.evaluation_budget.cost_limit_microusd ?? 500000) / 1_000_000);
  const interval = intervalOverride ?? String(current?.catalog_refresh_minutes ?? 15);
  const retention = retentionOverride ?? Boolean(current?.raw_content_retention);
  const setTokens = setTokensOverride; const setCost = setCostOverride;
  const setIntervalValue = setIntervalOverride; const setRetention = setRetentionOverride;
  async function run(work: () => Promise<unknown>) { setMessage(""); try { await work(); setMessage("Setting saved and audited."); setTokensOverride(null); setCostOverride(null); setIntervalOverride(null); setRetentionOverride(null); await settings.reload(); await sessions.reload(); } catch (value) { setMessage(value instanceof Error ? value.message : "Setting could not be saved."); } }
  return <><AdminHeading eyebrow="CONTROL / SETTINGS" title="Operational settings" description="Published settings, shared evaluation budget, emergency stop, and administrator sessions." />{message && <div className="admin-banner" role="status">{message}</div>}<AdminState loading={settings.loading} error={settings.error} empty={!settings.data}>{settings.data && <><div className="admin-two-col"><section className="admin-panel"><h2>Emergency kill switch</h2><p className="admin-muted">Blocks new paid OpenRouter calls while preserving existing run records.</p><p><Status value={settings.data.kill_switch ? "active" : "inactive"} /></p><ConfirmAction label={settings.data.kill_switch ? "Disable kill switch" : "Enable kill switch"} title="Change emergency kill switch" description="Confirm this audited production routing control." confirmation={settings.data.kill_switch ? "disable kill switch" : "enable kill switch"} danger={!settings.data.kill_switch} onConfirm={() => run(() => adminPut("/settings/kill-switch", { enabled: !settings.data?.kill_switch, confirmation: settings.data?.kill_switch ? "disable kill switch" : "enable kill switch" }))} /></section><section className="admin-panel"><h2>Evaluation budget</h2><p className="admin-muted">One shared cap for all admin evaluations. Spent {settings.data.evaluation_budget.consumed_tokens.toLocaleString()} tokens and {money(settings.data.evaluation_budget.consumed_cost_microusd)}; reserved {settings.data.evaluation_budget.reserved_tokens.toLocaleString()} tokens and {money(settings.data.evaluation_budget.reserved_cost_microusd)}.</p><div className="admin-filter"><label>Token cap<input type="number" min="0" value={tokens} onChange={event => setTokens(event.target.value)} /></label><label>Cost cap (USD)<input type="number" min="0" step="0.01" value={cost} onChange={event => setCost(event.target.value)} /></label></div><ConfirmAction label="Update evaluation cap" title="Change shared evaluation cap" description="This applies to every future admin evaluation. It cannot be reduced below committed usage." confirmation="change evaluation cap" onConfirm={() => run(() => adminPut("/settings/evaluation-budget", { token_limit: Number(tokens), cost_limit_microusd: Math.round(Number(cost) * 1_000_000), confirmation: "change evaluation cap" }))} /></section></div><section className="admin-panel"><h2>Versioned system settings</h2><p className="admin-muted">Active version: {current ? `v${current.number}` : "none"}. Raw prompt retention is disabled by default and requires an encryption key when enabled. Retained content expires within 24 hours.</p><div className="admin-filter"><label>Catalog refresh minutes<input type="number" min="1" max="1440" value={interval} onChange={event => setIntervalValue(event.target.value)} /></label><label style={{ display: "flex", alignItems: "center", gap: 8 }}>Retain encrypted raw content<input type="checkbox" checked={retention} onChange={event => setRetention(event.target.checked)} /></label></div><button className="admin-secondary" disabled={!current} onClick={() => void run(async () => { await adminPost("/settings/drafts", { source_version_id: current?.id, catalog_refresh_minutes: Number(interval), raw_content_retention: retention, raw_content_ttl_hours: 24, change_note: "Admin settings update" }); })}>Create settings draft</button><ul className="admin-list">{settings.data.versions.map(item => <li key={item.id}><div>v{item.number} <Status value={item.lifecycle} /> {item.id === settings.data?.active_version_id && <span className="admin-eyebrow"> ACTIVE</span>}<p className="admin-muted">Catalog every {item.catalog_refresh_minutes}m · raw retention {item.raw_content_retention ? "on" : "off"}</p></div><div className="admin-actions">{item.lifecycle === "draft" && <SettingsDraftControls draft={item} onDone={() => settings.reload()} />}{item.lifecycle === "draft" && <ConfirmAction label="Publish" title="Publish settings" description="Publication freezes the settings row. Activation remains separate." confirmation="publish settings" onConfirm={() => run(() => adminPost(`/settings/versions/${item.id}/publish`, {}))} />}{item.lifecycle === "published" && item.id !== settings.data?.active_version_id && <ConfirmAction label="Activate" title="Activate settings" description="The scheduler and retention policy will read this published version." confirmation="activate settings" onConfirm={() => run(() => adminPost(`/settings/versions/${item.id}/activate`, { confirmation: "activate settings", expected_active_version_id: settings.data?.active_version_id }))} />}</div></li>)}</ul></section></>}</AdminState><AdminCutoverControls /><section className="admin-panel"><h2>Sessions</h2><AdminState loading={sessions.loading} error={sessions.error} empty={sessions.data?.items.length === 0}><ul className="admin-list">{sessions.data?.items.map(item => <li key={item.id}><div><span className="admin-mono">{item.id.slice(0, 8)}…</span> {item.current && <span className="admin-eyebrow">CURRENT</span>}<p className="admin-muted">Created {dateTime(item.created_at)} · expires {dateTime(item.expires_at)}</p></div>{item.revoked_at ? <Status value="revoked" /> : <ConfirmAction label="Revoke" title="Revoke admin session" description="This device will need to sign in again." confirmation="revoke session" danger onConfirm={() => run(() => adminPost(`/sessions/${item.id}/revoke`, {}))} />}</li>)}</ul></AdminState></section></>;
}

type Audit = { id: string; actor_type: string; action: string; target_type: string; target_id: string | null; before_hash: string | null; after_hash: string | null; created_at: string; safe_metadata: Record<string, unknown> };
export function AdminAudit() {
  const search = useSearchParams(); const router = useRouter(); const action = search.get("action") || "";
  const [filter, setFilter] = useState(action); const [cursor, setCursor] = useState<string | null>(null);
  const params = new URLSearchParams({ limit: "25" }); if (action) params.set("action", action); if (cursor) params.set("cursor", cursor);
  const { data, loading, error } = useAdminData<Page<Audit>>(`/audit-events?${params}`);
  const columns: ColumnDef<Audit, unknown>[] = [
    { accessorKey: "created_at", header: "Time", cell: cell => dateTime(cell.row.original.created_at) },
    { accessorKey: "action", header: "Action" }, { accessorKey: "target_type", header: "Target type" },
    { accessorKey: "target_id", header: "Target" }, { accessorKey: "actor_type", header: "Actor" },
    { accessorKey: "after_hash", header: "After hash", cell: cell => cell.row.original.after_hash?.slice(0, 14) || "—" },
  ];
  return <><AdminHeading eyebrow="GOVERNANCE / AUDIT" title="Audit history" description="Content-free receipts for admin changes and recovery actions." /><section className="admin-panel"><form className="admin-filter" onSubmit={event => { event.preventDefault(); setCursor(null); router.replace(`/admin/audit${filter ? `?action=${encodeURIComponent(filter)}` : ""}`); }}><label>Action<input value={filter} onChange={event => setFilter(event.target.value)} placeholder="e.g. configuration.activated" /></label><button className="admin-secondary">Apply</button></form><AdminState loading={loading} error={error} empty={data?.items.length === 0}><DataTable data={data?.items || []} columns={columns} caption="Admin audit history" /><div className="admin-pager"><button disabled={!cursor} onClick={() => setCursor(null)}>First page</button><button disabled={!data?.next_cursor} onClick={() => setCursor(data?.next_cursor || null)}>Next page</button></div></AdminState></section></>;
}
