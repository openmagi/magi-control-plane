"use client"

/**
 * D55b: live IR draft preview pane (right column of Conversational compose).
 *
 * Shows:
 *   - A plain-language summary at the top ("When: ... | Condition: ...
 *     | Action: ..."). Placeholders for any field the draft hasn't
 *     filled yet. Renders inside aria-live="polite" so SR users hear
 *     each merge.
 *   - A "Dry-run on last 24h" button (gated on draft validity).
 *     Delegates to the shared DryRunPanel (D53b). We render IT here
 *     so the brief's "Reuse the existing DryRunPanel.tsx without
 *     modification" constraint holds.
 *   - A "Save this rule" CTA gated on `ready_to_save=true`. Posts to
 *     the existing saveCompiled server action exposed by the parent
 *     page; we render a real <form action={saveAction}> with the
 *     current draft serialized into the hidden `ir_json` field.
 *
 * Brief: this file MUST use sub-path imports ("@/components/ui/<X>").
 * The "@/components/ui" barrel pulls a server-only chain into the
 * client bundle and breaks `next build`.
 *
 * NEVER expose internal terms (regex / shacl / llm_critic / matcher /
 * lifecycle / kind / on_missing) to end users. The plain-language
 * summary uses friendly translations only. No raw IR / JSON view
 * lives on the chat surface (D55b code review P0); power users have
 * the Raw/Advanced mode for that.
 */

import { Button } from "@/components/ui/Button"
import { DryRunPanel } from "../../_components/DryRunPanel"

// i18n helper signature matches the rest of the policies/* tree.
type T = (
  k: import("@/lib/i18n/dict").TKey,
  v?: Record<string, string | number>,
) => string

// D63 review (P1): widened to include `run_command` so a wizard-handed-off
// or conversational-compose-emitted run_command IR renders its command
// body / script id / runtime / args / timeout in the right-column draft
// pane instead of falling through to "(not chosen yet)".
type ActionArchetype =
  | "block" | "ask" | "audit" | "strip"
  | "run_command"

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

// D56d (P2 #14): widened to the full 8-event surface the wizard now
// covers. The conversational compiler can emit any of these for an
// IR draft; lifecycleFromDraft mirrors LIFECYCLE_TO_EVENT in
// policies/new/page.tsx so the right-column draft pane renders the
// When summary for every lifecycle the cloud accepts.
type LifecycleKey =
  | "before_tool_use" | "after_tool_use" | "pre_final"
  | "subagent_stop"   | "user_prompt"    | "pre_compact"
  | "session_start"   | "session_end"

function lifecycleFromDraft(d: Record<string, unknown> | null): LifecycleKey | null {
  if (!d || typeof d !== "object") return null
  const trig = d.trigger
  if (!trig || typeof trig !== "object") return null
  const ev = (trig as Record<string, unknown>).event
  if (typeof ev !== "string") return null
  // Mirror policies/new/page.tsx LIFECYCLE_TO_EVENT (inverse).
  if (ev === "PreToolUse") return "before_tool_use"
  if (ev === "PostToolUse") return "after_tool_use"
  if (ev === "Stop") return "pre_final"
  if (ev === "SubagentStop") return "subagent_stop"
  if (ev === "UserPromptSubmit") return "user_prompt"
  if (ev === "PreCompact") return "pre_compact"
  if (ev === "SessionStart") return "session_start"
  if (ev === "SessionEnd") return "session_end"
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
  // D63 review (P1): the run_command IR uses `type: "run_command"`
  // (sibling-archetype dispatcher convention) rather than `action`. If
  // either field signals run_command, surface it as the action label
  // for the draft pane.
  const t = (d as Record<string, unknown>).type
  if (t === "run_command") return "run_command"
  const a = d.action
  if (
    a === "block" || a === "ask" || a === "audit" || a === "strip"
    || a === "run_command"
  ) {
    return a as ActionArchetype
  }
  return null
}

/** Read a run_command spec field from an IR draft.
 *
 * The persisted shape (`RunCommandDraftPersist` in page.tsx) carries
 * `runtime` / `command` / `script_path` / `args` / `timeout_ms` /
 * `fail_closed` directly on the top-level IR object. NL-compiled IR
 * uses the same shape (we widen the NL compiler prompt below). We
 * read defensively because the draft may be mid-merge.
 */
function readRunCommandField(
  d: Record<string, unknown> | null,
  key: "runtime" | "command" | "script_path",
): string {
  if (!d || typeof d !== "object") return ""
  const v = (d as Record<string, unknown>)[key]
  return typeof v === "string" ? v : ""
}

function readRunCommandArgs(d: Record<string, unknown> | null): string[] {
  if (!d || typeof d !== "object") return []
  const v = (d as Record<string, unknown>).args
  if (!Array.isArray(v)) return []
  return v.filter((x): x is string => typeof x === "string")
}

