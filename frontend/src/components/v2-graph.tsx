"use client";

import { useEffect, useMemo, useState } from "react";
import { ArrowRight, ExternalLink, GitBranch, List, Network, RotateCcw } from "lucide-react";
import { getGraphPage, type GraphEdge, type GraphNode, type Report } from "@/lib/v2";

type Filter = "all" | "strengths" | "caveats" | "disagreements" | "source";
const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "All" }, { key: "strengths", label: "Strengths" }, { key: "caveats", label: "Caveats" }, { key: "disagreements", label: "Disagreements" }, { key: "source", label: "Sources" },
];
const TYPES: GraphNode["type"][] = ["product", "finding", "source", "evidence"];

function visible(node: GraphNode, filter: Filter): boolean {
  if (filter === "all") return true;
  if (filter === "source") return node.type === "source" || node.type === "product";
  if (node.type === "product" || node.type === "source") return true;
  if (node.type !== "finding") return false;
  return filter === "strengths" ? node.polarity === "pro" : filter === "caveats" ? node.polarity === "con" : node.polarity === "disagreement";
}

function nodeDetail(report: Report, node: GraphNode): { description: string; source?: string; videoId?: string; seconds?: number | null } {
  if (node.type === "product") return { description: report.summary };
  const source = report.sources.find(item => item.id === node.id || item.video_id === node.video_id);
  if (source) return { description: source.recommendation_summary, source: source.channel, videoId: source.video_id };
  for (const item of report.sources) for (const claim of item.claims) for (const evidence of claim.evidence) if (evidence.id === node.id) return { description: evidence.text, source: item.channel, videoId: item.video_id, seconds: evidence.timestamp_start_seconds };
  const finding = [...report.consensus_pros, ...report.consensus_cons].find(item => item.id === node.id);
  if (finding) return { description: finding.statement, source: `${finding.source_ids.length} supporting sources` };
  const disagreement = report.disagreements.find(item => item.topic === node.label);
  if (disagreement) return { description: `${disagreement.side_a} / ${disagreement.side_b}`, source: "Reviewer disagreement" };
  return { description: node.label };
}

