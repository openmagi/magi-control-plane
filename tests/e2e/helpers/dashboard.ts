/**
 * D73. page-object helpers for the magi-cp dashboard.
 *
 * These wrap Playwright Page actions in named verbs the scenarios can
 * read. The wizard surface (/policies/new) is the busiest page-object;
 * the silent regressions D73 exists to catch (Step 3->4 silent reject,
 * Step 4->5 silent reject, NlAuthoringGuide t closure crash, picker
 * landing force-redirect) all live on this URL.
 *
 * The helpers prefer URL-state assertions over DOM-text assertions
 * wherever the wizard already round-trips state via search params:
 * that is the design contract the page is supposed to honour, and
 * URL stability is what regressions break first.
 */
import type { Page, Response } from "@playwright/test"
import { expect } from "@playwright/test"

export async function gotoRulesPolicies(page: Page): Promise<void> {
  await page.goto("/rules?tab=policies")
  await expect(page).toHaveURL(/\/rules/)
}

/** D74a: The wizard's "다음" / "Next" / "정책 저장" submit button is
 *  NOT the first `button[type="submit"]` on the page — the sidebar's
 *  language switcher uses type="submit" too and renders before the
 *  wizard. Scope by finding any form anchored to the wizard's URL
 *  shape (it carries either a hidden `_step` input on steps 1-5 or a
 *  hidden `id` input + `_compiled_sha256` on step 6's save form).
 *  Falls back to "any submit inside a <main>" so a future step-shape
 *  change does not silently match the language switcher again. */
function wizardSubmit(page: Page) {
  // Try the steps 1-5 advance form first (hidden _step input).
  const advance = page.locator(
    'form:has(input[name="_step"]) button[type="submit"]',
  )
  // Step 6 save form has no _step but lives inside <main>; the
  // language switcher lives in <complementary> (sidebar). Scope to
  // <main> so the save submit wins.
  const save = page.locator(
    'main button[type="submit"]',
  )
  // Playwright `or()` picks whichever resolves first.
  return advance.or(save).first()
}

export async function gotoNewPolicy(page: Page): Promise<void> {
  await page.goto("/policies/new")
  await expect(page).toHaveURL(/\/policies\/new/)
}

/** Click "Guided" (blank, start from step=1) on the picker landing.
 *
 *  Note: landing rendered ONLY when no mode= param is on the URL. If
 *  a regression force-redirects to ?mode=guided before the operator
 *  picks, the picker silently disappears, so the scenario asserts the
 *  landing is reachable, then advances.
 *
 *  D74a drift: D75+ added prebuilt "seed" links that also match
 *  href*="mode=guided" (they jump to step=5 with pre-filled state),
 *  which made the .first() match deliver a prebuilt seed instead of
 *  the blank wizard. Pin to the canonical blank-wizard href
 *  (`step=1` with no other params) so the scenario always lands on
 *  step 1 with an empty state. */
export async function pickGuided(page: Page): Promise<void> {
  // Match the canonical blank-wizard link. Encoded `&` from the
  // server-rendered HTML decodes back to `&` at the DOM attribute
  // level, so the literal href is `/policies/new?mode=guided&step=1`.
  const link = page.locator(
    'a[href="/policies/new?mode=guided&step=1"]',
  ).first()
  await expect(link).toBeVisible({ timeout: 10_000 })
  await link.click()
  await expect(page).toHaveURL(/mode=guided.*step=1|step=1.*mode=guided/)
}

/** Pick "Stop" on Step 1 (it's a radio input + visible label).
 *
 *  Stop sits in the "Common" group (D69+: 5 recommended events).
 *  The radio input has value="pre_final" (Stop's wizard slug). */
export async function step1PickStop(page: Page): Promise<void> {
  await page.waitForURL(/step=1|mode=guided/, { timeout: 10_000 })
  const radio = page.locator('input[type="radio"][value="pre_final"]').first()
  await expect(radio).toBeAttached({ timeout: 10_000 })
  await radio.check({ force: true })
  // Submit the form. The wizard uses a single advance button per step
  // (text varies by locale, match by type=submit + visible).
  await Promise.all([
    page.waitForURL(/step=[2-6]/, { timeout: 10_000 }),
    wizardSubmit(page).click(),
  ])
}