function readRunCommandTimeoutMs(d: Record<string, unknown> | null): number | null {
  if (!d || typeof d !== "object") return null
  const v = (d as Record<string, unknown>).timeout_ms
  return typeof v === "number" && Number.isFinite(v) ? v : null
}

function readRunCommandFailClosed(d: Record<string, unknown> | null): boolean {
  if (!d || typeof d !== "object") return false
  const v = (d as Record<string, unknown>).fail_closed
  return v === true
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
  // `llm_critic` / `step` (a verifier label is internal vocab too) to
  // the user.
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
      if (!step) return ko ? "필요한 확인 항목을 기다리는 중" : "Waiting for a required check"
      return ko ? `필수 확인: ${step}` : `Required check: ${step}`
    }
    default:
      return ko ? "(아직 정해지지 않음)" : "(not chosen yet)"
  }
}

/** Pretty-print a raw matcher value for the user. Hides regex /
 *  MCP-slug shape so the When summary stays plain language even when
 *  the underlying matcher is a regex alternation or an mcp__ slug. */
function prettyMatcher(raw: string, ko: boolean): string {
  // Trim wrapping parentheses or whitespace.
  let m = raw.trim()
  if (m.startsWith("(") && m.endsWith(")")) {
    m = m.slice(1, -1).trim()
  }
  // Alternation: `Bash|Edit|Write` -> "Bash or Edit or Write".
  if (m.includes("|")) {
    const parts = m.split("|").map((p) => prettyOneTool(p.trim(), ko)).filter(Boolean)
    if (parts.length === 0) return ko ? "특정 도구들" : "specific tools"
    const sep = ko ? " 또는 " : " or "
    return parts.join(sep)
  }
  return prettyOneTool(m, ko)
}

/** Friendly form of a single matcher token. */
function prettyOneTool(m: string, ko: boolean): string {
  if (!m) return ko ? "특정 도구" : "a specific tool"
  // MCP-shaped slug: `mcp__server__tool` -> `server.tool`.
  if (m.startsWith("mcp__")) {
    const tail = m.slice("mcp__".length)
    const parts = tail.split("__").filter(Boolean)
    if (parts.length >= 2) return parts.join(".")
    if (parts.length === 1) return parts[0]
    return ko ? "특정 도구" : "a specific tool"
  }
  return m
}

function whenLabel(d: Record<string, unknown> | null, ko: boolean): string {
  const life = lifecycleFromDraft(d)
  if (!life) return ko ? "(아직 정해지지 않음)" : "(not chosen yet)"
  const m = matcherFromDraft(d)
  // D56d (P2 #14): widened lifecycle map mirrors page.tsx
  // LIFECYCLE_LABEL_KO / _EN. CC Stop fires after the agent finishes
  // responding (not "just before the final answer").
  const lifeLabel = ko
    ? ({
        before_tool_use: "도구 실행 전",
        after_tool_use: "도구 실행 후",
        pre_final: "에이전트 응답 직후",
        subagent_stop: "서브에이전트 종료 시점",
        user_prompt: "유저 프롬프트 직전",
        pre_compact: "컨텍스트 컴팩션 직전",
        session_start: "세션 시작 시점",
        session_end: "세션 종료 시점",
      } as const)[life]
    : ({
        before_tool_use: "Before a tool runs",
        after_tool_use: "After a tool runs",
        pre_final: "After the agent finishes responding",
        subagent_stop: "When a subagent stops",
        user_prompt: "Before a user prompt reaches the LLM",
        pre_compact: "Before context compaction",
        session_start: "When the session opens",
        session_end: "When the session closes",
      } as const)[life]
  if (m && m !== "*") {
    const friendly = prettyMatcher(m, ko)
    return ko
      ? `${lifeLabel} (${friendly})`
      : `${lifeLabel} (${friendly})`
  }
  return lifeLabel
}

function actionLabel(d: Record<string, unknown> | null, ko: boolean): string {
  const a = actionFromDraft(d)
  if (!a) return ko ? "(아직 정해지지 않음)" : "(not chosen yet)"
  return ko
    ? ({
        block: "차단",
        ask: "사용자 승인 요청",
        audit: "기록만",
        strip: "출력에서 제거",
        run_command: "쉘 명령 실행",
      } as const)[a]
    : ({
        block: "Block the action",
        ask: "Ask a human",
        audit: "Just record",
        strip: "Strip from output",
        run_command: "Run a shell command",
      } as const)[a]
}

/* ── component ─────────────────────────────────────────────────────── */

