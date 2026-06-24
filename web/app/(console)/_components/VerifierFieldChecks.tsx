import {
  getVerifierDescriptor,
  type FieldCheck,
} from "@/lib/verifier-descriptors"
import { Code } from "@/components/ui"

/**
 * D52d: per-field check tree, rendered in two surfaces:
 *
 *   1. The Rules → Verifiers catalog expander (rules/_components/
 *      VerifierExpander.tsx), where it replaces the prior flat input
 *      payload chip row with structured `path -> check description`
 *      pairs.
 *   2. The policies/new wizard verifier picker (policies/new/_components/
 *      WizardEvidenceTree.tsx → this component), inline below the
 *      author's verifier checkbox so they can see what the verifier
 *      actually inspects before they save the policy.
 *
 * Same data, same render. The only difference is whether the parent
 * also surfaces `verdicts:` / `emits:` rows underneath. The wizard
 * inline form opts in via `showFooter`; the catalog expander already
 * has dedicated Verdicts + Output Evidence panels and turns the footer
 * off to avoid duplicating those rows.
 *
 * Semantic HTML:
 *   - <dl><dt><dd> markup for the path → description mapping so screen
 *     readers announce "term, description" instead of two anonymous
 *     spans.
 *   - The visual tree characters (└─ etc.) are purely decorative and
 *     marked aria-hidden so the SR experience reads the data, not the
 *     ASCII art.
 *
 * Preview branch: a verifier without a registered descriptor (custom
 * verifiers authored via /verifiers/new, third-party preview prefix)
 * renders a one-line note instead of a tree. The catalog expander
 * already shows a "no descriptor" panel for the same case; the wizard
 * picker DOES need the inline note so the author understands why no
 * tree expanded after they picked.
 */

type T = (k: import("@/lib/i18n/dict").TKey, v?: Record<string, string | number>) => string

export function VerifierFieldChecks({
  step,
  t,
  showFooter = false,
  className,
  fieldChecksOverride,
}: {
  step: string
  t: T
  /** When true, surface the verdicts + emits rows under the tree (used
   * by the policy wizard picker; the catalog expander already has
   * dedicated Verdicts + Output Evidence panels). */
  showFooter?: boolean
  className?: string
  /** D52d follow-up: explicit field_checks override for catalog rows
   * whose `step` does not map to a built-in descriptor. Custom
   * verifiers authored at /verifiers/new carry their author-supplied
   * field_checks on the EvidenceTypeEntry. Without this, the tree
   * would render the "preview mode" placeholder for the very rows
   * the operator just authored. When provided AND non-empty, the
   * override replaces the descriptor's field_checks; when omitted,
   * the descriptor mirror is used (built-in path). The footer rows
   * (verdicts + emits) are descriptor-only; custom rows render the
   * tree without a footer because they have no registered descriptor
   * to source verdicts/evidence from. */
  fieldChecksOverride?: FieldCheck[]
}) {
  const descriptor = getVerifierDescriptor(step)
  const fieldChecks: FieldCheck[] =
    fieldChecksOverride !== undefined
      ? fieldChecksOverride
      : descriptor?.field_checks ?? []

  if (fieldChecks.length === 0) {
    return (
      <div
        data-testid="verifier-field-checks-preview"
        className={
          (className ?? "") +
          " text-[11.5px] italic text-[var(--color-text-tertiary)] leading-relaxed"
        }
      >
        {t("rules.verifier.fieldChecks.preview")}
      </div>
    )
  }

  // Build the verdicts + emits footer once so the wizard picker can show
  // the same shape that the catalog expander shows in its dedicated
  // panels. Always rendered when `showFooter` is on AND we have a
  // registered descriptor; custom-source rows have no descriptor so the
  // footer is omitted there even when showFooter is requested.
  const verdicts = descriptor?.verdict_set ?? []
  const evidencePaths = (descriptor?.output_evidence ?? []).map((e) => e.path)
  const renderFooter = showFooter && descriptor !== null

  return (
    <div
      data-testid="verifier-field-checks-tree"
      className={(className ?? "") + " text-xs"}
    >
      {/* D52d follow-up (a11y): explicit role='list' on the <dl> plus
          role='listitem' on each (dt, dd) wrapper. Some AT/browser
          combinations (older NVDA + Firefox, VoiceOver + Safari < 17)
          strip the description-list role when display:grid is applied
          to <dl>, the same way they strip table semantics off
          display:grid <table>s. The explicit list semantics survive
          the role-strip; each row carries its own aria-label binding
          path → description so the connector glyph (├─/└─) staying
          aria-hidden does not orphan the term from its definition. */}
      <dl
        role="list"
        // Two-column grid; first column is the path Code chip, second
        // is the human-readable check description. We avoid <table>
        // because the tree is a key/value mapping, not tabular data,
        // and <dl> announces correctly under VoiceOver/NVDA.
        className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5"
      >
        {fieldChecks.map((fc, i) => {
          const isLast = i === fieldChecks.length - 1
          return (
            <Row
              key={`${fc.path}-${i}`}
              path={fc.path}
              description={fc.check_description}
              isLast={isLast}
            />
          )
        })}
      </dl>
      {renderFooter && (
        <FieldChecksFooter
          verdicts={verdicts}
          evidencePaths={evidencePaths}
          t={t}
        />
      )}
    </div>
  )
}

