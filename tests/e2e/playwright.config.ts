/**
 * D73. Playwright config for the magi-cp E2E harness.
 *
 * Opt-in only. NOT wired into CI defaults (see tests/e2e/README.md).
 *
 * Key choices:
 *   - workers: 1. scenarios touch shared cloud state (policies, scripts,
 *     ledger) and serial execution keeps assertions deterministic. The
 *     extra wall time is acceptable for a final-smoke harness.
 *   - retries: 0. silent regressions are the bug class this harness
 *     exists to catch. A flaky test is information, auto-retry hides it.
 *   - trace + screenshot on. cheap because workers=1 already serializes,
 *     and per-step screenshots are critical for wizard journeys where
 *     the only failure shot is otherwise the end-state corpse.
 *   - reporter: list + json + html. The json file is the structured
 *     payload our `helpers/report.ts` post-processes into the curated
 *     report.json + report.html under .report/.
 *   - baseURL: read from MAGI_CP_E2E_BASE_URL with a sensible local
 *     default. Docker-compose stack is brought up by global-setup.ts.
 *   - globalSetup / globalTeardown: drive the harness preflight (docker,
 *     admin keys, cloud /healthz, dashboard /) and write the curated
 *     report (always, including on graceful-skip paths so the artifact
 *     never disappears).
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
  globalSetup: require.resolve("./global-setup.ts"),
  globalTeardown: require.resolve("./global-teardown.ts"),
  reporter: [
    ["list"],
    ["json", { outputFile: ".report/playwright.json" }],
    ["html", { outputFolder: ".report/playwright-html", open: "never" }],
  ],
  use: {
    baseURL: BASE_URL,
    // `on` gives us per-step screenshots so wizard journeys carry the
    // intermediate states. workers=1 already keeps the cost bounded.
    screenshot: "on",
    trace: "retain-on-failure",
    video: "off",
  },
  outputDir: ".report/test-results",
})
