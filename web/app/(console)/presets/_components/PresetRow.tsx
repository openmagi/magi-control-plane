import type { PresetEntry } from "@/lib/cloud"
import { ChevronDownIcon, LockClosedIcon } from "@heroicons/react/24/outline"
import { PresetToggle } from "./PresetToggle"
import { PRESET_USAGE_HINTS } from "./PresetUsageHint"
import { togglePresetAction } from "../actions"

export interface PresetRowProps {
  p: PresetEntry
  enabled: boolean
  labelOn: string
  labelOff: string
  stepLabel: string
  whenLabel: string
  matchersLabel: string
  verdictLabel: string
  howLabel: string
  schemaLabel: string
  notWiredLabel: string
}

const STATUS_PILL_CLS: Record<PresetEntry["enforcement"] | "off", string> = {
  enforcing:    "bg-emerald-500/10 text-emerald-700",
  "always-on":  "bg-emerald-500/10 text-emerald-700",
  preview:      "bg-amber-500/10 text-amber-700",
  capability:   "bg-blue-500/10 text-blue-700",
  off:          "bg-gray-200 text-gray-600",
}

function StatusPill({
  enabled, enforcement,
}: { enabled: boolean; enforcement: PresetEntry["enforcement"] }) {
  // Always-on items: green Locked pill, no toggle (matches magi-agent).
  if (enforcement === "always-on") {
    return (
      <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${STATUS_PILL_CLS["always-on"]}`}>
        <LockClosedIcon aria-hidden="true" className="h-3 w-3" />
        always-on
      </span>
    )
  }
  const label = enabled ? "on" : "off"
  const cls = enabled ? STATUS_PILL_CLS[enforcement] : STATUS_PILL_CLS.off
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${cls}`}>
      {label}
    </span>
  )
}

/**
 * Row in the preset list. Mirrors the magi-agent Customize Row layout:
 * plain title, description below, small uppercase SCOPE/MODE meta
 * line, trailing status pill + toggle (or Locked pill for always-on).
 *
 * The row itself is a <details>: open pane reveals the operational
 * spec (when / matchers / verdict / how / input schema) — magi-cp
 * specific affordance that magi-agent's Row doesn't have.
 */
export function PresetRow({
  p, enabled, labelOn, labelOff,
  stepLabel, whenLabel, matchersLabel, verdictLabel, howLabel, schemaLabel,
  notWiredLabel,
}: PresetRowProps) {
  const hint = PRESET_USAGE_HINTS[p.id]
  const hasSpec = Boolean(hint || p.input_schema || p.step)
  const isLocked = p.enforcement === "always-on"

  return (
    <details className="group border-b border-black/[0.06] last:border-b-0 transition-colors duration-150 hover:bg-gray-50/40">
      <summary className="flex items-start justify-between gap-4 px-4 py-3.5 cursor-pointer list-none select-none">
        <div className="flex items-start gap-2.5 flex-1 min-w-0">
          <ChevronDownIcon
            aria-hidden="true"
            className="w-4 h-4 mt-0.5 text-[var(--color-text-tertiary)] shrink-0 transition-transform duration-200 group-open:rotate-0 -rotate-90"
          />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="text-sm font-semibold text-[var(--color-text-primary)] truncate" translate="no">
                {p.id}
              </span>
            </div>
            <p className="mt-1 text-xs leading-relaxed text-[var(--color-text-secondary)]">
              {p.description}
            </p>
            <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] uppercase tracking-[0.1em] text-[var(--color-text-tertiary)]">
              <span>scope: <span className="text-[var(--color-text-secondary)]">{p.category.toLowerCase()}</span></span>
              <span>mode: <span className="text-[var(--color-text-secondary)]">{p.enforcement}</span></span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2.5 pt-0.5">
          <StatusPill enabled={enabled} enforcement={p.enforcement} />
          {!isLocked && (
            <PresetToggle
              presetId={p.id}
              enabled={enabled}
              action={togglePresetAction}
              labelOn={labelOn}
              labelOff={labelOff}
            />
          )}
        </div>
      </summary>

      {hasSpec && (
        <div className="border-t border-black/[0.04] bg-gray-50/60 px-4 py-3 text-sm ml-6">
          {p.step && (
            <div className="flex flex-wrap items-baseline gap-2 mb-2">
              <span className="text-[11px] uppercase tracking-[0.1em] text-[var(--color-text-tertiary)] font-semibold">
                {stepLabel}
              </span>
              <code className="font-mono text-[12.5px] text-[var(--color-text-primary)]" translate="no">
                {p.step}
              </code>
            </div>
          )}
          {hint ? (
            <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-2 text-[13px]">
              <dt className="text-[11px] uppercase tracking-[0.1em] text-[var(--color-text-tertiary)] font-semibold pt-0.5">{whenLabel}</dt>
              <dd className="flex flex-wrap gap-1.5">
                {hint.when.map(e => (
                  <code key={e} className="font-mono text-[12px] px-1.5 py-0.5 rounded bg-[var(--color-accent)]/8 text-[var(--color-accent-light)] border border-[var(--color-accent)]/15" translate="no">
                    {e}
                  </code>
                ))}
              </dd>
              <dt className="text-[11px] uppercase tracking-[0.1em] text-[var(--color-text-tertiary)] font-semibold pt-0.5">{matchersLabel}</dt>
              <dd className="flex flex-wrap gap-1.5">
                {hint.matchers.map(m => (
                  <code key={m} className="font-mono text-[12px] px-1.5 py-0.5 rounded bg-white text-[var(--color-text-secondary)] border border-black/[0.06]" translate="no">
                    {m}
                  </code>
                ))}
              </dd>
              <dt className="text-[11px] uppercase tracking-[0.1em] text-[var(--color-text-tertiary)] font-semibold pt-0.5">{verdictLabel}</dt>
              <dd className="text-[var(--color-text-secondary)] leading-relaxed text-xs">{hint.verdict}</dd>
              <dt className="text-[11px] uppercase tracking-[0.1em] text-[var(--color-text-tertiary)] font-semibold pt-0.5">{howLabel}</dt>
              <dd className="text-[var(--color-text-secondary)] leading-relaxed text-xs">{hint.howItWorks}</dd>
            </dl>
          ) : (
            <p className="text-xs text-[var(--color-text-tertiary)] italic">
              {notWiredLabel}
            </p>
          )}
          {p.input_schema && (
            <details className="mt-3 group/schema">
              <summary className="cursor-pointer text-[11px] uppercase tracking-[0.1em] text-[var(--color-text-tertiary)] font-semibold inline-flex items-center gap-1">
                <ChevronDownIcon aria-hidden="true" className="w-3 h-3 transition-transform group-open/schema:rotate-0 -rotate-90" />
                {schemaLabel}
              </summary>
              <pre
                className="mt-2 max-h-64 overflow-auto rounded-lg border border-black/[0.06] bg-white px-3 py-2 font-mono text-[12px] text-[var(--color-text-primary)] leading-5"
                translate="no"
              >
                {JSON.stringify(p.input_schema, null, 2)}
              </pre>
            </details>
          )}
        </div>
      )}
    </details>
  )
}