/** D52d follow-up (a11y): the footer pipe/brace separators are
 * decorative ASCII, not data. We render verdicts + emits as <ul
 * role='list'> with one <li> per item so the SR experience reads
 * "verdicts: pass, fail" / "emits: step, subject, …" instead of
 * "verdicts colon pass space pipe space fail". Visual separators are
 * applied via CSS pseudo-elements with aria-hidden so sighted users
 * still see `pass | fail` and `{ step, subject }`. The colour token
 * is bumped from tertiary to secondary so 11/10 px labels clear
 * WCAG AA 4.5:1 on the off-white catalog/wizard surfaces. */
function FieldChecksFooter({
  verdicts,
  evidencePaths,
  t,
}: {
  verdicts: ReadonlyArray<string>
  evidencePaths: ReadonlyArray<string>
  t: T
}) {
  return (
    <div
      data-testid="verifier-field-checks-footer"
      className="mt-2 pt-2 border-t border-black/[0.05] space-y-1.5 text-[11px] text-[var(--color-text-secondary)] leading-relaxed"
    >
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span
          id="verifier-field-checks-footer-verdicts-label"
          className="uppercase tracking-wider font-semibold text-[10px] text-[var(--color-text-secondary)]"
        >
          {t("rules.verifier.fieldChecks.verdicts")}
        </span>
        <ul
          role="list"
          aria-labelledby="verifier-field-checks-footer-verdicts-label"
          className="font-mono inline-flex flex-wrap gap-x-2 [&>li+li]:before:content-['|'] [&>li+li]:before:mr-2 [&>li+li]:before:text-[var(--color-text-tertiary)]"
        >
          {verdicts.map((v) => (
            <li key={v} className="inline">
              {v}
            </li>
          ))}
        </ul>
      </div>
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span
          id="verifier-field-checks-footer-emits-label"
          className="uppercase tracking-wider font-semibold text-[10px] text-[var(--color-text-secondary)]"
        >
          {t("rules.verifier.fieldChecks.emits")}
        </span>
        <span aria-hidden className="font-mono text-[var(--color-text-tertiary)]">
          {"{"}
        </span>
        <ul
          role="list"
          aria-labelledby="verifier-field-checks-footer-emits-label"
          className="font-mono inline-flex flex-wrap gap-x-2 break-all [&>li+li]:before:content-[','] [&>li+li]:before:mr-1 [&>li+li]:before:text-[var(--color-text-tertiary)]"
        >
          {evidencePaths.map((p) => (
            <li key={p} className="inline">
              {p}
            </li>
          ))}
        </ul>
        <span aria-hidden className="font-mono text-[var(--color-text-tertiary)]">
          {"}"}
        </span>
      </div>
    </div>
  )
}

function Row({
  path,
  description,
  isLast,
}: {
  path: string
  description: string
  isLast: boolean
}) {
  // D52d follow-up (a11y): each (dt, dd) pair lives inside its own
  // role='listitem' wrapper so the grouping survives a description-list
  // role-strip under display:grid (see parent <dl role='list'> note).
  // We use display:contents on the wrapper so the dt/dd cells still
  // participate in the parent grid layout (no visual change).
  //
  // The connector glyph is purely decorative; semantic content is the
  // <dt> / <dd> pair. SR users hear "path X checks Y" via the aria-label
  // on the listitem; the role-stripped fallback still groups them.
  const connector = isLast ? "└─" : "├─"
  const ariaLabel = `${path} checks ${description}`
  return (
    <div role="listitem" aria-label={ariaLabel} className="contents">
      <dt className="flex items-baseline gap-1.5 font-mono text-[var(--color-text-primary)]">
        <span aria-hidden className="text-[var(--color-text-tertiary)] select-none">
          {connector}
        </span>
        <Code className="text-[11.5px]">{path}</Code>
      </dt>
      <dd className="text-[var(--color-text-secondary)] leading-relaxed">
        <span aria-hidden className="mr-1.5 text-[var(--color-text-tertiary)]">
          {"→"}
        </span>
        {description}
      </dd>
    </div>
  )
}
