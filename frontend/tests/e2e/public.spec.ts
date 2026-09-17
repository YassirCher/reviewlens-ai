import { expect, test } from "@playwright/test";

const TOKEN = "A".repeat(43);
const PARTIAL = "C".repeat(43);
const LONG = "D".repeat(43);
const RUN = "11111111-1111-4111-8111-111111111111";

test("intake checks quota and creates an owner-session run without a provider picker", async ({ page }) => {
  await page.goto("/research");
  await expect(page.getByRole("heading", { name: /See the buying signal/i })).toBeVisible();
  await expect(page.getByLabel("Product name or exact model")).toBeVisible();
  await expect(page.getByText("Auto provider")).toHaveCount(0);
  await page.getByText("Research options", { exact: false }).click();
  await expect(page.getByLabel("Review sources")).toHaveValue("5");
  await expect(page.getByLabel("Include top comments")).not.toBeChecked();
  await page.getByLabel("Product name or exact model").fill("Quota widget");
  await expect(page.getByText(/limit has been reached/i)).toBeVisible();
  await page.getByLabel("Product name or exact model").fill("Sony WH-1000XM5 headphones");
  await expect(page.getByText(/Research available/i)).toBeVisible();
  await page.getByRole("button", { name: /Analyze product/i }).click();
  await expect(page).toHaveURL(`/analysis/${RUN}`);
  await expect(page.getByRole("heading", { name: "Research timeline" })).toBeVisible();
  await expect(page.getByRole("link", { name: /Open report/i })).toBeVisible();
  await page.getByRole("link", { name: /Open report/i }).click();
  await expect(page).toHaveURL(`/r/${TOKEN}`);
  await expect(page.getByRole("heading", { name: "Buy With Caveats" })).toBeVisible();
});

test("submission retry reuses its durable idempotency key", async ({ page }) => {
  await page.goto("/research");
  await page.getByLabel("Product name or exact model").fill("Retry Widget");
  await page.getByRole("button", { name: /Analyze product/i }).click();
  await expect(page.getByText(/Connection interrupted. Retry the submission/i)).toBeVisible();
  await page.getByRole("button", { name: /Analyze product/i }).click();
  await expect(page).toHaveURL(`/analysis/${RUN}`);
});

test("report shows evidence and unlisted sharing without private fields", async ({ page }) => {
  await page.goto(`/r/${TOKEN}`);
  await expect(page.getByText("3 of 5 requested sources analyzed")).toBeVisible();
  await expect(page.getByText("3,128")).toBeVisible();
  await expect(page.getByText("8", { exact: true })).toBeVisible();
  await expect(page.getByText("Disagreement matters.")).toBeVisible();
  await page.getByText(/Comfort remains strong over long sessions/).first().click();
  await expect(page.getByText(/ear pads stayed comfortable/i).first()).toBeVisible();
  const timestamp = page.getByRole("link", { name: /1:32/i }).first();
  await expect(timestamp).toHaveAttribute("href", /youtube\.com\/watch\?v=7lCDEYXw3mM&t=92s/);
  await expect(page.locator("body")).not.toContainText(/microUSD|API key|OpenRouter|prompt_hash|node_version_id/i);
  const response = await page.request.get(`/r/${TOKEN}`);
  expect(response.headers()["x-robots-tag"]).toContain("noindex");
  expect(response.headers()["cache-control"]).toMatch(/no-store|no-cache/);
});

test("evidence map has filters, source provenance, contradiction, and a list alternative", async ({ page }) => {
  await page.goto(`/r/${TOKEN}/evidence`);
  await expect(page.getByRole("heading", { name: "How the evidence connects" })).toBeVisible();
  await page.getByRole("button", { name: "List" }).click();
  await expect(page.getByText("Select an item to inspect")).toBeVisible();
  await page.getByRole("button", { name: /Load more connections/i }).click();
  await page.getByRole("button", { name: "Disagreements" }).click();
  await expect(page.getByRole("button", { name: /Battery life disagreement/i })).toBeVisible();
  await page.getByRole("button", { name: /Battery life disagreement/i }).click();
  await expect(page.getByText("Reviewer disagreement")).toBeVisible();
});

