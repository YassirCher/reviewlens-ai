import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { productError, readEvents, reportTokenFromApiPath, validRunId, validToken } from "../../src/lib/v2";

const RUN = "11111111-1111-4111-8111-111111111111";

test("public path and input validation rejects unsafe shapes", () => {
  expect(productError("Sony WH-1000XM5")).toBeNull();
  expect(productError("  ")).toBeTruthy();
  expect(productError("a\nsecret")).toBeTruthy();
  expect(productError("x".repeat(501))).toBeTruthy();
  expect(reportTokenFromApiPath(`/api/v2/reports/${"A".repeat(43)}`)).toBe("A".repeat(43));
  expect(reportTokenFromApiPath("https://evil.example/report")).toBeNull();
  expect(validToken("A".repeat(43))).toBe(true);
  expect(validRunId(RUN)).toBe(true);
  expect(validRunId("../admin")).toBe(false);
});

test("SSE parser replays from Last-Event-ID and ignores duplicate frames and heartbeats", async () => {
  const original = globalThis.fetch;
  let header = "";
  globalThis.fetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
    header = String(new Headers(init?.headers).get("Last-Event-ID"));
    const events = [": heartbeat\n\n", `id: 2\nevent: task.progress\ndata: ${JSON.stringify({ sequence: 2, run_id: RUN, label: "Review underway", detail: "", completed_tasks: 2, total_tasks: 4, percent: 50, timestamp: "2026-09-17T09:00:00Z" })}\n\n`, `id: 3\nevent: run.completed\ndata: ${JSON.stringify({ sequence: 3, run_id: RUN, label: "Completed", detail: "", completed_tasks: 4, total_tasks: 4, percent: 100, timestamp: "2026-09-17T09:01:00Z" })}\n\n`].join("");
    return new Response(new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode(events.slice(0, 27))); controller.enqueue(new TextEncoder().encode(events.slice(27))); controller.close(); } }), { status: 200, headers: { "Content-Type": "text/event-stream" } });
  }) as typeof fetch;
  try {
    const seen: number[] = [];
    await readEvents(RUN, 2, (_name, data) => seen.push(data.sequence), new AbortController().signal);
    expect(header).toBe("2");
    expect(seen).toEqual([3]);
  } finally { globalThis.fetch = original; }
});

test("SSE parser rejects a mismatched run before delivering progress", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = (async () => new Response(`id: 1\nevent: run.started\ndata: ${JSON.stringify({ sequence: 1, run_id: "22222222-2222-4222-8222-222222222222" })}\n\n`, { status: 200, headers: { "Content-Type": "text/event-stream" } })) as typeof fetch;
  try { await expect(readEvents(RUN, 0, () => {}, new AbortController().signal)).rejects.toThrow("interrupted"); }
  finally { globalThis.fetch = original; }
});

test("SSE parser rejects a sequence gap so reconnect can replay from the last committed event", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = (async () => new Response(`id: 4\nevent: task.progress\ndata: ${JSON.stringify({ sequence: 4, run_id: RUN })}\n\n`, { status: 200, headers: { "Content-Type": "text/event-stream" } })) as typeof fetch;
  try { await expect(readEvents(RUN, 2, () => { throw new Error("gap delivered"); }, new AbortController().signal)).rejects.toThrow("interrupted"); }
  finally { globalThis.fetch = original; }
});

test("core graphite text and action colors meet WCAG AA contrast", () => {
  const css = readFileSync("src/app/v2.css", "utf8");
  const token = (name: string) => {
    const match = css.match(new RegExp(`--v2-${name}:\\s*(#[0-9A-Fa-f]{6})`));
    expect(match, `missing ${name} token`).not.toBeNull();
    return match![1];
  };
  const luminance = (hex: string) => {
    const channels = [1, 3, 5].map(index => parseInt(hex.slice(index, index + 2), 16) / 255)
      .map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
    return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
  };
  const contrast = (a: string, b: string) => {
    const values = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (values[0] + 0.05) / (values[1] + 0.05);
  };
  for (const foreground of ["text", "muted", "subtle", "primary", "teal", "rose"]) {
    expect(contrast(token(foreground), token("s2")), `${foreground} on s2`).toBeGreaterThanOrEqual(4.5);
  }
  expect(contrast(token("bg"), token("primary")), "button label on primary").toBeGreaterThanOrEqual(4.5);
});

test("light mode text and action colors meet WCAG AA contrast", () => {
  const css = readFileSync("src/app/v2.css", "utf8");
  const token = (name: string) => {
    const matches = Array.from(css.matchAll(new RegExp(`--v2-${name}:\\s*(#[0-9A-Fa-f]{6})`, "g")));
    expect(matches.length, `missing light ${name} token`).toBeGreaterThanOrEqual(2);
    return matches[1][1];
  };
  const luminance = (hex: string) => {
    const channels = [1, 3, 5].map(index => parseInt(hex.slice(index, index + 2), 16) / 255)
      .map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
    return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
  };
  const contrast = (a: string, b: string) => {
    const values = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (values[0] + 0.05) / (values[1] + 0.05);
  };
  for (const foreground of ["text", "muted", "subtle", "primary", "teal", "rose"]) {
    expect(contrast(token(foreground), token("s2")), `light ${foreground} on s2`).toBeGreaterThanOrEqual(4.5);
  }
  expect(contrast("#FFFFFF", token("primary")), "white button label on light primary").toBeGreaterThanOrEqual(4.5);
});

