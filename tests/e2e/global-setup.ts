/**
 * D73 follow-up. Playwright global setup.
 *
 * Runs once before any spec. Calls preflight, stashes a handle on a
 * global for global-teardown to consume.
 *
 * When preflight fails the harness still proceeds (scenarios will read
 * `process.env.PLAYWRIGHT_SKIP_ALL` and call `test.skip()`). this keeps
 * Playwright producing a JSON report instead of crashing the run.
 */
import type { FullConfig } from "@playwright/test"
import { runPreflight } from "./helpers/preflight"
import type { DockerStack } from "./helpers/docker"

declare global {
  // eslint-disable-next-line no-var
  var __MAGI_CP_E2E_STACK__: DockerStack | undefined
}

export default async function globalSetup(_config: FullConfig): Promise<void> {
  const r = await runPreflight()
  if (r.stack) globalThis.__MAGI_CP_E2E_STACK__ = r.stack
}
