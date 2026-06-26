/**
 * D73. scenario 05: inject_context roundtrip.
 *
 * Requires the `claude` binary. SKIPs when missing (do NOT fail). The
 * centralized `assertHarnessReady({ requiresClaude: true })` covers
 * every preflight skip surface (claude, docker, admin keys, cloud,
 * dashboard) with a single hook.
 *
 * Steps:
 *   1. Wire an inject_context policy on UserPromptSubmit with template
 *      "REMINDER: always cite sources".
 *   2. Run claude -p with a simple prompt. the gate fires
 *      additionalContext on the UserPromptSubmit hook.
 *   3. Assert the ledger has a row referencing the policy id. We do
 *      NOT assert the model's output contains the injected text. that
 *      is brittle without controlling the LLM. The ledger evidence is
 *      the contract we check.
 */
import { test, expect } from "@playwright/test"
import { runClaudePrompt } from "../helpers/claude"
import { currentLedgerCursor, waitForLedgerRow } from "../helpers/ledger"
import { assertHarnessReady } from "../helpers/preflight"
import { mkdtempSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

test.describe.configure({ mode: "serial" })

test("05 inject_context roundtrip", async ({}, testInfo) => {
  // D74a: also require the CC magi-gate hook to be wired (see the
  // sibling note in 04-run-command-roundtrip.spec.ts). Without it
  // the inject_context policy never fires, no ledger row is written,
  // and this scenario waits 30s before timing out instead of
  // surfacing the install gap as a clear SKIP reason.
  const skipReason = assertHarnessReady({ requiresClaudeHook: true })
  test.skip(skipReason != null, skipReason ?? "")

  const policyId = `e2e/inject-${Date.now()}`
  await _wirePolicy(policyId)

  const cursor = await currentLedgerCursor()

  const cwd = mkdtempSync(join(tmpdir(), "magi-cp-e2e-inject-"))
  const result = await runClaudePrompt(
    "Reply with a one-word answer to this question: hello?",
    { cwd, timeoutMs: 60_000 },
  )

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

  expect(result.available).toBe(true)

  // Ledger should carry an inject_context fire row referencing the
  // wired policy id.
  const row = await waitForLedgerRow(
    (r) => JSON.stringify(r.body ?? {}).includes(policyId),
    { timeoutMs: 30_000, startSinceId: cursor, testInfo },
  )
  expect(row, "no ledger row referencing inject policy").toBeTruthy()

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
      description: "e2e inject_context",
      version: "1",
      type: "context_injection",
      trigger: { host: "claude_code", event: "UserPromptSubmit", matcher: "*" },
      event: "UserPromptSubmit",
      matcher: "*",
      template: "REMINDER: always cite sources",
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
