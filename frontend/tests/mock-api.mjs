import { createServer } from "node:http";

const TOKEN = "A".repeat(43);
const PARTIAL_TOKEN = "C".repeat(43);
const RUN = "11111111-1111-4111-8111-111111111111";
const PARTIAL_RUN = "22222222-2222-4222-8222-222222222222";
const CANCEL_RUN = "33333333-3333-4333-8333-333333333333";
const FAIL_RUN = "44444444-4444-4444-8444-444444444444";
const sourceId = "source-public-1";
const evidenceId = "evidence-public-1";
const findingId = "finding-public-1";
const statusReads = new Map();
const cancelled = new Set();
const retryKeys = new Map();
const LONG_TOKEN = "D".repeat(43);

function send(res, status, value, extras = {}) {
  const origin = res._origin || "http://127.0.0.1:3000";
  res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store", "Access-Control-Allow-Origin": origin, "Access-Control-Allow-Credentials": "true", "Access-Control-Allow-Headers": "Content-Type, Idempotency-Key, Last-Event-ID", "Access-Control-Allow-Methods": "GET, POST, OPTIONS", ...extras });
  res.end(JSON.stringify(value));
}
function body(req) { return new Promise(resolve => { let data = ""; req.on("data", chunk => { data += chunk; }); req.on("end", () => { try { resolve(JSON.parse(data || "{}")); } catch { resolve({}); } }); }); }
function error(code, message) { return { error: { code, message, retryable: false, request_id: "test-request", details: {} } }; }
function productInfo() {
  const evidence = { video_id: "7lCDEYXw3mM", source_url: "https://www.youtube.com/watch?v=7lCDEYXw3mM&t=92s", source_part: "transcript", excerpt: "Battery lasted 30 hours in testing.", timestamp_seconds: 92 };
  const second = { video_id: "def456GHI78", source_url: "https://www.youtube.com/watch?v=def456GHI78&t=42s", source_part: "transcript", excerpt: "The charging port is USB-C.", timestamp_seconds: 42 };
  return { facts: [{ group: "Power", label: "Battery runtime", value: "30 hours", scope: null, evidence: [evidence], conflicting: false }, { group: "Connectivity", label: "Charging port", value: "USB-C", scope: null, evidence: [second], conflicting: false }], variants: [{ dimension: "Color", value: "black", scope: null, evidence: [{ ...evidence, source_url: "https://www.youtube.com/watch?v=7lCDEYXw3mM", source_part: "description", excerpt: "Available in black", timestamp_seconds: null }] }], coverage_note: "Details stated in selected reviews; available configurations may differ by model and region." };
}
function report(partial = false) {
  const value = {
    schema_version: 1, report_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", product_name: partial ? "Partial Widget" : "Sony WH-1000XM5 headphones", status: partial ? "partial" : "complete",
    source_count_requested: partial ? 3 : 5, source_count_analyzed: partial ? 1 : 3, overall_score: 78, verdict: "buy_with_caveats", confidence: partial ? 45 : 73, confidence_band: partial ? "medium" : "high",
    summary: "Comfort and noise isolation are strong in the reviewed sources. Battery behavior and value deserve a closer look before buying.",
    consensus_pros: [{ id: findingId, statement: "Comfort remains strong over long sessions", source_ids: [sourceId,"source-public-2"], evidence_ids: [evidenceId] }], consensus_cons: [],
    disagreements: [{ topic: "Battery life", side_a: "Long enough for travel", side_a_source_ids: [sourceId], side_b: "Shorter than expected", side_b_source_ids: ["source-public-2"] }],
    longest_usage_period: "Three months", longest_usage_source_id: sourceId, who_should_buy: ["You prioritize comfort and isolation."], who_should_avoid: ["You need a confirmed all-day battery."],
    limitations: ["Captions are automatic."], warnings: partial ? ["Only one source had a usable transcript."] : [],
    sources: [{ id: sourceId, video_id: "7lCDEYXw3mM", url: "https://www.youtube.com/watch?v=7lCDEYXw3mM", title: "A long reviewer title about the Sony WH-1000XM5 headphones", channel: "Reviewer One", views: 123456, duration_seconds: 542, published_at: "2026-01-01T00:00:00Z", review_type: "long_term", ownership_context: "owned", usage_period: "Three months", source_score: 82, evidence_quality_score: 77, recommendation_summary: "Comfort is strong, with battery caveats.", pros: ["Comfort"], cons: ["Battery life"], limitations: ["Automatic captions"], transcript_language: "en", translated: false, caption_kind: "automatic", claims: [{ claim: "Comfort holds up over extended sessions", central: true, evidence: [{ id: evidenceId, text: "The ear pads stayed comfortable during my long train rides.", timestamp_start_seconds: 92, timestamp_end_seconds: 99, support_type: "supports", confidence: 91 }] }] }],
    generated_at: "2026-09-17T10:00:00Z", total_tokens: 3128, model_call_count: 8, usage_pending: false,
  };
  if (!partial) {
    value.product_info = productInfo();
    value.sources[0].sample_used = { units: [{ role: "Review unit", details: [{ label: "Color", value: "black", evidence: { video_id: "7lCDEYXw3mM", source_url: "https://www.youtube.com/watch?v=7lCDEYXw3mM", source_part: "description", excerpt: "Review unit is black", timestamp_seconds: null } }] }] };
    value.sources.push({ ...value.sources[0], id: "source-public-2", video_id: "def456GHI78", url: "https://www.youtube.com/watch?v=def456GHI78", title: "Second independent review", channel: "Reviewer Two", claims: [], sample_used: { units: [] } });
  }
  return value;
}
function longReport() {
  const value = report();
  value.product_name = "A deliberately long model designation ".repeat(11);
  value.sources[0].title = "An independent long-term review with a very long title ".repeat(8);
  value.sources[0].recommendation_summary = "Evidence remains traceable even when a source has unusually long content. ".repeat(18);
  return value;
}
function status(id) {
  const reads = (statusReads.get(id) || 0) + 1; statusReads.set(id, reads);
  const partial = id === PARTIAL_RUN;
  const isCancelled = cancelled.has(id);
  const done = isCancelled || (id !== CANCEL_RUN && (partial ? reads > 2 : reads > 1));
  const state = isCancelled ? "cancelled" : done ? (id === FAIL_RUN ? "failed" : partial ? "partial" : "complete") : "running";
  return { run_id: id, status: state, product_name: partial ? "Partial Widget" : "Sony WH-1000XM5 headphones", product_info: id === RUN ? productInfo() : undefined, created_at: "2026-09-17T09:00:00Z", started_at: "2026-09-17T09:00:01Z", completed_at: done ? "2026-09-17T09:01:00Z" : null, source_count_requested: partial ? 3 : 5, source_count_analyzed: done && id !== FAIL_RUN ? (partial ? 1 : 3) : 0, completed_tasks: done ? 7 : 2, total_tasks: 7, warnings: partial && done ? ["transcript_unavailable"] : [], failure: state === "failed" ? { code: "no_transcripts", message: "Review videos were found, but usable captions were unavailable. Try another product or model." } : null, total_tokens: done ? 3128 : 84, usage_pending: false, tasks: [
    { task_key: "validate_request", status: "succeeded", label: "Validate request", started_at: "2026-09-17T09:00:01Z", completed_at: "2026-09-17T09:00:02Z" },
    { task_key: "plan_research", status: "succeeded", label: "Plan research", started_at: "2026-09-17T09:00:02Z", completed_at: "2026-09-17T09:00:04Z" },
    { task_key: "discover_candidates", status: done ? "succeeded" : "running", label: "Discover candidates", started_at: "2026-09-17T09:00:04Z", completed_at: done ? "2026-09-17T09:00:08Z" : null },
    { task_key: "fetch_transcript.source_1", status: done ? "succeeded" : "queued", label: "Fetch transcript", started_at: null, completed_at: null },
    { task_key: "analyze_review.source_1", status: done ? "succeeded" : "queued", label: "Analyze review", started_at: null, completed_at: null },
    { task_key: "audit_report", status: done ? "succeeded" : "queued", label: "Audit report", started_at: null, completed_at: null },
    { task_key: "publish_report", status: done ? "succeeded" : "queued", label: "Publish report", started_at: null, completed_at: null },
  ], report_url: done && !isCancelled && id !== FAIL_RUN ? `/api/v2/reports/${partial ? PARTIAL_TOKEN : TOKEN}` : null, progress_sequence: done ? 2 : 1 };
}

