/**
 * D73. docker-compose bring-up / tear-down for the E2E harness.
 *
 * The cloud + dashboard live in a single compose file at repo root.
 * For E2E we want a throwaway data volume so a failed run cannot pollute
 * the next attempt's policy / script / ledger state.
 *
 * Strategy:
 *   - The dashboard's web/ tree runs OUT-OF-CONTAINER via `next dev` /
 *     `next start` on host port 3787 (matches the repo's docker-compose
 *     baseline. the compose file itself only spins the cloud service).
 *   - The compose stack we own here is the `cloud` service (FastAPI on
 *     8787) with a unique throwaway data dir bind-mounted in place of
 *     the named volume.
 *   - waitForHealthy() polls /healthz on the cloud (and optionally the
 *     dashboard) until both come back 200 OR the timeout trips.
 *
 * The harness is happy to be told the stack is already up (CI: a smoke
 * agent uses an existing dev stack). When MAGI_CP_E2E_SKIP_DOCKER=1
 * is set, the bring-up / tear-down functions become no-ops and the
 * scenarios run against whatever stack the operator has already
 * provisioned.
 *
 * Graceful skip (D73 follow-up):
 *   - When the `docker` binary is missing OR the compose file cannot be
 *     located, `upStack()` returns a sentinel `{ available: false,
 *     reason }` instead of throwing. Callers (global-setup) convert
 *     that into a preflight skip so the report still emits.
 */
import { spawn } from "node:child_process"
import { existsSync, mkdtempSync } from "node:fs"
import { tmpdir } from "node:os"
import { delimiter, join } from "node:path"

function _skipDocker(): boolean {
  return (
    process.env.MAGI_CP_E2E_SKIP_DOCKER === "1" ||
    process.env.MAGI_CP_E2E_SKIP_DOCKER === "true"
  )
}

export type DockerStack =
  | {
      available: true
      /** Project name passed to docker compose so successive runs do not collide. */
      project: string
      /** Bind-mount target replacing the named magi-data volume. */
      dataDir: string
      /** True when the helper actually started the stack (and so should stop it). */
      brought_up: boolean
    }
  | {
      available: false
      reason: string
      project: string
      dataDir: null
      /** May be true when bring-up itself succeeded but a later check
       *  (e.g. /healthz never returns 2xx) failed. callers should still
       *  attempt downStack() to release the partial stack. */
      brought_up: boolean
    }

function _runOnce(
  cmd: string,
  args: string[],
  envOverrides: Record<string, string> = {},
  opts: { cwd?: string } = {},
): Promise<{ code: number; stdout: string; stderr: string; error?: string }> {
  return new Promise((resolve) => {
    const child = spawn(cmd, args, {
      env: { ...process.env, ...envOverrides },
      cwd: opts.cwd ?? process.cwd(),
    })
    let stdout = ""
    let stderr = ""
    let errorMsg: string | undefined
    child.stdout.on("data", (b) => { stdout += b.toString() })
    child.stderr.on("data", (b) => { stderr += b.toString() })
    child.on("error", (e) => {
      // ENOENT, EACCES and similar spawn errors land here. Without a
      // listener Node will throw uncaught. We capture the message and
      // let `close` resolve below. If `close` does not fire (some Node
      // versions on certain spawn errors) we resolve here.
      errorMsg = (e as Error).message
    })
    child.on("close", (code) => {
      resolve({ code: code ?? -1, stdout, stderr, error: errorMsg })
    })
    // Belt and suspenders: if the spawn fails so hard `close` never
    // fires, fall back to the error listener resolving the promise.
    child.on("error", () => {
      // If we already resolved, this is a no-op.
      resolve({ code: -1, stdout, stderr, error: errorMsg ?? "spawn error" })
    })
  })
}

/** Returns true when fetch(url) returns a 2xx within timeoutMs.
 *  Uses Node's global fetch (Node 20+). */
async function _isHealthy(url: string, timeoutMs = 2_000): Promise<boolean> {
  try {
    const r = await fetch(url, {
      signal: AbortSignal.timeout(timeoutMs),
    })
    return r.ok
  } catch {
    return false
  }
}

/** Poll a single URL until healthy or until totalTimeoutMs trips. */
export async function waitForHealthy(
  url: string,
  totalTimeoutMs = 60_000,
  intervalMs = 1_500,
): Promise<void> {
  const start = Date.now()
  while (Date.now() - start < totalTimeoutMs) {
    if (await _isHealthy(url)) return
    await new Promise((r) => setTimeout(r, intervalMs))
  }
  throw new Error(
    `waitForHealthy: ${url} did not return 2xx within ${totalTimeoutMs}ms`,
  )
}

/** Locate the repo root by walking upward from the harness directory.
 *  Returns null when no docker-compose.yml is found within 6 levels. */
