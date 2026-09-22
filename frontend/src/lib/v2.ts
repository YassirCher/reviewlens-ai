/** Browser contracts for the V2-only public and admin runtime. */
export type AnalysisInput = {
  product_name: string;
  video_count: number;
  analyze_comments: boolean;
};

export type Preflight = {
  normalized_options: AnalysisInput & { locale: string };
  allowed: boolean;
  denial_code: string | null;
  queue: { condition: "available" | "full"; queued_runs: number };
  remaining_public_quota: {
    hourly_remaining: number;
    daily_ip_remaining: number;
    daily_session_remaining: number;
    concurrent_remaining: number;
  };
  estimate: { token_band: { min: number; max: number }; cost_band: "low" | "medium" | "high"; non_binding: true };
};

export type CreatedRun = { run_id: string; status: string; status_url: string; events_url: string };
export type PublicTask = { task_key: string; status: string; label: string; started_at: string | null; completed_at: string | null };
export type ProductEvidence = { video_id: string; source_url: string; source_part: "title" | "description" | "transcript"; excerpt: string; timestamp_seconds: number | null };
export type ProductFact = { group: string; label: string; value: string; scope: string | null; evidence: ProductEvidence[]; conflicting: boolean };
export type ProductVariant = { dimension: string; value: string; scope: string | null; evidence: ProductEvidence[] };
export type ProductInfo = { facts: ProductFact[]; variants: ProductVariant[]; coverage_note: string };
export type SampleUsed = { units: { role: string; details: { label: string; value: string; evidence: ProductEvidence }[] }[] };
export type RunStatus = {
  run_id: string;
  status: "queued" | "running" | "cancelling" | "complete" | "partial" | "failed" | "cancelled";
  product_name: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  source_count_requested: number;
  source_count_analyzed: number;
  completed_tasks: number;
  total_tasks: number;
  warnings: string[];
  failure: { code: string; message: string } | null;
  total_tokens: number;
  usage_pending: boolean;
  tasks: PublicTask[];
  report_url: string | null;
  progress_sequence: number;
  product_info?: ProductInfo | null;
};
export type ProgressEvent = {
  sequence: number;
  run_id: string;
  task_id?: string;
  task_key?: string;
  label: string;
  detail: string;
  completed_tasks: number;
  total_tasks: number;
  percent: number;
  timestamp: string;
  result_summary?: Record<string, unknown>;
};
export type Evidence = { id: string; text: string; timestamp_start_seconds: number | null; timestamp_end_seconds: number | null; support_type: "supports" | "contradicts"; confidence: number };
export type Claim = { claim: string; central: boolean; evidence: Evidence[] };
export type Source = {
  id: string; video_id: string; url: string; title: string; channel: string;
  views: number | null; duration_seconds: number | null; published_at: string | null;
  review_type: string; ownership_context: string; usage_period: string | null;
  source_score: number; evidence_quality_score: number; recommendation_summary: string;
  pros: string[]; cons: string[]; limitations: string[];
  transcript_language: string; translated: boolean; caption_kind: string; claims: Claim[];
  sample_used?: SampleUsed | null;
};
export type Finding = { id: string; statement: string; source_ids: string[]; evidence_ids: string[] };
export type Disagreement = { topic: string; side_a: string; side_a_source_ids: string[]; side_b: string; side_b_source_ids: string[] };
export type Report = {
  schema_version: 1; report_id: string; product_name: string; status: "complete" | "partial";
  source_count_requested: number; source_count_analyzed: number; overall_score: number;
  verdict: string; confidence: number; confidence_band: string; summary: string;
  consensus_pros: Finding[]; consensus_cons: Finding[]; disagreements: Disagreement[];
  longest_usage_period: string | null; longest_usage_source_id: string | null;
  who_should_buy: string[]; who_should_avoid: string[]; limitations: string[]; warnings: string[];
  sources: Source[]; generated_at: string; total_tokens: number; model_call_count: number; usage_pending: boolean;
  product_info?: ProductInfo | null;
};
export type GraphNode = { id: string; type: "product" | "source" | "finding" | "evidence"; label: string; video_id?: string | null; polarity?: string | null };
export type GraphEdge = { source: string; target: string; type: "ABOUT" | "SUPPORTS" | "CONTRADICTS" };
export type GraphPage = { nodes: GraphNode[]; edges: GraphEdge[]; next_cursor: string | null };

export class V2ApiError extends Error {
  constructor(public code: string, message: string, public retryable: boolean, public retryAfter: string | null = null, public status = 0) {
    super(message);
    this.name = "V2ApiError";
  }
}

const BASE = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");
const TOKEN_PATH = /^\/api\/v2\/reports\/([A-Za-z0-9_-]{43})$/;
const RUN_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const TOKEN = /^[A-Za-z0-9_-]{43}$/;

