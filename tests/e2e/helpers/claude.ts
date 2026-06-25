/**
 * D73 — wrapper around the `claude -p` (Claude Code "print") subprocess.
 *
 * Scenarios 04 and 05 need to actually fire a CC hook so the runtime
 * gate writes a row to the ledger; an HTTP-only assertion doesn't
 * cover the gate.py → cloud /ledger code path.
 *
 * The wrapper:
 *   - Resolves the claude binary from MAGI_CP_E2E_CLAUDE_BIN or PATH.
 *   - Returns a sentinel { available: false, reason } when the binary
 *     is missing so scenarios can SKIP without failing.
 *   - Runs claude -p with controlled env (PROMPT + allowed tools +
 *     cwd) and captures stdout / stderr / exit code.
 *   - Bounds wall time at MAGI_CP_E2E_CLAUDE_TIMEOUT_MS or 60s default.
 *
 * The CC hook the scenarios depend on is configured ELSEWHERE (the
 * cloud's gate.py shim writes a managed-settings JSON the cloud
 * already emits when a policy is enabled). The wrapper just fires
 * claude with a prompt that will tickle the right tool.
 */
import { spawn } from "node:child_process"
import { existsSync } from "node:fs"
import { join } from "node:path"

export type ClaudeResult =
  | {
      available: true
      stdout: string
      stderr: string
      code: number
      duration_ms: number
    }
  | { available: false; reason: string }

/** Return the path of `claude` if invocable; else null. */
export function locateClaude(): string | null {
  const envBin = process.env.MAGI_CP_E2E_CLAUDE_BIN
  if (envBin) {
    if (existsSync(envBin)) return envBin
    return null
  }
  // Walk PATH for a `claude` entry.
  const PATH = process.env.PATH ?? ""
  for (const p of PATH.split(":")) {
    if (!p) continue
    const candidate = join(p, "claude")
    if (existsSync(candidate)) return candidate
  }
  return null
}

const DEFAULT_TIMEOUT_MS = Number(
  process.env.MAGI_CP_E2E_CLAUDE_TIMEOUT_MS ?? 60_000,
)

export async function runClaudePrompt(
  prompt: string,
  opts: {
    cwd?: string
    timeoutMs?: number
    allowedTools?: string[]
    extraEnv?: Record<string, string>
  } = {},
): Promise<ClaudeResult> {
  const bin = locateClaude()
  if (!bin) {
    return {
      available: false,
      reason:
        "claude binary not found (set MAGI_CP_E2E_CLAUDE_BIN or install the Claude Code CLI)",
    }
  }
  const args = ["-p", prompt]
  if (opts.allowedTools && opts.allowedTools.length > 0) {
    args.push("--allowed-tools", opts.allowedTools.join(","))
  }
  const child = spawn(bin, args, {
    cwd: opts.cwd ?? process.cwd(),
    env: { ...process.env, ...(opts.extraEnv ?? {}) },
  })
  let stdout = ""
  let stderr = ""
  child.stdout.on("data", (b) => { stdout += b.toString() })
  child.stderr.on("data", (b) => { stderr += b.toString() })
  const start = Date.now()
  const result: ClaudeResult = await new Promise((resolve) => {
    const timer = setTimeout(() => {
      child.kill("SIGTERM")
    }, opts.timeoutMs ?? DEFAULT_TIMEOUT_MS)
    child.on("close", (code) => {
      clearTimeout(timer)
      resolve({
        available: true,
        stdout,
        stderr,
        code: code ?? -1,
        duration_ms: Date.now() - start,
      })
    })
  })
  return result
}
