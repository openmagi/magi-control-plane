"use client"

import { useState } from "react"

type Kind = "step" | "regex" | "llm_critic" | "shacl"

interface WiredStep {
  step: string
  description: string
  recommended: boolean
}

interface Props {
  initialKind: Kind
  wiredSteps: WiredStep[]
  initialPicks: string[]
  initialPattern: string
  initialCriterion: string
  initialShapeTtl: string
  labels: {
    kindStep: string
    kindRegex: string
    kindLlm: string
    kindShacl: string
    pickAtLeastOne: string
    patternLabel: string
    patternHint: string
    patternPlaceholder: string
    patternInvalid: string
    criterionLabel: string
    criterionHint: string
    criterionPlaceholder: string
    shaclLabel: string
    shaclHint: string
    shaclPlaceholder: string
    llmPreviewBadge: string
    shaclPreviewBadge: string
  }
}

/** D35: Step 3 client component — radio toggle between condition
 * kinds + the kind-specific input panel. The wizard's submit-action
 * reads:
 *   condition_kind:  "step" | "regex" | "llm_critic" | "shacl"
 *   verifier:        N checkboxes (kind=step)
 *   cond_pattern:    string         (kind=regex)
 *   cond_criterion:  string         (kind=llm_critic)
 *   cond_shape_ttl:  string         (kind=shacl)
 * saveWizard picks the right field set based on condition_kind. */
export default function ConditionKindSection({
  initialKind, wiredSteps, initialPicks,
  initialPattern, initialCriterion, initialShapeTtl,
  labels,
}: Props) {
  const [kind, setKind] = useState<Kind>(initialKind)
  const [pattern, setPattern] = useState(initialPattern)
  const [criterion, setCriterion] = useState(initialCriterion)
  const [shapeTtl, setShapeTtl] = useState(initialShapeTtl)
  const pickedInitial = new Set(initialPicks)

  let patternValid = true
  if (kind === "regex" && pattern.length > 0) {
    try { new RegExp(pattern) }
    catch { patternValid = false }
  }

  return (
    <div className="space-y-4">
      <input type="hidden" name="condition_kind" value={kind} />

      <fieldset>
        <legend className="block text-xs font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)] mb-1.5">
          Condition kind
        </legend>
        <div className="grid grid-cols-2 gap-2">
          {[
            { value: "step",       label: labels.kindStep,  badge: null },
            { value: "regex",      label: labels.kindRegex, badge: null },
            { value: "llm_critic", label: labels.kindLlm,   badge: labels.llmPreviewBadge },
            { value: "shacl",      label: labels.kindShacl, badge: labels.shaclPreviewBadge },
          ].map((opt) => (
            <label key={opt.value} className="cursor-pointer">
              <input
                type="radio"
                name="_condition_kind_ui"
                value={opt.value}
                checked={kind === opt.value}
                onChange={() => setKind(opt.value as Kind)}
                className="peer sr-only"
              />
              <span className="block rounded-xl border border-black/[0.08] bg-white p-3 hover:border-[var(--color-accent)]/40 peer-checked:border-[var(--color-accent)] peer-checked:bg-[var(--color-accent)]/[0.05]">
                <span className="flex items-center justify-between gap-2">
                  <span className="text-sm font-semibold text-[var(--color-text-primary)]">
                    {opt.label}
                  </span>
                  {opt.badge && (
                    <span className="inline-flex items-center rounded-full px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-amber-50 text-amber-900 border border-amber-300">
                      {opt.badge}
                    </span>
                  )}
                </span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      {kind === "step" && (
        <div className="space-y-2">
          {wiredSteps.length === 0 ? (
            <p className="text-xs text-[var(--color-text-tertiary)]">
              (no wired verifiers — try regex / llm_critic / shacl kinds)
            </p>
          ) : wiredSteps.map((v) => (
            <label key={v.step} className="block cursor-pointer">
              <input
                type="checkbox"
                name="verifier"
                value={v.step}
                defaultChecked={pickedInitial.has(v.step)}
                className="peer sr-only"
              />
              <span className="block rounded-xl border border-black/[0.08] bg-white p-3 hover:border-[var(--color-accent)]/40 peer-checked:border-[var(--color-accent)] peer-checked:bg-[var(--color-accent)]/[0.05]">
                <span className="block text-sm font-semibold text-[var(--color-text-primary)]">
                  {v.step}
                  {v.recommended && (
                    <span className="ml-2 inline-flex items-center rounded-full px-1.5 py-0 text-[10px] font-semibold uppercase tracking-wider bg-emerald-50 text-emerald-800">
                      recommended
                    </span>
                  )}
                </span>
                <span className="block text-xs text-[var(--color-text-secondary)] leading-relaxed mt-0.5">
                  {v.description}
                </span>
              </span>
            </label>
          ))}
        </div>
      )}

      {kind === "regex" && (
        <div className="space-y-1">
          <label htmlFor="cond-pattern" className="block text-xs font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)] mb-1.5">
            {labels.patternLabel}
          </label>
          <input
            id="cond-pattern"
            name="cond_pattern"
            value={pattern}
            onChange={(e) => setPattern(e.target.value)}
            maxLength={2000}
            placeholder={labels.patternPlaceholder}
            spellCheck={false}
            autoComplete="off"
            className="w-full rounded-xl border border-black/[0.08] bg-white px-4 py-3 text-base leading-6 text-[var(--color-text-primary)] focus:border-[var(--color-accent)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]/20 font-mono"
          />
          <p className="text-xs text-[var(--color-text-tertiary)]">{labels.patternHint}</p>
          {!patternValid && (
            <p className="text-xs text-rose-700">{labels.patternInvalid}</p>
          )}
        </div>
      )}

      {kind === "llm_critic" && (
        <div className="space-y-1">
          <label htmlFor="cond-criterion" className="block text-xs font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)] mb-1.5">
            {labels.criterionLabel}
          </label>
          <textarea
            id="cond-criterion"
            name="cond_criterion"
            value={criterion}
            onChange={(e) => setCriterion(e.target.value)}
            rows={4}
            maxLength={4000}
            placeholder={labels.criterionPlaceholder}
            spellCheck={true}
            className="w-full rounded-xl border border-black/[0.08] bg-white px-4 py-3 text-sm leading-5 text-[var(--color-text-primary)] focus:border-[var(--color-accent)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]/20"
          />
          <p className="text-xs text-[var(--color-text-tertiary)]">{labels.criterionHint}</p>
        </div>
      )}

      {kind === "shacl" && (
        <div className="space-y-1">
          <label htmlFor="cond-shape" className="block text-xs font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)] mb-1.5">
            {labels.shaclLabel}
          </label>
          <textarea
            id="cond-shape"
            name="cond_shape_ttl"
            value={shapeTtl}
            onChange={(e) => setShapeTtl(e.target.value)}
            rows={8}
            maxLength={16000}
            placeholder={labels.shaclPlaceholder}
            spellCheck={false}
            className="w-full rounded-xl border border-black/[0.08] bg-white px-4 py-3 text-sm leading-5 text-[var(--color-text-primary)] focus:border-[var(--color-accent)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]/20 font-mono"
          />
          <p className="text-xs text-[var(--color-text-tertiary)]">{labels.shaclHint}</p>
        </div>
      )}
    </div>
  )
}
