"use client"

/**
 * D55b: live IR draft preview pane (right column of Conversational compose).
 *
 * Shows:
 *   - A plain-language summary at the top ("When: ... | Condition: ...
 *     | Action: ..."). Placeholders for any field the draft hasn't
 *     filled yet. Renders inside aria-live="polite" so SR users hear
 *     each merge.
 *   - A collapsible JSON view of the draft (for power users).
 *   - A "Dry-run on last 24h" button (gated on draft validity).
 *     Delegates to the shared DryRunPanel (D53b) — we render IT here
 *     so the brief's "Reuse the existing DryRunPanel.tsx without
 *     modification" constraint holds.
 *   - A "Save this rule" CTA gated on `ready_to_save=true`. Posts to
 *     the existing saveCompiled server action exposed by the parent
 *     page; we render a real <form action={saveAction}> with the
 *     current draft serialized into the hidden `ir_json` field.
 *
 * Brief: this file MUST use sub-path imports ("@/components/ui/<X>")
 * — the "@/components/ui" barrel pulls a server-only chain into the
 * client bundle and breaks `next build`.
 *
 * NEVER expose internal terms (regex / shacl / llm_critic / matcher /
 * lifecycle / kind / on_missing) to end users. The plain-language
 * summary uses friendly translations only.
 */

import { useState } from "react"
import { Button } from "@/components/ui/Button"
import { DryRunPanel } from "../../_components/DryRunPanel"

// i18n helper signature matches the rest of the policies/* tree.
type T = (
  k: import("@/lib/i18n/dict").TKey,
  v?: Record<string, string | number>,
) => string

type ActionArchetype = "block" | "ask" | "audit" | "strip"

export interface IrDraftPaneProps {
  t: T
  locale: "ko" | "en"
  /** The current draft, server-side. May be partially populated. */
  draft: Record<string, unknown> | null
  /** When true the Save CTA is enabled. */
  readyToSave: boolean
  /** Server action posted to by the Save CTA. The parent (page.tsx)
   *  threads through `saveCompiled` from its server-action wiring. */
  saveAction: (fd: FormData) => Promise<void>
  /** Optional test id for the root container. */
  testId?: string
}

/* ── plain-language summary helpers ─────────────────────────────────── */

type LifecycleKey = "before_tool_use" | "after_tool_use" | "pre_final"

function lifecycleFromDraft(d: Record<string, unknown> | null): LifecycleKey | null {
  if (!d || typeof d !== "object") return null
  const trig = d.trigger
  if (!trig || typeof trig !== "object") return null
  const ev = (trig as Record<string, unknown>).event
  if (typeof ev !== "string") return null
  // Mirror nl_compiler_interactive._EVENT_TO_LIFECYCLE
  if (ev === "PreToolUse") return "before_tool_use"
  if (ev === "PostToolUse") return "after_tool_use"
  if (ev === "Stop") return "pre_final"
  return null
}

function matcherFromDraft(d: Record<string, unknown> | null): string | null {
  if (!d || typeof d !== "object") return null
  const trig = d.trigger
  if (!trig || typeof trig !== "object") return null
  const m = (trig as Record<string, unknown>).matcher
  return typeof m === "string" && m.trim() ? m.trim() : null
}

function actionFromDraft(d: Record<string, unknown> | null): ActionArchetype | null {
  if (!d || typeof d !== "object") return null
  const a = d.action
  if (a === "block" || a === "ask" || a === "audit" || a === "strip") {
    return a as ActionArchetype
  }
  return null
}

function conditionLabel(
  d: Record<string, unknown> | null,
  ko: boolean,
): string {
  if (!d || typeof d !== "object") {
    return ko ? "(아직 정해지지 않음)" : "(not chosen yet)"
  }
  const reqs = d.requires
  if (!Array.isArray(reqs) || reqs.length === 0) {
    return ko ? "(아직 정해지지 않음)" : "(not chosen yet)"
  }
  const first = reqs[0]
  if (!first || typeof first !== "object") {
    return ko ? "(아직 정해지지 않음)" : "(not chosen yet)"
  }
  const item = first as Record<string, unknown>
  const kind = typeof item.kind === "string"
    ? item.kind
    : "step" in item ? "step" : null
  // Plain-language translation. NEVER expose `regex` / `shacl` /
  // `llm_critic` to the user.
  switch (kind) {
    case "regex": {
      const pat = typeof item.pattern === "string" ? item.pattern : ""
      if (!pat) return ko ? "응답에서 어떤 패턴을 찾을지 기다리는 중" : "Waiting for a pattern to look for"
      return ko ? `응답에서 패턴 발견` : `Pattern in the response`
    }
    case "llm_critic": {
      const c = typeof item.criterion === "string" ? item.criterion : ""
      if (!c) return ko ? "AI 판단 기준 입력 대기 중" : "Waiting for an AI judge criterion"
      return ko ? "AI 판단" : "AI judge"
    }
    case "shacl": {
      const ttl = typeof item.shape_ttl === "string" ? item.shape_ttl : ""
      if (!ttl) return ko ? "구조 규칙 입력 대기 중" : "Waiting for a structured rule"
      return ko ? "구조 규칙" : "Structured rule"
    }
    case "step": {
      const step = typeof item.step === "string" ? item.step : ""
      if (!step) return ko ? "검증기 이름 입력 대기 중" : "Waiting for a verifier name"
      return ko ? `검증기: ${step}` : `Verifier: ${step}`
    }
    default:
      return ko ? "(아직 정해지지 않음)" : "(not chosen yet)"
  }
}

