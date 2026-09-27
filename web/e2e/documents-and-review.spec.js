import { test, expect } from "@playwright/test";
import { requireApi } from "./helpers.js";

test("documents catalog renders", async ({ page }) => {
  await requireApi(test);
  await page.goto("/documents");
  await expect(page.locator("tbody tr").first()).toBeVisible({ timeout: 15000 });
  await page.screenshot({ path: "../docs/screens/09-documents.png", fullPage: true });
});

test("review queue renders crop + candidate values", async ({ page }) => {
  await requireApi(test);
  await page.goto("/review");
  await expect(page.getByTestId("review-item").first()).toBeVisible({ timeout: 15000 });
  await page.screenshot({ path: "../docs/screens/10-review-queue.png", fullPage: true });
});
