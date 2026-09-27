import { defineConfig, devices } from "@playwright/test";

// Smoke tests for the read-only cloud build (T7.2). Runs the dev server in `cloud` mode
// (web/.env.cloud: VITE_READ_ONLY=true) so the read-only UI branches render, but overrides
// VITE_API_BASE back to the local API (http://localhost:8000) via a real process env var —
// Vite always lets an existing process env var win over a value from an .env file — so the
// page still has real data to render while exercising the cloud UI. A separate port (5174)
// keeps this from colliding with the default e2e dev server on 5173.
export default defineConfig({
  testDir: "./e2e",
  testMatch: /readonly\.spec\.js$/,
  timeout: 30_000,
  expect: { timeout: 8_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report-readonly" }]],
  use: {
    baseURL: "http://localhost:5174",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    viewport: { width: 1366, height: 768 },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npm run dev -- --mode cloud --port 5174",
    url: "http://localhost:5174",
    reuseExistingServer: true,
    timeout: 30_000,
    env: { VITE_API_BASE: "http://localhost:8000" },
  },
});
