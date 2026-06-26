"use client"

/**
 * Q97b — LLM keys form on /settings.
 *
 * Sub-path imports only (NEVER `@/components/ui` barrel — the barrel
 * drags a server-only chain into the client bundle). Takes `locale`
 * explicitly and resolves copy with `translate(locale, key, vars)`
 * because client components must not call the server-only translator
 * factory.
 */

import { useCallback, useState, useTransition } from "react"
import { useRouter } from "next/navigation"
import type { Locale, TKey } from "@/lib/i18n/dict"
import { translate } from "@/lib/i18n/dict"
import type { LlmKeysStatus, LlmKeysTestSingle } from "@/lib/cloud"
import {
  saveLlmKeysAction, testConnectionAction,
  type SaveResult, type TestResult,
} from "../actions"

type ProviderId = "anthropic" | "openai"

type PerProvider = {
  id: ProviderId
  labelKey: TKey
  fieldName: "anthropic_api_key" | "openai_api_key"
  clearFieldName: "anthropic_clear" | "openai_clear"
}

const PROVIDERS: PerProvider[] = [
  {
    id: "anthropic",
    labelKey: "settings.llm.row.anthropic",
    fieldName: "anthropic_api_key",
    clearFieldName: "anthropic_clear",
  },
  {
    id: "openai",
    labelKey: "settings.llm.row.openai",
    fieldName: "openai_api_key",
    clearFieldName: "openai_clear",
  },
]

type PillTone = "emerald" | "amber" | "red"

function pillClass(tone: PillTone): string {
  if (tone === "emerald") {
    return "bg-[var(--color-pass-bg)] text-[var(--color-pass-fg)]"
  }
  if (tone === "red") {
    return "bg-[var(--color-deny-bg)] text-[var(--color-deny-fg)]"
  }
  return "bg-[var(--color-review-bg)] text-[var(--color-review-fg)]"
}

function classifyStatus(
  status: LlmKeysStatus,
  lastTest: TestResult | null,
  provider: ProviderId,
): { toneKey: TKey; tone: PillTone } {
  const set = provider === "anthropic" ? status.anthropic.set : status.openai.set
  const probe = lastTest
    ? (provider === "anthropic" ? lastTest.anthropic : lastTest.openai)
    : null
  if (!set) {
    return { toneKey: "settings.llm.status.notConfigured", tone: "amber" }
  }
  if (probe && probe.ok) {
    return { toneKey: "settings.llm.status.active", tone: "emerald" }
  }
  if (probe && !probe.ok) {
    return { toneKey: "settings.llm.status.failed", tone: "red" }
  }
  return { toneKey: "settings.llm.status.configured", tone: "amber" }
}

export interface LlmKeysFormProps {
  locale: Locale
  initialStatus: LlmKeysStatus
}

