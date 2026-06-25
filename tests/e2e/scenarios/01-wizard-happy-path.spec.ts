/**
 * D73 — scenario 01: wizard happy path (no LLM).
 *
 * Drives the Guided wizard from picker landing through Save, then
 * asserts the saved policy is present in /policies and disappears
 * after the dashboard's "delete" (disable) call.
 *
 * Catches:
 *   - picker landing force-redirect (mode=guided auto-picked → silent)
 *   - Step 3 -> 4 silent reject (the wizard land back on step=3 with
 *     ?err=, the helper asserts not-URL on err=)
 *   - Step 4 -> 5 silent reject (same pattern)
 *   - NlAuthoringGuide t closure crash (any unhandled client exception
 *     surfaces as a page error event, caught below)
 */
import { test, expect } from "@playwright/test"
import {
  gotoNewPolicy, pickGuided,
  step1PickStop, step2ExpectAutoSkipped,
  step3PickDefaultAndAdvance, step4PickAuditAndAdvance,
  step5SetIdAndAdvance, step6Save,
} from "../helpers/dashboard"
import { listPolicies, deletePolicy } from "../helpers/cloud"

test.describe.configure({ mode: "serial" })

test("01 wizard happy path", async ({ page }) => {
  // Surface any uncaught client-side exception as a test failure.
  // NlAuthoringGuide's `t` closure crash was the exact regression that
  // tsc + vitest let slip; this listener turns it into a failed assertion.
  const clientErrors: string[] = []
  page.on("pageerror", (err) => clientErrors.push(err.message))

  const id = `e2e/wizard-happy-${Date.now()}`

  await gotoNewPolicy(page)
  await pickGuided(page)
  await step1PickStop(page)
  await step2ExpectAutoSkipped(page)
  await step3PickDefaultAndAdvance(page)
  await step4PickAuditAndAdvance(page)
  await step5SetIdAndAdvance(page, id)
  await step6Save(page)

  // Backend assertion: the new id is present.
  const policies = await listPolicies()
  const found = policies.find((p) => p.id === id)
  expect(found, `policy ${id} should be present after save`).toBeTruthy()

  // Soft-delete via cloud (the dashboard's delete button maps to the
  // same patch). Validates the disable path round-trips.
  await deletePolicy(id)
  const after = await listPolicies()
  const stillEnabled = after.find((p) => p.id === id && p.enabled)
  expect(stillEnabled, `policy ${id} should be disabled after delete`).toBeFalsy()

  // No uncaught client exceptions.
  expect(clientErrors, "client-side exceptions").toEqual([])
})
