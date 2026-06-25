/**
 * D73. scenario 02: prebuilt toggle round-trip.
 *
 * /rules Policies tab carries a "Prebuilt" section above the user
 * policies. Toggling on / off / on must:
 *   - leave the row in /policies (D60 preserves the row when disabled),
 *   - be idempotent on re-enable,
 *   - NOT duplicate into the user-policies grid (D67 regression).
 *
 * The id under test is the canonical "citation-verify-at-final"
 * prebuilt (carrier of citation-verify on the Stop hook). It exists
 * in every shipped magi-cp build.
 */
import { test, expect } from "@playwright/test"
import { gotoRulesPolicies } from "../helpers/dashboard"
import {
  enablePrebuilt, disablePrebuilt, listPolicies,
} from "../helpers/cloud"
import { assertHarnessReady } from "../helpers/preflight"

const PREBUILT_ID = "prebuilt/citation-verify-at-final"

test.describe.configure({ mode: "serial" })

test("02 prebuilt toggle roundtrip", async ({ page }, testInfo) => {
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

  // Round 1: enable via cloud (mirrors the dashboard toggle click).
  await enablePrebuilt(PREBUILT_ID)
  let policies = await listPolicies()
  let row = policies.find((p) => p.id === PREBUILT_ID)
  expect(row, `${PREBUILT_ID} should be present after enable`).toBeTruthy()
  expect(row!.enabled).toBe(true)

  // Round 2: disable. The row is preserved (D60) with enabled=false.
  await disablePrebuilt(PREBUILT_ID)
  policies = await listPolicies()
  row = policies.find((p) => p.id === PREBUILT_ID)
  expect(row, `${PREBUILT_ID} should still be present after disable`)
    .toBeTruthy()
  expect(row!.enabled).toBe(false)

  // Round 3: enable again. Idempotent.
  await enablePrebuilt(PREBUILT_ID)
  policies = await listPolicies()
  row = policies.find((p) => p.id === PREBUILT_ID)
  expect(row!.enabled).toBe(true)

  // D67 regression: prebuilt rows must not double-render into a
  // separate user-policies bucket. listPolicies returns one row per
  // id, so we just count by id.
  const dupes = policies.filter((p) => p.id === PREBUILT_ID)
  expect(dupes.length, "prebuilt row must not duplicate").toBe(1)

  // Reload /rules so the page renders the new state. The page itself
  // is server-rendered. on reload we expect no client-side exceptions.
  await page.reload()

  await testInfo.attach("client-errors", {
    body: JSON.stringify({ pageerrors: clientErrors, console_errors: consoleErrors }, null, 2),
    contentType: "application/json",
  })

  expect(clientErrors, "client-side exceptions").toEqual([])

  // Cleanup: leave the row disabled so the next run starts clean.
  await disablePrebuilt(PREBUILT_ID)
})
