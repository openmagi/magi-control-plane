"use client"

import { useCallback, useId, useMemo, useState } from "react"
import { Button } from "@/components/ui/Button"
import { translate, type Locale, type TKey } from "@/lib/i18n/dict"
import {
  SYNTHETIC_PAYLOAD_TEMPLATES,
  templateById,
  type SyntheticPayloadTemplate,
} from "@/lib/synthetic-payloads"

/**
 * D77 — synthetic CC hook payload simulator panel.
 *
 * Mounts on the policy detail page (and the pack detail page via
 * `kind="pack"`). The operator picks a starter template from a
 * dropdown, optionally edits the JSON, and clicks "Run test". The
 * panel POSTs to `/api/policies/test` (same-origin proxy) which
 * forwards to the cloud's `/policies/{id}/test` or
 * `/policy-packs/{id}/test` endpoint.
 *
 * The result panel renders verdict + action pills, a per-requires
 * reason list, and (collapsed by default) the raw hookSpecificOutput
 * JSON. For run_command policies we surface the `would_run` block
 * naming the command the runtime WOULD execute (we never execute
 * locally). For input_rewrite policies we surface the new tool_input
 * the rewriter would emit.
 *
 * No actual CC invocation. The cloud simulator is a pure function
 * over the IR.
 */

type T = (k: TKey, vars?: Record<string, string | number>) => string

export type TestKind = "policy" | "pack"

export type PolicyTestPanelProps = {
  locale: Locale
  /** The id of the policy (kind="policy") or pack (kind="pack") to
   *  test. The component uses this to address the proxy. */
  id: string
  kind: TestKind
}

type PolicyTestResponse = {
  verdict: string
  action: string
  evidence_match_reasons: string[]
  hook_specific_output: Record<string, unknown>
  requires_results: Array<{ kind: string; status: string; reason: string }>
  new_tool_input?: Record<string, unknown>
  would_run?: Record<string, unknown>
  inject_context?: string
  skipped_reason?: string
  policy_id?: string
  policy_type?: string
}

type PolicyPackTestResponse = {
  pack_id: string
  member_count: number
  members: PolicyTestResponse[]
}

