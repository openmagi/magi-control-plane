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
 *  string reason when the spec should SKIP.
 *
 *  D74a: `requiresClaudeHook` adds a check for the local CC settings
 *  carrying a magi-gate PreToolUse hook entry. Without that wiring
 *  the `claude -p` invocation issued by scenarios 04 / 05 emits NO
 *  ledger row — the magi-gate.sh binary may exist on PATH but
 *  Claude Code never spawns it without the settings.json mapping.
 *  Reading settings.json once is cheaper + clearer than waiting 30s
 *  for a ledger row that the hooks pipeline never invokes. */
export function assertHarnessReady(opts: {
  requiresClaude?: boolean
  requiresClaudeHook?: boolean
} = {}): string | null {
  if (process.env.PLAYWRIGHT_SKIP_ALL === "1") {
    return process.env.PLAYWRIGHT_SKIP_ALL_REASON || "harness preflight failed"
  }
  if (opts.requiresClaude || opts.requiresClaudeHook) {
    const bin = locateClaude()
    if (!bin) {
      return "claude binary not found. Set MAGI_CP_E2E_CLAUDE_BIN to an executable file or install the Claude Code CLI."
    }
  }
  if (opts.requiresClaudeHook) {
    const hookReason = _claudeHookMissingReason()
    if (hookReason != null) return hookReason
  }
  return null
}

/** D74a: returns null when CC's settings.json (project, user, or
 *  enterprise) carries at least one PreToolUse / UserPromptSubmit
 *  hook entry that fires a magi-gate-shaped command. The harness
 *  cannot itself install the hook (writing into ~/.claude/settings.json
 *  is a privileged operator action); when missing we mark the spec
 *  SKIP with a precise reason so a fix-pass agent points the operator
 *  at the install step instead of staring at a 30s ledger timeout. */
function _claudeHookMissingReason(): string | null {
  try {
    // Lazy-load these so the helper has no top-level fs/path imports
    // (keeps the surface focused; node:fs is available everywhere
    // Playwright runs).
    const fs = require("node:fs") as typeof import("node:fs")
    const path = require("node:path") as typeof import("node:path")
    const os = require("node:os") as typeof import("node:os")
    const candidates = [
      path.join(os.homedir(), ".claude", "settings.json"),
      path.join(os.homedir(), ".claude", "settings.local.json"),
    ]
    for (const fp of candidates) {
      if (!fs.existsSync(fp)) continue
      try {
        const raw = fs.readFileSync(fp, "utf8")
        if (!raw) continue
        const data = JSON.parse(raw) as { hooks?: Record<string, unknown> }
        const hooks = data?.hooks ?? {}
        // Any PreToolUse or UserPromptSubmit entry referencing
        // magi-gate or magi-cp is treated as evidence of wiring;
        // false-positive risk is acceptable here (the actual
        // assertion is the ledger row in the spec).
        const json = JSON.stringify(hooks)
        if (/magi-gate|magi-cp|magi_cp/i.test(json)) return null
      } catch {
        // Malformed settings file — fall through to next candidate.
      }
    }
    return (
      "no magi-gate hook configured in ~/.claude/settings.json — " +
      "claude -p will run with no PreToolUse hook, so the ledger " +
      "row this scenario asserts on never gets emitted. Install the " +
      "magi-cp PreToolUse hook (see scripts/quickstart.sh) and rerun."
    )
  } catch {
    return "claude hook check failed (could not read settings.json)."
  }
}
