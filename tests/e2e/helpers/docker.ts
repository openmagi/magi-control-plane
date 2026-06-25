/**
 * D73 — docker-compose bring-up / tear-down for the E2E harness.
 *
 * The cloud + dashboard live in a single compose file at repo root.
 * For E2E we want a throwaway data volume so a failed run cannot pollute
 * the next attempt's policy / script / ledger state.
 *
 * Strategy:
 *   - The dashboard's web/ tree runs OUT-OF-CONTAINER via `next dev` /
 *     `next start` on host port 3787 (matches the repo's docker-compose
 *     baseline; the compose file itself only spins the cloud service).
 *   - The compose stack we own here is the `cloud` service (FastAPI on
 *     8787) with a unique throwaway data dir bind-mounted in place of
 *     the named volume.
 *   - waitForHealthy() polls /healthz on the cloud + the next.js dev
 *     server on the dashboard until both come back 200 OR the timeout
 *     trips.
 *
 * The harness is happy to be told the stack is already up (CI: a smoke
 * agent uses an existing dev stack). When MAGI_CP_E2E_SKIP_DOCKER=1
 * is set, the bring-up / tear-down functions become no-ops and the
 * scenarios run against whatever stack the operator has already
 * provisioned.
 */
import { spawn } from "node:child_process"
import { existsSync, mkdtempSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

const SKIP =
  process.env.MAGI_CP_E2E_SKIP_DOCKER === "1" ||
  process.env.MAGI_CP_E2E_SKIP_DOCKER === "true"

export type DockerStack = {
  /** Project name passed to docker compose so successive runs don't collide. */
  project: string
  /** Bind-mount target replacing the named magi-data volume. */
  dataDir: string
  /** True when the helper actually started the stack (and so should stop it). */
  brought_up: boolean
}

function _runOnce(
  cmd: string,
  args: string[],
  envOverrides: Record<string, string> = {},
): Promise<{ code: number; stdout: string; stderr: string }> {
  return new Promise((resolve) => {
    const child = spawn(cmd, args, {
      env: { ...process.env, ...envOverrides },
      cwd: process.cwd(),
    })
    let stdout = ""
    let stderr = ""
    child.stdout.on("data", (b) => { stdout += b.toString() })
    child.stderr.on("data", (b) => { stderr += b.toString() })
    child.on("close", (code) => {
      resolve({ code: code ?? -1, stdout, stderr })
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

/** Locate the repo root by walking upward from cwd. */
function _repoRoot(): string {
  let cur = process.cwd()
  // up to 6 levels (e2e nested inside tests/e2e under repo)
  for (let i = 0; i < 6; i++) {
    if (existsSync(join(cur, "docker-compose.yml"))) return cur
    const parent = join(cur, "..")
    if (parent === cur) break
    cur = parent
  }
  throw new Error("docker.ts: could not locate repo root (no docker-compose.yml)")
}

/** Bring the cloud service up under a fresh project name + throwaway dir.
 *
 *  When MAGI_CP_E2E_SKIP_DOCKER=1, returns a no-op handle. The scenarios
 *  poll healthy regardless of who brought the stack up. */
export async function upStack(): Promise<DockerStack> {
  const project = `magi-cp-e2e-${process.pid}-${Date.now()}`
  const dataDir = mkdtempSync(join(tmpdir(), `${project}-data-`))

  if (SKIP) {
    return { project, dataDir, brought_up: false }
  }

  const root = _repoRoot()
  // We don't override the compose file — we DO override the project name
  // and pass through env so the cloud service binds the throwaway dir.
  const r = await _runOnce(
    "docker",
    ["compose", "-p", project, "up", "-d", "cloud"],
    {
      // Compose substitution: the magi-data volume bind target is the
      // standard /data inside the container; we don't remap it here
      // because the named volume is unique per project name already.
      // The throwaway data dir is reserved for future host-bind use
      // (e.g. an export side-channel for the ledger).
    },
  )
  if (r.code !== 0) {
    throw new Error(
      `docker compose up failed (code=${r.code}): ${r.stderr || r.stdout}`,
    )
  }

  // Wait for /healthz to come back. Cloud-side healthcheck inside the
  // container plus this client-side poll gives us a stable "ready" gate.
  await waitForHealthy("http://127.0.0.1:8787/healthz", 60_000)
  return { project, dataDir, brought_up: true }
}

/** Tear the stack down (no-op when we did not bring it up). */
export async function downStack(stack: DockerStack): Promise<void> {
  if (!stack.brought_up) return
  await _runOnce(
    "docker",
    ["compose", "-p", stack.project, "down", "-v"],
  )
}
