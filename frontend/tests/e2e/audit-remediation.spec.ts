import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const RUN = "11111111-1111-4111-8111-111111111111";
const API = "http://127.0.0.1:8899";
const allowed = { normalized_options: { product_name: "Test model", video_count: 5, analyze_comments: false, locale: "en" },
  allowed: true, denial_code: null, recovery: null, queue: { condition: "available", queued_runs: 0 },
  remaining_public_quota: { hourly_remaining: 3, daily_ip_remaining: 10, daily_session_remaining: 10, concurrent_remaining: 2 },
  estimate: { token_band: { min: 0, max: 1000 }, cost_band: "low", non_binding: true } };

test("sign-in modal contains focus, restores the opener, and handles both forms", async ({ page }) => {
  await page.route("**/api/v2/user/me", route => route.fulfill({ status: 401, json: {} }));
  await page.route("**/api/v2/user/register", route => route.fulfill({ status: 422, json: { error: { message: "Please check your registration details." } } }));
  await page.goto("/");
  const opener = page.getByRole("button", { name: "Sign in", exact: true });
  await opener.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("Email Address")).toBeFocused();
  for (let index = 0; index < 20; index++) {
    await page.keyboard.press(index < 10 ? "Tab" : "Shift+Tab");
    expect(await dialog.evaluate(element => element.contains(document.activeElement))).toBe(true);
  }
  await dialog.getByRole("button", { name: "Create Account", exact: true }).first().click();
  await dialog.getByLabel("Email Address").fill("audit@example.invalid");
  await dialog.getByLabel("Password", { exact: true }).fill("audit-fixture-password");
  await dialog.getByRole("button", { name: "Create Account", exact: true }).last().click();
  await expect(dialog.getByRole("alert")).toContainText("registration details");
  const axe = await new AxeBuilder({ page }).analyze();
  expect(axe.violations.filter(item => ["serious", "critical"].includes(item.impact || ""))).toEqual([]);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(opener).toBeFocused();
  await opener.click();
  await dialog.getByRole("button", { name: "Close dialog" }).click();
  await expect(opener).toBeFocused();
});

test("option changes clear a previous creation failure", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Research options", { exact: false }).click();
  await page.getByLabel("Product name or exact model").fill(`Retry Widget options ${Date.now()}`);
  await page.getByRole("button", { name: /Analyze product/i }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Connection interrupted" })).toBeVisible();
  await page.getByLabel("Include top comments").check();
  await expect(page.getByText("Connection interrupted. Retry the submission.")).toHaveCount(0);
  await expect(page.locator(".v2-admission")).toContainText("Research available");
  await page.getByLabel("Review sources").selectOption("4");
  await expect(page.locator(".v2-admission")).toContainText("Research available");
});

