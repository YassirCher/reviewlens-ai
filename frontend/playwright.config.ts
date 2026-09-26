import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.REVIEWLENS_E2E_BASE_URL || "http://127.0.0.1:3000";
const mockOrigin = "http://127.0.0.1:8899";
const appPort = new URL(baseURL).port || "3000";

export default defineConfig({
  testDir: "./tests",
  timeout: 35_000,
  expect: { timeout: 12_000 },
  fullyParallel: false,
  workers: 1,
  use: {
    ...devices["Desktop Chrome"],
    baseURL,
    browserName: "chromium",
    channel: process.env.PLAYWRIGHT_CHROME_CHANNEL || (process.platform === "win32" ? "chrome" : undefined),
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: process.env.PHASE8_EXTERNAL_SERVERS === "1" ? undefined : [
    { command: "node tests/mock-api.mjs", url: `${mockOrigin}/health`, reuseExistingServer: false, timeout: 30_000 },
    { command: `node node_modules/next/dist/bin/next dev --hostname 127.0.0.1 --port ${appPort}`, url: baseURL, reuseExistingServer: false, timeout: 90_000, env: { NEXT_PUBLIC_API_BASE_URL: mockOrigin, V2_API_INTERNAL_URL: mockOrigin } },
  ],
});