export function LlmKeysForm({ locale, initialStatus }: LlmKeysFormProps) {
  const router = useRouter()
  const [status, setStatus] = useState<LlmKeysStatus>(initialStatus)
  const [lastTest, setLastTest] = useState<TestResult | null>(null)
  const [toast, setToast] = useState<{
    kind: "ok" | "error"; msg: string
  } | null>(null)
  const [saving, startSave] = useTransition()
  const [testing, startTest] = useTransition()
  const t = (k: TKey, vars?: Record<string, string | number>) =>
    translate(locale, k, vars)

  const onSave = useCallback((formData: FormData) => {
    startSave(async () => {
      const r: SaveResult = await saveLlmKeysAction(formData)
      if (r.ok) {
        setStatus(r.status)
        setToast({ kind: "ok", msg: t("settings.llm.toast.saved") })
        // Reset any prior probe because the singletons were just
        // rebuilt; an old "failed" pill would be misleading.
        setLastTest(null)
        // Refresh server-rendered shell so the initial-status SSR
        // matches what the client just stored.
        router.refresh()
      } else {
        setToast({
          kind: "error",
          msg: t("settings.llm.toast.error", { detail: r.error }),
        })
      }
    })
  }, [router, t])

  const onTest = useCallback(() => {
    startTest(async () => {
      const r: TestResult = await testConnectionAction()
      setLastTest(r)
      if (r.error) {
        setToast({
          kind: "error",
          msg: t("settings.llm.toast.error", { detail: r.error }),
        })
      }
    })
  }, [t])

  return (
    <section aria-labelledby="settings-llm-section">
      <h2
        id="settings-llm-section"
        className="text-md font-semibold mb-1"
      >
        {t("settings.llm.section.title")}
      </h2>
      <p className="text-xs text-[var(--color-text-tertiary)] mb-4">
        {t("settings.llm.section.hint")}
      </p>

      <form
        action={onSave}
        data-testid="llm-keys-form"
        className="space-y-4"
      >
        {PROVIDERS.map((p) => {
          const meta = status[p.id]
          const cls = classifyStatus(status, lastTest, p.id)
          const probe = lastTest
            ? (p.id === "anthropic" ? lastTest.anthropic : lastTest.openai)
            : null
          const placeholder = meta.set
            ? t("settings.llm.placeholderSet", { last4: meta.last4 || "????" })
            : t("settings.llm.placeholderUnset")
          return (
            <div
              key={p.id}
              className="grid grid-cols-1 md:grid-cols-[1fr_auto] gap-3 items-start"
              data-testid={`llm-row-${p.id}`}
            >
              <div className="space-y-1">
                <label
                  htmlFor={`llm-${p.id}`}
                  className="block text-xs font-medium text-[var(--color-text-secondary)]"
                >
                  {t(p.labelKey)}
                </label>
                <input
                  id={`llm-${p.id}`}
                  name={p.fieldName}
                  type="password"
                  autoComplete="off"
                  spellCheck={false}
                  placeholder={placeholder}
                  className="block w-full bg-[var(--color-surface-input)] border border-[var(--color-border-strong)] rounded-md text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-tertiary)] transition-colors duration-150 focus:border-[var(--color-border-focus)] focus:outline-none focus:ring-2 focus:ring-[var(--color-border-focus)]/40 h-9 px-3"
                />
                <label className="inline-flex items-center gap-2 text-xs text-[var(--color-text-tertiary)] mt-1">
                  <input
                    type="checkbox"
                    name={p.clearFieldName}
                    value="1"
                    className="rounded border-[var(--color-border-strong)]"
                  />
                  {t("settings.llm.clearLabel")}
                </label>
              </div>
              <div className="flex flex-col items-start md:items-end gap-2">
                <span
                  data-testid={`llm-status-${p.id}`}
                  className={
                    "inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs leading-4 font-medium " +
                    pillClass(cls.tone)
                  }
                >
                  {t(cls.toneKey)}
                </span>
                {probe && !probe.ok && probe.error && (
                  <p
                    className="text-xs text-[var(--color-deny-fg)] max-w-xs text-right"
                    role="status"
                  >
                    {t("settings.llm.testResult.fail", {
                      provider: p.id, detail: probe.error,
                    })}
                  </p>
                )}
                {probe && probe.ok && (
                  <p
                    className="text-xs text-[var(--color-pass-fg)]"
                    role="status"
                  >
                    {t("settings.llm.testResult.ok", { provider: p.id })}
                  </p>
                )}
              </div>
            </div>
          )
        })}

        <div className="flex flex-wrap gap-2 pt-2">
          <button
            type="submit"
            disabled={saving}
            className="inline-flex items-center gap-2 rounded-md bg-[var(--color-accent)] px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            {saving ? t("settings.llm.save.pending") : t("settings.llm.save")}
          </button>
          <button
            type="button"
            onClick={onTest}
            disabled={testing}
            className="inline-flex items-center gap-2 rounded-md border border-[var(--color-border-strong)] bg-[var(--color-surface-input)] px-3 py-1.5 text-sm font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-surface-overlay)] disabled:opacity-50"
          >
            {testing ? t("settings.llm.test.pending") : t("settings.llm.test")}
          </button>
        </div>

        {toast && (
          <p
            role="alert"
            data-testid="llm-toast"
            className={
              "text-xs " + (toast.kind === "ok"
                ? "text-[var(--color-pass-fg)]"
                : "text-[var(--color-deny-fg)]")
            }
          >
            {toast.msg}
          </p>
        )}
      </form>
    </section>
  )
}
