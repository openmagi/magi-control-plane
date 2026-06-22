import type { PresetEntry } from "@/lib/cloud"
import { ChevronDownIcon, LockClosedIcon } from "@heroicons/react/24/outline"
import { VerifierToggle } from "./VerifierToggle"
import { PRESET_USAGE_HINTS } from "../../presets/_components/PresetUsageHint"
import {
  toggleBuiltinVerifierAction,
  toggleCustomVerifierAction,
  deleteCustomVerifierAction,
} from "../actions"

export interface VerifierRowProps {
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
  customBadgeLabel: string
  editLabel: string
  deleteLabel: string
  confirmDeleteLabel: string
}

function StatusPill({
  enabled, enforcement,
}: { enabled: boolean; enforcement: PresetEntry["enforcement"] }) {
  if (enforcement === "always-on") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium bg-emerald-500/10 text-emerald-700">
        <LockClosedIcon aria-hidden="true" className="h-3 w-3" />
        always-on
      </span>
    )
  }
  return enabled ? (
    <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium bg-emerald-500/10 text-emerald-700">
      on
    </span>
  ) : (
    <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium bg-gray-100 text-gray-600">
      off
    </span>
  )
}

/**
 * Adapted from the /presets PresetRow. Adds the "custom" badge and
 * edit / delete affordances when `p.is_custom` is true. Toggle wiring
 * routes to the backend for custom rows and to the cookie-disabled set
 * for built-ins.
 */
export function VerifierRow({
  p, enabled, labelOn, labelOff,
  stepLabel, whenLabel, matchersLabel, verdictLabel, howLabel, schemaLabel,
  notWiredLabel, customBadgeLabel, editLabel, deleteLabel, confirmDeleteLabel,
}: VerifierRowProps) {
  const hint = PRESET_USAGE_HINTS[p.id]
  const hasSpec = Boolean(hint || p.input_schema || p.step || p.is_custom)
  const isLocked = p.enforcement === "always-on"
  const isCustom = Boolean(p.is_custom)

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
              {isCustom && (
                <span className="inline-flex items-center rounded-full px-1.5 py-0 text-[10px] font-semibold uppercase tracking-wider bg-[var(--color-accent)]/10 text-[var(--color-accent-light)]">
                  {customBadgeLabel}
                </span>
              )}
            </div>
            <p className="mt-1 text-xs leading-relaxed text-[var(--color-text-secondary)]">
              {p.description}
            </p>
            <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] uppercase tracking-[0.1em] text-[var(--color-text-tertiary)]">
              <span>scope: <span className="text-[var(--color-text-secondary)]">{p.category.toLowerCase()}</span></span>
              <span>mode: <span className="text-[var(--color-text-secondary)]">{p.enforcement}</span></span>
              {isCustom && p.kind && (
                <span>kind: <span className="text-[var(--color-text-secondary)]">{p.kind}</span></span>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2.5 pt-0.5">
          <StatusPill enabled={enabled} enforcement={p.enforcement} />
          {!isLocked && (
            <VerifierToggle
              presetId={p.id}
              step={p.step}
              isCustom={isCustom}
              enabled={enabled}
              builtinAction={toggleBuiltinVerifierAction}
              customAction={toggleCustomVerifierAction}
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
                {hint.when.map((e) => (
                  <code key={e} className="font-mono text-[12px] px-1.5 py-0.5 rounded bg-[var(--color-accent)]/8 text-[var(--color-accent-light)] border border-[var(--color-accent)]/15" translate="no">
                    {e}
                  </code>
                ))}
              </dd>
              <dt className="text-[11px] uppercase tracking-[0.1em] text-[var(--color-text-tertiary)] font-semibold pt-0.5">{matchersLabel}</dt>
              <dd className="flex flex-wrap gap-1.5">
                {hint.matchers.map((m) => (
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
            !isCustom && (
              <p className="text-xs text-[var(--color-text-tertiary)] italic">
                {notWiredLabel}
              </p>
            )
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
          {isCustom && p.step && (
            <div className="mt-3 flex items-center gap-2">
              <a
                href={`/rules/new?edit=${encodeURIComponent(p.step)}`}
                className="text-xs font-medium text-[var(--color-accent-light)] hover:underline px-2 py-1 rounded"
              >
                {editLabel}
              </a>
              <form action={deleteCustomVerifierAction}>
                <input type="hidden" name="step" value={p.step} />
                <button
                  type="submit"
                  formNoValidate
                  className="text-xs font-medium text-rose-700 hover:underline px-2 py-1 rounded cursor-pointer"
                  aria-label={`${deleteLabel} — ${p.step}`}
                  data-confirm={confirmDeleteLabel}
                >
                  {deleteLabel}
                </button>
              </form>
            </div>
          )}
        </div>
      )}
    </details>
  )
}
