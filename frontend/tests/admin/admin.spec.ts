import { expect, test, type Page, type Route } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const API = "http://127.0.0.1:8899/api/v2/admin";
const RUN = "11111111-1111-4111-8111-111111111111";
const TASK = "22222222-2222-4222-8222-222222222222";
const ATTEMPT = "33333333-3333-4333-8333-333333333333";
const WORKSPACE = "44444444-4444-4444-8444-444444444444";
const NODE = "55555555-5555-4555-8555-555555555555";
const NODE_VERSION = "66666666-6666-4666-8666-666666666666";
const AGENT = "77777777-7777-4777-8777-777777777777";
const PUBLISHED = "88888888-8888-4888-8888-888888888888";
const DRAFT = "99999999-9999-4999-8999-999999999999";
const SESSION = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const REPORT = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const NOW = "2026-09-18T12:00:00+00:00";

type MockState = { versions: Record<string, unknown>[]; settingsVersions: Record<string, unknown>[];
  activeSettingsId: string; kill: boolean; jobs: Record<string, unknown>[]; csrfRequests: number; mutations: number };

function response(route: Route, value: unknown, status = 200, extras: Record<string, string> = {}) {
  const origin = route.request().headers().origin || "http://127.0.0.1:3000";
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value), headers: {
    "Access-Control-Allow-Origin": origin, "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, X-CSRF-Token, Idempotency-Key",
    "Cache-Control": "no-store", ...extras,
  } });
}

