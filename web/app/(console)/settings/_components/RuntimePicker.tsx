"use client"

/**
 * P4 (Codex runtime adapter) — runtime picker on /settings.
 *
 * Sub-path imports only (NEVER the `@/components/ui` barrel — it drags a
 * server-only chain into the client bundle). Takes `locale` explicitly
 * and resolves copy with `translate(locale, key, vars)` since a client
 * component must not call the server-only translator factory.
 *
 * Two-step confirm (design doc Section 7.1):
 *   1. Click the "Codex CLI" radio → renders the coverage PREVIEW and
 *      keeps the current runtime. Nothing is persisted.
 *   2. Click "Confirm switch" → the server action writes
 *      tenants.runtime_id.
 *
 * With MAGI_CP_CODEX_RUNTIME_ENABLED off (initial.codex_enabled === false)
 * the Codex radio is disabled and the confirm button never renders, so an
 * accidental click cannot flip a live tenant onto a build-disabled runtime.
 */

import { useCallback, useState, useTransition } from "react"
import { useRouter } from "next/navigation"
import type { Locale, TKey } from "@/lib/i18n/dict"
import { translate } from "@/lib/i18n/dict"
import type { RuntimeCoverage, TenantRuntimeState } from "@/lib/cloud"
import { setRuntimeAction } from "../actions"

const KNOWN_RUNTIMES = ["claude-code", "codex"] as const
type RuntimeId = (typeof KNOWN_RUNTIMES)[number]

const RUNTIME_NAME_KEY: Record<RuntimeId, TKey> = {
  "claude-code": "runtime.name.claude-code",
  codex: "runtime.name.codex",
}

export function RuntimePicker({
  locale, initial,
}: {
  locale: Locale
  initial: TenantRuntimeState
}) {
  const t = (k: TKey, v?: Record<string, string | number>) =>
    translate(locale, k, v)
  const router = useRouter()
  const [pending, startTransition] = useTransition()
  const current = initial.runtime_id
  // The runtime the operator has *selected to preview* — starts on the
  // persisted current runtime, so step 1 (radio click) is a pure preview.
  const [selected, setSelected] = useState<string>(current)
  const [error, setError] = useState<string | null>(null)

  const rollupFor = (id: string): RuntimeCoverage | undefined =>
    initial.runtimes.find((r) => r.id === id)

  const confirmSwitch = useCallback(() => {
    setError(null)
    startTransition(async () => {
      const res = await setRuntimeAction(selected)
      if (res.ok) {
        router.refresh()
      } else {
        setError(res.error)
      }
    })
  }, [selected, router])

  // A switch is offered only when the operator previewed a DIFFERENT
  // runtime AND that runtime is selectable (codex requires the flag).
  const codexSelectable = initial.codex_enabled === true
  const canConfirm =
    selected !== current && (selected !== "codex" || codexSelectable)

  const currentRollup = rollupFor(current)

  return (
    <section className="rounded-2xl border border-black/[0.06] bg-[var(--color-surface-1,#f9fafb)]/40 p-4">
      <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">
        {t("settings.runtime.label")}
      </h2>
      <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">
        {t("settings.runtime.description")}
      </p>

      {/* Current runtime + its enforced-policy coverage. */}
      <div className="mt-3 text-sm text-[var(--color-text-secondary)]">
        <span className="font-medium">{t("settings.runtime.current")}: </span>
        {t(RUNTIME_NAME_KEY[current as RuntimeId] ?? "runtime.name.claude-code")}
        <div className="text-xs text-[var(--color-text-tertiary)]">
          {t("settings.runtime.coverage_preview", {
            enforced: currentRollup?.enforced ?? 0,
          })}
        </div>
      </div>

      {/* Alternatives — a radio per known runtime. */}
      <fieldset className="mt-4">
        <legend className="text-xs font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)]">
          {t("settings.runtime.alternatives")}
        </legend>
        <div className="mt-2 flex flex-col gap-2">
          {KNOWN_RUNTIMES.map((id) => {
            const disabled = id === "codex" && !codexSelectable
            const rollup = rollupFor(id)
            const isPreview = selected === id && id !== current
            return (
              <label
                key={id}
                data-testid={`runtime-radio-${id}`}
                className={`flex flex-col gap-1 rounded-lg border p-2 text-sm ${
                  disabled
                    ? "cursor-not-allowed border-black/5 opacity-60"
                    : "cursor-pointer border-black/10 hover:border-black/20"
                }`}
              >
                <span className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="runtime"
                    value={id}
                    checked={selected === id}
                    disabled={disabled}
                    onChange={() => !disabled && setSelected(id)}
                  />
                  <span className="font-medium">
                    {t(RUNTIME_NAME_KEY[id])}
                  </span>
                  {id === current && (
                    <span className="text-[11px] text-[var(--color-text-tertiary)]">
                      ({t("settings.runtime.current")})
                    </span>
                  )}
                </span>
                {disabled && (
                  <span className="pl-6 text-[11px] text-[var(--color-text-tertiary)]">
                    {t("settings.runtime.requiresFlag")}
                  </span>
                )}
                {/* Step 1 preview: coverage for the selected alternative. */}
                {isPreview && rollup && (
                  <span
                    data-testid="runtime-coverage-preview"
                    className="pl-6 text-[11px] text-[var(--color-text-tertiary)]"
                  >
                    {t("settings.runtime.coverage_preview_full", {
                      enforced: rollup.enforced,
                      downgraded: rollup.downgraded,
                      unsupported: rollup.unsupported,
                    })}
                  </span>
                )}
              </label>
            )
          })}
        </div>
      </fieldset>

      {/* Step 2: confirm. Only rendered once the operator previewed a
          different, selectable runtime — an accidental radio click never
          persists on its own. */}
      {canConfirm && (
        <div className="mt-3 flex items-center gap-3">
          <button
            type="button"
            data-testid="runtime-confirm"
            disabled={pending}
            onClick={confirmSwitch}
            className="rounded-md bg-[var(--color-accent)] px-3 py-1.5 text-xs font-semibold text-white hover:opacity-90 disabled:opacity-60"
          >
            {t("settings.runtime.switch_confirm")}
          </button>
          <button
            type="button"
            disabled={pending}
            onClick={() => setSelected(current)}
            className="text-xs text-[var(--color-text-tertiary)] hover:underline"
          >
            {t("settings.runtime.cancel")}
          </button>
        </div>
      )}

      {error && (
        <p className="mt-2 text-xs text-red-700">
          {t("settings.runtime.error", { detail: error })}
        </p>
      )}
    </section>
  )
}