export function IrDraftPane({
  t, locale, draft, readyToSave, saveAction, testId,
}: IrDraftPaneProps) {
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
              <span>{actionLabel(draft, ko)}</span>
              {action === "run_command" && (
                <span
                  data-testid="ir-draft-action-warning"
                  className="ml-2 inline-flex items-center rounded-md bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-amber-900"
                  title={
                    ko
                      ? "이 정책은 magi-cp 프로세스 권한으로 쉘 명령을 실행합니다."
                      : "This policy runs a shell command as the magi-cp process."
                  }
                >
                  {ko ? "쉘 실행" : "runs shell"}
                </span>
              )}
            </dd>
            {/*
             * D63 review (P1): render run_command specifics so the
             * operator sees what will execute, not a generic
             * placeholder. The pane's "NEVER expose internal terms"
             * rule still holds (we use plain-language `Runs:` etc),
             * but the command body itself IS the operator-authored
             * surface — withholding it would hide the policy's
             * effect.
             */}
            {action === "run_command" && (() => {
              const runtime = readRunCommandField(draft, "runtime") || "bash"
              const command = readRunCommandField(draft, "command")
              const scriptPath = readRunCommandField(draft, "script_path")
              const args = readRunCommandArgs(draft)
              const timeoutMs = readRunCommandTimeoutMs(draft)
              const failClosed = readRunCommandFailClosed(draft)
              return (
                <>
                  <dt className="text-[10px] uppercase tracking-wider font-semibold text-[var(--color-text-tertiary)]">
                    {ko ? "런타임" : "Runtime"}
                  </dt>
                  <dd
                    className="m-0 font-mono text-[11px]"
                    data-testid="ir-draft-run-command-runtime"
                  >
                    {runtime}
                  </dd>
                  <dt className="text-[10px] uppercase tracking-wider font-semibold text-[var(--color-text-tertiary)]">
                    {ko ? "실행 내용" : "Runs"}
                  </dt>
                  <dd
                    className="m-0 font-mono text-[11px] whitespace-pre-wrap break-words"
                    data-testid="ir-draft-run-command-body"
                  >
                    {command && (
                      <code className="block">{command}</code>
                    )}
                    {!command && scriptPath && (
                      <code className="block">
                        {ko ? "스크립트 id: " : "script id: "}
                        {scriptPath}
                      </code>
                    )}
                    {!command && !scriptPath && (
                      <em>{ko ? "(아직 명령 없음)" : "(no command yet)"}</em>
                    )}
                  </dd>
                  {args.length > 0 && (
                    <>
                      <dt className="text-[10px] uppercase tracking-wider font-semibold text-[var(--color-text-tertiary)]">
                        {ko ? "인자" : "Args"}
                      </dt>
                      <dd
                        className="m-0 font-mono text-[11px]"
                        data-testid="ir-draft-run-command-args"
                      >
                        [{args.map((a) => JSON.stringify(a)).join(", ")}]
                      </dd>
                    </>
                  )}
                  {timeoutMs !== null && (
                    <>
                      <dt className="text-[10px] uppercase tracking-wider font-semibold text-[var(--color-text-tertiary)]">
                        {ko ? "타임아웃" : "Timeout"}
                      </dt>
                      <dd
                        className="m-0 font-mono text-[11px]"
                        data-testid="ir-draft-run-command-timeout"
                      >
                        {timeoutMs}ms
                      </dd>
                    </>
                  )}
                  <dt className="text-[10px] uppercase tracking-wider font-semibold text-[var(--color-text-tertiary)]">
                    {ko ? "실패 처리" : "On failure"}
                  </dt>
                  <dd
                    className="m-0 text-[11px]"
                    data-testid="ir-draft-run-command-fail-closed"
                  >
                    {failClosed
                      ? (ko ? "타임아웃/실패 시 deny" : "Deny on timeout or non-zero exit")
                      : (ko ? "타임아웃/실패 시 통과 + ledger 기록" : "Allow on timeout / failure, log to ledger")}
                  </dd>
                </>
              )
            })()}
          </dl>
        )}
      </section>

      {/* Brief: NO raw JSON / IR view on the conversational chat
       *  surface. Banned tokens (lifecycle / matcher / requires.kind /
       *  on_missing / sentinel_re / gate_binary) leak through any
       *  verbatim render. Power users see the IR shape in the Raw /
       *  Advanced mode (PolicyBuilder), not here. */}

      {/*
       * DryRunPanel's ActionArchetype union still covers the legacy 4
       * archetypes (block / ask / audit / strip). For run_command we
       * pass `audit` as a label hint (the dry-run replay shows
       * historical hits, not a forecast of what run_command would
       * decide — the script's stdout JSON is operator-supplied and
       * cannot be replayed deterministically). A future widening of
       * the panel's union can map run_command directly.
       */}
      <DryRunPanel
        locale={locale}
        ir={readyToSave && draft ? draft : null}
        disabled={!readyToSave}
        action={action === "run_command" ? "audit" : (action ?? "audit")}
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