export function locateRepoRoot(): string | null {
  // Start from __dirname (so it works regardless of cwd at invocation
  // time). __dirname here is tests/e2e/helpers, the repo root is two
  // levels up.
  let cur = __dirname
  for (let i = 0; i < 8; i++) {
    if (existsSync(join(cur, "docker-compose.yml"))) return cur
    const parent = join(cur, "..")
    if (parent === cur) break
    cur = parent
  }
  // Fallback: walk up from cwd in case the harness was installed somewhere odd.
  cur = process.cwd()
  for (let i = 0; i < 8; i++) {
    if (existsSync(join(cur, "docker-compose.yml"))) return cur
    const parent = join(cur, "..")
    if (parent === cur) break
    cur = parent
  }
  return null
}

/** Probe for a working `docker` binary. Returns its resolved path or
 *  null when the binary is missing / not executable / daemon down. */
export async function locateDocker(): Promise<string | null> {
  // Walk PATH for a `docker` entry. We do an executability probe via
  // `docker --version` so we also catch the "binary on PATH but daemon
  // not reachable" case (which most distros surface as a non-zero
  // exit on any docker subcommand).
  const PATH = process.env.PATH ?? ""
  let candidate: string | null = null
  for (const p of PATH.split(delimiter)) {
    if (!p) continue
    const fp = join(p, process.platform === "win32" ? "docker.exe" : "docker")
    if (existsSync(fp)) { candidate = fp; break }
  }
  if (!candidate) return null
  const r = await _runOnce(candidate, ["--version"]).catch(() => null)
  if (!r) return null
  if (r.error || r.code !== 0) return null
  return candidate
}

/** Bring the cloud service up under a fresh project name + throwaway dir.
 *
 *  When MAGI_CP_E2E_SKIP_DOCKER=1, returns a no-op handle. The scenarios
 *  poll healthy regardless of who brought the stack up.
 *
 *  When docker is missing OR the compose file cannot be found, returns
 *  an `available: false` sentinel so callers can convert into a SKIP
 *  rather than a cryptic ENOENT throw. */
export async function upStack(): Promise<DockerStack> {
  const project = `magi-cp-e2e-${process.pid}-${Date.now()}`

  // SKIP path. Do NOT mkdtempSync here. it would leak an empty dir on
  // every invocation with no downStack to clean it up.
  if (_skipDocker()) {
    return {
      available: true,
      project,
      dataDir: "",
      brought_up: false,
    }
  }

  const dockerBin = await locateDocker()
  if (!dockerBin) {
    return {
      available: false,
      reason:
        "docker not found on PATH (or daemon down). Set MAGI_CP_E2E_SKIP_DOCKER=1 if the stack is already running.",
      project,
      dataDir: null,
      brought_up: false,
    }
  }

  const root = locateRepoRoot()
  if (!root) {
    return {
      available: false,
      reason:
        "docker-compose.yml not found by walking up from tests/e2e. Run the harness from inside a checkout of magi-control-plane.",
      project,
      dataDir: null,
      brought_up: false,
    }
  }

  // Throwaway dir. Only created once we know we will run compose.
  const dataDir = mkdtempSync(join(tmpdir(), `${project}-data-`))

  // We do not override the compose file. we DO override the project name
  // and pass through env so the cloud service binds the throwaway dir.
  const r = await _runOnce(
    dockerBin,
    ["compose", "-f", join(root, "docker-compose.yml"), "-p", project, "up", "-d", "cloud"],
    {
      // Compose substitution: the magi-data volume bind target is the
      // standard /data inside the container. we do not remap it here
      // because the named volume is unique per project name already.
      // The throwaway data dir is reserved for future host-bind use
      // (e.g. an export side-channel for the ledger).
    },
    { cwd: root },
  )
  if (r.code !== 0 || r.error) {
    return {
      available: false,
      reason:
        `docker compose up failed (code=${r.code}${r.error ? `, spawn=${r.error}` : ""}): ${(r.stderr || r.stdout || "").slice(0, 400)}`,
      project,
      dataDir: null,
      brought_up: false,
    }
  }

  // Wait for /healthz to come back. Cloud-side healthcheck inside the
  // container plus this client-side poll gives us a stable "ready" gate.
  try {
    await waitForHealthy("http://127.0.0.1:8787/healthz", 60_000)
  } catch (e) {
    return {
      available: false,
      reason: `cloud /healthz never returned 2xx: ${(e as Error).message}`,
      project,
      dataDir: null,
      brought_up: true,
    }
  }
  return { available: true, project, dataDir, brought_up: true }
}

/** Tear the stack down (no-op when we did not bring it up). */
export async function downStack(stack: DockerStack): Promise<void> {
  if (!stack.available) return
  if (!stack.brought_up) return
  const dockerBin = await locateDocker()
  if (!dockerBin) return
  const root = locateRepoRoot()
  if (!root) return
  await _runOnce(
    dockerBin,
    ["compose", "-f", join(root, "docker-compose.yml"), "-p", stack.project, "down", "-v"],
    {},
    { cwd: root },
  )
}
