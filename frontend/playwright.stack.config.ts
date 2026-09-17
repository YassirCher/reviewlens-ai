import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/stack",
  timeout: 300_000,
  expect: { timeout: 20_000 },
  workers: 1,
  use: {
    ...devices["Desktop Chrome"],
    baseURL: "http://localhost:3000",
    browserName: "chromium",
    channel: process.env.PLAYWRIGHT_CHROME_CHANNEL || (process.platform === "win32" ? "chrome" : undefined),
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
