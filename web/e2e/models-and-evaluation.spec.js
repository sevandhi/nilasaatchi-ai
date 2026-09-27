import { test, expect } from "@playwright/test";
import { requireApi } from "./helpers.js";

test("models page shows the router registry", async ({ page }) => {
  await requireApi(test);
  await page.goto("/models");
  await expect(page.getByText(/Model registry/i)).toBeVisible();
  await expect(page.locator("table").first().locator("tbody tr").first()).toBeVisible({ timeout: 15000 });
  await page.screenshot({ path: "../docs/screens/07-models-routing.png", fullPage: true });
});


