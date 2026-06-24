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
}: {
  step: string
  t: T
  /** When true, surface the verdicts + emits rows under the tree (used
   * by the policy wizard picker; the catalog expander already has
   * dedicated Verdicts + Output Evidence panels). */
  showFooter?: boolean
  className?: string
}) {
  const descriptor = getVerifierDescriptor(step)
  const fieldChecks: FieldCheck[] = descriptor?.field_checks ?? []

  if (descriptor === null || fieldChecks.length === 0) {
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
  // panels. Always rendered when `showFooter` is on; the catalog
  // expander leaves it off because it already has rich panels.
  const verdicts = descriptor.verdict_set
  const evidencePaths = descriptor.output_evidence.map((e) => e.path)

  return (
    <div
      data-testid="verifier-field-checks-tree"
      className={(className ?? "") + " text-xs"}
    >
      <dl
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
      {showFooter && (
        <div
          data-testid="verifier-field-checks-footer"
          className="mt-2 pt-2 border-t border-black/[0.05] space-y-1 text-[11px] text-[var(--color-text-tertiary)] leading-relaxed"
        >
          <div>
            <span className="uppercase tracking-wider font-semibold mr-2 text-[10px]">
              {t("rules.verifier.fieldChecks.verdicts")}
            </span>
            <span className="font-mono">{verdicts.join(" | ")}</span>
          </div>
          <div>
            <span className="uppercase tracking-wider font-semibold mr-2 text-[10px]">
              {t("rules.verifier.fieldChecks.emits")}
            </span>
            <span className="font-mono break-all">
              {"{ " + evidencePaths.join(", ") + " }"}
            </span>
          </div>
        </div>
      )}
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
  // The connector glyph is purely decorative; semantic content is the
  // <dt> / <dd> pair. SR users hear "term: tool_input.url, definition:
  // hostname is in allowlist".
  const connector = isLast ? "└─" : "├─"
  return (
    <>
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
    </>
  )
}