createServer(async (req, res) => {
  const origin = req.headers.origin;
  res._origin = origin && (origin.includes("localhost") || origin.includes("127.0.0.1")) ? origin : "http://127.0.0.1:3000";
  const path = new URL(req.url || "/", "http://127.0.0.1:8899").pathname;
  if (req.method === "OPTIONS") return send(res, 204, {});
  if (path === "/health") return send(res, 200, { status: "ok" });
  if (path === "/api/v2/analyses/preflight" && req.method === "POST") {
    const input = await body(req); const denied = String(input.product_name || "").toLowerCase().includes("quota");
    if (String(input.product_name || "").toLowerCase().includes("maintenance")) return send(res, 503, error("admission_unavailable", "Research availability cannot be checked now."));
    return send(res, 200, { normalized_options: { ...input, locale: "en" }, allowed: !denied, denial_code: denied ? "public_rate_limit_exceeded" : null, queue: { condition: "available", queued_runs: 0 }, remaining_public_quota: { hourly_remaining: denied ? 0 : 3, daily_ip_remaining: 10, daily_session_remaining: 10, concurrent_remaining: 2 }, estimate: { token_band: { min: 1000, max: 10000 }, cost_band: "low", non_binding: true } }, { "Set-Cookie": "reviewlens_anonymous_session=mock-session; HttpOnly; SameSite=Lax; Path=/api/v2" });
  }
  if (path === "/api/v2/analyses" && req.method === "POST") {
    const input = await body(req);
    if (String(input.product_name).toLowerCase().includes("retry widget")) {
      const key = req.headers["idempotency-key"];
      if (!retryKeys.has(input.product_name)) { retryKeys.set(input.product_name, key); return send(res, 503, error("temporary_unavailable", "Connection interrupted. Retry the submission.")); }
      if (retryKeys.get(input.product_name) !== key) return send(res, 409, error("idempotency_conflict", "Submission key changed."));
    }
    const id = String(input.product_name).toLowerCase().includes("partial") ? PARTIAL_RUN : String(input.product_name).toLowerCase().includes("cancel") ? CANCEL_RUN : String(input.product_name).toLowerCase().includes("fail") ? FAIL_RUN : RUN;
    return send(res, 202, { run_id: id, status: "queued", status_url: `/api/v2/analyses/${id}`, events_url: `/api/v2/analyses/${id}/events` });
  }
  const runMatch = path.match(/^\/api\/v2\/analyses\/([0-9a-f-]{36})(\/events|\/cancel)?$/);
  if (runMatch) {
    if (!req.headers.cookie?.includes("reviewlens_anonymous_session=mock-session")) return send(res, 404, error("not_found", "The requested resource was not found."));
    const id = runMatch[1];
    if (runMatch[2] === "/cancel" && req.method === "POST") { cancelled.add(id); return send(res, 202, { run_id: id, status: "cancelling" }); }
    if (runMatch[2] === "/events") {
      if (id === PARTIAL_RUN || id === CANCEL_RUN || id === FAIL_RUN) return send(res, 503, error("stream_unavailable", "Live stream unavailable."));
      res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-store", "Access-Control-Allow-Origin": res._origin, "Access-Control-Allow-Credentials": "true", "Access-Control-Allow-Headers": "Last-Event-ID" });
      res.write(`id: 2\nevent: run.completed\ndata: ${JSON.stringify({ sequence: 2, run_id: id, label: "Analysis complete", detail: "", completed_tasks: 7, total_tasks: 7, percent: 100, timestamp: "2026-09-17T09:01:00Z" })}\n\n`); res.end(); return;
    }
    return send(res, 200, status(id));
  }
  const reportMatch = path.match(/^\/api\/v2\/reports\/([A-Za-z0-9_-]{43})(\/graph)?$/);
  if (reportMatch) {
    if (![TOKEN, PARTIAL_TOKEN, LONG_TOKEN].includes(reportMatch[1])) return send(res, 404, error("not_found", "The requested resource was not found."));
    if (reportMatch[2] === "/graph") {
      const cursor = new URL(req.url, "http://127.0.0.1:8899").searchParams.get("cursor");
      const nodes = [{ id: "product-public", type: "product", label: "Sony WH-1000XM5 headphones" }, { id: sourceId, type: "source", label: "A long reviewer title", video_id: "7lCDEYXw3mM" }, { id: findingId, type: "finding", label: "Comfort remains strong", polarity: "pro" }, { id: evidenceId, type: "evidence", label: "The ear pads stayed comfortable" }, { id: "disagreement-public", type: "finding", label: "Battery life", polarity: "disagreement" }];
      const edges = [{ source: sourceId, target: "product-public", type: "ABOUT" }, { source: sourceId, target: findingId, type: "SUPPORTS" }, { source: evidenceId, target: findingId, type: "SUPPORTS" }, { source: sourceId, target: "disagreement-public", type: "CONTRADICTS" }];
      return send(res, 200, { nodes: cursor ? nodes.slice(3) : nodes.slice(0, 3), edges, next_cursor: cursor ? null : "next" });
    }
    return send(res, 200, reportMatch[1] === LONG_TOKEN ? longReport() : report(reportMatch[1] === PARTIAL_TOKEN));
  }
  return send(res, 404, error("not_found", "The requested resource was not found."));
}).listen(8899, "127.0.0.1");
