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
 *  carrying a magi-gate hook entry on the channel the scenario needs.
 *  Without that wiring the `claude -p` invocation issued by scenarios
 *  04 / 05 emits NO ledger row — the magi-gate.sh binary may exist on
 *  PATH but Claude Code never spawns it without the settings.json
 *  mapping. Reading settings.json once is cheaper + clearer than
 *  waiting 30s for a ledger row that the hooks pipeline never invokes.
 *
 *  D74a follow-up: scope the check to the specific channel each
 *  scenario binds to (`PreToolUse` for scenario 04 run_command vs
 *  `UserPromptSubmit` for scenario 05 inject_context). The previous
 *  JSON-stringify + magi-gate|magi-cp regex matched ANY mention of
 *  the substring anywhere in the hooks tree (e.g. an unrelated
 *  PostToolUse logger piping into `magi-cp log`, or a stale
 *  filesystem path), false-positively flipping the SKIP back to a
 *  30s hang on a CI box that does not have the right channel wired. */
export function assertHarnessReady(opts: {
  requiresClaude?: boolean
  requiresClaudeHook?: boolean | "PreToolUse" | "UserPromptSubmit"
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
    const channel =
      typeof opts.requiresClaudeHook === "string"
        ? opts.requiresClaudeHook
        : "PreToolUse"
    const hookReason = _claudeHookMissingReason(channel)
    if (hookReason != null) return hookReason
  }
  return null
}

type ClaudeHookEntry = { type?: string; command?: string }
type ClaudeHookBlock = { matcher?: string; hooks?: ClaudeHookEntry[] }
type ClaudeSettings = {
  hooks?: Partial<Record<string, ClaudeHookBlock[]>>
}

/** D74a: returns null when CC's settings.json (project, user, or
 *  enterprise) carries at least one hook entry on the requested
 *  channel that fires a magi-gate-shaped command. The harness cannot
 *  itself install the hook (writing into ~/.claude/settings.json is a
 *  privileged operator action); when missing we mark the spec SKIP
 *  with a precise reason so a fix-pass agent points the operator at
 *  the install step instead of staring at a 30s ledger timeout.
 *
 *  D74a follow-up:
 *    1. Only inspect the requested channel (`PreToolUse` or
 *       `UserPromptSubmit`) — NOT JSON.stringify(hooks). The previous
 *       implementation false-positived on any `magi-cp` mention in
 *       Stop / PostToolUse / SubagentStop and on stale magi-cp
 *       filesystem paths, defeating the SKIP-vs-30s-hang guarantee.
 *    2. Match the magi-gate / magi-cp token in the individual hook
 *       entry's `command` string (the channel CC actually spawns) —
 *       not in serialized container metadata.
 *    3. Honor Claude Code's documented precedence chain: project-level
 *       `<cwd>/.claude/settings.json` first (a common dev setup),
 *       then `<cwd>/.claude/settings.local.json`, then the user-level
 *       `~/.claude/settings*.json`, then a `CLAUDE_PROJECT_DIR`
 *       override. The harness's own short-circuit
 *       (`MAGI_CP_E2E_CLAUDE_SETTINGS=<path>`) overrides the discovery
 *       chain so scenarios 04/05 can install a hermetic settings file
 *       in a beforeAll and point this helper at it without touching
 *       the operator's real `~/.claude/settings.json`. */
function _claudeHookMissingReason(
  channel: "PreToolUse" | "UserPromptSubmit",
): string | null {
  try {
    const fs = require("node:fs") as typeof import("node:fs")
    const path = require("node:path") as typeof import("node:path")
    const os = require("node:os") as typeof import("node:os")
    const candidates: string[] = []
    const override = process.env.MAGI_CP_E2E_CLAUDE_SETTINGS
    if (override) candidates.push(override)
    const projectDir = process.env.CLAUDE_PROJECT_DIR ?? process.cwd()
    candidates.push(
      path.join(projectDir, ".claude", "settings.json"),
      path.join(projectDir, ".claude", "settings.local.json"),
      path.join(os.homedir(), ".claude", "settings.json"),
      path.join(os.homedir(), ".claude", "settings.local.json"),
    )
    for (const fp of candidates) {
      if (!fs.existsSync(fp)) continue
      try {
        const raw = fs.readFileSync(fp, "utf8")
        if (!raw) continue
        const data = JSON.parse(raw) as ClaudeSettings
        const blocks = data?.hooks?.[channel] ?? []
        for (const block of blocks) {
          for (const entry of block?.hooks ?? []) {
            const cmd = entry?.command
            if (typeof cmd !== "string") continue
            // The canonical installer drops magi-gate.sh; the alpha
            // installer path used `magi-cp-gate`. Match either token
            // INSIDE the hook entry's command string only.
            if (/magi-gate(\.sh)?\b|\bmagi-cp-gate\b/.test(cmd)) {
              return null
            }
          }
        }
      } catch {
        // Malformed settings file — fall through to next candidate.
      }
    }
    return (
      `no magi-gate ${channel} hook configured in any CC settings.json ` +
      `(checked MAGI_CP_E2E_CLAUDE_SETTINGS, <cwd>/.claude/settings*.json, ` +
      `~/.claude/settings*.json). claude -p will run with no ${channel} ` +
      `hook, so the ledger row this scenario asserts on never gets ` +
      `emitted. Install the magi-cp ${channel} hook (see ` +
      `scripts/quickstart.sh) or set MAGI_CP_E2E_CLAUDE_SETTINGS to a ` +
      `hermetic settings file and rerun.`
    )
  } catch {
    return "claude hook check failed (could not read settings.json)."
  }
}
