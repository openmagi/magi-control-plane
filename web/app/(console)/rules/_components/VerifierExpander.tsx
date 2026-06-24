import Link from "next/link"
import { ledgerHref } from "@/lib/ledger-url"
import { availableFields, type FieldDescriptor as PayloadFieldDescriptor } from "@/lib/payload-schemas"
import {
  getVerifierDescriptor,
  type EvidenceField,
  type InputField,
  type TriggerSpec,
} from "@/lib/verifier-descriptors"
import { Code } from "@/components/ui"
import { VerifierFieldChecks } from "../../_components/VerifierFieldChecks"
import { VerifierSamplesList } from "./VerifierSamplesList"

/**
 * D52b: per-verifier expander rendered on the Rules → Verifiers tab.
 *
 * Native <details>/<summary> gives:
 *   - keyboard accessibility (Space / Enter to toggle, focus ring)
 *   - aria-expanded for free
 *   - CSS-only open/close transition
 *   - no JS hydration cost (this is a server component)
 *
 * The four panels surface:
 *   1. Triggers: CC hook events + matcher class + a one-line author hint
 *   2. Input payload paths: chips (path + type + hover description / example)
 *   3. Output verdict shape: possible verdicts the verifier may return
 *   4. Output evidence shape: the record this verifier emits to the ledger
 *
 * For verifiers without a descriptor (e.g. derived-policy steps the cloud
 * has not yet bound to a built-in implementation) the expander renders a
 * neutral notice and skips the four panels.
 */

