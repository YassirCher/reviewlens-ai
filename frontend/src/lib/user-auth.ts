export type UserProfile = {
  id: string;
  email: string;
  name: string | null;
  created_at: string;
};

export type UserResearchItem = {
  run_id: string;
  product_name: string;
  status: "queued" | "running" | "cancelling" | "complete" | "partial" | "failed" | "cancelled";
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  source_count_requested: number;
  source_count_analyzed: number;
  report_url: string | null;
  public_token: string | null;
  pdf_url: string | null;
  overall_score: number | null;
  verdict: string | null;
  summary: string | null;
};

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

async function parseJsonOrError(res: Response) {
  const json = await res.json().catch(() => null);
  if (!res.ok) {
    const message = json?.error?.message || json?.message || `Request failed with status ${res.status}`;
    throw new Error(message);
  }
  return json;
}

export async function fetchCurrentUser(): Promise<UserProfile | null> {
  try {
    const res = await fetch(`${API_BASE}/api/v2/user/me`, {
      method: "GET",
      credentials: "include",
      headers: { Accept: "application/json" },
    });
    if (res.status === 401 || res.status === 403) return null;
    const data = await parseJsonOrError(res);
    return data?.user || null;
  } catch {
    return null;
  }
}

export async function registerUser(email: string, password: string, name?: string): Promise<UserProfile> {
  const res = await fetch(`${API_BASE}/api/v2/user/register`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ email, password, name: name?.trim() || undefined }),
  });
  const data = await parseJsonOrError(res);
  return data.user;
}

export async function loginUser(email: string, password: string): Promise<UserProfile> {
  const res = await fetch(`${API_BASE}/api/v2/user/login`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const data = await parseJsonOrError(res);
  return data.user;
}

export async function logoutUser(): Promise<void> {
  await fetch(`${API_BASE}/api/v2/user/logout`, {
    method: "POST",
    credentials: "include",
  }).catch(() => null);
}

export async function fetchUserResearches(): Promise<UserResearchItem[]> {
  const res = await fetch(`${API_BASE}/api/v2/user/researches`, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  const data = await parseJsonOrError(res);
  return data.researches || [];
}

export async function adoptResearches(target?: { run_id?: string; public_token?: string }): Promise<number> {
  try {
    const res = await fetch(`${API_BASE}/api/v2/user/researches/adopt`, {
      method: "POST",
      credentials: "include",
      headers: {
        Accept: "application/json",
        ...(target ? { "Content-Type": "application/json" } : {}),
      },
      ...(target ? { body: JSON.stringify(target) } : {}),
    });
    const data = await parseJsonOrError(res);
    return data?.adopted_count || 0;
  } catch {
    return 0;
  }
}