export function PolicyTestPanel({
  locale, id, kind,
}: PolicyTestPanelProps) {
  const t: T = useCallback(
    (key, vars) => translate(locale, key, vars), [locale],
  )
  const titleKey: TKey = kind === "pack"
    ? "packs.test.title" : "policies.test.title"
  const subtitleKey: TKey = kind === "pack"
    ? "packs.test.subtitle" : "policies.test.subtitle"

  const [templateId, setTemplateId] = useState<string>(
    SYNTHETIC_PAYLOAD_TEMPLATES[0].id,
  )
  const [payloadText, setPayloadText] = useState<string>(
    () => JSON.stringify(SYNTHETIC_PAYLOAD_TEMPLATES[0].payload, null, 2),
  )
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [policyResult, setPolicyResult] = useState<PolicyTestResponse | null>(null)
  const [packResult, setPackResult] = useState<PolicyPackTestResponse | null>(null)
  const editorId = useId()
  const templateSelectId = useId()
  const resultId = useId()

  const selectedTemplate: SyntheticPayloadTemplate = useMemo(
    () => templateById(templateId) ?? SYNTHETIC_PAYLOAD_TEMPLATES[0],
    [templateId],
  )

  const payloadParseError = useMemo<string | null>(() => {
    try {
      const parsed = JSON.parse(payloadText)
      if (parsed == null || typeof parsed !== "object" || Array.isArray(parsed)) {
        return t("policies.test.payloadInvalid")
      }
      return null
    } catch {
      return t("policies.test.payloadInvalid")
    }
  }, [payloadText, t])

  const onTemplateChange = useCallback((next: string) => {
    setTemplateId(next)
    const tpl = templateById(next)
    if (tpl) {
      setPayloadText(JSON.stringify(tpl.payload, null, 2))
      setErr(null)
    }
  }, [])

  const onRun = useCallback(async () => {
    if (payloadParseError) return
    setLoading(true)
    setErr(null)
    setPolicyResult(null)
    setPackResult(null)
    try {
      const parsed = JSON.parse(payloadText) as Record<string, unknown>
      // P2 fix: read hook_event_name out of the parsed JSON and prefer
      // it over the selected template's default event. Otherwise an
      // operator who picks the PreToolUse template, edits the JSON to
      // hook_event_name='PostToolUse', and clicks Run would see the
      // simulator evaluate as PreToolUse (silently mismatching the
      // policy frame).
      const payloadEvent = typeof parsed.hook_event_name === "string"
        ? parsed.hook_event_name
        : ""
      const effectiveEvent = payloadEvent || selectedTemplate.event
      const r = await fetch("/api/policies/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({
          kind, id, payload: parsed,
          event: effectiveEvent,
        }),
      })
      if (!r.ok) {
        let reason = "upstream"
        try {
          const j = (await r.json()) as { error?: string }
          if (j && typeof j.error === "string") reason = j.error
        } catch { /* keep default */ }
        setErr(t("policies.test.failed", { reason }))
        return
      }
      const data = await r.json() as unknown
      if (kind === "pack") {
        setPackResult(data as PolicyPackTestResponse)
      } else {
        setPolicyResult(data as PolicyTestResponse)
      }
    } catch (e) {
      const reason = e instanceof Error ? e.message : "network"
      setErr(t("policies.test.failed", { reason }))
    } finally {
      setLoading(false)
    }
  }, [
    payloadText, payloadParseError, kind, id,
    selectedTemplate.event, t,
    // selectedTemplate is captured via .event above; payloadText
    // re-renders whenever the editor mutates and onRun re-reads the
    // hook_event_name from the parsed JSON each time.
  ])

  return (
    <section
      data-testid="policy-test-panel"
      className="mt-6 rounded-lg border border-[var(--color-border-subtle)] bg-[var(--color-surface-raised)] p-4"
    >
      <h2 className="text-md font-semibold m-0">
        {t(titleKey)}
      </h2>
      <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">
        {t(subtitleKey)}
      </p>

      <div className="mt-3">
        <label
          htmlFor={templateSelectId}
          className="block text-xs font-medium text-[var(--color-text-secondary)]"
        >
          {t("policies.test.template")}
        </label>
        <select
          id={templateSelectId}
          data-testid="policy-test-template"
          value={templateId}
          onChange={(e) => onTemplateChange(e.target.value)}
          className="mt-1 w-full max-w-md rounded-md border border-[var(--color-border-subtle)] bg-white px-2 py-1.5 text-sm"
        >
          {SYNTHETIC_PAYLOAD_TEMPLATES.map((tpl) => (
            <option key={tpl.id} value={tpl.id}>
              {tpl.displayLabel[locale] ?? tpl.displayLabel.en}
            </option>
          ))}
        </select>
        <p className="mt-1 text-[11px] italic text-[var(--color-text-tertiary)]">
          {selectedTemplate.hint[locale] ?? selectedTemplate.hint.en}
        </p>
      </div>

      <div className="mt-3">
        <label
          htmlFor={editorId}
          className="block text-xs font-medium text-[var(--color-text-secondary)]"
        >
          {t("policies.test.payload")}
        </label>
        <textarea
          id={editorId}
          data-testid="policy-test-payload-editor"
          value={payloadText}
          onChange={(e) => setPayloadText(e.target.value)}
          rows={10}
          spellCheck={false}
          className="mt-1 w-full rounded-md border border-[var(--color-border-subtle)] bg-white px-2 py-1.5 font-mono text-[12px]"
        />
        {payloadParseError && (
          <p
            data-testid="policy-test-payload-error"
            className="mt-1 text-xs text-[var(--color-deny-fg)]"
            role="alert"
          >
            {payloadParseError}
          </p>
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="primary"
          size="sm"
          onClick={onRun}
          disabled={loading || payloadParseError != null}
          data-testid="policy-test-run"
          aria-controls={resultId}
        >
          {loading ? t("policies.test.loading") : t("policies.test.run")}
        </Button>
        <span className="text-[11px] italic text-[var(--color-text-tertiary)]">
          {t("policies.test.dryRunSecondary")}
        </span>
      </div>

      <div
        id={resultId}
        aria-live="polite"
        aria-busy={loading}
        className="mt-4"
      >
        {!loading && err && (
          <p
            data-testid="policy-test-error"
            className="text-xs text-[var(--color-deny-fg)]"
            role="alert"
          >
            {err}
          </p>
        )}
        {!loading && !err && policyResult && kind === "policy" && (
          <PolicyResultBlock t={t} locale={locale} result={policyResult} />
        )}
        {!loading && !err && packResult && kind === "pack" && (
          <PackResultBlock t={t} locale={locale} result={packResult} />
        )}
      </div>
    </section>
  )
}

function verdictTone(v: string): string {
  switch (v) {
    case "deny":
    case "fail":
      return "bg-[var(--color-deny-bg)] text-[var(--color-deny-fg)]"
    case "review":
      return "bg-[var(--color-review-bg)] text-[var(--color-review-fg)]"
    case "pass":
      return "bg-[var(--color-pass-bg)] text-[var(--color-pass-fg)]"
    case "skipped":
    case "indeterminate":
    default:
      return "bg-[var(--color-surface-overlay)] text-[var(--color-text-tertiary)]"
  }
}

function actionTone(a: string): string {
  switch (a) {
    case "block":
      return "bg-[var(--color-deny-bg)] text-[var(--color-deny-fg)]"
    case "ask":
      return "bg-[var(--color-review-bg)] text-[var(--color-review-fg)]"
    case "audit":
      return "bg-[var(--color-pass-bg)] text-[var(--color-pass-fg)]"
    case "rewrite":
    case "inject_context":
    case "run_command":
      return "bg-[var(--color-info-bg)] text-[var(--color-info-fg)]"
    case "allow":
    case "skipped":
    case "indeterminate":
    default:
      return "bg-[var(--color-surface-overlay)] text-[var(--color-text-tertiary)]"
  }
}

function verdictLabel(t: T, v: string): string {
  const key: TKey | null = (() => {
    switch (v) {
      case "pass": return "policies.test.verdict.pass"
      case "fail": return "policies.test.verdict.fail"
      case "deny": return "policies.test.verdict.deny"
      case "review": return "policies.test.verdict.review"
      case "skipped": return "policies.test.verdict.skipped"
      case "indeterminate": return "policies.test.verdict.indeterminate"
      default: return null
    }
  })()
  return key ? t(key) : v
}

function actionLabel(t: T, a: string): string {
  const key: TKey | null = (() => {
    switch (a) {
      case "block": return "policies.test.action.block"
      case "ask": return "policies.test.action.ask"
      case "audit": return "policies.test.action.audit"
      case "allow": return "policies.test.action.allow"
      case "rewrite": return "policies.test.action.rewrite"
      case "inject_context": return "policies.test.action.inject_context"
      case "run_command": return "policies.test.action.run_command"
      case "skipped": return "policies.test.action.skipped"
      case "indeterminate": return "policies.test.action.indeterminate"
      default: return null
    }
  })()
  return key ? t(key) : a
}

function PolicyResultBlock({
  t, locale, result,
}: {
  t: T
  locale: Locale
  result: PolicyTestResponse
}) {
  return (
    <div
      data-testid="policy-test-result"
      className="space-y-3"
    >
      <h3 className="text-sm font-semibold m-0">
        {t("policies.test.result.title")}
      </h3>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-[var(--color-text-tertiary)]">
          {t("policies.test.result.verdict")}:
        </span>
        <span
          data-testid="policy-test-verdict-pill"
          className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider ${verdictTone(result.verdict)}`}
        >
          {verdictLabel(t, result.verdict)}
        </span>
        <span className="ml-2 text-[var(--color-text-tertiary)]">
          {t("policies.test.result.action")}:
        </span>
        <span
          data-testid="policy-test-action-pill"
          className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider ${actionTone(result.action)}`}
        >
          {actionLabel(t, result.action)}
        </span>
      </div>

      {result.skipped_reason && (
        <p
          data-testid="policy-test-skipped"
          className="text-xs italic text-[var(--color-text-tertiary)]"
        >
          {t("policies.test.result.skipped", { reason: result.skipped_reason })}
        </p>
      )}

      {result.evidence_match_reasons.length > 0 && (
        <div>
          <p className="text-xs font-medium text-[var(--color-text-secondary)]">
            {t("policies.test.result.reasons")}
          </p>
          <ul
            data-testid="policy-test-reasons"
            className="mt-1 space-y-1"
          >
            {result.evidence_match_reasons.map((r, i) => (
              <li
                key={i}
                className="text-[11.5px] font-mono text-[var(--color-text-secondary)]"
              >
                {r}
              </li>
            ))}
          </ul>
        </div>
      )}

      {result.would_run && (
        <details data-testid="policy-test-would-run">
          <summary className="cursor-pointer text-xs font-medium text-[var(--color-text-secondary)]">
            {t("policies.test.result.wouldRun")}
          </summary>
          <pre className="mt-1 max-h-48 overflow-auto rounded-md bg-white p-2 font-mono text-[11px]">
            {JSON.stringify(result.would_run, null, 2)}
          </pre>
        </details>
      )}

      {result.new_tool_input && (
        <details data-testid="policy-test-new-input">
          <summary className="cursor-pointer text-xs font-medium text-[var(--color-text-secondary)]">
            {t("policies.test.result.newToolInput")}
          </summary>
          <pre className="mt-1 max-h-48 overflow-auto rounded-md bg-white p-2 font-mono text-[11px]">
            {JSON.stringify(result.new_tool_input, null, 2)}
          </pre>
        </details>
      )}

      {result.inject_context && (
        <details data-testid="policy-test-inject-context">
          <summary className="cursor-pointer text-xs font-medium text-[var(--color-text-secondary)]">
            {t("policies.test.result.injectContext")}
          </summary>
          <pre className="mt-1 max-h-48 overflow-auto rounded-md bg-white p-2 font-mono text-[11px] whitespace-pre-wrap">
            {result.inject_context}
          </pre>
        </details>
      )}

      <details data-testid="policy-test-hook-specific">
        <summary className="cursor-pointer text-xs font-medium text-[var(--color-text-tertiary)]">
          {t("policies.test.result.hookSpecific")}
        </summary>
        <pre className="mt-1 max-h-48 overflow-auto rounded-md bg-white p-2 font-mono text-[11px]">
          {JSON.stringify(result.hook_specific_output, null, 2)}
        </pre>
      </details>
      {/* locale reserved for future per-locale formatters */}
      <span className="sr-only" aria-hidden>{locale}</span>
    </div>
  )
}

