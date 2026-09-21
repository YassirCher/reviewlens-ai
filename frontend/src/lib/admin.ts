/** Browser-only admin transport. The HttpOnly cookie is never read by JavaScript. */
export const ADMIN_API_BASE = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

export type Page<T> = { items: T[]; next_cursor: string | null };
export type AdminSession = { admin: { id: string; email: string }; expires_at: string; absolute_expires_at: string };
export type AdminRun = { id: string; product: string; status: string; initiator_type: string; created_at: string; duration_ms: number | null; total_tokens: number; total_cost_microusd: number; model_call_count: number; pending_usage_count: number };
export type Version = { id: string; definition_id: string; version_number: number; lifecycle: string; version: number; content_hash: string; change_note: string; published_at: string | null; payload: Record<string, unknown>; evaluation: Record<string, unknown> | null; active?: boolean };
export type Definition = { id: string; key: string; name: string; description: string; versions: { id: string; number: number; lifecycle: string; active: boolean; content_hash: string }[] };
export type AdminJob = { id: string; kind: string; status: string; safe_result: Record<string, unknown>; error_code: string | null };

export class AdminApiError extends Error {
  constructor(public code: string, message: string, public status: number) { super(message); }
}

let csrfToken: string | null = null;
export function clearAdminCsrf(): void { csrfToken = null; }
export async function adminRequest<T>(
  path: string,
  init: RequestInit = {},
  mutation = false,
  attempt = 0,
): Promise<T> {
  const headers = new Headers(init.headers);
  if (mutation) {
    if (!csrfToken) {
      const csrf = await adminRequest<{ csrf_token: string }>("/csrf");
      csrfToken = csrf.csrf_token;
    }
    headers.set("X-CSRF-Token", csrfToken);
    if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${ADMIN_API_BASE}/api/v2/admin${path}`, {
    ...init, headers, credentials: "include", cache: "no-store",
  });
  if (!response.ok) {
    let payload: { error?: { code?: string; message?: string; retryable?: boolean } } = {};
    try { payload = await response.json(); } catch { /* Preserve safe generic error. */ }
    if (response.status === 401 || response.status === 403) csrfToken = null;
    if (!mutation && response.status === 503 && payload.error?.retryable && attempt < 2) {
      await new Promise((resolve) => setTimeout(resolve, 200 * (attempt + 1)));
      return adminRequest<T>(path, init, mutation, attempt + 1);
    }
    throw new AdminApiError(payload.error?.code || "request_failed",
      payload.error?.message || "The admin request could not be completed.", response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function adminGet<T>(path: string): Promise<T> { return adminRequest<T>(path); }
export function adminPost<T>(path: string, body: unknown, idempotencyKey?: string): Promise<T> {
  return adminRequest<T>(path, { method: "POST", body: JSON.stringify(body),
    headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined }, true);
}
export function adminPut<T>(path: string, body: unknown): Promise<T> {
  return adminRequest<T>(path, { method: "PUT", body: JSON.stringify(body) }, true);
}
export function newJobKey(): string { return crypto.randomUUID(); }
export function money(micros: number | null | undefined): string {
  return micros == null ? "—" : `$${(micros / 1_000_000).toFixed(4)}`;
}
export function dateTime(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString() : "—";
}
export async function downloadAdminCsv(path: string, filename: string): Promise<void> {
  const response = await fetch(`${ADMIN_API_BASE}/api/v2/admin${path}`, { credentials: "include", cache: "no-store" });
  if (!response.ok) throw new AdminApiError("export_failed", "Export could not be downloaded.", response.status);
  const objectUrl = URL.createObjectURL(await response.blob());
  const link = document.createElement("a"); link.href = objectUrl; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}