async function mockAdmin(page: Page): Promise<MockState> {
  const initial = { id: PUBLISHED, definition_id: AGENT, version_number: 1, number: 1,
    lifecycle: "published", version: 1, content_hash: "a".repeat(64), change_note: "seed",
    published_at: NOW, active: true, payload: { purpose: "Assess review evidence", system_prompt: "Safe source analysis" }, evaluation: null };
  const state: MockState = { versions: [initial], settingsVersions: [{ id: PUBLISHED,
    number: 1, version: 1, lifecycle: "published", catalog_refresh_minutes: 15,
    raw_content_retention: false, raw_content_ttl_hours: 24, change_note: "seed" }],
    activeSettingsId: PUBLISHED, kill: false, jobs: [], csrfRequests: 0, mutations: 0 };
  await page.route(`${API}/**`, async route => {
    const request = route.request(); const url = new URL(request.url());
    const path = url.pathname.replace("/api/v2/admin", ""); const method = request.method();
    if (method === "OPTIONS") return response(route, {}, 204);
    if (path === "/session" && method === "POST") return response(route,
      { admin: { id: SESSION, email: "admin@example.test" }, expires_at: NOW, absolute_expires_at: NOW },
      200, { "Set-Cookie": "admin_mock=ok; Path=/; HttpOnly; SameSite=Lax" });
    if (!request.headers().cookie?.includes("admin_mock=ok")) return response(route,
      { error: { code: "admin_auth_required", message: "Sign in required." } }, 401);
    if (path === "/session" && method === "GET") return response(route,
      { admin: { id: SESSION, email: "admin@example.test" }, expires_at: NOW, absolute_expires_at: NOW });
    if (path === "/csrf") { state.csrfRequests++; return response(route, { csrf_token: "mock-csrf" }); }
    if (["POST", "PUT", "DELETE"].includes(method)) {
      if (request.headers()["x-csrf-token"] !== "mock-csrf") return response(route,
        { error: { code: "csrf_validation_failed", message: "CSRF required." } }, 403);
      state.mutations++;
    }
    if (path === "/session" && method === "DELETE") return response(route, {}, 204,
      { "Set-Cookie": "admin_mock=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax" });
    if (path === "/overview") return response(route, { run_counts: { complete: 1 },
      local_usage: { cost_microusd: 7000, tokens: 1200, calls: 1 },
      hourly: [{ bucket_start: NOW, total_cost_microusd: 7000, total_tokens: 1200 }],
      aggregates_stale: false, aggregates_refreshed_at: NOW,
      openrouter_credits: { status: "unavailable", remaining_microusd: null },
      operations: { worker_available: false, scheduler_fresh: false, alerts: [{
        severity: "critical", code: "worker_unavailable", title: "Worker is unavailable",
        detail: "Background run and maintenance tasks cannot be dispatched.", observed_at: NOW,
        threshold: "available", observed_value: "unavailable", recovery_link: "/admin/runs",
      }] } });
    if (path === "/cutover") return response(route, {
      phase: "retired", retirement_authorized: true,
      observation: {
        id: SESSION, environment: "production", status: "passed", test_evidence: false,
        root_mode: "v2", started_at: NOW, evaluated_at: NOW, ended_at: NOW,
        thresholds: { stable_window_hours: 24, min_terminal_runs: 20,
          public_run_token_cap: 100000, public_run_cost_cap_microusd: 500000 },
        result: { passed: true, blockers: [], metrics: [
          { code: "terminal_runs", passed: true, observed: 24, threshold: 20 },
          { code: "compatibility_quiet_period_hours", passed: true, observed: 24, threshold: 24 },
        ], distributions: { total_tokens: 34000, total_cost_microusd: 120000,
          p95_tokens_per_run: 2200, p95_cost_microusd_per_run: 9000 } },
      },
    });
    if (path === "/runs") return response(route, { items: [{ id: RUN, product: "Aurora Headphones",
      status: "running", initiator_type: "public", created_at: NOW, duration_ms: 3000,
      total_tokens: 1200, total_cost_microusd: 7000, model_call_count: 1, pending_usage_count: 0 }], next_cursor: null });
    if (path === `/runs/${RUN}`) return response(route, { id: RUN, product: "Aurora Headphones",
      status: "running", initiator_type: "public", created_at: NOW, duration_ms: 3000,
      total_tokens: 1200, total_cost_microusd: 7000, model_call_count: 1, pending_usage_count: 0,
      snapshot: { id: SESSION, workflow_version_id: PUBLISHED, budget_policy_version_id: PUBLISHED,
        agents: [], model_policies: [] }, tasks: [{ id: TASK, task_key: "analyze_review.source_1",
        status: "failed", agent_version_id: PUBLISHED, current_attempt: 1 }], dependencies: [],
      attempts: [{ id: ATTEMPT, task_run_id: TASK, number: 1, status: "failed", error_code: "upstream_timeout",
        duration_ms: 2500, prompt_hash: "b".repeat(64) }],
      usage: [{ id: SESSION, task_attempt_id: ATTEMPT, actual_model: "deepseek/deepseek-v4-flash",
        actual_provider: "mock", total_tokens: 1200, total_cost_microusd: 7000, usage_status: "complete" }],
      tool_invocations: [], workspace_id: WORKSPACE, report_id: REPORT, public_report_active: true });
    if (path === `/runs/${RUN}/cancel` && method === "POST") return response(route,
      { run_id: RUN, status: "cancelling" });
    if (path === `/tasks/${TASK}/retry` && method === "POST") return response(route,
      { task_id: TASK, status: "queued", next_attempt: 2 });
    if (path === `/reports/${REPORT}/revoke` && method === "POST") return route.fulfill({ status: 204,
      headers: { "Access-Control-Allow-Origin": "http://127.0.0.1:3000",
        "Access-Control-Allow-Credentials": "true" } });
    if (path === "/models") return response(route, { status: "ok", stale: false, fetched_at: NOW,
      last_error_category: null, total_count: 1, next_cursor: null, items: [{ slug: "deepseek/deepseek-v4-flash",
        name: "DeepSeek V4 Flash", author: "deepseek", context_length: 128000,
        pricing: { prompt: "0.00000010", completion: "0.00000020" }, available: true,
        supported_parameters: ["response_format"], output_modalities: ["text"] }] });
    if (path === "/models/deepseek/deepseek-v4-flash/endpoints") return response(route,
      { status: "ok", stale: false, endpoints: [{ provider_slug: "mock", provider_name: "Mock provider",
        context_length: 128000, pricing: { prompt: "0.00000010" },
        supported_parameters: ["response_format"], privacy: {}, status: "available",
        eligible: true, eligibility_reasons: [] }] });
    if (path === "/providers") return response(route, { status: "ok", items: [], fetched_at: NOW });
    if (path === "/models/refresh" && method === "POST") {
      state.jobs.push({ id: SESSION, kind: "catalog_refresh", status: "succeeded", safe_result: {}, error_code: null });
      return response(route, { job_id: SESSION, status: "queued" }, 202);
    }
    if (path === `/jobs/${SESSION}`) return response(route, state.jobs.at(-1) || { id: SESSION,
      kind: "agent_evaluation", status: "succeeded", safe_result: { status: "passed" }, error_code: null });
    if (path === `/jobs/${SESSION}/download`) return route.fulfill({ status: 200, body: "mock export",
      contentType: "application/zip", headers: { "Access-Control-Allow-Origin": "http://127.0.0.1:3000",
        "Access-Control-Allow-Credentials": "true" } });
    if (path.startsWith("/configuration/")) {
      const chunks = path.split("/").filter(Boolean); const kind = chunks[1];
      if (chunks.length === 2) return response(route, { items: kind === "agents" ? [{ id: AGENT,
        key: "review_analyst", name: "Review Analyst", description: "Review source evidence",
        versions: state.versions.map(version => ({ id: version.id, number: version.version_number,
          lifecycle: version.lifecycle, active: version.active, content_hash: version.content_hash })) }] : [],
        next_cursor: null, active_versions: { workflows: PUBLISHED } });
      if (chunks.length === 3) return response(route, { id: AGENT, key: "review_analyst",
        name: "Review Analyst", description: "Review source evidence",
        versions: state.versions, active_versions: { workflows: PUBLISHED } });
      if (chunks.at(-1) === "drafts" && method === "POST") {
        const draft = { ...initial, id: DRAFT, version_number: state.versions.length + 1,
          lifecycle: "draft", active: false, version: 1, published_at: null, evaluation: null };
        state.versions.unshift(draft); return response(route, draft, 201);
      }
      const draft = state.versions.find(version => version.id === chunks[4]);
      if (draft && method === "PUT") { const body = request.postDataJSON();
        draft.payload = body.payload; draft.version = Number(draft.version) + 1;
        draft.change_note = body.change_note; return response(route, draft); }
      if (chunks.at(-1) === "validate") return response(route, { valid: true });
      if (chunks.at(-1) === "evaluate") {
        if (draft) draft.evaluation = { status: "passed", metrics: { schema_valid_rate: 1 } };
        state.jobs.push({ id: SESSION, kind: "agent_evaluation", status: "succeeded",
          safe_result: { status: "passed" }, error_code: null });
        return response(route, { job_id: SESSION, status: "queued" }, 202);
      }
      if (chunks.at(-1) === "publish") { if (draft) draft.lifecycle = "published"; return response(route, draft); }
    }
    if (path === "/workspaces") return response(route, { items: [{ id: WORKSPACE, run_id: RUN,
      status: "active", neo4j_status: "current", qdrant_status: "current", projection_error_code: null,
      created_at: NOW }], next_cursor: null });
    if (path === `/workspaces/${WORKSPACE}/nodes`) return response(route, { items: [{ id: NODE,
      current_version_id: NODE_VERSION, title: "Aurora evidence", node_type: "evidence",
      status: "active", body_hash: "c".repeat(64), trust_level: "primary_source", created_at: NOW }], next_cursor: null });
    if (path === `/workspaces/${WORKSPACE}/edges`) return response(route, { items: [], next_cursor: null });
    if (path === `/workspaces/${WORKSPACE}/nodes/${NODE}`) return response(route,
      { id: NODE, versions: [{ id: NODE_VERSION, title: "Aurora evidence", body_hash: "c".repeat(64) }] });
    if (path.endsWith(`/versions/${NODE_VERSION}/body`)) return response(route,
      { body: "The battery lasted thirty hours in testing." });
    if (path === `/workspaces/${WORKSPACE}/export` || path === `/workspaces/${WORKSPACE}/rebuild-neo4j`) {
      state.jobs.push({ id: SESSION, kind: path.endsWith("export") ? "export" : "neo4j_rebuild",
        status: "succeeded", safe_result: {}, error_code: null });
      return response(route, { job_id: SESSION, status: "queued" }, 202);
    }
    if (path === "/settings" && method === "GET") return response(route, {
      active_version_id: state.activeSettingsId, kill_switch: state.kill, public_analysis_enabled: true,
      evaluation_budget: { token_limit: 300000, cost_limit_microusd: 500000,
        reserved_tokens: 0, consumed_tokens: 1000, reserved_cost_microusd: 0, consumed_cost_microusd: 2000 },
      versions: state.settingsVersions,
    });
    if (path === "/settings/kill-switch" && method === "PUT") { state.kill = request.postDataJSON().enabled;
      return response(route, { enabled: state.kill }); }
    if (path === "/settings/evaluation-budget" && method === "PUT") return response(route, request.postDataJSON());
    if (path === "/settings/drafts" && method === "POST") {
      const body = request.postDataJSON();
      const draft = { id: DRAFT, number: 2, version: 1, lifecycle: "draft",
        catalog_refresh_minutes: body.catalog_refresh_minutes, raw_content_retention: body.raw_content_retention,
        raw_content_ttl_hours: 24, change_note: body.change_note };
      state.settingsVersions.unshift(draft); return response(route, draft, 201);
    }
    if (path === `/settings/versions/${DRAFT}` && method === "PUT") {
      const draft = state.settingsVersions.find(value => value.id === DRAFT)!;
      Object.assign(draft, request.postDataJSON(), { version: Number(draft.version) + 1 });
      return response(route, draft);
    }
    if (path === `/settings/versions/${DRAFT}/publish` && method === "POST") {
      const draft = state.settingsVersions.find(value => value.id === DRAFT)!;
      draft.lifecycle = "published"; return response(route, draft);
    }
    if (path === `/settings/versions/${DRAFT}/activate` && method === "POST") {
      state.activeSettingsId = DRAFT; return response(route, { active_version_id: DRAFT });
    }
    if (path === "/sessions") return response(route, { items: [{ id: SESSION, created_at: NOW,
      last_seen_at: NOW, expires_at: NOW, revoked_at: null, current: true }], next_cursor: null });
    if (path === `/sessions/${SESSION}/revoke` && method === "POST") return response(route,
      { id: SESSION, revoked_at: NOW });
    if (path === "/analytics") return response(route, { items: [{ bucket_start: NOW,
      dimension_key: "all", request_count: 1, error_count: 0, total_tokens: 1200,
      total_cost_microusd: 7000 }], next_cursor: null });
    if (path === "/analytics/export.csv") return route.fulfill({ status: 200, contentType: "text/csv",
      body: "bucket_start,total_cost_microusd\n2026-09-18,7000\n", headers: {
        "Access-Control-Allow-Origin": "http://127.0.0.1:3000", "Access-Control-Allow-Credentials": "true",
        "Content-Disposition": 'attachment; filename="reviewlens-analytics.csv"' } });
    if (path === "/audit-events") return response(route, { items: [{ id: SESSION,
      actor_type: "admin", action: "configuration.activated", target_type: "workflow",
      target_id: PUBLISHED, before_hash: null, after_hash: "a".repeat(64),
      created_at: NOW, safe_metadata: {} }], next_cursor: null });
    if (path === "/tools") return response(route, { items: [] });
    if (path === "/tool-invocations") return response(route, { items: [], next_cursor: null });
    return response(route, { error: { code: "missing_mock", message: `Missing mock: ${method} ${path}` } }, 404);
  });
  return state;
}