function whenLabel(d: Record<string, unknown> | null, ko: boolean): string {
  const life = lifecycleFromDraft(d)
  if (!life) return ko ? "(아직 정해지지 않음)" : "(not chosen yet)"
  const m = matcherFromDraft(d)
  const lifeLabel = ko
    ? ({
        before_tool_use: "도구 실행 전",
        after_tool_use: "도구 실행 후",
        pre_final: "최종 응답 직전",
      } as const)[life]
    : ({
        before_tool_use: "Before a tool runs",
        after_tool_use: "After a tool runs",
        pre_final: "Just before the final answer",
      } as const)[life]
  if (m && m !== "*") {
    return ko
      ? `${lifeLabel} (${m})`
      : `${lifeLabel} (${m})`
  }
  return lifeLabel
}

function actionLabel(d: Record<string, unknown> | null, ko: boolean): string {
  const a = actionFromDraft(d)
  if (!a) return ko ? "(아직 정해지지 않음)" : "(not chosen yet)"
  return ko
    ? ({ block: "차단", ask: "사용자 승인 요청", audit: "기록만", strip: "출력에서 제거" } as const)[a]
    : ({ block: "Block the action", ask: "Ask a human", audit: "Just record", strip: "Strip from output" } as const)[a]
}

/* ── component ─────────────────────────────────────────────────────── */

export function IrDraftPane({
  t, locale, draft, readyToSave, saveAction, testId,
}: IrDraftPaneProps) {
  const [jsonOpen, setJsonOpen] = useState(false)
  const ko = locale === "ko"
  const action = actionFromDraft(draft)
  const irJson = draft ? JSON.stringify(draft, null, 2) : ""
  const hasDraft = !!draft && Object.keys(draft).length > 0

  return (
    <aside
      data-testid={testId ?? "ir-draft-pane"}
      aria-label={t("newPolicy.conv.draftPane.title")}
      className="rounded-2xl border border-black/[0.08] bg-white p-4 shadow-sm flex flex-col gap-3"
    >
      <header className="flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] m-0">
          {t("newPolicy.conv.draftPane.title")}
        </h2>
        {readyToSave && (
          <span
            className="text-[10px] font-bold uppercase tracking-[0.16em] text-[var(--color-accent)]"
            data-testid="ir-draft-ready-pill"
          >
            {t("newPolicy.conv.saveReady")}
          </span>
        )}
      </header>

      <section
        aria-live="polite"
        data-testid="ir-draft-summary"
        className="rounded-xl border border-black/[0.06] bg-gray-50/60 p-3 text-xs leading-relaxed text-[var(--color-text-secondary)]"
      >
        {!hasDraft && (
          <p data-testid="ir-draft-empty" className="m-0 italic">
            {t("newPolicy.conv.draftPane.emptyHint")}
          </p>
        )}
        {hasDraft && (
          <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 m-0">
            <dt className="text-[10px] uppercase tracking-wider font-semibold text-[var(--color-text-tertiary)]">
              {ko ? "언제" : "When"}
            </dt>
            <dd className="m-0" data-testid="ir-draft-when">
              {whenLabel(draft, ko)}
            </dd>
            <dt className="text-[10px] uppercase tracking-wider font-semibold text-[var(--color-text-tertiary)]">
              {ko ? "조건" : "Condition"}
            </dt>
            <dd className="m-0" data-testid="ir-draft-condition">
              {conditionLabel(draft, ko)}
            </dd>
            <dt className="text-[10px] uppercase tracking-wider font-semibold text-[var(--color-text-tertiary)]">
              {ko ? "동작" : "Action"}
            </dt>
            <dd className="m-0" data-testid="ir-draft-action">
              {actionLabel(draft, ko)}
            </dd>
          </dl>
        )}
      </section>

      {hasDraft && (
        <details
          data-testid="ir-draft-json"
          open={jsonOpen}
          onToggle={(e) => setJsonOpen((e.target as HTMLDetailsElement).open)}
          className="rounded-xl bg-gray-50/60 p-2"
        >
          <summary className="cursor-pointer text-[10px] font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)]">
            {ko ? "JSON 미리보기" : "JSON preview"}
          </summary>
          <pre className="mt-2 max-h-64 overflow-auto rounded bg-white p-2 text-[11px] font-mono leading-snug text-[var(--color-text-secondary)]">
            {irJson}
          </pre>
        </details>
      )}

      <DryRunPanel
        t={t}
        ir={readyToSave && draft ? draft : null}
        disabled={!readyToSave}
        action={action ?? "audit"}
      />

      {readyToSave && draft && (
        <form
          action={saveAction}
          className="mt-1 flex items-center gap-2"
          data-testid="ir-draft-save-form"
        >
          <input type="hidden" name="ir_json" value={irJson} />
          <input type="hidden" name="source" value="org" />
          <Button
            type="submit"
            variant="primary"
            size="md"
            data-testid="ir-draft-save"
          >
            {t("newPolicy.conv.saveReady")}
          </Button>
        </form>
      )}
    </aside>
  )
}

export default IrDraftPane
