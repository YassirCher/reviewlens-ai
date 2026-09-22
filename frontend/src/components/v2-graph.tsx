"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  ChevronRight,
  ExternalLink,
  GitBranch,
  GitCompare,
  List,
  Maximize2,
  Minimize2,
  Network,
  Package,
  PanelRightClose,
  PanelRightOpen,
  Quote,
  RotateCcw,
  Sparkles,
  ThumbsUp,
  X,
  Youtube,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { getGraphPage, type GraphEdge, type GraphNode, type Report, type Source } from "@/lib/v2";
import { useTheme } from "@/components/v2-theme-provider";

type Filter = "all" | "strengths" | "caveats" | "disagreements" | "source";
const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "All Evidence" },
  { key: "strengths", label: "Strengths" },
  { key: "caveats", label: "Caveats" },
  { key: "disagreements", label: "Disagreements" },
  { key: "source", label: "Sources Only" },
];

const TYPES: GraphNode["type"][] = ["product", "source", "finding", "evidence"];

const COLUMN_CONFIG: Record<
  GraphNode["type"],
  { title: string; subtitle: string; icon: typeof Package; color: string }
> = {
  product: { title: "Target Product", subtitle: "Subject of audit", icon: Package, color: "#8B5CF6" },
  source: { title: "Source Reviews", subtitle: "Independent videos", icon: Youtube, color: "#3B82F6" },
  finding: { title: "Consensus Findings", subtitle: "Corroborated takeaways", icon: GitBranch, color: "#10B981" },
  evidence: { title: "Direct Evidence", subtitle: "Timestamped quotes", icon: Quote, color: "#06B6D4" },
};

const NODE_WIDTH = 224;
const NODE_HEIGHT = 82;
const COL_GAP = 96;
const ROW_HEIGHT = 104;
const HEADER_OFFSET = 54;

function formatViews(views: number | null): string {
  if (views == null) return "Views unavailable";
  return `${new Intl.NumberFormat("en", { notation: "compact" }).format(views)} views`;
}

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "Duration n/a";
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function formatTime(seconds: number | null): string {
  if (seconds == null) return "Start";
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
}

