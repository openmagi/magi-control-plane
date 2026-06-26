/**
 * D73. scenario 04: run_command roundtrip.
 *
 * Requires the `claude` binary on PATH (or MAGI_CP_E2E_CLAUDE_BIN).
 * When missing OR when other preflight checks fail (admin keys, cloud,
 * dashboard), the scenario marks itself SKIP with a clear reason. do
 * NOT fail.
 *
 * Steps:
 *   1. Wire a run_command policy that fires on PreToolUse + Bash,
 *      executing `echo MAGI_E2E` with timeout 5s.
 *   2. Spawn claude -p with a prompt that runs the Bash tool.
 *   3. Poll /ledger for a row carrying command=echo MAGI_E2E and
 *      stdout containing MAGI_E2E.
 *   4. Assert the redacted preview shows MAGI_E2E (not masked) and
 *      the policy_id matches the wired policy.
 *
 * The "claude binary missing -> SKIP, not fail" rule lives in
 * helpers/claude.ts (locateClaude returns null in that case) AND in
 * the centralized `assertHarnessReady({ requiresClaude: true })`.
 */
import { test, expect } from "@playwright/test"
import { runClaudePrompt, locateClaude } from "../helpers/claude"
import { currentLedgerCursor, waitForLedgerRow } from "../helpers/ledger"
import { assertHarnessReady } from "../helpers/preflight"
import { mkdtempSync, writeFileSync, existsSync } from "node:fs"
import { tmpdir } from "node:os"
import { join, resolve } from "node:path"

test.describe.configure({ mode: "serial" })

test("04 run_command roundtrip", async ({}, testInfo) => {
  // D74a follow-up: install a hermetic CC settings file with a single
  // PreToolUse hook entry pointing at the in-repo `scripts/magi-gate.sh`
  // when one isn't already installed. This converts the previous
  // permanent SKIP on every CI/dev box without `~/.claude/settings.json`
  // into a real end-to-end run on every machine where the claude
  // binary is reachable. The hermetic install touches a tmp dir; the
  // operator's real `~/.claude/settings.json` is never written.
  //
  // Operator override: setting MAGI_CP_E2E_CLAUDE_SETTINGS skips the
  // auto-install and uses the file already at that path (lets the
  // operator pin a known-good fixture).
  const settingsPath = _ensureHermeticHook("PreToolUse")
  if (settingsPath) {
    process.env.MAGI_CP_E2E_CLAUDE_SETTINGS = settingsPath
  }
  // D74a: also require the CC magi-gate hook to be wired in
  // ~/.claude/settings.json — without it `claude -p` runs with no
  // PreToolUse hook, the policy never fires, and the ledger row this
  // scenario asserts on never appears. The previous version waited
  // 30s for that row before timing out; the explicit preflight
  // surfaces the install gap as a clear SKIP reason.
  const skipReason = assertHarnessReady({
    requiresClaudeHook: "PreToolUse",
  })
  test.skip(skipReason != null, skipReason ?? "")

  // 1. Wire the policy (PreToolUse + Bash + audit + script that
  //    echoes the sentinel). The sentinel `MAGI_E2E` is a known plain
  //    string the redactor's allowlist passes through verbatim.
  const policyId = `e2e/run-${Date.now()}`
  await _wirePolicy(policyId)

  // 2. Snapshot ledger cursor BEFORE firing claude.
  const cursor = await currentLedgerCursor()

  // 3. Run claude -p. The prompt asks claude to use the Bash tool to
  //    emit the sentinel. We allowlist Bash explicitly. cwd is a
  //    throwaway dir so claude does not index the e2e repo.
  const cwd = mkdtempSync(join(tmpdir(), "magi-cp-e2e-claude-"))
  // D74a follow-up: forward the hermetic settings via CLAUDE_CONFIG_DIR
  // so the claude binary picks up our PreToolUse hook entry without
  // mutating the operator's real `~/.claude/settings.json`.
  const claudeConfigDir = process.env.MAGI_CP_E2E_CLAUDE_SETTINGS
    ? join(process.env.MAGI_CP_E2E_CLAUDE_SETTINGS, "..")
    : undefined
  const result = await runClaudePrompt(
    "Use the Bash tool to run: echo MAGI_E2E",
    {
      cwd,
      allowedTools: ["Bash"],
      timeoutMs: 60_000,
      extraEnv: claudeConfigDir
        ? { CLAUDE_CONFIG_DIR: claudeConfigDir }
        : undefined,
    },
  )

  // Always attach claude output BEFORE asserting so a fix-pass agent
  // reading report.json sees stdout/stderr regardless of pass/fail.
  if (result.available) {
    await testInfo.attach("claude-output", {
      body: JSON.stringify({
        code: result.code,
        stdout: result.stdout,
        stderr: result.stderr,
        duration_ms: result.duration_ms,
      }, null, 2),
      contentType: "application/json",
    })
  } else {
    await testInfo.attach("claude-output", {
      body: JSON.stringify({ available: false, reason: result.reason }, null, 2),
      contentType: "application/json",
    })
  }

  expect(result.available, result.available ? "" : `claude unavailable: ${(result as { reason?: string }).reason ?? ""}`).toBe(true)
  if (result.available) {
    expect(result.code,
      `claude exited ${result.code}: ${result.stderr.slice(0, 200)}`,
    ).toBe(0)
  }

  // 4. Poll /ledger for a row referencing the sentinel.
  const row = await waitForLedgerRow(
    (r) => {
      const body = JSON.stringify(r.body ?? {})
      return body.includes("MAGI_E2E")
    },
    { timeoutMs: 30_000, startSinceId: cursor, testInfo },
  )
  expect(row, "no ledger row carrying MAGI_E2E").toBeTruthy()

  // 5. Spot-check the body: policy_id should match the wired policy.
  const body = JSON.stringify(row.body ?? {})
  expect(body, "ledger row should reference the wired policy")
    .toMatch(new RegExp(policyId.replace(/[/]/g, "\\/")))

  // Cleanup.
  await _disable(policyId).catch(() => {})
})