test("partial report and revoked report keep honest, neutral states", async ({ page }) => {
  await page.goto(`/r/${PARTIAL}`);
  await expect(page.getByText("PARTIAL COVERAGE")).toBeVisible();
  await expect(page.getByText(/Some requested sources were unavailable/i)).toBeVisible();
  await page.goto(`/r/${"B".repeat(43)}`);
  await expect(page.getByRole("heading", { name: "Report not found" })).toBeVisible();
  await expect(page.locator("body")).not.toContainText(/revoked|token hash|admin/i);
});

test("owner status is isolated and a failed stream falls back to polling", async ({ page }) => {
  await page.goto(`/analysis/${RUN}`);
  await expect(page.getByText(/Analysis not found or access expired/i)).toBeVisible();
  await page.goto("/research");
  await page.getByLabel("Product name or exact model").fill("Partial Widget");
  await page.getByRole("button", { name: /Analyze product/i }).click();
  await expect(page).toHaveURL(/\/analysis\/22222222/);
  await expect(page.getByText(/Reconnecting|Polling for updates/i)).toBeVisible();
  await expect(page.getByRole("link", { name: /Open report/i })).toBeVisible({ timeout: 20_000 });
});

test("a failed run exposes an actionable safe category and never offers a report", async ({ page }) => {
  await page.goto("/research");
  await page.getByLabel("Product name or exact model").fill("Fail Widget");
  await page.getByRole("button", { name: /Analyze product/i }).click();
  await expect(page).toHaveURL(/\/analysis\/44444444/);
  await expect(page.getByText(/usable captions were unavailable/i)).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("link", { name: /Open report/i })).toHaveCount(0);
});

test("temporary admission failure remains clear without exposing upstream details", async ({ page }) => {
  await page.goto("/research");
  await page.getByLabel("Product name or exact model").fill("Maintenance Widget");
  await expect(page.getByText(/availability cannot be checked now/i)).toBeVisible();
  await expect(page.locator("body")).not.toContainText(/secret|traceback|upstream body/i);
});

test("cancellation ends without a public report", async ({ page }) => {
  await page.goto("/research");
  await page.getByLabel("Product name or exact model").fill("Cancel Widget");
  await page.getByRole("button", { name: /Analyze product/i }).click();
  await expect(page).toHaveURL(/\/analysis\/33333333/);
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: /Cancel analysis/i }).click();
  await expect(page.getByText(/No public report was published/i)).toBeVisible();
  await expect(page.getByRole("link", { name: /Open report/i })).toHaveCount(0);
});

for (const width of [375, 768, 1024, 1440]) {
  test(`research and report avoid horizontal page overflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 850 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/research");
    await expect(page.getByRole("link", { name: /Skip to main content/i })).toHaveCount(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    await page.goto(`/r/${TOKEN}`);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  });
}

test("200% zoom and keyboard navigation retain the primary flow", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.goto("/research");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: /Skip to main content/i })).toBeFocused();
  await page.evaluate(() => { document.body.style.zoom = "200%"; });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  await page.getByLabel("Product name or exact model").fill("Sony WH-1000XM5 headphones");
  await expect(page.getByRole("button", { name: /Analyze product/i })).toBeEnabled();
});

test("long content, mobile V1 link, and reduced motion remain usable", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 850 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await expect(page.getByRole("link", { name: /V2 research preview/i })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  await page.goto(`/r/${LONG}`);
  await expect(page.getByRole("heading", { name: /deliberately long model designation/i })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  await page.goto("/research");
  expect(await page.locator(".v2-button").first().evaluate(element => getComputedStyle(element).transitionDuration)).toBe("0s");
});

test("visual review captures desktop and mobile intake/report states", async ({ page }) => {
  for (const width of [375, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/research");
    await page.screenshot({ path: `test-results/research-${width}.png` });
    await page.goto(`/r/${TOKEN}`);
    await page.screenshot({ path: `test-results/report-${width}.png` });
  }
});