function verdictLabel(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function visible(node: GraphNode, filter: Filter): boolean {
  if (filter === "all") return true;
  if (filter === "source") return node.type === "source" || node.type === "product";
  if (node.type === "product" || node.type === "source" || node.type === "evidence") return true;
  if (node.type !== "finding") return false;
  return filter === "strengths"
    ? node.polarity === "pro"
    : filter === "caveats"
    ? node.polarity === "con"
    : node.polarity === "disagreement";
}

type NodeDetailInfo = {
  title: string;
  category: string;
  badgeColor: string;
  description: string;
  sourceChannel?: string;
  videoId?: string;
  seconds?: number | null;
  metrics: { label: string; value: string }[];
};

function getNodeDetail(report: Report, node: GraphNode, isLight = false): NodeDetailInfo {
  if (node.type === "product") {
    return {
      title: report.product_name,
      category: "TARGET PRODUCT",
      badgeColor: isLight ? "#6D28D9" : "#A78BFA",
      description: report.summary,
      metrics: [
        { label: "Overall Score", value: `${report.overall_score}/100` },
        { label: "Verdict", value: verdictLabel(report.verdict) },
        { label: "Confidence", value: `${report.confidence}%` },
        { label: "Coverage", value: `${report.source_count_analyzed} of ${report.source_count_requested} sources` },
      ],
    };
  }

  if (node.type === "source") {
    const source = report.sources.find((item) => item.id === node.id || item.video_id === node.video_id);
    return {
      title: source?.title || node.label,
      category: "YOUTUBE REVIEW",
      badgeColor: isLight ? "#1D4ED8" : "#60A5FA",
      sourceChannel: source?.channel,
      videoId: source?.video_id,
      description: source?.recommendation_summary || "Source review contributing to cross-evidence synthesis.",
      metrics: [
        { label: "Channel", value: source?.channel || "Unknown" },
        { label: "View Count", value: source?.views ? formatViews(source.views) : "N/A" },
        { label: "Duration", value: formatDuration(source?.duration_seconds ?? null) },
        { label: "Testing Window", value: source?.usage_period || "Standard testing" },
        { label: "Evidence Quality", value: `${source?.evidence_quality_score ?? 85}/100` },
      ],
    };
  }

  if (node.type === "finding") {
    const pro = report.consensus_pros.find((item) => item.id === node.id);
    const con = report.consensus_cons.find((item) => item.id === node.id);
    const disagreement = report.disagreements.find((item) => item.topic === node.label);
    const finding = pro || con;
    const isPro = Boolean(pro);
    const isCon = Boolean(con);
    const category = isPro
      ? "CONSENSUS STRENGTH"
      : isCon
      ? "CONSENSUS CAVEAT"
      : "REVIEWER DISAGREEMENT";
    const badgeColor = isPro
      ? (isLight ? "#047857" : "#10B981")
      : isCon
      ? (isLight ? "#B45309" : "#F59E0B")
      : (isLight ? "#BE123C" : "#F43F5E");

    const description = finding
      ? finding.statement
      : disagreement
      ? `Side A: ${disagreement.side_a} — Side B: ${disagreement.side_b}`
      : node.label;

    return {
      title: node.label,
      category,
      badgeColor,
      description,
      metrics: [
        {
          label: "Independent Sources",
          value: finding ? `${finding.source_ids.length} creators agreed` : "Split reviewer opinions",
        },
        {
          label: "Evidence Grounding",
          value: finding ? `${finding.evidence_ids.length} citations verified` : "Contrasting timestamps",
        },
      ],
    };
  }

  // Evidence node
  let matchSource: Source | undefined;
  let matchEvidenceText = node.label;
  let matchSeconds: number | null = null;
  let matchSupport = "supports";
  let matchConfidence = 90;

  for (const item of report.sources) {
    for (const claim of item.claims) {
      for (const ev of claim.evidence) {
        if (ev.id === node.id) {
          matchSource = item;
          matchEvidenceText = ev.text;
          matchSeconds = ev.timestamp_start_seconds;
          matchSupport = ev.support_type;
          matchConfidence = ev.confidence;
          break;
        }
      }
    }
  }

  return {
    title: `Transcript Quote (${matchSupport.toUpperCase()})`,
    category: "DIRECT CITATION",
    badgeColor: isLight ? "#0369A1" : "#06B6D4",
    sourceChannel: matchSource?.channel,
    videoId: matchSource?.video_id,
    seconds: matchSeconds,
    description: `“${matchEvidenceText}”`,
    metrics: [
      { label: "Channel", value: matchSource?.channel || "Unknown" },
      { label: "Timestamp", value: formatTime(matchSeconds) },
      { label: "Support Type", value: matchSupport === "contradicts" ? "Contradicts Finding" : "Supports Finding" },
      { label: "Confidence", value: `${matchConfidence}%` },
    ],
  };
}

export function V2Graph({ report, token, full = false }: { report: Report; token: string; full?: boolean }) {
  const { resolvedTheme } = useTheme();
  const isLight = resolvedTheme === "light";
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [mode, setMode] = useState<"map" | "list">("map");
  const [selected, setSelected] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [showInspector, setShowInspector] = useState(true);
  const [isExpanded, setIsExpanded] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    getGraphPage(token, null, controller.signal)
      .then((page) => {
        setNodes(page.nodes);
        setEdges(page.edges);
        setCursor(page.next_cursor);
        setSelected(page.nodes[0]?.id || null);
        setError(false);
      })
      .catch(() => {
        if (!controller.signal.aborted) setError(true);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [token]);

  async function loadMore() {
    if (!cursor || loading) return;
    setLoading(true);
    try {
      const page = await getGraphPage(token, cursor);
      setNodes((old) => [...old, ...page.nodes.filter((item) => !old.some((prior) => prior.id === item.id))]);
      setEdges((old) => [
        ...old,
        ...page.edges.filter(
          (item) => !old.some((prior) => prior.source === item.source && prior.target === item.target && prior.type === item.type)
        ),
      ]);
      setCursor(page.next_cursor);
      setError(false);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }

  const filtered = useMemo(() => nodes.filter((node) => visible(node, filter)), [nodes, filter]);
  const visibleIds = useMemo(() => new Set(filtered.map((node) => node.id)), [filtered]);
  const relations = useMemo(
    () => edges.filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target)),
    [edges, visibleIds]
  );

  const current = filtered.find((node) => node.id === selected) || filtered[0] || null;
  const detail = current ? getNodeDetail(report, current, isLight) : null;

  // Slicing: On review page show up to 6 per column, on full page show up to 14
  const columns = useMemo(() => {
    return TYPES.map((type) => filtered.filter((node) => node.type === type).slice(0, full ? 14 : 6));
  }, [filtered, full]);

  const maxRows = Math.max(1, ...columns.map((col) => col.length));
  const planeHeight = Math.max(isExpanded ? 640 : 490, HEADER_OFFSET + maxRows * ROW_HEIGHT + 36);
  const planeWidth = 24 + 3 * (NODE_WIDTH + COL_GAP) + NODE_WIDTH + 24; // ~1232px

  const positions = useMemo(() => {
    const map = new Map<string, { x: number; y: number }>();
    columns.forEach((column, colIdx) => {
      const x = 24 + colIdx * (NODE_WIDTH + COL_GAP);
      if (colIdx === 0 && column.length === 1) {
        // Center the product node vertically relative to Column 1 (sources)
        const col1Len = Math.max(1, columns[1]?.length || 1);
        const y = HEADER_OFFSET + Math.max(0, ((col1Len - 1) * ROW_HEIGHT) / 2);
        map.set(column[0].id, { x, y });
      } else {
        column.forEach((node, rowIdx) => {
          map.set(node.id, { x, y: HEADER_OFFSET + rowIdx * ROW_HEIGHT });
        });
      }
    });
    return map;
  }, [columns]);

  const activeId = hovered || selected;

  const connectedNodeIds = useMemo(() => {
    if (!activeId) return new Set<string>();
    const set = new Set<string>([activeId]);
    for (const edge of relations) {
      if (edge.source === activeId) set.add(edge.target);
      if (edge.target === activeId) set.add(edge.source);
    }
    return set;
  }, [activeId, relations]);

  const activeConnections = useMemo(() => {
    if (!current) return [];
    return relations.filter((edge) => edge.source === current.id || edge.target === current.id).slice(0, 15);
  }, [current, relations]);

  function scrollToNode(nodeId: string) {
    const point = positions.get(nodeId);
    if (point && scrollRef.current) {
      const container = scrollRef.current;
      const targetX = point.x * zoom - container.clientWidth / 2 + (NODE_WIDTH * zoom) / 2;
      const targetY = point.y * zoom - container.clientHeight / 2 + (NODE_HEIGHT * zoom) / 2;
      container.scrollTo({ left: Math.max(0, targetX), top: Math.max(0, targetY), behavior: "smooth" });
    }
  }

  function handleZoomIn() {
    setZoom((z) => Math.min(1.3, Math.round((z + 0.15) * 100) / 100));
  }

  function handleZoomOut() {
    setZoom((z) => Math.max(0.65, Math.round((z - 0.15) * 100) / 100));
  }

  function handleZoomReset() {
    setZoom(1);
    if (scrollRef.current) {
      scrollRef.current.scrollTo({ left: 0, top: 0, behavior: "smooth" });
    }
  }

  function handleZoomFit() {
    if (scrollRef.current) {
      const available = scrollRef.current.clientWidth - 32;
      const scale = Math.min(1, Math.max(0.65, available / planeWidth));
      setZoom(Math.round(scale * 100) / 100);
      scrollRef.current.scrollTo({ left: 0, top: 0, behavior: "smooth" });
    } else {
      setZoom(0.8);
    }
  }

  // Edge drawing with smooth horizontal cubic bezier
  function renderEdgePath(edge: GraphEdge, idx: number, isActive: boolean) {
    const a = positions.get(edge.source);
    const b = positions.get(edge.target);
    if (!a || !b) return null;

    const [left, right] = a.x <= b.x ? [a, b] : [b, a];
    const x1 = left.x + NODE_WIDTH;
    const y1 = left.y + NODE_HEIGHT / 2;
    const x2 = right.x;
    const y2 = right.y + NODE_HEIGHT / 2;
    const dx = Math.max(45, (x2 - x1) * 0.45);
    const d = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;

    const strokeColor =
      edge.type === "CONTRADICTS"
        ? "url(#grad-contradicts)"
        : edge.type === "SUPPORTS"
        ? "url(#grad-supports)"
        : "url(#grad-about)";

    const isDimmed = activeId && !isActive;

    return (
      <path
        key={`${edge.source}-${edge.target}-${idx}-${isActive ? "active" : "inactive"}`}
        d={d}
        fill="none"
        stroke={strokeColor}
        strokeWidth={isActive ? 2.8 : 1.35}
        strokeDasharray={edge.type === "CONTRADICTS" ? "5 4" : undefined}
        opacity={isActive ? 1 : isDimmed ? 0.08 : 0.38}
        filter={isActive ? "url(#edge-glow)" : undefined}
        className="v2-graph-edge"
      />
    );
  }

  return (
    <section className={`v2-graph-panel ${isExpanded ? "v2-graph-expanded" : ""}`} aria-label="Evidence connection map">
      {/* Top Controls Toolbar */}
      <div className="v2-graph-toolbar">
        <div className="v2-graph-toolbar-left">
          <div role="group" aria-label="Filter evidence map" className="v2-graph-filters">
            {FILTERS.map((item) => {
              const count =
                item.key === "all"
                  ? nodes.length
                  : item.key === "source"
                  ? nodes.filter((n) => n.type === "source").length
                  : item.key === "strengths"
                  ? nodes.filter((n) => n.type === "finding" && n.polarity === "pro").length
                  : item.key === "caveats"
                  ? nodes.filter((n) => n.type === "finding" && n.polarity === "con").length
                  : nodes.filter((n) => n.type === "finding" && n.polarity === "disagreement").length;

              return (
                <button
                  key={item.key}
                  type="button"
                  aria-pressed={filter === item.key}
                  onClick={() => {
                    setFilter(item.key);
                    setSelected(null);
                  }}
                  className="v2-graph-filter-btn"
                >
                  <span>{item.label}</span>
                  {count > 0 && <small className="v2-filter-count">{count}</small>}
                </button>
              );
            })}
          </div>
        </div>

        <div className="v2-graph-toolbar-right">
          {/* Zoom controls */}
          <div className="v2-zoom-controls" role="group" aria-label="Canvas zoom">
            <button type="button" onClick={handleZoomOut} title="Zoom out" aria-label="Zoom out">
              <ZoomOut size={15} />
            </button>
            <button type="button" onClick={handleZoomFit} title="Fit to width" className="v2-zoom-label">
              Fit
            </button>
            <button type="button" onClick={handleZoomReset} title="Reset zoom (100%)" className="v2-zoom-label">
              {Math.round(zoom * 100)}%
            </button>
            <button type="button" onClick={handleZoomIn} title="Zoom in" aria-label="Zoom in">
              <ZoomIn size={15} />
            </button>
          </div>

          {/* View toggle (Map / List) */}
          <div role="group" aria-label="Evidence map view" className="v2-view-toggle">
            <button type="button" aria-pressed={mode === "map"} onClick={() => setMode("map")}>
              <Network size={15} aria-hidden="true" /> Map
            </button>
            <button type="button" aria-pressed={mode === "list"} onClick={() => setMode("list")}>
              <List size={15} aria-hidden="true" /> List
            </button>
          </div>

          {/* Inspector toggle */}
          <button
            type="button"
            className={`v2-tool-btn ${showInspector ? "v2-tool-active" : ""}`}
            onClick={() => setShowInspector((prev) => !prev)}
            title={showInspector ? "Hide node inspector" : "Show node inspector"}
          >
            {showInspector ? <PanelRightClose size={15} /> : <PanelRightOpen size={15} />}
            <span className="v2-btn-text">Details</span>
          </button>

          {/* Theater mode toggle */}
          <button
            type="button"
            className={`v2-tool-btn ${isExpanded ? "v2-tool-active" : ""}`}
            onClick={() => setIsExpanded((prev) => !prev)}
            title={isExpanded ? "Collapse graph view" : "Expand graph view"}
          >
            {isExpanded ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
          </button>
        </div>
      </div>

      {/* Main Content Area */}
      {loading && nodes.length === 0 ? (
        <div className="v2-graph-state" role="status">
          <Sparkles className="v2-spin" size={20} />
          <p>Mapping verified cross-source evidence relationships…</p>
        </div>
      ) : error && nodes.length === 0 ? (
        <div className="v2-graph-state" role="alert">
          <p>The evidence map is temporarily unavailable.</p>
          <button className="v2-text-link" onClick={() => window.location.reload()}>
            <RotateCcw size={15} /> Retry
          </button>
        </div>
      ) : filtered.length === 0 ? (
        <div className="v2-graph-state">
          <p>No evidence nodes match this filter.</p>
          <button className="v2-text-link" onClick={() => setFilter("all")}>
            Reset filters
          </button>
        </div>
      ) : (
        <div className={`v2-graph-content ${!showInspector ? "v2-inspector-closed" : ""}`}>
          <div className="v2-graph-main">
            {/* Visual Canvas Mode */}
            <div className={`v2-map ${mode === "list" ? "v2-map-hidden" : ""}`}>
              <div className="v2-map-scroll" ref={scrollRef}>
                <div
                  className="v2-map-plane"
                  style={{
                    width: planeWidth,
                    height: planeHeight,
                    transform: `scale(${zoom})`,
                    transformOrigin: "top left",
                    marginBottom: zoom < 1 ? `-${Math.round((1 - zoom) * planeHeight)}px` : 0,
                    marginRight: zoom < 1 ? `-${Math.round((1 - zoom) * planeWidth)}px` : 0,
                  }}
                >
                  {/* SVG Canvas for Bezier Curves & Grid */}
                  <svg
                    aria-hidden="true"
                    className="v2-map-svg"
                    viewBox={`0 0 ${planeWidth} ${planeHeight}`}
                    style={{ width: planeWidth, height: planeHeight }}
                  >
                    <defs>
                      <pattern id="graph-grid" width="28" height="28" patternUnits="userSpaceOnUse">
                        <circle
                          cx="2"
                          cy="2"
                          r="1.2"
                          fill={isLight ? "rgba(15, 23, 42, 0.12)" : "var(--v2-border-strong)"}
                          opacity={isLight ? "1" : "0.35"}
                        />
                      </pattern>
                      <filter id="edge-glow" x="-20%" y="-20%" width="140%" height="140%">
                        <feGaussianBlur stdDeviation="2.5" result="blur" />
                        <feComposite in="SourceGraphic" in2="blur" operator="over" />
                      </filter>
                      <linearGradient id="grad-supports" x1="0%" y1="0%" x2="100%" y2="0%">
                        <stop offset="0%" stopColor={isLight ? "#0D9488" : "#35D0BA"} />
                        <stop offset="100%" stopColor={isLight ? "#059669" : "#10B981"} />
                      </linearGradient>
                      <linearGradient id="grad-contradicts" x1="0%" y1="0%" x2="100%" y2="0%">
                        <stop offset="0%" stopColor={isLight ? "#E11D48" : "#F43F5E"} />
                        <stop offset="100%" stopColor={isLight ? "#F43F5E" : "#FB7185"} />
                      </linearGradient>
                      <linearGradient id="grad-about" x1="0%" y1="0%" x2="100%" y2="0%">
                        <stop offset="0%" stopColor={isLight ? "#7C3AED" : "#8B5CF6"} />
                        <stop offset="100%" stopColor={isLight ? "#6366F1" : "#818CF8"} />
                      </linearGradient>
                    </defs>

                    <rect width="100%" height="100%" fill="url(#graph-grid)" />

                    {/* Inactive edges rendered first */}
                    {relations.map((edge, idx) => {
                      const isActive = activeId ? edge.source === activeId || edge.target === activeId : false;
                      if (isActive) return null;
                      return renderEdgePath(edge, idx, false);
                    })}

                    {/* Active highlighted edges rendered on top */}
                    {relations.map((edge, idx) => {
                      const isActive = activeId ? edge.source === activeId || edge.target === activeId : false;
                      if (!isActive) return null;
                      return renderEdgePath(edge, idx, true);
                    })}
                  </svg>

                  {/* Column Header Titles */}
                  {TYPES.map((type, colIdx) => {
                    const cfg = COLUMN_CONFIG[type];
                    const IconComponent = cfg.icon;
                    const count = columns[colIdx]?.length || 0;
                    const x = 24 + colIdx * (NODE_WIDTH + COL_GAP);

                    return (
                      <div
                        key={type}
                        className="v2-col-header"
                        style={{ left: x, width: NODE_WIDTH }}
                        title={cfg.subtitle}
                      >
                        <div className="v2-col-title-wrap">
                          <IconComponent size={14} style={{ color: cfg.color }} aria-hidden="true" />
                          <span>{cfg.title}</span>
                        </div>
                        <span className="v2-col-count">{count}</span>
                      </div>
                    );
                  })}

                  {/* Positioned Node Cards */}
                  {columns.flatMap((column) =>
                    column.map((node) => {
                      const point = positions.get(node.id);
                      if (!point) return null;

                      const isSelected = current?.id === node.id;
                      const isHovered = hovered === node.id;
                      const isConnected = activeId ? connectedNodeIds.has(node.id) : true;
                      const isDimmed = activeId ? !isConnected : false;

                      const polarity = node.polarity;
                      const isPro = polarity === "pro";
                      const isCon = polarity === "con";
                      const isDisagreement = polarity === "disagreement";

                      let badgeText = node.type.toUpperCase();
                      if (node.type === "product") badgeText = "PRODUCT";
                      else if (node.type === "source") badgeText = "REVIEW";
                      else if (isPro) badgeText = "STRENGTH";
                      else if (isCon) badgeText = "CAVEAT";
                      else if (isDisagreement) badgeText = "DISAGREEMENT";
                      else if (node.type === "evidence") badgeText = "EVIDENCE";

                      const sourceObj =
                        node.type === "source"
                          ? report.sources.find((s) => s.id === node.id || s.video_id === node.video_id)
                          : undefined;

                      return (
                        <button
                          type="button"
                          key={node.id}
                          className={`v2-map-card v2-card-${node.type} ${
                            polarity ? `v2-card-${polarity}` : ""
                          } ${isSelected ? "v2-card-selected" : ""} ${
                            isHovered ? "v2-card-hovered" : ""
                          } ${isDimmed ? "v2-card-dimmed" : ""}`}
                          style={{ left: point.x, top: point.y, width: NODE_WIDTH, height: NODE_HEIGHT }}
                          aria-label={`${node.type}: ${node.label}`}
                          aria-pressed={isSelected}
                          onClick={() => {
                            setSelected(node.id);
                            if (!showInspector) setShowInspector(true);
                          }}
                          onMouseEnter={() => setHovered(node.id)}
                          onMouseLeave={() => setHovered(null)}
                        >
                          <div className="v2-card-header">
                            <span className="v2-card-badge">
                              {node.type === "product" && <Package size={11} />}
                              {node.type === "source" && <Youtube size={11} />}
                              {isPro && <ThumbsUp size={11} />}
                              {isCon && <AlertTriangle size={11} />}
                              {isDisagreement && <GitCompare size={11} />}
                              {node.type === "evidence" && <Quote size={11} />}
                              <span>{badgeText}</span>
                            </span>
                            {sourceObj?.channel && (
                              <span className="v2-card-channel" title={sourceObj.channel}>
                                {sourceObj.channel}
                              </span>
                            )}
                          </div>

                          <div className="v2-card-label" title={node.label}>
                            {node.label}
                          </div>

                          <div className="v2-card-footer">
                            {node.type === "product" && (
                              <span>
                                Score: {report.overall_score}/100 · {verdictLabel(report.verdict)}
                              </span>
                            )}
                            {node.type === "source" && sourceObj && (
                              <span>
                                {sourceObj.views ? formatViews(sourceObj.views) : "Tested"} ·{" "}
                                {formatDuration(sourceObj.duration_seconds)}
                              </span>
                            )}
                            {node.type === "finding" && (
                              <span>Click to inspect citations & sources</span>
                            )}
                            {node.type === "evidence" && (
                              <span>Verified transcript excerpt</span>
                            )}
                          </div>
                        </button>
                      );
                    })
                  )}
                </div>
              </div>
            </div>

            {/* Accessible List Mode */}
            <div className={`v2-graph-list ${mode === "map" ? "v2-list-hidden" : ""}`}>
              <p className="v2-help">Select any node below to inspect its grounded evidence and YouTube timestamps.</p>
              <ul>
                {filtered.map((node) => {
                  const isSelected = current?.id === node.id;
                  const polarity = node.polarity;
                  return (
                    <li key={node.id}>
                      <button
                        type="button"
                        aria-pressed={isSelected}
                        onClick={() => {
                          setSelected(node.id);
                          if (!showInspector) setShowInspector(true);
                        }}
                        className={`v2-list-item-btn ${isSelected ? "v2-list-selected" : ""}`}
                      >
                        <span className={`v2-node-dot v2-node-${node.type} ${polarity ? `v2-dot-${polarity}` : ""}`} aria-hidden="true" />
                        <div className="v2-list-text">
                          <strong>{node.label}</strong>
                          <small>
                            {node.type.toUpperCase()} {polarity ? `· ${polarity.toUpperCase()}` : ""}
                          </small>
                        </div>
                        <ArrowRight size={15} aria-hidden="true" />
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          </div>

          {/* Right Inspector Sidebar */}
          {showInspector && current && detail && (
            <aside className="v2-graph-detail" aria-live="polite">
              <div className="v2-detail-top">
                <div className="v2-detail-category-pill" style={{ borderColor: detail.badgeColor, color: detail.badgeColor }}>
                  {detail.category}
                </div>
                <button
                  type="button"
                  className="v2-detail-close-btn"
                  onClick={() => setShowInspector(false)}
                  title="Close inspector (full width canvas)"
                  aria-label="Close inspector"
                >
                  <X size={15} />
                </button>
              </div>

              <h3 className="v2-detail-title">{detail.title}</h3>

              {/* Metrics pill strip */}
              {detail.metrics.length > 0 && (
                <div className="v2-detail-metrics">
                  {detail.metrics.map((m, idx) => (
                    <div key={`${m.label}-${idx}`} className="v2-detail-metric-chip">
                      <small>{m.label}</small>
                      <strong>{m.value}</strong>
                    </div>
                  ))}
                </div>
              )}

              {/* Body narrative / quote */}
              <div className="v2-detail-body">
                {current.type === "evidence" ? (
                  <blockquote className="v2-detail-quote">{detail.description}</blockquote>
                ) : (
                  <p>{detail.description}</p>
                )}
              </div>

              {/* Video direct timestamp link */}
              {detail.videoId && /^[A-Za-z0-9_-]{11}$/.test(detail.videoId) && (
                <a
                  className="v2-detail-video-btn"
                  href={`https://www.youtube.com/watch?v=${detail.videoId}${
                    detail.seconds == null ? "" : `&t=${Math.max(0, Math.floor(detail.seconds))}s`
                  }`}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <Youtube size={16} />
                  <span>
                    Watch source review {detail.seconds != null ? `at ${formatTime(detail.seconds)}` : ""}
                  </span>
                  <ExternalLink size={13} />
                </a>
              )}

              {/* Interactive Connections Matrix */}
              <div className="v2-relations">
                <div className="v2-relations-header">
                  <strong>Connected Nodes ({activeConnections.length})</strong>
                  <small>Click to navigate & illuminate path</small>
                </div>

                {activeConnections.length === 0 ? (
                  <p className="v2-muted">No adjacent connections in current view.</p>
                ) : (
                  <ul className="v2-conn-list">
                    {activeConnections.map((edge, idx) => {
                      const otherId = edge.source === current.id ? edge.target : edge.source;
                      const otherNode = filtered.find((n) => n.id === otherId);
                      if (!otherNode) return null;

                      const relType = edge.type;
                      const isSupport = relType === "SUPPORTS";
                      const isContradict = relType === "CONTRADICTS";

                      return (
                        <li key={`${edge.source}-${edge.target}-${idx}`}>
                          <button
                            type="button"
                            className="v2-conn-btn"
                            onClick={() => {
                              setSelected(otherNode.id);
                              scrollToNode(otherNode.id);
                            }}
                          >
                            <div className="v2-conn-badge-row">
                              <span
                                className={`v2-conn-pill ${
                                  isSupport ? "v2-pill-support" : isContradict ? "v2-pill-contradict" : "v2-pill-about"
                                }`}
                              >
                                {relType}
                              </span>
                              <span className="v2-conn-type">{otherNode.type}</span>
                            </div>
                            <span className="v2-conn-label">{otherNode.label}</span>
                            <ChevronRight size={14} className="v2-conn-arrow" />
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            </aside>
          )}
        </div>
      )}

      {/* Pagination button if more nodes available */}
      {cursor && (
        <button type="button" className="v2-button-secondary v2-graph-more" disabled={loading} onClick={loadMore}>
          {loading ? "Loading more evidence…" : "Load more connections from database"}
        </button>
      )}

      {/* Legend footer */}
      <footer className="v2-graph-legend">
        <GitBranch size={15} aria-hidden="true" />
        <span>
          <strong>Grounded Evidence Map:</strong> Solid teal paths represent corroborated support; dashed rose paths
          represent reviewer disputes. Click any node to illuminate its full relational chain.
        </span>
      </footer>
    </section>
  );
}
