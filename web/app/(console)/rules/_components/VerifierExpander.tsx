import { availableFields, type FieldDescriptor as PayloadFieldDescriptor } from "@/lib/payload-schemas"
import { getVerifierDescriptor, type EvidenceField, type TriggerSpec } from "@/lib/verifier-descriptors"
import { Code } from "@/components/ui"

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

export function VerifierExpander({ step, t }: { step: string; t: T }) {
  const descriptor = getVerifierDescriptor(step)

  return (
    <details className="group mt-2 rounded-lg border border-black/[0.05] bg-[var(--color-surface-1,#f9fafb)]/40">
      <summary
        className="flex cursor-pointer items-center justify-between gap-2 rounded-lg px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)] hover:bg-black/[0.02] focus-visible:outline-2 focus-visible:outline-[var(--color-accent)]"
      >
        <span>{t("rules.verifier.expander.toggle")}</span>
        <span
          aria-hidden
          className="inline-block transition-transform duration-150 group-open:rotate-180"
        >
          ▾
        </span>
      </summary>

      <div className="px-3 pb-3 pt-1">
        {descriptor === null ? (
          <p className="text-xs text-[var(--color-text-tertiary)] italic">
            {t("rules.verifier.expander.noDescriptor")}
          </p>
        ) : (
          <>
            <TriggersPanel triggers={descriptor.triggers} t={t} />
            <InputPathsPanel
              step={step}
              paths={descriptor.input_payload_paths}
              triggers={descriptor.triggers}
              t={t}
            />
            <VerdictPanel verdicts={descriptor.verdict_set} t={t} />
            <EvidencePanel evidence={descriptor.output_evidence} t={t} />
          </>
        )}
      </div>
    </details>
  )
}

function PanelHeader({ children }: { children: React.ReactNode }) {
  return (
    <h4 className="mt-3 mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)]">
      {children}
    </h4>
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
              <span className="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-gray-700">
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
  triggers,
  t,
}: {
  step: string
  paths: string[]
  triggers: TriggerSpec[]
  t: T
}) {
  // Resolve descriptions from the canonical CC hook payload schema where
  // possible (chip hover gives the operator the runtime type + example).
  // Paths the verifier reads from its OWN input dict (e.g. `text` for
  // privilege_scan, `citations[].quote` for citation_verify) are not in
  // the CC stdin envelope and fall through to a neutral chip.
  const lookup: Record<string, PayloadFieldDescriptor> = {}
  for (const tr of triggers) {
    const fields = availableFields(
      tr.event,
      tr.matcher_class === "tool" ? "*" : undefined,
    )
    for (const f of fields) {
      // First match wins so authors see the most specific trigger's
      // example first. The chip set across triggers is union-like.
      if (!lookup[f.path]) lookup[f.path] = f
    }
  }

  return (
    <div data-testid="verifier-expander-input">
      <PanelHeader>{t("rules.verifier.expander.input")}</PanelHeader>
      <div className="flex flex-wrap gap-1.5" role="list" data-step={step}>
        {paths.map((p) => {
          const field = lookup[p]
          const description = field?.description ?? t("rules.verifier.expander.inputFallback")
          const example = field?.example
          const type = field?.type ?? "json"
          const titleParts = [`${type}: ${description}`]
          if (example) titleParts.push(`example: ${example}`)
          return (
            <span
              key={p}
              role="listitem"
              title={titleParts.join("\n\n")}
              className="inline-flex items-center gap-1 rounded-md border border-black/[0.08] bg-white px-2 py-0.5 text-[11px] font-mono text-[var(--color-text-secondary)]"
            >
              <span>{p}</span>
              <span className="text-[10px] text-[var(--color-text-tertiary)]">
                :{type}
              </span>
            </span>
          )
        })}
      </div>
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
  switch (verdict) {
    case "pass":
      return "bg-emerald-50 text-emerald-700"
    case "deny":
    case "fail":
      return "bg-rose-50 text-rose-700"
    case "review":
    case "needs_review":
      return "bg-amber-50 text-amber-700"
    case "not_applicable":
      return "bg-gray-100 text-gray-700"
    default:
      return "bg-gray-100 text-gray-700"
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
