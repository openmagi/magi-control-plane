"use client"

/**
 * D55b: conversational compose UI.
 *
 * Two-column desktop, stacked mobile:
 *   - Left 60%: chat scroll + input box + send button.
 *   - Right 40%: live IR draft preview pane.
 *
 * Talks to D55a's POST /policies/compile-interactive via the same-origin
 * proxy at /api/policies/compile-interactive (the admin key stays on the
 * server). Every turn round-trips:
 *   - send `history`, `draft_so_far`, `answers` to the server,
 *   - render the next assistant turn (assistant_message + up to 2
 *     question objects),
 *   - merge the new draft into our local mirror,
 *   - flip `ready_to_save` when the server says so → Save CTA appears
 *     in the IrDraftPane.
 *
 * Brief: NEVER expose internal terms (regex / shacl / llm_critic /
 * EvidenceReq / matcher / kind / lifecycle) to end users. The
 * scrubbing already runs on the backend (D55a), so we just render
 * what the server returned verbatim.
 *
 * Sub-path imports ONLY (NOT from "@/components/ui"). The barrel
 * pulls a server-only chain into the client bundle and breaks build.
 */

import { useCallback, useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/Button"
import { Skeleton } from "@/components/ui/Skeleton"
import ChatTurn, { type QuestionVM } from "./ChatTurn"
import IrDraftPane from "./IrDraftPane"

type T = (
  k: import("@/lib/i18n/dict").TKey,
  v?: Record<string, string | number>,
) => string

/** Mirror of D55a's wire response shape. */
interface InteractiveTurnResponse {
  assistant_message: string
  draft: Record<string, unknown> | null
  missing_fields: string[]
  questions: QuestionVM[]
  needs_more: boolean
  ready_to_save: boolean
}

interface HistoryTurn {
  role: "user" | "assistant"
  content: string
  /** Local-only metadata: questions attached to this assistant turn.
   *  Not sent back over the wire. The server reconstructs the question
   *  set deterministically from `draft_so_far`. */
  questions?: QuestionVM[]
}

interface StarterPill {
  /** i18n key for the visible pill label. */
  labelKey: import("@/lib/i18n/dict").TKey
  /** i18n key for the user-message body sent on click. */
  fillKey: import("@/lib/i18n/dict").TKey
}

/* The 5 starter pills mirror the brief's "TRY ONE OF THESE" set from
 * D52e. We do not auto-submit on click; the click drops the i18n
 * string into the input so the user can edit before sending. */
const STARTER_PILLS: StarterPill[] = [
  {
    labelKey: "newPolicy.conv.starterPills.blockSudo.label",
    fillKey: "newPolicy.conv.starterPills.blockSudo.fill",
  },
  {
    labelKey: "newPolicy.conv.starterPills.webFetchAllowlist.label",
    fillKey: "newPolicy.conv.starterPills.webFetchAllowlist.fill",
  },
  {
    labelKey: "newPolicy.conv.starterPills.requireCitations.label",
    fillKey: "newPolicy.conv.starterPills.requireCitations.fill",
  },
  {
    labelKey: "newPolicy.conv.starterPills.auditAwsKey.label",
    fillKey: "newPolicy.conv.starterPills.auditAwsKey.fill",
  },
  {
    labelKey: "newPolicy.conv.starterPills.askPiiInAnswer.label",
    fillKey: "newPolicy.conv.starterPills.askPiiInAnswer.fill",
  },
]

export interface ConversationalComposeProps {
  t: T
  locale: "ko" | "en"
  /** Server action threaded from policies/new/page.tsx. Posts the
   *  current draft to PUT /policies (same path the NL and Raw modes
   *  use). */
  saveAction: (fd: FormData) => Promise<void>
}

export function ConversationalCompose({
  t, locale, saveAction,
}: ConversationalComposeProps) {
  const [history, setHistory] = useState<HistoryTurn[]>([])
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null)
  const [readyToSave, setReadyToSave] = useState(false)
  const [pending, setPending] = useState(false)
  const [input, setInput] = useState("")
  const [picks, setPicks] = useState<Record<string, string[]>>({})
  /** Error state surfaces as an assistant bubble per the brief
   *  ("provider_unconfigured surfaces as an assistant bubble with the
   *  actionable copy, not a top-of-page banner"). We keep a flag so
   *  the input can re-enable for retry. */
  const [errored, setErrored] = useState(false)

  const scrollRef = useRef<HTMLDivElement | null>(null)
  const mountedRef = useRef(true)
  useEffect(() => {
    return () => { mountedRef.current = false }
  }, [])

  // Autoscroll on new turns.
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [history.length, pending])

  /** The questions currently rendered as live pills sit on the LAST
   *  assistant turn. Older assistant turns render their (read-only)
   *  text; their pills get stripped from the live set so a user can't
   *  re-click an out-of-date question. */
  const lastAssistantIdx = (() => {
    for (let i = history.length - 1; i >= 0; i--) {
      if (history[i].role === "assistant") return i
    }
    return -1
  })()

  const sendTurn = useCallback(async (params: {
    userText: string | null
    answers: Record<string, string> | null
  }) => {
    setPending(true)
    setErrored(false)
    // Optimistically render the user's bubble if they typed text.
    const optimisticHistory: HistoryTurn[] = [...history]
    if (params.userText) {
      optimisticHistory.push({ role: "user", content: params.userText })
      setHistory(optimisticHistory)
    }
    try {
      // Build wire history: every turn except the optimistic user
      // bubble we already pushed. Drop the local-only `questions`
      // metadata before sending.
      const wireHistory: { role: "user" | "assistant"; content: string }[] =
        optimisticHistory.map((h) => ({ role: h.role, content: h.content }))
      const res = await fetch("/api/policies/compile-interactive", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({
          history: wireHistory,
          draft_so_far: draft,
          answers: params.answers,
        }),
      })
      if (!mountedRef.current) return
      if (!res.ok) {
        let code = "upstream"
        try {
          const j = (await res.json()) as { error?: string }
          if (j && typeof j.error === "string") code = j.error
        } catch { /* keep default */ }
        const assistantMsg = errorBubbleText(code, t, locale)
        setHistory([
          ...optimisticHistory,
          { role: "assistant", content: assistantMsg, questions: [] },
        ])
        setErrored(true)
        return
      }
      const data = (await res.json()) as InteractiveTurnResponse
      if (!mountedRef.current) return
      const assistantMsg = typeof data.assistant_message === "string"
        ? data.assistant_message
        : ""
      const questions = Array.isArray(data.questions) ? data.questions : []
      setHistory([
        ...optimisticHistory,
        { role: "assistant", content: assistantMsg, questions },
      ])
      setDraft(data.draft ?? null)
      setReadyToSave(!!data.ready_to_save)
      // Picks for the previous turn are spent; reset to a fresh map.
      setPicks({})
    } catch {
      if (!mountedRef.current) return
      const assistantMsg = errorBubbleText("network", t, locale)
      setHistory([
        ...optimisticHistory,
        { role: "assistant", content: assistantMsg, questions: [] },
      ])
      setErrored(true)
    } finally {
      if (mountedRef.current) setPending(false)
    }
  }, [history, draft, t, locale])

  const onSendInput = useCallback(() => {
    const text = input.trim()
    if (!text || pending) return
    setInput("")
    void sendTurn({ userText: text, answers: null })
  }, [input, pending, sendTurn])

  const onPickSingle = useCallback((qid: string, value: string) => {
    // Single-select pills submit immediately. The answers payload only
    // ever carries the freshly picked option (the server validates
    // against the previous turn's question set).
    if (pending) return
    void sendTurn({ userText: null, answers: { [qid]: value } })
  }, [pending, sendTurn])

  const onPickMulti = useCallback((qid: string, value: string) => {
    setPicks((prev) => {
      const cur = prev[qid] ?? []
      const next = cur.includes(value)
        ? cur.filter((v) => v !== value)
        : [...cur, value]
      return { ...prev, [qid]: next }
    })
  }, [])

  const onSubmitMultiPicks = useCallback((qid: string) => {
    const cur = picks[qid] ?? []
    if (cur.length === 0 || pending) return
    void sendTurn({ userText: null, answers: { [qid]: cur.join(",") } })
  }, [picks, pending, sendTurn])

  const onStarterPillClick = useCallback((fillText: string) => {
    if (pending) return
    setInput(fillText)
  }, [pending])

  const lastAssistant = lastAssistantIdx >= 0 ? history[lastAssistantIdx] : null
  const liveQuestions = lastAssistant?.questions ?? null
  const showStarters = history.length === 0 && !pending

  const isMultiSelect = (q: QuestionVM): boolean => q.kind === "multi_select"

  const onPickRouter = useCallback((qid: string, value: string) => {
    const q = liveQuestions?.find((x) => x.id === qid)
    if (!q) return
    if (isMultiSelect(q)) onPickMulti(qid, value)
    else onPickSingle(qid, value)
  }, [liveQuestions, onPickMulti, onPickSingle])

  return (
    <div
      data-testid="conversational-compose"
      className="grid grid-cols-1 lg:grid-cols-[3fr_2fr] gap-4"
    >
      {/* Left column: chat */}
      <section
        className="flex flex-col rounded-2xl border border-black/[0.08] bg-white shadow-sm overflow-hidden"
        data-testid="conv-chat-column"
      >
        <div
          ref={scrollRef}
          role="log"
          aria-live="polite"
          aria-label={t("newPolicy.conv.draftPane.title")}
          data-testid="conv-chat-scroll"
          className="flex flex-col gap-3 p-4 min-h-[420px] max-h-[60vh] overflow-y-auto"
        >
          {history.length === 0 && (
            <ChatTurn
              role="assistant"
              message={t("newPolicy.conv.intro")}
              testId="conv-chat-intro"
            />
          )}

          {history.map((h, idx) => {
            const isLastAssistant = idx === lastAssistantIdx
            const questionsForTurn = isLastAssistant && !errored
              ? h.questions ?? null
              : null
            return (
              <ChatTurn
                key={idx}
                role={h.role}
                message={h.content}
                questions={questionsForTurn}
                picks={picks}
                pending={pending}
                onPick={onPickRouter}
                onSubmitPicks={onSubmitMultiPicks}
                submitLabel={t("newPolicy.conv.send")}
                testId={`conv-chat-turn-${idx}`}
              />
            )
          })}

          {pending && (
            <div
              data-testid="conv-chat-typing"
              className="mr-auto flex flex-col gap-2 max-w-[85%]"
              aria-label={t("newPolicy.conv.assistantTyping")}
            >
              <Skeleton className="h-4 w-40" />
              <Skeleton className="h-4 w-64" />
            </div>
          )}

          {showStarters && (
            <div
              data-testid="conv-starter-pills"
              className="flex flex-wrap gap-2 mt-2"
            >
              {STARTER_PILLS.map((p) => (
                <button
                  key={p.labelKey}
                  type="button"
                  onClick={() => onStarterPillClick(t(p.fillKey))}
                  className={
                    "rounded-xl border border-black/[0.08] bg-white px-3 py-1.5 " +
                    "text-xs text-[var(--color-text-primary)] " +
                    "hover:border-[var(--color-accent)] hover:bg-[var(--color-accent)]/[0.04]"
                  }
                  data-testid={`conv-starter-${p.labelKey}`}
                >
                  {t(p.labelKey)}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Input box */}
        <div className="border-t border-black/[0.06] bg-gray-50/40 p-3">
          <form
            data-testid="conv-input-form"
            onSubmit={(e) => { e.preventDefault(); onSendInput() }}
            className="flex items-end gap-2"
          >
            <label htmlFor="conv-input" className="sr-only">
              {t("newPolicy.conv.input.placeholder")}
            </label>
            <textarea
              id="conv-input"
              data-testid="conv-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault()
                  onSendInput()
                }
              }}
              disabled={pending}
              rows={2}
              placeholder={t("newPolicy.conv.input.placeholder")}
              className={
                "flex-1 resize-none rounded-lg border border-black/[0.08] " +
                "bg-white px-3 py-2 text-sm leading-relaxed " +
                "focus:border-[var(--color-accent)] focus:outline-none " +
                "disabled:opacity-50"
              }
            />
            <Button
              type="submit"
              variant="primary"
              size="md"
              disabled={pending || input.trim().length === 0}
              data-testid="conv-send"
            >
              {t("newPolicy.conv.send")}
            </Button>
          </form>
        </div>
      </section>

      {/* Right column: live IR draft */}
      <IrDraftPane
        t={t}
        locale={locale}
        draft={draft}
        readyToSave={readyToSave}
        saveAction={saveAction}
      />
    </div>
  )
}

/** Translate a backend error code (or the proxy's surfaced code) into
 *  a plain-language assistant bubble. Maps to D52e's actionable
 *  banner messages: provider_unconfigured tells the operator EXACTLY
 *  what env var to set. */
function errorBubbleText(
  code: string,
  t: T,
  _locale: "ko" | "en",
): string {
  if (code === "provider_unconfigured") {
    return t("newPolicy.conv.error.providerUnconfigured")
  }
  if (code === "invalid_input" || code === "invalid policy") {
    return t("newPolicy.conv.error.invalidInput")
  }
  if (code === "config" || code === "server config") {
    return t("newPolicy.conv.error.configError")
  }
  if (code === "network") {
    return t("newPolicy.conv.error.network")
  }
  return t("newPolicy.conv.error.upstream")
}

export default ConversationalCompose