function PackResultBlock({
  t, locale, result,
}: {
  t: T
  locale: Locale
  result: PolicyPackTestResponse
}) {
  return (
    <div data-testid="pack-test-result" className="space-y-3">
      <h3 className="text-sm font-semibold m-0">
        {t("packs.test.perMember")}
      </h3>
      <p className="text-xs text-[var(--color-text-tertiary)]">
        {t("packs.test.memberCount", { n: result.member_count })}
      </p>
      <ul role="list" className="space-y-3">
        {result.members.map((m, i) => (
          <li
            key={m.policy_id ?? i}
            data-testid="pack-test-member"
            className="rounded-md border border-[var(--color-border-subtle)] bg-white p-3"
          >
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="font-mono text-[12px] text-[var(--color-text-primary)]">
                {m.policy_id ?? `(member ${i + 1})`}
              </span>
              <span
                className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${verdictTone(m.verdict)}`}
              >
                {verdictLabel(t, m.verdict)}
              </span>
              <span
                className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${actionTone(m.action)}`}
              >
                {actionLabel(t, m.action)}
              </span>
            </div>
            {m.skipped_reason && (
              <p className="mt-1 text-[11px] italic text-[var(--color-text-tertiary)]">
                {t("policies.test.result.skipped", { reason: m.skipped_reason })}
              </p>
            )}
            {m.evidence_match_reasons.length > 0 && (
              <ul className="mt-1 space-y-0.5">
                {m.evidence_match_reasons.map((r, j) => (
                  <li
                    key={j}
                    className="text-[11px] font-mono text-[var(--color-text-secondary)]"
                  >
                    {r}
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
      <span className="sr-only" aria-hidden>{locale}</span>
    </div>
  )
}
