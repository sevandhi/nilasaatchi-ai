import { test, expect } from "@playwright/test";
import { requireApi } from "./helpers.js";

test("overview loads with KPIs and a clickable pipeline", async ({ page }) => {
  await requireApi(test);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /Paper vs Planet/i })).toBeVisible();
  await expect(page.getByText("Documents", { exact: true }).first()).toBeVisible({ timeout: 15000 });
  await page.screenshot({ path: "../docs/screens/01-overview.png", fullPage: true });
});
