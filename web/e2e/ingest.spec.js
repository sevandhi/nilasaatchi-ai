import { test, expect } from "@playwright/test";

// These tests mock the /ingest/* endpoints with page.route() so they exercise the upload +
// satellite-refresh UI without needing the backend feature to exist yet (it is being built in
// parallel — see the contract in web/src/components/ingest/schemas.js). Other endpoints the
// pages also call (/documents, /stats/overview, /ingest/jobs list on the pages that don't mock
// it) hit the real API at :8000, which is expected to be running.

const DOC_STAGES = ["store", "catalog", "classify", "extract", "load", "match", "findings"];

function docStagesAt(doneCount) {
  return DOC_STAGES.map((name, i) => ({
    name,
    status: i < doneCount ? "done" : i === doneCount ? "running" : "pending",
    started_at: i <= doneCount ? "2026-09-27T00:00:00Z" : null,
    finished_at: i < doneCount ? "2026-09-27T00:00:01Z" : null,
    detail: i < doneCount ? "ok" : null,
  }));
}

function pdfFile(name) {
  return { name, mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 test file contents") };
}

test.describe("document upload", () => {
  test("progresses queued -> running -> done and shows a result summary", async ({ page }) => {
    let jobCall = 0;
    await page.route("**/ingest/documents", async (route) => {
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({ job_id: "job-1", status: "queued", duplicate_of: null }),
      });
    });
    await page.route(/\/ingest\/jobs\/job-1(\?|$)/, async (route) => {
      jobCall += 1;
      const base = { id: "job-1", kind: "document", filename: "sample.pdf", created_at: "2026-09-27T00:00:00Z" };
      if (jobCall === 1) {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...base, status: "queued", stages: docStagesAt(0) }) });
      } else if (jobCall === 2) {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...base, status: "running", stages: docStagesAt(3) }) });
      } else {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ...base,
            status: "done",
            stages: docStagesAt(7),
            result: {
              document_id: 42,
              doc_type: "Award (4(1))",
              pages: 6,
              extraction_rows: 18,
              facts: 12,
              events: 3,
              linked_parcels: ["ALK-12-034"],
              new_findings: 2,
              review_items: 1,
            },
          }),
        });
      }
    });
    await page.route(/\/ingest\/jobs\?/, async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ jobs: [] }) });
    });

    await page.goto("/documents");
    await page.getByTestId("upload-input").setInputFiles(pdfFile("sample.pdf"));
    await page.getByTestId("upload-submit").click();

    const progress = page.getByTestId("job-progress");
    await expect(progress).toBeVisible({ timeout: 10000 });
    await expect(progress.getByText(/Queued|Processing/)).toBeVisible();

    const result = page.getByTestId("document-result");
    await expect(result).toBeVisible({ timeout: 10000 });
    await expect(result).toContainText("18");
    await expect(page.getByRole("link", { name: "ALK-12-034" })).toBeVisible();
    await expect(page.getByRole("link", { name: /new findings/ })).toContainText("2");
    await page.screenshot({ path: "../docs/screens/11-upload-progress.png", fullPage: true });
  });

  test("shows a duplicate message when the document already exists", async ({ page }) => {
    await page.route("**/ingest/documents", async (route) => {
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({ job_id: "job-dup", status: "duplicate", duplicate_of: 7 }),
      });
    });
    await page.route(/\/ingest\/jobs\/job-dup(\?|$)/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ id: "job-dup", kind: "document", status: "duplicate", stages: [], document_id: 7, duplicate_of: 7 }),
      });
    });
    await page.route(/\/ingest\/jobs\?/, async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ jobs: [] }) });
    });

    await page.goto("/documents");
    await page.getByTestId("upload-input").setInputFiles(pdfFile("dup.pdf"));
    await page.getByTestId("upload-submit").click();

    const dup = page.getByTestId("duplicate-result");
    await expect(dup).toBeVisible({ timeout: 10000 });
    await expect(dup).toContainText(/already in the system/i);
    await expect(dup.getByRole("link")).toBeVisible();
  });

  test("shows a clear message when the upload service is not available (404)", async ({ page }) => {
    await page.route("**/ingest/documents", async (route) => {
      await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "Not Found" }) });
    });
    await page.route(/\/ingest\/jobs\?/, async (route) => {
      await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "Not Found" }) });
    });

    await page.goto("/documents");
    await page.getByTestId("upload-input").setInputFiles(pdfFile("sample.pdf"));
    await page.getByTestId("upload-submit").click();

    await expect(page.getByTestId("upload-submit-error")).toContainText(/not available/i, { timeout: 10000 });
  });
});

test.describe("satellite refresh", () => {
  test("shows 'already running' on a 409 and attaches to the running job's progress", async ({ page }) => {
    // Keyed off whether the refresh POST has actually been hit (by the button click), not off a
    // raw call counter — React StrictMode intentionally double-invokes mount effects in dev, and
    // a counter tied to "how many times has status been fetched" would flag the second, discarded
    // mount-effect invocation as if the user had already clicked "check for new images".
    let refreshTriggered = false;
    await page.route(/\/ingest\/satellite\/status(\?|$)/, async (route) => {
      const running = refreshTriggered ? "sat-job-1" : null;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ latest_scene_date: "2026-08-01", scene_count: 340, last_refresh_at: "2026-08-02T00:00:00Z", running_job_id: running }),
      });
    });
    await page.route("**/ingest/satellite-refresh", async (route) => {
      refreshTriggered = true;
      await route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: "A refresh is already running." }) });
    });
    await page.route(/\/ingest\/jobs\/sat-job-1(\?|$)/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "sat-job-1",
          kind: "satellite",
          status: "running",
          stages: [
            { name: "inventory", status: "done" },
            { name: "extract", status: "running" },
            { name: "features", status: "pending" },
            { name: "classify", status: "pending" },
            { name: "findings", status: "pending" },
          ],
        }),
      });
    });

    await page.goto("/");
    await expect(page.getByTestId("satellite-freshness")).toBeVisible({ timeout: 10000 });
    await page.getByTestId("satellite-refresh-button").click();

    await expect(page.getByTestId("satellite-refresh-error")).toContainText(/already running/i, { timeout: 10000 });
    await expect(page.getByTestId("job-progress")).toBeVisible({ timeout: 10000 });
    await page.screenshot({ path: "../docs/screens/12-satellite-refresh-conflict.png", fullPage: true });
  });

  test("shows a clear message when the satellite service is not available (404)", async ({ page }) => {
    await page.route(/\/ingest\/satellite\/status(\?|$)/, async (route) => {
      await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "Not Found" }) });
    });

    await page.goto("/");
    await expect(page.getByTestId("satellite-freshness-unavailable")).toBeVisible({ timeout: 10000 });
  });
});