async function signIn(page: Page) {
  await page.goto("http://127.0.0.1:3000/admin/login");
  await page.getByLabel("Email").fill("admin@example.test");
  await page.getByLabel("Password").fill("mock-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "System overview" })).toBeVisible();
}

async function confirm(page: Page, label: string, phrase: string) {
  await page.getByRole("button", { name: label, exact: true }).first().click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("textbox").fill(phrase);
  await dialog.getByRole("button", { name: label, exact: true }).click();
  await expect(dialog).toBeHidden();
}

test("admin overview has no serious automated accessibility violations", async ({ page }) => {
  await mockAdmin(page);
  await signIn(page);
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.filter(item => ["serious", "critical"].includes(item.impact || ""))).toEqual([]);
});

test("protected login, navigation, keyboard, and responsive layouts", async ({ page, context }) => {
  await mockAdmin(page);
  await page.goto("http://127.0.0.1:3000/admin/runs");
  await expect(page).toHaveURL(/\/admin\/login/);
  await signIn(page);
  await expect(page.getByRole("heading", { name: "Operational alerts" })).toBeVisible();
  await expect(page.getByText("worker_unavailable")).toBeVisible();
  const cookies = await context.cookies(API);
  expect(cookies.find(cookie => cookie.name === "admin_mock")?.httpOnly).toBe(true);
  await page.keyboard.press("Control+k");
  await expect(page.getByRole("dialog", { name: "Go to section" })).toBeVisible();
  await page.getByRole("dialog").getByRole("link", { name: "Models" }).click();
  await expect(page.getByRole("heading", { name: "Models and providers" })).toBeVisible();
  for (const width of [375, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(page.locator("#admin-main")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1);
  }
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.evaluate(() => { document.documentElement.style.zoom = "2"; });
  await expect(page.getByRole("heading", { name: "Models and providers" })).toBeVisible();
});

test("model routing and agent draft through evaluation, publication, and rollback", async ({ page }) => {
  const state = await mockAdmin(page); await signIn(page);
  await page.goto("http://127.0.0.1:3000/admin/models");
  await page.getByRole("textbox", { name: "Author" }).fill("deepseek");
  await expect(page).toHaveURL(/author=deepseek/);
  await page.getByRole("button", { name: /DeepSeek V4 Flash/ }).click();
  await expect(page.getByText("ELIGIBLE")).toBeVisible();
  await page.goto("http://127.0.0.1:3000/admin/agents");
  await page.getByRole("button", { name: "Open versions" }).click();
  await page.getByRole("button", { name: "Rollback as draft" }).click();
  await expect(page.getByText("New draft created.", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Save draft" }).click();
  await page.getByRole("button", { name: "Validate" }).click();
  await confirm(page, "Run evaluation", "run evaluation");
  await expect(page.getByText(/Evaluation job: succeeded/)).toBeVisible();
  const draftButton = page.getByRole("button", { name: /v2.*draft/ });
  await draftButton.click();
  await expect(draftButton).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: "Publish", exact: true })).toBeVisible();
  await confirm(page, "Publish", "review_analyst");
  expect(state.versions.some(version => version.id === DRAFT && version.lifecycle === "published")).toBe(true);
  expect(state.csrfRequests).toBeGreaterThan(0);
});

