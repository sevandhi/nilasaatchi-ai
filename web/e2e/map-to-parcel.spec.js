import { test, expect } from "@playwright/test";
import { requireApi } from "./helpers.js";

// Flow: map click -> parcel page with the Paper-vs-Planet timeline -> click a document-event
// marker -> the evidence viewer shows its bbox (phase6 SKILL flow 0 / ui-spec quality bar).
test("map click opens a parcel page with the Paper vs Planet timeline", async ({ page }) => {
  await requireApi(test);
  await page.goto("/map");
  const map = page.getByTestId("maplibre-map");
  await expect(map).toBeVisible();
  const legend = page.getByTestId("map-legend");
  await expect(legend).toBeVisible();
  // Wait for the colour-by legend to actually populate (not its "Loading…" placeholder) so the
  // screenshot doesn't race the parcel layer fetch + colour-by application.
  await expect(legend).not.toContainText("Loading…", { timeout: 15000 });
  await page.screenshot({ path: "../docs/screens/02-map.png", fullPage: false });

  const box = await map.boundingBox();
  if (!box) test.fail(true, "map container has no bounding box");

  // The parcel fill layer covers most of the park; try a small grid of points until one hits a
  // parcel and the app navigates to /parcel/:uid.
  let navigated = false;
  for (const [fx, fy] of [[0.5, 0.5], [0.45, 0.5], [0.55, 0.5], [0.5, 0.45], [0.5, 0.55], [0.4, 0.4], [0.6, 0.6]]) {
    await page.mouse.click(box.x + box.width * fx, box.y + box.height * fy);
    try {
      await page.waitForURL(/\/parcel\//, { timeout: 3000 });
      navigated = true;
      break;
    } catch {
      // try the next point
    }
  }
  expect(navigated, "clicking the map should select a parcel and navigate to its page").toBeTruthy();

  await expect(page.getByTestId("paper-planet-timeline")).toBeVisible({ timeout: 15000 });
  await page.waitForTimeout(500); // let the timeline chart finish animating in
  await page.screenshot({ path: "../docs/screens/03-parcel-timeline.png", fullPage: true });
});