test("login keeps dialog focus during loading and recovers after an authentication error", async ({ page }) => {
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  let calls = 0;
  await page.route("**/api/v2/user/me", route => route.fulfill({ status: 401, json: {} }));
  await page.route("**/api/v2/user/login", async route => {
    if (++calls === 1) {
      await held;
      await route.fulfill({ status: 401, json: { error: { message: "Invalid email or password." } } });
    } else await route.fulfill({ json: { user: { id: RUN, email: "audit@example.invalid", name: "Audit fixture" } } });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Email Address").fill("audit@example.invalid");
  await dialog.getByLabel("Password", { exact: true }).fill("audit-fixture-password");
  await dialog.getByRole("button", { name: "Sign In", exact: true }).last().click();
  await expect(dialog.getByRole("button", { name: "Signing in...", exact: true })).toBeDisabled();
  await page.keyboard.press("Tab");
  expect(await dialog.evaluate(element => element.contains(document.activeElement))).toBe(true);
  release();
  await expect(dialog.getByRole("alert")).toContainText("Invalid email or password");
  await dialog.getByRole("button", { name: "Sign In", exact: true }).last().click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText("Audit fixture", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign out", exact: true })).toBeVisible();
});

test("unchanged retry retains its key and changed input receives a new key", async ({ page }) => {
  const keys: string[] = [];
  await page.route("**/api/v2/analyses", async route => {
    keys.push(route.request().headers()["idempotency-key"]);
    await route.fulfill({ status: 503, json: { error: { code: "temporary_unavailable", message: "Please retry this submission.", retryable: true } } });
  });
  await page.goto("/");
  await page.getByText("Research options", { exact: false }).click();
  await page.getByLabel("Product name or exact model").fill("Idempotency fixture");
  const submit = page.getByRole("button", { name: /Analyze product/i });
  await submit.click();
  await expect(page.getByText("Please retry this submission.")).toBeVisible();
  await submit.click();
  await expect.poll(() => keys.length).toBe(2);
  await expect(submit).toBeEnabled();
  expect(keys[1]).toBe(keys[0]);
  await page.getByLabel("Include top comments").check();
  await submit.click();
  await expect.poll(() => keys.length).toBe(3);
  expect(keys[2]).not.toBe(keys[1]);
});

test("outdated availability responses cannot replace current options", async ({ page }) => {
  let release!: () => void;
  let waiting = false;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/v2/analyses/preflight", async route => {
    if (route.request().postDataJSON().product_name === "Old fixture") {
      waiting = true;
      await held;
      await route.fulfill({ json: { ...allowed, allowed: false, denial_code: "public_quota_exceeded" } }).catch(() => {});
    } else await route.fulfill({ json: allowed });
  });
  await page.goto("/");
  await page.getByLabel("Product name or exact model").fill("Old fixture");
  await expect.poll(() => waiting).toBe(true);
  await page.getByLabel("Product name or exact model").fill("Current fixture");
  await expect(page.locator(".v2-admission")).toContainText("Research available");
  release();
  await expect(page.locator(".v2-admission")).not.toContainText("limit has been reached");
});

test("timed recovery rechecks availability without creating a run", async ({ page }) => {
  let checks = 0;
  let creations = 0;
  await page.route("**/api/v2/analyses/preflight", route => route.fulfill({ json: ++checks === 1 ? {
    ...allowed, allowed: false, denial_code: "public_quota_exceeded",
    recovery: { reasons: ["hourly"], retry_at: null, retry_after_seconds: 1 },
  } : allowed }));
  await page.route("**/api/v2/analyses", route => { creations++; return route.abort(); });
  await page.goto("/");
  await page.getByLabel("Product name or exact model").fill("Timed recovery fixture");
  await expect(page.locator(".v2-admission")).toContainText("1 seconds");
  await expect(page.locator(".v2-admission")).toContainText("Research available");
  expect(checks).toBe(2);
  expect(creations).toBe(0);
  await expect(page.getByLabel("Product name or exact model")).toHaveValue("Timed recovery fixture");
});

test("report fixture counts, references, and terminal timeline are consistent", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Product name or exact model").fill("Fixture consistency model");
  await page.getByRole("button", { name: /Analyze product/i }).click();
  await expect(page.getByRole("link", { name: /Open report/i })).toBeVisible();
  const report = await (await page.request.get(`${API}/api/v2/reports/${"A".repeat(43)}`)).json();
  expect(report.source_count_analyzed).toBe(5);
  expect(report.sources).toHaveLength(5);
  const ids = new Set(report.sources.map((source: { id: string }) => source.id));
  for (const finding of [...report.consensus_pros, ...report.consensus_cons]) {
    for (const id of finding.source_ids) expect(ids.has(id)).toBe(true);
  }
  const partial = await (await page.request.get(`${API}/api/v2/reports/${"C".repeat(43)}`)).json();
  expect(partial.status).toBe("partial");
  expect(partial.source_count_analyzed).toBe(partial.sources.length);
  expect(partial.source_count_analyzed).toBeLessThan(partial.source_count_requested);
  const partialIds = new Set(partial.sources.map((source: { id: string }) => source.id));
  for (const finding of [...partial.consensus_pros, ...partial.consensus_cons]) {
    for (const id of finding.source_ids) expect(partialIds.has(id)).toBe(true);
  }
  const status = await (await page.request.get(`${API}/api/v2/analyses/${RUN}`)).json();
  expect(status.completed_tasks).toBe(status.tasks.length);
  expect(status.total_tasks).toBe(status.tasks.length);
  expect(status.tasks.every((task: { status: string }) => task.status === "succeeded")).toBe(true);
  expect(status.tasks.some((task: { task_key: string }) => task.task_key === "build_consensus")).toBe(true);
  await expect(page.locator(".v2-timeline")).not.toContainText("queued");
  const alias = await page.request.get(`http://localhost:3000/analysis/${RUN}?audit=1`, { maxRedirects: 0 });
  expect(alias.status()).toBe(307);
  expect(alias.headers().location).toBe(`http://127.0.0.1:3000/analysis/${RUN}?audit=1`);
});
