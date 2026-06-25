/**
 * Flash messages. sanitize ?msg / ?err search params.
 *
 * The previous design echoed arbitrary querystring text into "action error"
 * banners, which a phishing link could weaponize ("error: paste your API key
 * at evil.example"). Server actions now redirect with stable CODES; this
 * module maps codes → display strings. Unknown codes render nothing.
 */
export type FlashKind = "ok" | "error"

const OK_CODES: Record<string, string> = {
  toggled: "Policy updated.",
  saved: "Saved.",
  verifier_created: "Custom verifier created.",
  // D75 follow-up: createPackAction redirects with
  // `?msg=pack_created` on the happy path. Without an entry here
  // resolveFlash returned null and the first-time visitor who
  // followed the empty-state CTA and submitted the form landed back
  // on /rules with zero confirmation.
  pack_created: "Policy pack created.",
  // D75 follow-up: enable/disable cascade reports per-member
  // outcomes in `results[]`; the action surfaces a "partial
  // success" banner via this msg code when at least one member
  // succeeded. Distinct from the bare `toggled` code so the
  // operator knows not every member committed.
  pack_partial_success: "Pack toggled. Some members did not apply — check the pack card.",
}

const ERR_CODES: Record<string, string> = {
  cloud_unreachable: "Cloud unreachable. Check that the docker compose 'cloud' service is up (docker compose ps).",
  provider_unconfigured: "LLM providers are not configured on this self-host deployment. Add OPENAI_API_KEY (or ANTHROPIC_API_KEY / OPENROUTER_API_KEY) to your .env, then run 'docker compose restart cloud'.",
  cloud_5xx: "The cloud service returned an error. Check 'docker compose logs cloud' for details.",
  config_error: "Server is misconfigured. see server logs.",
  forbidden: "You are not authorized for this action.",
  not_found: "Not found.",
  invalid_id: "Invalid policy id.",
  invalid_input: "Invalid input.",
  conflict: "Action conflicted with current state.",
  template_too_long: "Inject template is too long (max 16000 chars).",
  strip_unsupported: "Strip action is not available for this lifecycle.",
  // D75 follow-up: createPackAction redirected with
  // `?err=name_required` when the form's name field was empty, but
  // the page only rendered a banner when resolveFlash returned a
  // non-null value — the visitor saw nothing and assumed the click
  // had no effect.
  name_required: "Pack name is required.",
  // D75 follow-up: togglePackAction reads the cloud cascade's
  // `results[]` and reroutes here when at least one member failed
  // outright (no successes either). Distinct from `pack_partial_success`
  // (which is an OK_CODE for "some succeeded, some didn't").
  pack_partial_failure: "Pack cascade failed — check the pack card for failed members.",
  // D62 follow-up: the seven Step 3 specifics codes (pick_condition,
  // missing_criterion, missing_pattern, missing_shacl, missing_domain,
  // missing_allowlist, missing_evidence) DELIBERATELY do not appear
  // here. Step3Condition renders a localized inline banner with
  // role="alert" plus a per-input red-ring helper for each code,
  // which is the natural focus location and is fully KO+EN. If we
  // also mapped them in ERR_CODES, resolveFlash would render a
  // duplicate English page-level banner above the localized inline
  // copy, regressing locale parity (a Korean operator would see one
  // English banner stacked above one Korean banner). The codes are
  // covered by the lib/i18n/dict.ts step3.err.* keys instead. See
  // STEP3_ERR_CODES in this file for the canonical list (still
  // exported so wizard-wiring tests can pin the mapping).
  // D62 codes intentionally omitted from ERR_CODES; see comment above.
  // D68: the Step 4 action-specifics codes (missing_template,
  // missing_command_or_script, missing_rewriter_prefix,
  // missing_rewriter_scheme, missing_rewriter_pattern) follow the
  // same locale-parity rule. Step4Action renders an inline banner
  // plus per-input red-ring helper inside the Step 4b sub-form for
  // each code; see STEP4_ERR_CODES below.
}

/** D62 follow-up: canonical Step 3 specifics err codes. Exported so
 *  the wizard-wiring test can iterate them and assert every code
 *  emitted by validateStep3Specifics has a corresponding dict key
 *  (closes the i18n-drift gap reported in the D62 review). */
export const STEP3_ERR_CODES = [
  "pick_condition",
  "missing_criterion",
  "missing_pattern",
  "missing_shacl",
  "missing_domain",
  "missing_allowlist",
  "missing_evidence",
] as const
export type Step3ErrCode = (typeof STEP3_ERR_CODES)[number]

/** D68: canonical Step 4 action-specifics err codes. Mirrors the
 *  D62 pattern for Step 3. advanceWizard now refuses the Step 4 to
 *  Step 5 advance when the chosen action's sub-form fields are
 *  empty, redirecting back to step=4 with one of these codes.
 *  Step4Action renders an inline error banner near the Step 4b
 *  sub-form (NOT at the top of the page) plus a per-input red-ring
 *  helper, replacing the old generic `invalid_input` banner.
 *
 *  D68 follow-up (P2 ux-clarity): the original `missing_rewriter_config`
 *  code was split into three per-kind codes so the inline copy can
 *  name only the relevant field instead of leaking IR kind names
 *  (prefix_strip / scheme_force / regex_substitute) into the user-
 *  visible banner. This mirrors the D62 per-condition split where
 *  Step 3 has separate `missing_pattern` / `missing_shacl` /
 *  `missing_domain` / etc. codes for each condition kind.
 *
 *  Codes are deliberately omitted from ERR_CODES above so
 *  resolveFlash returns null for them: the inline localized banner
 *  is the single source of truth (a duplicate English page-level
 *  banner above the localized inline copy would regress locale
 *  parity exactly as the D62 review documented). */
export const STEP4_ERR_CODES = [
  "missing_template",
  "missing_command_or_script",
  "missing_rewriter_prefix",
  "missing_rewriter_scheme",
  "missing_rewriter_pattern",
] as const
export type Step4ErrCode = (typeof STEP4_ERR_CODES)[number]

export function resolveFlash(
  msg: string | undefined,
  err: string | undefined,
): { kind: FlashKind; text: string } | null {
  if (msg && OK_CODES[msg]) return { kind: "ok", text: OK_CODES[msg] }
  if (err && ERR_CODES[err]) return { kind: "error", text: ERR_CODES[err] }
  return null
}

/** Server-side helper: convert a thrown error into a stable code.
 *
 * 5xx distinguished from "cloud_unreachable" because the cloud IS up
 * but returned an error. provider_unconfigured catches the most
 * common self-host paper-cut: a fresh install with no LLM keys. */
export function codeForError(e: unknown): string {
  const msg = e instanceof Error ? e.message : String(e)
  if (msg === "cloud config error") return "config_error"
  if (/providers? not configured|llm providers? not configured/i.test(msg)) {
    return "provider_unconfigured"
  }
  if (/^cloud 401|^cloud 403/.test(msg)) return "forbidden"
  if (/^cloud 404/.test(msg)) return "not_found"
  if (/^cloud 409/.test(msg)) return "conflict"
  if (/^cloud 4\d\d/.test(msg)) return "invalid_input"
  if (/^cloud 5\d\d/.test(msg)) return "cloud_5xx"
  return "cloud_unreachable"
}
