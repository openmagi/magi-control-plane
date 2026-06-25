/**
 * D73 follow-up. Playwright global teardown.
 *
 * Tears the docker stack down (no-op when we did not bring it up) AND
 * synthesizes the curated report so the artifact always exists.
 *
 * The report path conventions:
 *   - Playwright writes `.report/playwright.json` (raw).
 *   - We write `.report/report.json` + `.report/report.html` (curated).
 * Both live under tests/e2e/.report/.
 */
import { join } from "node:path"
import { writeReport } from "./helpers/report"
import { downStack } from "./helpers/docker"

export default async function globalTeardown(): Promise<void> {
  const reportDir = join(__dirname, ".report")
  const pwPath = join(reportDir, "playwright.json")
  try {
    writeReport(pwPath, reportDir)
  } catch {
    // writeReport is hardened. it falls back to a skeleton in its own
    // catch. nothing for us to do here.
  }
  const stack = globalThis.__MAGI_CP_E2E_STACK__
  if (stack) {
    try {
      await downStack(stack)
    } catch {
      // best effort. operator can `docker compose -p <project> down -v` manually.
    }
  }
}
