import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const RUN = "66666666-6666-4666-8666-666666666666";
const QUESTION = "77777777-7777-4777-8777-777777777777";
const availability = { allowed: true, denial_code: null, queue: { condition: "available", queued_runs: 0 },
  remaining_public_quota: { hourly_remaining: 3, daily_ip_remaining: 10, daily_session_remaining: 10, concurrent_remaining: 2 },
  estimate: { token_band: { min: 0, max: 1000 }, cost_band: "low", non_binding: true } };

function waiting() { return { run_id: RUN, status: "waiting_for_input", product_name: "Sony WH-1000XM5",
  created_at: new Date().toISOString(), started_at: new Date().toISOString(), completed_at: null,
  source_count_requested: 3, source_count_analyzed: 0, completed_tasks: 3, total_tasks: 20,
  warnings: [], failure: null, total_tokens: 100, usage_pending: false, report_url: null, progress_sequence: 1,
  tasks: [{ task_key: "resolve_discovered_product", label: "Resolve product", status: "waiting_for_input", started_at: null, completed_at: null }],
  clarification: { id: QUESTION, resolver_version: "product-intent-v1", question: "Which exact product should this run analyze?",
    expires_at: new Date(Date.now() + 600_000).toISOString(), choices: [
      { id: "wf1000xm5", product_name: "Sony WF-1000XM5", sources: [{ title: "WF-1000XM5 long term review", url: "https://www.youtube.com/watch?v=abcdefghijk" }] },
      { id: "wh1000xm4", product_name: "Sony WH-1000XM4", sources: [{ title: "WH-1000XM4 review", url: "https://www.youtube.com/watch?v=lmnopqrstuv" }] },
    ] } }; }

test("broad intake asks before admission and requires an explicit analyze after editing", async ({ page }) => {
  const submissions: unknown[] = [];
  await page.route("**/api/v2/analyses/preflight", async route => {
    const input = route.request().postDataJSON();
    const broad = input.product_name === "iPhone";
    await route.fulfill({ json: { ...availability, normalized_options: input, allowed: !broad,
      denial_code: broad ? "product_clarification_required" : null,
      intent_resolution: { status: broad ? "requires_clarification" : "resolved", reason: broad ? "incomplete_family" : null,
        question: broad ? "Which exact iPhone model do you mean?" : null, canonical_name: broad ? null : input.product_name, resolver_version: "product-intent-v1" } } });
  });
  await page.route("**/api/v2/analyses", async route => { submissions.push(route.request().postDataJSON()); await route.fulfill({ status: 503, json: { error: { message: "Fixture stopped after admission check." } } }); });
  await page.goto("/");
  await page.getByLabel("Product name or exact model").fill("iPhone");
  await expect(page.getByRole("heading", { name: "Which product do you mean?" })).toBeVisible();
  await page.getByRole("button", { name: "Analyze product" }).click();
  expect(submissions).toHaveLength(0);
  await page.getByLabel("Product name or exact model").fill("iPhone 16 Pro Max");
  await expect(page.locator(".v2-admission")).toContainText("Research available");
  expect(submissions).toHaveLength(0);
  await page.getByText("Research options", { exact: false }).click();
  await expect(page.getByLabel("Review sources")).toHaveValue("3");
  expect(await page.getByLabel("Review sources").locator("option").allTextContents()).toEqual(["3 videos · default", "4 videos", "5 videos"]);
  await page.getByRole("button", { name: "Analyze product" }).click();
  await expect.poll(() => submissions.length).toBe(1);
  expect(submissions[0]).toMatchObject({ product_name: "iPhone 16 Pro Max", video_count: 3 });
});

for (const theme of ["dark", "light"] as const) for (const width of [375, 768, 1024, 1440]) {
  test(`waiting question recovers, keyboard works and layout is accessible: ${theme} ${width}`, async ({ page }) => {
    let state = waiting();
    let answers = 0;
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.addInitScript(value => localStorage.setItem("reviewlens-theme", value), theme);
    await page.route(`**/api/v2/analyses/${RUN}`, route => route.fulfill({ json: state }));
    await page.route(`**/api/v2/analyses/${RUN}/events`, route => route.fulfill({ status: 503, json: {} }));
    await page.route(`**/clarifications/${QUESTION}/answer`, async route => {
      answers++;
      expect(route.request().postDataJSON()).toEqual({ choice_id: "wf1000xm5" });
      state = { ...state, status: "running", clarification: null } as unknown as ReturnType<typeof waiting>;
      await route.fulfill({ json: state });
    });
    await page.goto(`/analysis/${RUN}`);
    await expect(page.getByRole("heading", { name: "Which exact product should this run analyze?" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Confirm model and resume" })).toBeDisabled();
    expect(await page.getByRole("radio", { checked: true }).count()).toBe(0);
    await page.reload();
    await expect(page.getByRole("heading", { name: "Which exact product should this run analyze?" })).toBeVisible();
    const radio = page.getByRole("radio", { name: "Sony WF-1000XM5", exact: true });
    await radio.focus();
    await page.keyboard.press("Space");
    await expect(radio).toBeChecked();
    expect(answers).toBe(0);
    await page.evaluate(() => { document.documentElement.style.zoom = "2"; });
    const overflow = await page.evaluate(() => {
      if (document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1) return [];
      return [...document.querySelectorAll("body *")].filter(element => element.getBoundingClientRect().right > innerWidth + 1)
        .slice(0, 12).map(element => ({ tag: element.tagName, class: element.className, right: element.getBoundingClientRect().right }));
    });
    expect(overflow).toEqual([]);
    const axe = await new AxeBuilder({ page }).analyze();
    expect(axe.violations.filter(item => ["serious", "critical"].includes(item.impact || ""))).toEqual([]);
    await page.getByRole("button", { name: "Confirm model and resume" }).click();
    await expect(page.getByText("YOUR INPUT IS NEEDED", { exact: true })).toHaveCount(0);
    expect(answers).toBe(1);
  });
}

test("return from cancellation populates intake without automatic submission", async ({ page }) => {
  let creations = 0;
  await page.route("**/api/v2/analyses", route => { creations++; return route.fulfill({ status: 503, json: {} }); });
  await page.goto("/?product=Sony%20WH-1000XM5");
  await expect(page.getByLabel("Product name or exact model")).toHaveValue("Sony WH-1000XM5");
  expect(creations).toBe(0);
});
