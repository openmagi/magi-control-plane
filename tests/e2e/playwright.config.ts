/**
 * D73 — Playwright config for the magi-cp E2E harness.
 *
 * Opt-in only. NOT wired into CI defaults (see tests/e2e/README.md).
 *
 * Key choices:
 *   - workers: 1 — scenarios touch shared cloud state (policies, scripts,
 *     ledger) and serial execution keeps assertions deterministic. The
 *     extra wall time is acceptable for a final-smoke harness.
 *   - retries: 0 — silent regressions are the bug class this harness
 *     exists to catch. A flaky test is information; auto-retry hides it.
 *   - trace + screenshot on failure — first-failure debuggability.
 *   - reporter: list + json + html. The json file is the structured
 *     payload our `helpers/report.ts` post-processes into the curated
 *     report.json + report.html under .report/.
 *   - baseURL: read from MAGI_CP_E2E_BASE_URL with a sensible local
 *     default. Docker-compose stack is brought up by helpers/docker.ts.
 */
import { defineConfig } from "@playwright/test"

const BASE_URL =
  process.env.MAGI_CP_E2E_BASE_URL ?? "http://127.0.0.1:3787"

export default defineConfig({
  testDir: "./scenarios",
  timeout: 90_000,
  expect: { timeout: 10_000 },
  retries: 0,
  workers: 1,
  fullyParallel: false,
  reporter: [
    ["list"],
    ["json", { outputFile: ".report/playwright.json" }],
    ["html", { outputFolder: ".report/playwright-html", open: "never" }],
  ],
  use: {
    baseURL: BASE_URL,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "off",
  },
  outputDir: ".report/test-results",
})
