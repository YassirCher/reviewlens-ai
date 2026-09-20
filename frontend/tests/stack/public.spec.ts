import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("production root has no serious accessibility violations", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /See the buying signal/i })).toBeVisible();
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.filter((violation) => ["serious", "critical"].includes(violation.impact ?? ""))).toEqual([]);
});

test("real public V2 root journey publishes a mocked-source report", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /See the buying signal/i })).toBeVisible();
  await page.getByLabel("Product name or exact model").fill("Phase 6 complete fixture");
  await expect(page.getByText(/Research available/i)).toBeVisible();
  await page.getByRole("button", { name: /Analyze product/i }).click();
  await expect(page).toHaveURL(/\/analysis\/[0-9a-f-]{36}$/);
  await expect(page.getByRole("heading", { name: "Research timeline" })).toBeVisible();
  const reportLink = page.getByRole("link", { name: /Open report/i });
  await expect(reportLink).toBeVisible({ timeout: 240_000 });
  await reportLink.click();
  await expect(page).toHaveURL(/\/r\/[A-Za-z0-9_-]{43}$/);
  const response = await page.request.get(page.url());
  expect(response.headers()["x-robots-tag"]).toContain("noindex");
  expect(response.headers()["cache-control"]).toContain("no-store");
  await expect(page.getByText(/requested sources analyzed/i)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Inspect every source" })).toBeVisible();
  await expect(page.locator("body")).not.toContainText(/OpenRouter|API key|prompt_hash|Ignore previous instructions/i);
  await page.getByRole("link", { name: /Open full evidence map/i }).click();
  await expect(page.getByRole("heading", { name: "How the evidence connects" })).toBeVisible();
  await page.getByRole("button", { name: "List" }).click();
  await expect(page.getByText(/Select an item to inspect/i)).toBeVisible();
});
