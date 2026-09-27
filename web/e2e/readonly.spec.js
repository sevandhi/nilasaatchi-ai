import { test, expect } from "@playwright/test";
import { requireApi } from "./helpers.js";

// Read-only cloud build smoke tests (T7.2). Run with `npx playwright test --config
// playwright.readonly.config.js` — a separate dev server started in `--mode cloud` (see that
// config) so `VITE_READ_ONLY=true` is baked in while still pointed at a real API for data.

test("read-only banner is visible on every page", async ({ page }) => {
  await requireApi(test);
  for (const path of ["/", "/documents", "/agent", "/review", "/map"]) {
    await page.goto(path);
    await expect(page.getByTestId("readonly-banner")).toBeVisible();
    await expect(page.getByTestId("readonly-banner")).toContainText(/Read-only cloud demo/i);
  }
});

test("overview KPIs still load", async ({ page }) => {
  await requireApi(test);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /Paper vs Planet/i })).toBeVisible();
  await expect(page.getByText("Documents", { exact: true }).first()).toBeVisible({ timeout: 15000 });
  // The satellite-refresh button is a mutation and must be hidden; the freshness numbers (a GET)
  // must still render.
  await expect(page.getByTestId("satellite-freshness")).toBeVisible({ timeout: 15000 });
  await expect(page.getByTestId("satellite-refresh-button")).toHaveCount(0);
  await page.screenshot({ path: "test-results/readonly-01-overview.png", fullPage: true });
});

test("map page loads", async ({ page }) => {
  await requireApi(test);
  await page.goto("/map");
  await expect(page.getByTestId("readonly-banner")).toBeVisible();
  await expect(page.getByTestId("colorby-select")).toBeVisible({ timeout: 15000 });
  await page.screenshot({ path: "test-results/readonly-02-map.png", fullPage: true });
});

test("documents page hides the upload panel and recent uploads", async ({ page }) => {
  await requireApi(test);
  await page.goto("/documents");
  await expect(page.locator("tbody tr").first()).toBeVisible({ timeout: 15000 });
  await expect(page.getByTestId("upload-panel")).toHaveCount(0);
  await expect(page.getByTestId("upload-job-row")).toHaveCount(0);
  await page.screenshot({ path: "test-results/readonly-03-documents.png", fullPage: true });
});

test("agent console hides the Run control but keeps run history", async ({ page }) => {
  await requireApi(test);
  await page.goto("/agent");
  await expect(page.getByTestId("readonly-banner")).toBeVisible();
  await expect(page.getByTestId("agent-command-input")).toHaveCount(0);
  await expect(page.getByTestId("agent-command-submit")).toHaveCount(0);
  await expect(page.getByTestId("example-chip")).toHaveCount(0);
  await expect(page.getByText(/Run history/i)).toBeVisible({ timeout: 15000 });
  await page.screenshot({ path: "test-results/readonly-04-agent.png", fullPage: true });
});

test("chaos toggle and top-bar search are absent from the top bar", async ({ page }) => {
  await requireApi(test);
  await page.goto("/");
  await expect(page.getByTestId("chaos-toggle")).toHaveCount(0);
  await expect(page.getByTestId("command-bar-input")).toHaveCount(0);
  // Demo mask + language toggle are not mutations against the API — they must still work.
  await expect(page.getByTestId("demo-mask-toggle")).toBeVisible();
  await expect(page.getByTestId("lang-ta")).toBeVisible();
});

test("review queue keeps viewing but hides accept/edit/reject", async ({ page }) => {
  await requireApi(test);
  await page.goto("/review");
  const item = page.getByTestId("review-item").first();
  await expect(item).toBeVisible({ timeout: 15000 });
  await expect(item.getByRole("button", { name: /Approve|Confirm/i })).toHaveCount(0);
  await expect(item.getByRole("button", { name: /Reject/i })).toHaveCount(0);
  await expect(item.getByRole("button", { name: /Correct values/i })).toHaveCount(0);
  await page.screenshot({ path: "test-results/readonly-05-review.png", fullPage: true });
});