type T = (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string

export function VerifierExpander({
  step, t, locale, recentEmissions24h, nfFormat, source, enforcement, fieldChecksOverride,
}: {
  step: string
  t: T
  /** Resolved locale, threaded down to client-component children that
   * cannot accept the `t` closure across the RSC boundary. They
   * rebuild `t` via the pure `translate()` from dict.ts. */
  locale: import("@/lib/i18n/dict").Locale
  /** D52c: count of ledger entries emitted by this verifier in the last
   * 24h. `null` = cloud unreachable for the count (render dash so
   * operators don't misread a transient outage as "no emissions"). */
  recentEmissions24h?: number | null
  nfFormat?: (n: number) => string
  /** D52c follow-up: catalog source bucket. `custom` verifiers
   * (authored via /verifiers/new) have NO runtime binding today, so
   * the count will always be 0, which we explain inline instead of
   * silently mis-signalling "no usage". `policy-derived` rows with
   * enforcement=missing get the same treatment (the policy
   * references a step name nothing implements). */
  source?: "builtin" | "custom" | "policy-derived"
  enforcement?: "enforcing" | "always-on" | "preview" | "missing"
  /** D52d follow-up: author-supplied field_checks for `source:
   * "custom"` rows (where getVerifierDescriptor returns null). Passed
   * through to VerifierFieldChecks so the catalog renders the
   * operator's authored tree instead of the preview placeholder. */
  fieldChecksOverride?: Array<{ path: string; check_description: string }>
}) {
  const descriptor = getVerifierDescriptor(step)
  // Distinct accessible name per row so a SR user scanning the list
  // hears "details, citation_verify" instead of five "details"s in a row.
  const summaryLabel = t("rules.verifier.expander.toggleWithStep", { step })

  return (
    <details className="group mt-2 rounded-lg border border-black/[0.05] bg-[var(--color-surface-1,#f9fafb)]/40">
      <summary
        aria-label={summaryLabel}
        className="flex cursor-pointer items-center justify-between gap-2 rounded-lg px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)] hover:bg-black/[0.02] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-accent)]"
      >
        <span>
          {t("rules.verifier.expander.toggle")}
          <span className="ml-1.5 font-mono normal-case tracking-normal text-[var(--color-text-secondary)]">
            {step}
          </span>
        </span>
        <span
          aria-hidden
          className="inline-block transition-transform duration-150 group-open:rotate-180"
        >
          ▾
        </span>
      </summary>

      <div className="px-3 pb-3 pt-1">
        {descriptor === null ? (
          <>
            {/* D52d follow-up: a custom-source row has no descriptor
                but DOES carry an operator-authored field_checks list.
                Render the tree from that list so the catalog row stops
                misleading the operator that their authoring "didn't
                stick". Fall back to the neutral "no descriptor" notice
                only when there are no authored rows either. */}
            {fieldChecksOverride && fieldChecksOverride.length > 0 ? (
              <FieldChecksPanel
                step={step}
                t={t}
                fieldChecksOverride={fieldChecksOverride}
              />
            ) : (
              <p className="text-xs text-[var(--color-text-tertiary)] italic">
                {t("rules.verifier.expander.noDescriptor")}
              </p>
            )}
          </>
        ) : (
          <>
            <TriggersPanel triggers={descriptor.triggers} t={t} />
            <FieldChecksPanel step={step} t={t} />
            <InputPathsPanel
              step={step}
              paths={descriptor.input_payload_paths}
              inputFields={descriptor.input_fields ?? []}
              triggers={descriptor.triggers}
              t={t}
            />
            <VerdictPanel verdicts={descriptor.verdict_set} t={t} />
            <EvidencePanel evidence={descriptor.output_evidence} t={t} />
          </>
        )}
        {/* D52c: recent emissions widget. Rendered for EVERY verifier
            (descriptor null or not) so operators of an unknown / derived
            step can still jump straight to the ledger view filtered to
            that step. */}
        <RecentEmissionsPanel
          step={step}
          count={recentEmissions24h ?? null}
          nfFormat={nfFormat}
          source={source}
          enforcement={enforcement}
          t={t}
          locale={locale}
        />
      </div>
    </details>
  )
}

function RecentEmissionsPanel({
  step, count, nfFormat, source, enforcement, t, locale,
}: {
  step: string
  count: number | null
  nfFormat?: (n: number) => string
  source?: "builtin" | "custom" | "policy-derived"
  enforcement?: "enforcing" | "always-on" | "preview" | "missing"
  t: T
  /** Threaded down to VerifierSamplesList (client) so it can rebuild
   * `t` locally without crossing the RSC boundary with a function. */
  locale: import("@/lib/i18n/dict").Locale
}) {
  // D52c follow-up: a `custom` verifier has no runtime binding today
  // (D52b authored at /verifiers/new but POST /verify/{name} returns
  // 404), so a count of 0 is structural, not "no usage". Same for
  // `policy-derived` rows that the cloud labels `enforcement:
  // missing` (a policy references a step name nothing implements).
  // We surface a tiny status note so an operator does not chase a
  // non-bug; the jump-link to /ledger stays available so they can
  // confirm the empty filter view themselves.
  const noRuntimeBinding =
    source === "custom" || (source === "policy-derived" && enforcement === "missing")
  const noteKey: keyof typeof RECENT_NOTE_KEYS | null = noRuntimeBinding
    ? (source === "custom"
        ? "custom"
        : "missing")
    : null
  const formatted = count === null
    ? t("rules.verifier.expander.recentEmissionsUnavailable")
    : (nfFormat ? nfFormat(count) : String(count))
  // D52c follow-up: route the jump-link through the same `ledgerHref`
  // builder the chip selector uses, so the URL is byte-identical to
  // the one the chip selector emits after the user navigates and
  // re-clicks the chip (back-button history collapses cleanly). Was:
  // hand-rolled `encodeURIComponent` here + `URLSearchParams` there,
  // which differed on `%20` vs `+` for any verifier step that ever
  // contained a space (step names are alphanumeric+underscore today
  // so no regression observed; the foot-gun was the duplicated
  // contract, fixed at the source).
  const href = ledgerHref({ verifiers: [step] })
  // D53a: only render the inline samples list when the verifier has a
  // runtime binding (built-in or wired policy-derived step). Custom
  // verifiers + `enforcement: missing` rows do not emit, so the list
  // would always be empty there and the "Show samples" affordance
  // would be misleading. The structural note is already surfaced by
  // the `noteKey` branch below.
  const showSamples = !noRuntimeBinding
  return (
    <div data-testid="verifier-expander-recent-emissions">
      <PanelHeader>
        {t("rules.verifier.expander.recentEmissions")}
      </PanelHeader>
      <div className="flex flex-wrap items-baseline gap-3 text-xs">
        <span
          data-testid="verifier-expander-recent-emissions-count"
          className="font-mono text-sm font-semibold text-[var(--color-text-primary)]"
        >
          {formatted}
        </span>
        <span className="text-[var(--color-text-tertiary)]">
          {t("rules.verifier.expander.recentEmissionsWindow")}
        </span>
        <Link
          href={href}
          data-testid="verifier-expander-ledger-link"
          className="ml-auto font-medium text-[var(--color-accent-light)] hover:underline"
        >
          {t("rules.verifier.expander.viewInLedger")}
        </Link>
      </div>
      {noteKey && (
        <p
          data-testid="verifier-expander-no-runtime-binding"
          className="mt-1.5 text-[11px] italic text-[var(--color-text-tertiary)] leading-relaxed"
        >
          {t(RECENT_NOTE_KEYS[noteKey])}
        </p>
      )}
      {showSamples && (
        <VerifierSamplesList step={step} locale={locale} initialCount={count} />
      )}
    </div>
  )
}

/** D52c follow-up: dictionary keys for the no-runtime-binding note. */
const RECENT_NOTE_KEYS = {
  custom: "rules.verifier.expander.recentEmissionsNoRuntimeCustom",
  missing: "rules.verifier.expander.recentEmissionsNoRuntimeMissing",
} as const

function PanelHeader({ children }: { children: React.ReactNode }) {
  return (
    <h4 className="mt-3 mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)]">
      {children}
    </h4>
  )
}

/** D52d: per-field check tree. Renders the new `path -> check
 * description` tree the brief asks for, between the Triggers panel and
 * the existing Input panel (which keeps showing the verifier-side
 * input schema; field_checks is the CC-stdin-side contract, so the two
 * panels do not duplicate).
 *
 * D52d follow-up: optional `fieldChecksOverride` so custom-source
 * catalog rows (no registered descriptor) can still render the
 * operator's authored tree. */
function FieldChecksPanel({
  step, t, fieldChecksOverride,
}: {
  step: string
  t: T
  fieldChecksOverride?: Array<{ path: string; check_description: string }>
}) {
  return (
    <div data-testid="verifier-expander-field-checks">
      <PanelHeader>
        {t("rules.verifier.expander.fieldChecks")}
      </PanelHeader>
      <VerifierFieldChecks
        step={step}
        t={t}
        fieldChecksOverride={fieldChecksOverride}
      />
    </div>
  )
}

function TriggersPanel({ triggers, t }: { triggers: TriggerSpec[]; t: T }) {
  return (
    <div data-testid="verifier-expander-triggers">
      <PanelHeader>{t("rules.verifier.expander.triggers")}</PanelHeader>
      <ul className="space-y-1.5">
        {triggers.map((tr, i) => (
          <li key={`${tr.event}:${tr.matcher_class}:${i}`} className="text-xs">
            <div className="flex flex-wrap items-baseline gap-2">
              <Code className="text-[12px]">{tr.event}</Code>
              <span className="inline-flex items-center rounded-full bg-[var(--color-muted-bg,#f3f4f6)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-[var(--color-muted-fg,#374151)]">
                {tr.matcher_class}
              </span>
            </div>
            <p className="mt-1 text-[11.5px] text-[var(--color-text-secondary)] leading-relaxed">
              {tr.note}
            </p>
          </li>
        ))}
      </ul>
    </div>
  )
}

function InputPathsPanel({
  step,
  paths,
  inputFields,
  triggers,
  t,
}: {
  step: string
  paths: string[]
  inputFields: InputField[]
  triggers: TriggerSpec[]
  t: T
}) {
  // Primary lookup: descriptor's own input_fields (sourced from the
  // verifier's input_schema). These describe the verifier's OWN input
  // dict — `text` for privilege_scan, `citations[].quote` for
  // citation_verify, etc. They are NOT the CC stdin envelope.
  const inputLookup: Record<string, InputField> = {}
  for (const f of inputFields) {
    if (f.path) inputLookup[f.path] = f
  }
  // Secondary fallback: CC hook payload schema cross-reference. When
  // an author lists a path that overlaps a CC stdin field (e.g.
  // `tool_input.command`), we surface the schema's description / type
  // for free.
  const ccLookup: Record<string, PayloadFieldDescriptor> = {}
  for (const tr of triggers) {
    const fields = availableFields(
      tr.event,
      tr.matcher_class === "tool" ? "*" : undefined,
    )
    for (const f of fields) {
      if (!ccLookup[f.path]) ccLookup[f.path] = f
    }
  }

  return (
    <div data-testid="verifier-expander-input">
      <PanelHeader>{t("rules.verifier.expander.input")}</PanelHeader>
      <ul className="space-y-1.5" role="list" data-step={step}>
        {paths.map((p) => {
          const inputField = inputLookup[p]
          const ccField = ccLookup[p]
          const description =
            inputField?.description
            ?? ccField?.description
            ?? t("rules.verifier.expander.inputFallback")
          const example = inputField?.example ?? ccField?.example
          const type = inputField?.type ?? ccField?.type ?? "json"
          // Stable id so aria-describedby resolves; one path per row.
          const descId = `verifier-${step}-input-${p.replace(/[^a-zA-Z0-9_-]/g, "_")}`
          return (
            <li
              key={p}
              role="listitem"
              className="rounded-md border border-black/[0.08] bg-white p-2"
            >
              <div className="flex flex-wrap items-baseline gap-1.5">
                <Code className="text-[11.5px]">{p}</Code>
                <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
                  {type}
                </span>
              </div>
              {/* Description + example rendered inline so they are reachable
                  by keyboard / screen reader. The prior `title` attribute
                  was mouse-only and invisible to AT (WCAG 2.1.1 / 1.3.1). */}
              <p
                id={descId}
                className="mt-1 text-[11px] text-[var(--color-text-secondary)] leading-relaxed"
              >
                {description}
              </p>
              {example && (
                <p className="mt-0.5 text-[10.5px] text-[var(--color-text-tertiary)] font-mono break-all">
                  <span className="not-italic uppercase tracking-wider text-[9.5px] mr-1.5">
                    {t("rules.verifier.expander.inputExample")}
                  </span>
                  {example}
                </p>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}

function VerdictPanel({
  verdicts,
  t,
}: {
  verdicts: ReadonlyArray<string>
  t: T
}) {
  return (
    <div data-testid="verifier-expander-verdicts">
      <PanelHeader>{t("rules.verifier.expander.verdicts")}</PanelHeader>
      <div className="flex flex-wrap gap-1.5" role="list">
        {verdicts.map((v) => (
          <span
            key={v}
            role="listitem"
            className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${verdictTone(v)}`}
          >
            {v}
          </span>
        ))}
      </div>
    </div>
  )
}

function verdictTone(verdict: string): string {
  // Theme-aware CSS variable pairs so a future dark-mode flip does not
  // strand the chips on a near-white bg + light-tinted fg (WCAG 1.4.3).
  // Fallback values match the prior emerald/rose/amber/gray palette so
  // the chip render is byte-identical until tokens are themed.
  switch (verdict) {
    case "pass":
      return "bg-[var(--color-pass-bg,#ecfdf5)] text-[var(--color-pass-fg,#047857)]"
    case "deny":
    case "fail":
      return "bg-[var(--color-deny-bg,#fff1f2)] text-[var(--color-deny-fg,#be123c)]"
    case "review":
    case "needs_review":
      return "bg-[var(--color-review-bg,#fffbeb)] text-[var(--color-review-fg,#b45309)]"
    case "not_applicable":
      return "bg-[var(--color-muted-bg,#f3f4f6)] text-[var(--color-muted-fg,#374151)]"
    default:
      return "bg-[var(--color-muted-bg,#f3f4f6)] text-[var(--color-muted-fg,#374151)]"
  }
}

function EvidencePanel({ evidence, t }: { evidence: EvidenceField[]; t: T }) {
  return (
    <div data-testid="verifier-expander-evidence">
      <PanelHeader>{t("rules.verifier.expander.evidence")}</PanelHeader>
      <div className="rounded-lg border border-black/[0.05] bg-white">
        <table className="w-full text-left text-[11.5px]">
          <thead>
            <tr className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
              <th className="px-2 py-1.5 font-semibold">
                {t("rules.verifier.expander.evidence.path")}
              </th>
              <th className="px-2 py-1.5 font-semibold">
                {t("rules.verifier.expander.evidence.type")}
              </th>
              <th className="px-2 py-1.5 font-semibold">
                {t("rules.verifier.expander.evidence.description")}
              </th>
            </tr>
          </thead>
          <tbody>
            {evidence.map((f, i) => (
              <tr
                key={`${f.path}-${i}`}
                className={i > 0 ? "border-t border-black/[0.04]" : ""}
              >
                <td className="px-2 py-1.5 align-top">
                  <Code className="text-[11.5px]">{f.path}</Code>
                </td>
                <td className="px-2 py-1.5 align-top">
                  <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
                    {f.type}
                  </span>
                </td>
                <td className="px-2 py-1.5 align-top text-[var(--color-text-secondary)]">
                  {f.description}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