async function _wirePolicy(policyId: string): Promise<void> {
  const url = `${process.env.MAGI_CP_CLOUD_URL ?? "http://127.0.0.1:8787"}/policies/${encodeURIComponent(policyId)}`
  const adminKey = process.env.MAGI_CP_ADMIN_API_KEY
  if (!adminKey) throw new Error("MAGI_CP_ADMIN_API_KEY not set")
  // D74a: PUT /policies expects `{policy, source, enabled?}`. `source`
  // must be one of the 5 precedence tiers (platform|org|bot|user|session).
  const body = {
    policy: {
      id: policyId,
      description: "e2e run_command echo",
      version: "1",
      type: "run_command",
      trigger: { host: "claude_code", event: "PreToolUse", matcher: "Bash" },
      command: "echo MAGI_E2E",
      timeout_s: 5,
      action: "audit",
    },
    source: "user",
  }
  const r = await fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json", "X-Admin-Api-Key": adminKey },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(8000),
  })
  if (!r.ok) throw new Error(`PUT /policies/<id> ${r.status}: ${await r.text().catch(() => "")}`)
}

async function _disable(policyId: string): Promise<void> {
  const url = `${process.env.MAGI_CP_CLOUD_URL ?? "http://127.0.0.1:8787"}/policies/${encodeURIComponent(policyId)}/enabled`
  const adminKey = process.env.MAGI_CP_ADMIN_API_KEY
  if (!adminKey) return
  await fetch(url, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", "X-Admin-Api-Key": adminKey },
    body: JSON.stringify({ enabled: false }),
    signal: AbortSignal.timeout(8000),
  }).catch(() => {})
}

/** D74a follow-up: write a hermetic `settings.json` carrying a single
 *  hook entry on the requested channel that points at the in-repo
 *  `scripts/magi-gate.sh`. Returns the absolute path of the written
 *  settings file (so the caller can also forward it as
 *  `CLAUDE_CONFIG_DIR`); returns null when the operator already pinned
 *  one via `MAGI_CP_E2E_CLAUDE_SETTINGS` OR when the claude binary is
 *  unreachable (no point installing a hook for a binary we can't run).
 *
 *  This keeps the user's real `~/.claude/settings.json` untouched and
 *  converts the previous permanent SKIP on machines without the hook
 *  into a real end-to-end run. _claudeHookMissingReason will pick up
 *  the override from the env var via the same name. */
function _ensureHermeticHook(
  channel: "PreToolUse" | "UserPromptSubmit",
): string | null {
  // Operator-pinned settings win — don't second-guess them.
  if (process.env.MAGI_CP_E2E_CLAUDE_SETTINGS) return null
  if (locateClaude() == null) return null

  // Resolve the in-repo magi-gate.sh from this file's location:
  //   tests/e2e/scenarios/04-...spec.ts -> ../../../scripts/magi-gate.sh
  const here = resolve(__dirname)
  const gatePath = resolve(here, "..", "..", "..", "scripts", "magi-gate.sh")
  if (!existsSync(gatePath)) return null

  const tmpDir = mkdtempSync(join(tmpdir(), "magi-cp-e2e-claude-cfg-"))
  const settingsPath = join(tmpDir, "settings.json")
  const matcher = channel === "PreToolUse" ? "Bash" : "*"
  const settings = {
    hooks: {
      [channel]: [
        {
          matcher,
          hooks: [
            {
              type: "command",
              command: gatePath,
              env: {
                MAGI_CP_CLOUD_URL:
                  process.env.MAGI_CP_CLOUD_URL ?? "http://127.0.0.1:8787",
                MAGI_CP_API_KEY: process.env.MAGI_CP_API_KEY ?? "",
              },
            },
          ],
        },
      ],
    },
  }
  writeFileSync(settingsPath, JSON.stringify(settings, null, 2))
  return settingsPath
}
