/**
 * D73 follow-up. Centralized harness preflight.
 *
 * Wired from playwright.config.ts globalSetup. probes every dependency
 * the scenarios need and, on any miss, writes `.report/preflight.json`
 * with `{ skip: true, reason }` AND sets `process.env.PLAYWRIGHT_SKIP_ALL=1`
 * (consumed by a top-level beforeAll in each spec via `assertHarnessReady`).
 *
 * Why centralized:
 *   - "claude missing" SKIP was the only graceful-skip path. docker
 *     missing, dashboard not up, missing cloud admin keys all
 *     hard-failed with cryptic stderr.
 *   - Each spec checking independently produced fail-vs-skip drift
 *     (a missing MAGI_CP_ADMIN_API_KEY threw inside _wirePolicy
 *     instead of triggering test.skip()).
 *
 * The preflight does NOT throw. it always writes the sidecar so
 * `writeReport()` (and `globalTeardown()` below) can synthesize a
 * SKIP report even when Playwright never produced JSON.
 */
import { existsSync, mkdirSync, writeFileSync } from "node:fs"
import { join } from "node:path"
import { locateClaude } from "./claude"
import { locateDocker, locateRepoRoot, upStack, waitForHealthy } from "./docker"
import type { DockerStack } from "./docker"

export type PreflightResult = {
  ok: boolean
  reasons: string[]
  claude_available: boolean
  stack?: DockerStack
}

const SKIP_DOCKER =
  process.env.MAGI_CP_E2E_SKIP_DOCKER === "1" ||
  process.env.MAGI_CP_E2E_SKIP_DOCKER === "true"

const REPORT_DIR_DEFAULT = join(__dirname, "..", ".report")

async function _probe(url: string, timeoutMs = 4_000): Promise<boolean> {
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) })
    return r.ok
  } catch {
    return false
  }
}

/** Run every preflight check. Returns a structured result and writes
 *  `.report/preflight.json` either way (so writeReport can find it). */
export async function runPreflight(opts: {
  reportDir?: string
  // Optional: skip docker bring-up even if SKIP env unset (used by unit tests).
  skipDockerBringUp?: boolean
} = {}): Promise<PreflightResult> {
  const reasons: string[] = []
  const reportDir = opts.reportDir ?? REPORT_DIR_DEFAULT
  if (!existsSync(reportDir)) mkdirSync(reportDir, { recursive: true })

  // 1. Admin / api keys. Scenarios 02-05 all need at least admin key.
  if (!process.env.MAGI_CP_ADMIN_API_KEY) {
    reasons.push("MAGI_CP_ADMIN_API_KEY not set in env")
  }
  if (!process.env.MAGI_CP_API_KEY) {
    reasons.push("MAGI_CP_API_KEY not set in env")
  }

  // 2. Docker. only required when SKIP env is NOT set.
  let stack: DockerStack | undefined
  if (!SKIP_DOCKER && !opts.skipDockerBringUp) {
    const dockerBin = await locateDocker()
    if (!dockerBin) {
      reasons.push(
        "docker not found on PATH (or daemon down). Set MAGI_CP_E2E_SKIP_DOCKER=1 if the stack is already running.",
      )
    } else if (!locateRepoRoot()) {
      reasons.push(
        "docker-compose.yml not located by walking up from tests/e2e. is the harness installed inside a checkout of magi-control-plane?",
      )
    } else {
      stack = await upStack()
      if (!stack.available) {
        reasons.push(`docker stack bring-up failed: ${stack.reason}`)
      }
    }
  }

  // 3. Cloud + dashboard health.
  const cloudUrl = process.env.MAGI_CP_CLOUD_URL ?? "http://127.0.0.1:8787"
  const dashUrl = process.env.MAGI_CP_E2E_BASE_URL ?? "http://127.0.0.1:3787"
  try {
    await waitForHealthy(`${cloudUrl}/healthz`, 30_000)
  } catch (e) {
    reasons.push(`cloud not healthy at ${cloudUrl}/healthz: ${(e as Error).message}`)
  }
  const dashOk = await _probe(`${dashUrl}/`)
  if (!dashOk) {
    reasons.push(`dashboard not reachable at ${dashUrl}/`)
  }

  // 4. claude binary. NOT a hard requirement, scenarios 04/05 SKIP
  //    individually when missing. record availability so the scenario-
  //    level skip can use a consistent surface.
  const claudeBin = locateClaude()
  const claudeAvailable = claudeBin != null

  const ok = reasons.length === 0
  const sidecar = {
    skip: !ok,
    reason: ok ? null : reasons.join(" | "),
    claude_available: claudeAvailable,
    claude_bin: claudeBin,
    ts: new Date().toISOString(),
  }
  writeFileSync(join(reportDir, "preflight.json"), JSON.stringify(sidecar, null, 2))

  if (!ok) {
    // Surface to specs that read this env var in a top-level beforeAll.
    process.env.PLAYWRIGHT_SKIP_ALL = "1"
    process.env.PLAYWRIGHT_SKIP_ALL_REASON = sidecar.reason ?? ""
  }
  return { ok, reasons, claude_available: claudeAvailable, stack }
}

/** Spec-level helper consumed by every scenario's top-of-file
 *  `test.skip(...)`. returns `null` when the harness is ready or a
 *  string reason when the spec should SKIP. */
export function assertHarnessReady(opts: { requiresClaude?: boolean } = {}): string | null {
  if (process.env.PLAYWRIGHT_SKIP_ALL === "1") {
    return process.env.PLAYWRIGHT_SKIP_ALL_REASON || "harness preflight failed"
  }
  if (opts.requiresClaude) {
    const bin = locateClaude()
    if (!bin) {
      return "claude binary not found. Set MAGI_CP_E2E_CLAUDE_BIN to an executable file or install the Claude Code CLI."
    }
  }
  return null
}
