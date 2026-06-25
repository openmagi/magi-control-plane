/**
 * D75. scenario 06: policy-pack toggle round-trip.
 *
 * The Policies tab gains a "Policy packs" section above Prebuilts.
 * Enabling `pack/research-mode` must cascade to the 3 referenced
 * prebuilts; disabling it must flip every member back. The cloud's
 * cascade handler routes prebuilt members through the same enable
 * path used by the prebuilt-toggle scenario, so the failure modes
 * mirror scenario 02.
 *
 * This scenario uses the cloud helpers (HTTP) directly. The dashboard
 * page reload at the end pins the server-component render. Marked
 * with the same MAGI_CP_E2E_SKIP_DOCKER opt-out as the rest of the
 * scenarios.
 */
import { test, expect } from "@playwright/test"
import { gotoRulesPolicies } from "../helpers/dashboard"
import {
  disablePack, enablePack, listPolicies,
} from "../helpers/cloud"
import { assertHarnessReady } from "../helpers/preflight"

const PACK_ID = "pack/research-mode"
const MEMBERS = [
  "prebuilt/citation-verify-at-final",
  "prebuilt/source-allowlist-webfetch",
  "prebuilt/prompt-injection-webfetch",
]

test.describe.configure({ mode: "serial" })

test("06 policy pack research-mode roundtrip", async ({ page }, testInfo) => {
  const skipReason = assertHarnessReady()
  test.skip(skipReason != null, skipReason ?? "")

  const clientErrors: string[] = []
  const consoleErrors: string[] = []
  page.on("pageerror", (err) => clientErrors.push(err.message))
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text())
  })

  await test.step("goto /rules?tab=policies", async () => {
    await gotoRulesPolicies(page)
  })

  // Round 1: enable the pack via cloud. All 3 members land in
  // /policies as enabled prebuilt rows.
  const enableResult = await enablePack(PACK_ID)
  expect(enableResult.status).toBe("all")
  for (const r of enableResult.results) {
    expect(r.ok, `member ${r.id} should succeed`).toBe(true)
  }
  let policies = await listPolicies()
  for (const mid of MEMBERS) {
    const row = policies.find((p) => p.id === mid)
    expect(row, `${mid} should be present after pack enable`).toBeTruthy()
    expect(row!.enabled).toBe(true)
  }

  // Round 2: disable cascade. Rows survive but enabled flips to false.
  const disableResult = await disablePack(PACK_ID)
  expect(disableResult.status).toBe("none")
  policies = await listPolicies()
  for (const mid of MEMBERS) {
    const row = policies.find((p) => p.id === mid)
    expect(row, `${mid} should still be present after pack disable`)
      .toBeTruthy()
    expect(row!.enabled).toBe(false)
  }

  // Round 3: re-enable cascade is idempotent.
  await enablePack(PACK_ID)
  policies = await listPolicies()
  for (const mid of MEMBERS) {
    const row = policies.find((p) => p.id === mid)
    expect(row!.enabled).toBe(true)
  }

  // Reload /rules so the server component re-renders with the new
  // pack status badges + member toggle state. No client-side
  // exceptions expected.
  await page.reload()

  await testInfo.attach("client-errors", {
    body: JSON.stringify({
      pageerrors: clientErrors, console_errors: consoleErrors,
    }, null, 2),
    contentType: "application/json",
  })
  expect(clientErrors, "client-side exceptions").toEqual([])

  // Cleanup: leave members disabled so a follow-on run starts clean.
  await disablePack(PACK_ID)
})
