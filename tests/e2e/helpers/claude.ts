/**
 * D73. wrapper around the `claude -p` (Claude Code "print") subprocess.
 *
 * Scenarios 04 and 05 need to actually fire a CC hook so the runtime
 * gate writes a row to the ledger. an HTTP-only assertion does not
 * cover the gate.py to cloud /ledger code path.
 *
 * The wrapper:
 *   - Resolves the claude binary from MAGI_CP_E2E_CLAUDE_BIN or PATH.
 *   - Returns a sentinel { available: false, reason } when the binary
 *     is missing OR not executable so scenarios can SKIP without failing.
 *   - Runs claude -p with controlled env (PROMPT + allowed tools +
 *     cwd) and captures stdout / stderr / exit code.
 *   - Bounds wall time at MAGI_CP_E2E_CLAUDE_TIMEOUT_MS or 60s default.
 *     On timeout, escalates SIGTERM to SIGKILL after 5s and resolves so
 *     callers never hang the suite.
 *   - Attaches a child.on("error", ...) listener so ENOENT or EACCES
 *     spawn errors surface as `{ available: false, reason }` rather
 *     than leaving the Promise hanging.
 */
import { spawn } from "node:child_process"
import { accessSync, constants as fsConstants, existsSync, statSync } from "node:fs"
import { delimiter, join } from "node:path"

export type ClaudeResult =
  | {
      available: true
      stdout: string
      stderr: string
      code: number
      duration_ms: number
    }
  | { available: false; reason: string }

function _isExecutableFile(p: string): boolean {
  try {
    const st = statSync(p)
    if (!st.isFile()) return false
    accessSync(p, fsConstants.X_OK)
    return true
  } catch {
    return false
  }
}

/** Return the path of `claude` if invocable as an executable file; else null. */
export function locateClaude(): string | null {
  const envBin = process.env.MAGI_CP_E2E_CLAUDE_BIN
  if (envBin) {
    if (existsSync(envBin) && _isExecutableFile(envBin)) return envBin
    return null
  }
  // Walk PATH for a `claude` entry. node:path.delimiter handles win32.
  const PATH = process.env.PATH ?? ""
  for (const p of PATH.split(delimiter)) {
    if (!p) continue
    const candidate = join(p, process.platform === "win32" ? "claude.exe" : "claude")
    if (existsSync(candidate) && _isExecutableFile(candidate)) return candidate
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
        "claude binary not found. Set MAGI_CP_E2E_CLAUDE_BIN to an executable file or install the Claude Code CLI on PATH.",
    }
  }
  const args = ["-p", prompt]
  if (opts.allowedTools && opts.allowedTools.length > 0) {
    args.push("--allowed-tools", opts.allowedTools.join(","))
  }
  const start = Date.now()
  let stdout = ""
  let stderr = ""
  return new Promise<ClaudeResult>((resolve) => {
    let settled = false
    const settle = (r: ClaudeResult) => {
      if (settled) return
      settled = true
      resolve(r)
    }
    let child
    try {
      child = spawn(bin, args, {
        cwd: opts.cwd ?? process.cwd(),
        env: { ...process.env, ...(opts.extraEnv ?? {}) },
      })
    } catch (e) {
      settle({
        available: false,
        reason: `claude spawn failed synchronously: ${(e as Error).message}`,
      })
      return
    }
    const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS
    let killTimer: NodeJS.Timeout | null = null
    const softTimer = setTimeout(() => {
      try { child.kill("SIGTERM") } catch { /* noop */ }
      // Escalate to SIGKILL if the child does not respond. Resolve the
      // outer promise with a sentinel so callers never hang the suite.
      killTimer = setTimeout(() => {
        try { child.kill("SIGKILL") } catch { /* noop */ }
        settle({
          available: true,
          stdout,
          stderr: stderr + "\n[e2e] claude killed after timeout (SIGKILL)",
          code: 124,
          duration_ms: Date.now() - start,
        })
      }, 5_000)
    }, timeoutMs)
    child.stdout?.on("data", (b) => { stdout += b.toString() })
    child.stderr?.on("data", (b) => { stderr += b.toString() })
    child.on("error", (e) => {
      // ENOENT (rare here since locate already filtered), EACCES, etc.
      // Without this listener Node throws uncaught + the Promise hangs.
      clearTimeout(softTimer)
      if (killTimer) clearTimeout(killTimer)
      settle({
        available: false,
        reason: `claude spawn failed: ${(e as Error).message}`,
      })
    })
    child.on("close", (code) => {
      clearTimeout(softTimer)
      if (killTimer) clearTimeout(killTimer)
      settle({
        available: true,
        stdout,
        stderr,
        code: code ?? -1,
        duration_ms: Date.now() - start,
      })
    })
  })
}
