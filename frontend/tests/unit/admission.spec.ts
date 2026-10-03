import { expect, test } from "@playwright/test";
import { admissionMessage, createRun, nextAvailabilityCheck } from "../../src/lib/v2";
import { canonicalLocalRedirect } from "../../src/lib/local-origin";

test("timed, concurrency, queue, and run budget guidance stay distinct", () => {
  expect(admissionMessage("public_quota_exceeded", { reasons: ["hourly"], retry_at: "2026-10-04T12:00:00Z", retry_after_seconds: 60 })).toMatch(/hourly.*Earliest retry/);
  expect(admissionMessage("public_quota_exceeded", { reasons: ["concurrent"], retry_at: null, retry_after_seconds: null })).toMatch(/active research to finish/);
  expect(admissionMessage("queue_full", { reasons: ["queue"], retry_at: null, retry_after_seconds: 60 })).toMatch(/queue.*60 seconds/);
  expect(admissionMessage("public_daily_budget_exceeded", { reasons: ["run_budget"], retry_at: null, retry_after_seconds: null })).toMatch(/fewer review sources/);
  expect(admissionMessage("public_daily_budget_exceeded", { reasons: ["run_budget"], retry_at: null, retry_after_seconds: null })).not.toMatch(/tomorrow/);
});

test("Retry-After accepts delta seconds or HTTP dates and never auto-submits", () => {
  const now = Date.parse("2026-10-03T12:00:00Z");
  expect(nextAvailabilityCheck(null, "120", now)).toBe(now + 120_000);
  expect(nextAvailabilityCheck(null, "Sat, 03 Oct 2026 12:02:00 GMT", now)).toBe(now + 120_000);
  expect(nextAvailabilityCheck(null, "invalid", now)).toBeNull();
  expect(nextAvailabilityCheck({ reasons: ["concurrent"], retry_at: null, retry_after_seconds: null }, null, now)).toBeNull();
});

test("API error preserves recovery body and browser-readable Retry-After", async () => {
  const original = globalThis.fetch;
  const recovery = { reasons: ["daily_session"], retry_at: "2026-10-04T12:00:00Z", retry_after_seconds: 3600 };
  globalThis.fetch = (async () => new Response(JSON.stringify({ error: { code: "public_rate_limit_exceeded", message: "Limit reached", retryable: true, details: { recovery } } }), { status: 429, headers: { "Retry-After": "3600" } })) as typeof fetch;
  try {
    await expect(createRun({ product_name: "Test model", video_count: 5, analyze_comments: false }, "independent-test-key")).rejects.toMatchObject({
      name: "V2ApiError", recovery, retryAfter: "3600",
    });
  } finally { globalThis.fetch = original; }
});

test("header-only quota errors still render retry guidance", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = (async () => new Response(JSON.stringify({ error: { code: "public_rate_limit_exceeded", message: "Limit reached" } }), { status: 429, headers: { "Retry-After": "120" } })) as typeof fetch;
  try {
    const error = await createRun({ product_name: "Test model", video_count: 5, analyze_comments: false }, "header-only-test-key").catch(value => value);
    expect(error.name).toBe("V2ApiError");
    expect(admissionMessage(error.code, error.recovery, error.message)).toContain("120 seconds");
  } finally { globalThis.fetch = original; }
});

test("canonical loopback alias preserves paths and queries without redirecting deployments", () => {
  expect(canonicalLocalRedirect("http://127.0.0.1:3000/analysis/123?retry=1")).toBe("http://localhost:3000/analysis/123?retry=1");
  expect(canonicalLocalRedirect("http://localhost:3000/")).toBeNull();
  expect(canonicalLocalRedirect("https://review.example/r/token", "https://review.example")).toBeNull();
  expect(canonicalLocalRedirect("http://localhost:3000/", "http://127.0.0.1:3000")).toBe("http://127.0.0.1:3000/");
  expect(canonicalLocalRedirect("http://127.0.0.1:3000/", "invalid")).toBeNull();
});