export function V2Graph({ report, token, full = false }: { report: Report; token: string; full?: boolean }) {
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [mode, setMode] = useState<"map" | "list">("map");
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    getGraphPage(token, null, controller.signal).then(page => { setNodes(page.nodes); setEdges(page.edges); setCursor(page.next_cursor); setSelected(page.nodes[0]?.id || null); setError(false); }).catch(() => { if (!controller.signal.aborted) setError(true); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [token]);
  async function more() {
    if (!cursor || loading) return;
    setLoading(true);
    try { const page = await getGraphPage(token, cursor); setNodes(old => [...old, ...page.nodes.filter(item => !old.some(prior => prior.id === item.id))]); setEdges(old => [...old, ...page.edges.filter(item => !old.some(prior => prior.source === item.source && prior.target === item.target && prior.type === item.type))]); setCursor(page.next_cursor); setError(false); }
    catch { setError(true); }
    finally { setLoading(false); }
  }
  const filtered = useMemo(() => nodes.filter(node => visible(node, filter)), [nodes, filter]);
  const visibleIds = useMemo(() => new Set(filtered.map(node => node.id)), [filtered]);
  const relations = useMemo(() => edges.filter(edge => visibleIds.has(edge.source) && visibleIds.has(edge.target)), [edges, visibleIds]);
  const current = filtered.find(node => node.id === selected) || filtered[0] || null;
  const detail = current ? nodeDetail(report, current) : null;
  const columns = TYPES.map(type => filtered.filter(node => node.type === type).slice(0, full ? 7 : 4));
  const positions = new Map<string, { x: number; y: number }>();
  columns.forEach((column, col) => column.forEach((node, row) => positions.set(node.id, { x: 20 + col * 244, y: 52 + row * 92 })));
  const height = Math.max(220, ...columns.map(column => 85 + column.length * 92));

  return <section className="v2-graph-panel" aria-label="Public evidence map"><div className="v2-graph-toolbar"><div role="group" aria-label="Filter evidence map" className="v2-graph-filters">{FILTERS.map(item => <button key={item.key} type="button" aria-pressed={filter === item.key} onClick={() => { setFilter(item.key); setSelected(null); }}>{item.label}</button>)}</div><div role="group" aria-label="Evidence map view" className="v2-view-toggle"><button type="button" aria-pressed={mode === "map"} onClick={() => setMode("map")}><Network size={16} aria-hidden="true" /> Map</button><button type="button" aria-pressed={mode === "list"} onClick={() => setMode("list")}><List size={16} aria-hidden="true" /> List</button></div></div>
    {loading && nodes.length === 0 ? <p className="v2-graph-state" role="status">Loading evidence connections…</p> : error && nodes.length === 0 ? <div className="v2-graph-state" role="alert">The evidence map is temporarily unavailable. <button className="v2-text-link" onClick={() => window.location.reload()}><RotateCcw size={15} /> Retry</button></div> : filtered.length === 0 ? <p className="v2-graph-state">No nodes match this filter.</p> : <div className="v2-graph-content"><div className="v2-graph-main">
      <div className={`v2-map ${mode === "list" ? "v2-map-hidden" : ""}`} role="group" aria-label="Visual evidence relationships; use the list view for keyboard navigation"><div className="v2-map-scroll"><div className="v2-map-plane" style={{ height }}><svg aria-hidden="true" viewBox={`0 0 1000 ${height}`} preserveAspectRatio="none">{relations.filter(edge => positions.has(edge.source) && positions.has(edge.target)).map((edge,index) => { const a = positions.get(edge.source)!, b = positions.get(edge.target)!; return <line key={`${edge.source}-${edge.target}-${index}`} x1={a.x+190} y1={a.y+28} x2={b.x} y2={b.y+28} stroke={edge.type === "CONTRADICTS" ? "#F27D94" : edge.type === "SUPPORTS" ? "#35D0BA" : "#64A8FF"} strokeDasharray={edge.type === "CONTRADICTS" ? "6 5" : undefined} strokeWidth="1.5" opacity=".7" />; })}</svg>{columns.flatMap(column => column.map(node => { const point = positions.get(node.id)!; return <button type="button" key={node.id} className={`v2-map-node v2-map-${node.type}`} style={{ left: point.x, top: point.y }} aria-label={`${node.type}: ${node.label}`} aria-pressed={current?.id === node.id} onClick={() => setSelected(node.id)}><small>{node.polarity || node.type}</small><span>{node.label}</span></button>; }))}</div></div></div>
      <div className={`v2-graph-list ${mode === "map" ? "v2-list-hidden" : ""}`}><p className="v2-help">Select an item to inspect its public source and relationships.</p><ul>{filtered.map(node => <li key={node.id}><button type="button" aria-pressed={current?.id === node.id} onClick={() => setSelected(node.id)}><span className={`v2-node-dot v2-node-${node.type}`} aria-hidden="true" /><span><strong>{node.label}</strong><small>{node.polarity || node.type}</small></span><ArrowRight size={15} aria-hidden="true" /></button></li>)}</ul></div>
    </div><aside className="v2-graph-detail" aria-live="polite"><p className="v2-eyebrow">SELECTED {current?.type.toUpperCase()}</p><h3>{current?.label}</h3><p>{detail?.description}</p>{detail?.source && <p className="v2-muted">Provenance: {detail.source}</p>}{detail?.videoId && /^[A-Za-z0-9_-]{11}$/.test(detail.videoId) && <a href={`https://www.youtube.com/watch?v=${detail.videoId}${detail.seconds == null ? "" : `&t=${Math.floor(detail.seconds)}s`}`} target="_blank" rel="noopener noreferrer">Open source video <ExternalLink size={15} aria-hidden="true" /><span className="v2-sr-only"> in new tab</span></a>}<div className="v2-relations"><strong>Connections</strong><ul>{relations.filter(edge => edge.source === current?.id || edge.target === current?.id).slice(0, 12).map((edge,index) => { const other = filtered.find(node => node.id === (edge.source === current?.id ? edge.target : edge.source)); return <li key={`${edge.source}-${edge.target}-${index}`}>{edge.type.toLowerCase()} · {other?.label || "related evidence"}</li>; })}</ul></div></aside></div>}
    {cursor && <button type="button" className="v2-button-secondary v2-graph-more" disabled={loading} onClick={more}>{loading ? "Loading…" : "Load more connections"}</button>}{error && nodes.length > 0 && <p className="v2-graph-state" role="alert">More connections could not be loaded. Try again.</p>}
    <p className="v2-graph-legend"><GitBranch size={15} aria-hidden="true" /> Solid teal lines support a finding; dashed rose lines indicate contradiction. The list view is the accessible alternative.</p>
  </section>;
}