/** Step 2 is auto-skipped for non-tool-context lifecycles (Stop is
 *  one). The wizard advances by URL redirect, so the assertion is
 *  on the landing step. */
export async function step2ExpectAutoSkipped(page: Page): Promise<void> {
  await page.waitForURL(/step=[3-6]/, { timeout: 10_000 })
}

/** Step 3 picks a verifier kind (evidence_ref → citation_verify is the
 *  documented happy-path combo; for Stop the wizard offers a single
 *  default verifier which we accept). The submit is the typical
 *  redirect-to-step-4 contract.
 *
 *  When the page silently rejects (the bug class), the URL stays on
 *  step=3 with an err= flash; assertion catches that. */
export async function step3PickDefaultAndAdvance(page: Page): Promise<void> {
  await page.waitForURL(/step=3/, { timeout: 10_000 })
  // The Step 3 surface either offers a "skip / no specifics" advance
  // OR a per-verifier picker. Either way the form's submit is the
  // contract. We rely on the first :checked-or-default radio being
  // pre-selected when only one option exists; otherwise the test
  // surfaces the case via an explicit miss.
  await Promise.all([
    page.waitForURL(/step=[4-6]|err=/, { timeout: 10_000 }),
    wizardSubmit(page).click(),
  ])
  // Catch the silent regression class: a submit that lands back on
  // step=3 with an err= param is a regression, not a pass.
  await expect(page).not.toHaveURL(/step=3.*err=/, { timeout: 5_000 })
}

/** Step 4. pick "audit" action and advance. The audit card is a
 *  radio input with value="audit". */
export async function step4PickAuditAndAdvance(page: Page): Promise<void> {
  await page.waitForURL(/step=4/, { timeout: 10_000 })
  const auditRadio = page.locator('input[type="radio"][value="audit"]').first()
  await expect(auditRadio).toBeAttached({ timeout: 10_000 })
  await auditRadio.check({ force: true })
  await Promise.all([
    page.waitForURL(/step=[5-6]|err=/, { timeout: 10_000 }),
    wizardSubmit(page).click(),
  ])
  await expect(page).not.toHaveURL(/step=4.*err=/, { timeout: 5_000 })
}

/** Step 5. type a unique policy id. */
export async function step5SetIdAndAdvance(
  page: Page,
  id: string,
): Promise<void> {
  await page.waitForURL(/step=5/, { timeout: 10_000 })
  // The id input is name="id" (single text input on Step 5).
  const idInput = page.locator('input[name="id"]').first()
  await expect(idInput).toBeVisible({ timeout: 10_000 })
  await idInput.fill(id)
  await Promise.all([
    page.waitForURL(/step=6|err=/, { timeout: 10_000 }),
    wizardSubmit(page).click(),
  ])
  await expect(page).not.toHaveURL(/step=5.*err=/, { timeout: 5_000 })
}

/** Step 6. confirm save. The review screen has a final "Save" submit
 *  that POSTs to /policies and redirects to /rules (or /policies/<id>). */
export async function step6Save(page: Page): Promise<Response | null> {
  await page.waitForURL(/step=6/, { timeout: 10_000 })
  const submit = wizardSubmit(page)
  const [response] = await Promise.all([
    page.waitForResponse(
      (r) => r.request().method() === "POST" && /\/policies/.test(r.url()),
      { timeout: 15_000 },
    ).catch(() => null),
    submit.click(),
  ])
  // After save the dashboard navigates away from /policies/new.

  await page.waitForURL(/^(?!.*\/policies\/new).*/, { timeout: 10_000 }).catch(() => {})
  return response
}

export async function gotoScripts(page: Page): Promise<void> {
  await page.goto("/scripts")
  await expect(page).toHaveURL(/\/scripts/)
}

export async function gotoLedger(page: Page): Promise<void> {
  await page.goto("/ledger")
  await expect(page).toHaveURL(/\/ledger/)
}
