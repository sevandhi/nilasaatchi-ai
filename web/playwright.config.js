import { defineConfig, devices } from "@playwright/test";

// Smoke tests for the 9-page workspace (phase6-workspace-ui skill: "make e2e"). Runs against
// the real API (`make api`, default http://localhost:8000) — there is no mocked data in the
// shipped build. `API_BASE` lets CI point at a different instance.
export default defineConfig({
  testDir: "./e2e",
  // e2e/readonly.spec.js targets the read-only cloud build (VITE_READ_ONLY=true) and needs its
  // own dev server started in `--mode cloud` — see playwright.readonly.config.js / `npm run
  // e2e:readonly`. It would fail here since this config's dev server is the ordinary local build.
  testIgnore: /readonly\.spec\.js$/,
  timeout: 30_000,
  expect: { timeout: 8_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://localhost:5173",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    viewport: { width: 1366, height: 768 },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npm run dev -- --port 5173",
    url: "http://localhost:5173",
    reuseExistingServer: true,
    timeout: 30_000,
  },
});
