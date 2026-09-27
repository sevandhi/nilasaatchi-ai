import { test, expect } from "@playwright/test";
import { requireApi, requireEndpoint } from "./helpers.js";

test("findings table row click opens its evidence pack", async ({ page }) => {
  await requireApi(test);
  await requireEndpoint(test, "/findings?limit=1");
  await page.goto("/findings");
  const firstRow = page.locator("tbody tr").first();
  await expect(firstRow).toBeVisible({ timeout: 15000 });
  await firstRow.click();
  const pack = page.getByTestId("evidence-pack");
  await expect(pack).toBeVisible();
  await expect(pack.getByText(/Caveats/i)).toBeVisible({ timeout: 15000 });
  await page.screenshot({ path: "../docs/screens/04-findings-evidence-pack.png", fullPage: true });
});