test("run recovery, graph inspector, budgets, kill switch, sessions, and CSV", async ({ page }) => {
  const state = await mockAdmin(page); await signIn(page);
  await page.goto("http://127.0.0.1:3000/admin/runs");
  await page.getByRole("link", { name: "Aurora Headphones" }).click();
  await expect(page.getByRole("heading", { name: "Task DAG" })).toBeVisible();
  await expect(page.getByText("deepseek/deepseek-v4-flash")).toBeVisible();
  await confirm(page, "Cancel run", "cancel run");
  await confirm(page, "Revoke report", "revoke report");
  await page.getByText("Attempts and recovery").click();
  await confirm(page, "Retry task", "retry task");
  await page.getByRole("link", { name: /Open workspace graph/ }).click();
  await expect(page.getByRole("heading", { name: "Workspace graph" })).toBeVisible();
  await page.getByText(/Readable workspace graph list/).click();
  await page.getByRole("button", { name: "Inspect" }).click();
  await expect(page.getByText("The battery lasted thirty hours in testing.")).toBeVisible();
  await page.getByRole("button", { name: "Export workspace" }).click();
  await expect(page.getByRole("link", { name: "Download ZIP" })).toBeVisible();
  await page.goto("http://127.0.0.1:3000/admin/settings");
  await expect(page.getByRole("spinbutton", { name: "Token cap" })).toHaveValue("300000");
  await page.getByRole("button", { name: "Create settings draft" }).click();
  await expect(page.getByRole("button", { name: "Save draft" })).toBeVisible();
  await page.getByRole("button", { name: "Save draft" }).click();
  await confirm(page, "Publish", "publish settings");
  await confirm(page, "Activate", "activate settings");
  expect(state.activeSettingsId).toBe(DRAFT);
  await confirm(page, "Enable kill switch", "enable kill switch");
  expect(state.kill).toBe(true);
  await confirm(page, "Disable kill switch", "disable kill switch");
  expect(state.kill).toBe(false);
  await confirm(page, "Revoke", "revoke session");
  await page.goto("http://127.0.0.1:3000/admin/analytics");
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export CSV" }).click();
  expect((await download).suggestedFilename()).toBe("reviewlens-analytics.csv");
  await page.goto("http://127.0.0.1:3000/admin/audit");
  await expect(page.getByRole("table", { name: "Admin audit history" })).toBeVisible();
  expect(state.mutations).toBeGreaterThanOrEqual(4);
});
