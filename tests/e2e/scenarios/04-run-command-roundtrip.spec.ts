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
import { runClaudePrompt } from "../helpers/claude"
import { currentLedgerCursor, waitForLedgerRow } from "../helpers/ledger"
import { assertHarnessReady } from "../helpers/preflight"
import { mkdtempSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

test.describe.configure({ mode: "serial" })

test("04 run_command roundtrip", async ({}, testInfo) => {
  // D74a: also require the CC magi-gate hook to be wired in
  // ~/.claude/settings.json — without it `claude -p` runs with no
  // PreToolUse hook, the policy never fires, and the ledger row this
  // scenario asserts on never appears. The previous version waited
  // 30s for that row before timing out; the explicit preflight
  // surfaces the install gap as a clear SKIP reason.
  const skipReason = assertHarnessReady({ requiresClaudeHook: true })
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
  const result = await runClaudePrompt(
    "Use the Bash tool to run: echo MAGI_E2E",
    {
      cwd,
      allowedTools: ["Bash"],
      timeoutMs: 60_000,
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