export function validRunId(value: string): boolean { return RUN_ID.test(value); }
export function validToken(value: string): boolean { return TOKEN.test(value); }
export function reportTokenFromApiPath(path: string | null): string | null { return path?.match(TOKEN_PATH)?.[1] ?? null; }
export function productError(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return "Enter a product name to begin.";
  if (trimmed.length > 500) return "Keep the product name under 500 characters.";
  if (/[\x00-\x1f]/.test(trimmed)) return "Remove control characters from the product name.";
  return null;
}

async function checked<T>(response: Response): Promise<T> {
  if (response.ok) return response.json() as Promise<T>;
  let envelope: { error?: { code?: string; message?: string; retryable?: boolean } } = {};
  try { envelope = await response.json(); } catch { /* Never surface an upstream body. */ }
  const error = envelope.error;
  throw new V2ApiError(error?.code || "request_failed", error?.message || "ReviewLens could not complete this request.", Boolean(error?.retryable), response.headers.get("Retry-After"), response.status);
}

function api(path: string): string { return `${BASE}${path}`; }
export function preflight(input: AnalysisInput, signal?: AbortSignal): Promise<Preflight> {
  return fetch(api("/api/v2/analyses/preflight"), { method: "POST", credentials: "include", cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input), signal }).then(checked<Preflight>);
}
export function createRun(input: AnalysisInput, key: string, signal?: AbortSignal): Promise<CreatedRun> {
  return fetch(api("/api/v2/analyses"), { method: "POST", credentials: "include", cache: "no-store", headers: { "Content-Type": "application/json", "Idempotency-Key": key }, body: JSON.stringify(input), signal }).then(checked<CreatedRun>);
}
export function getRun(id: string, signal?: AbortSignal): Promise<RunStatus> {
  if (!validRunId(id)) return Promise.reject(new V2ApiError("not_found", "Analysis not found.", false, null, 404));
  return fetch(api(`/api/v2/analyses/${id}`), { credentials: "include", cache: "no-store", signal }).then(checked<RunStatus>);
}
export function cancelRun(id: string): Promise<{ run_id: string; status: string }> {
  if (!validRunId(id)) return Promise.reject(new V2ApiError("not_found", "Analysis not found.", false, null, 404));
  return fetch(api(`/api/v2/analyses/${id}/cancel`), { method: "POST", credentials: "include", cache: "no-store" }).then(checked<{ run_id: string; status: string }>);
}
export function getReport(token: string, signal?: AbortSignal): Promise<Report> {
  if (!validToken(token)) return Promise.reject(new V2ApiError("not_found", "Report not found.", false, null, 404));
  return fetch(api(`/api/v2/reports/${token}`), { cache: "no-store", signal }).then(checked<Report>);
}
export function getGraphPage(token: string, cursor?: string | null, signal?: AbortSignal): Promise<GraphPage> {
  if (!validToken(token)) return Promise.reject(new V2ApiError("not_found", "Report not found.", false, null, 404));
  const query = new URLSearchParams({ limit: "100" });
  if (cursor) query.set("cursor", cursor);
  return fetch(api(`/api/v2/reports/${token}/graph?${query}`), { cache: "no-store", signal }).then(checked<GraphPage>);
}

/** Fetch streaming allows replay from a persisted sequence; EventSource cannot set that header. */
export async function readEvents(id: string, after: number, onEvent: (name: string, data: ProgressEvent) => void, signal: AbortSignal, onOpen?: () => void): Promise<void> {
  if (!validRunId(id)) throw new V2ApiError("not_found", "Analysis not found.", false, null, 404);
  const response = await fetch(api(`/api/v2/analyses/${id}/events`), {
    credentials: "include", cache: "no-store", signal,
    headers: { Accept: "text/event-stream", ...(after > 0 ? { "Last-Event-ID": String(after) } : {}) },
  });
  if (!response.ok) await checked(response);
  if (!response.body) throw new V2ApiError("stream_unavailable", "Live updates are unavailable.", true);
  onOpen?.();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      if (buffer.length > 65536) throw new V2ApiError("stream_invalid", "Live updates were interrupted.", true);
      let match: RegExpExecArray | null;
      while ((match = /\r?\n\r?\n/.exec(buffer))) {
        const frame = buffer.slice(0, match.index);
        buffer = buffer.slice(match.index + match[0].length);
        let name = "message", raw = "", sequence = 0;
        for (const line of frame.split(/\r?\n/)) {
          if (line.startsWith("event:")) name = line.slice(6).trim();
          else if (line.startsWith("id:")) sequence = Number(line.slice(3).trim());
          else if (line.startsWith("data:")) raw += line.slice(5).trimStart();
        }
        if (!raw || !Number.isSafeInteger(sequence) || sequence <= after) continue;
        if (sequence !== after + 1) throw new V2ApiError("stream_gap", "Live updates were interrupted.", true);
        let data: ProgressEvent;
        try { data = JSON.parse(raw) as ProgressEvent; } catch { throw new V2ApiError("stream_invalid", "Live updates were interrupted.", true); }
        if (data.sequence !== sequence || data.run_id !== id) throw new V2ApiError("stream_invalid", "Live updates were interrupted.", true);
        after = sequence;
        onEvent(name, data);
      }
    }
  } finally { reader.releaseLock(); }
}
