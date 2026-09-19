import { expect, test } from "@playwright/test";

const email = process.env.PHASE10_ADMIN_EMAIL;
const password = process.env.PHASE10_ADMIN_PASSWORD;

test("live admin login protects private routes and exposes operational health", async ({ page }) => {
  expect(email).toBeTruthy();
  expect(password).toBeTruthy();

  await page.goto("/admin/runs");
  await expect(page).toHaveURL(/\/admin\/login/);
  await expect(page.locator("body")).not.toContainText(/prompt_hash|deepseek\/deepseek|phase6-admin/i);

  await page.goto("/admin/login");
  await page.getByLabel("Email").fill(email!);
  await page.getByLabel("Password").fill(password!);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "System overview" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Operational alerts" })).toBeVisible();

  const response = await page.request.get("http://localhost:8000/api/v2/admin/system/health");
  expect(response.status()).toBe(200);
  expect(response.headers()["content-security-policy"]).toContain("default-src 'none'");
  const health = await response.json();
  expect(Array.isArray(health.operations.alerts)).toBe(true);

  await page.getByRole("link", { name: "Models" }).click();
  await expect(page.getByRole("heading", { name: "Models and providers" })).toBeVisible();
  await page.getByRole("link", { name: "Settings" }).click();
  await expect(page.getByRole("heading", { name: "Operational settings" })).toBeVisible();
});
