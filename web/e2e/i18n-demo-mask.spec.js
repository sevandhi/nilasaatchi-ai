import { test, expect } from "@playwright/test";
import { requireApi } from "./helpers.js";

test("demo mask is ON by default and the EN/Tamil toggle changes nav labels", async ({ page }) => {
  await requireApi(test);
  await page.goto("/");
  await expect(page.getByTestId("demo-mask-toggle")).toBeChecked();

  await expect(page.getByRole("link", { name: /Findings/i })).toBeVisible();
  await page.getByTestId("lang-ta").click();
  await expect(page.getByRole("link", { name: /கண்டறிதல்/i })).toBeVisible();
  await page.getByTestId("lang-en").click();
  await expect(page.getByRole("link", { name: /Findings/i })).toBeVisible();
});
