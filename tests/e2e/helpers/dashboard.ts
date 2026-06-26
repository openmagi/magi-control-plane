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

/** D74a: The wizard's "다음" / "Next" advance button is NOT the first
 *  `button[type="submit"]` on the page — the sidebar's language
 *  switcher uses type="submit" too and renders before the wizard. Scope
 *  by finding any form anchored to the wizard's URL shape: steps 1-5
 *  carry a hidden `_step` input, so we target the submit inside that
 *  form. (Step 6's save form has NO `_step` input — see
 *  `wizardSavePolicy` below.)
 *
 *  D74a follow-up: the previous helper resolved to `advance.or(save).first()`
 *  with `save = main button[type="submit"]`. On Step 6 with any
 *  non-trivial conditionKind (regex / llm_critic / shacl / fetchDomain /
 *  domain_allowlist), `InlineSubConfigPanel` renders BEFORE the real
 *  save form and contains its own `<form action={advanceAction}>` with
 *  `<input type="hidden" name="_step" value="5" />` plus a submit
 *  button. That hidden `_step` satisfied the advance selector AND lived
 *  inside `<main>`, AND appeared before the real save form in DOM
 *  order, so `.or().first()` clicked the inline sub-config save
 *  (looping the operator back to Step 5/6 with the inline edit posted)
 *  instead of the final save. The 01/02/06 scenarios masked the bug
 *  because they use Stop + default verifier (conditionKind == 'none',
 *  InlineSubConfigPanel returns null). Splitting the helper here keeps
 *  steps 1-5 on the `_step` form path and pins step 6 to the explicit
 *  `data-testid="wizard-save"` on the save NextButton. */
function wizardAdvance(page: Page) {
  return page.locator(
    'form:has(input[name="_step"]) button[type="submit"]',
  ).first()
}

/** D74a follow-up: Step 6 save button is pinned to the
 *  `data-testid="wizard-save"` testid on the real save form's
 *  NextButton. The InlineSubConfigPanel inline-edit forms render
 *  earlier in DOM and live inside `<main>` too, but they carry the
 *  inline-edit submit's own styling and no testid. */
function wizardSavePolicy(page: Page) {
  return page.locator('[data-testid="wizard-save"]').first()
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
 *  which made the previous `.first()` match deliver a prebuilt seed
 *  instead of the blank wizard.
 *
 *  D74a follow-up: the previous full-string-equality selector
 *  (`a[href="/policies/new?mode=guided&step=1"]`) was strictly correct
 *  but brittle — any future innocent extra query param on the picker
 *  landing's blank-wizard link (locale=ko, source=picker, intent=blank)
 *  would silently fail with a flat 10s timeout even though the picker
 *  was healthy. Prefer the stable `data-testid="picker-card-guided"`
 *  on the canonical ChoiceCard; fall through to a structural
 *  href-shape matcher (mode=guided + step=1, no &draft= or &step=5
 *  seed segments) so this still works on older dashboard builds where
 *  the testid is not yet present. */
export async function pickGuided(page: Page): Promise<void> {
  const byTestId = page.locator('[data-testid="picker-card-guided"]').first()
  // Structural matcher: any `/policies/new` link in `mode=guided` +
  // `step=1` that is NOT a prebuilt seed (`&draft=` or `&step=5`).
  const byShape = page.locator(
    'a[href*="/policies/new"][href*="mode=guided"][href*="step=1"]'
    + ':not([href*="draft="]):not([href*="step=5"])',
  ).first()
  const link = byTestId.or(byShape).first()
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
    wizardAdvance(page).click(),
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
    wizardAdvance(page).click(),
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
    wizardAdvance(page).click(),
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
    wizardAdvance(page).click(),
  ])
  await expect(page).not.toHaveURL(/step=5.*err=/, { timeout: 5_000 })
}

/** Step 6. confirm save. The review screen has a final "Save" submit
 *  that POSTs to /policies and redirects to /rules (or /policies/<id>). */
export async function step6Save(page: Page): Promise<Response | null> {
  await page.waitForURL(/step=6/, { timeout: 10_000 })
  const submit = wizardSavePolicy(page)
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
