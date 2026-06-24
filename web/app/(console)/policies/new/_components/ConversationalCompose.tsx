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
 *   - flip `ready_to_save` when the server says so. Save CTA appears
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

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import Link from "next/link"
import { Button } from "@/components/ui/Button"
import { Skeleton } from "@/components/ui/Skeleton"
import { translate } from "@/lib/i18n/dict"
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
  /** D57a: tag an assistant turn as a structured error bubble so the
   *  renderer can swap in a richer layout (escape-hatch CTAs +
   *  collapsible setup guide) instead of the plain text bubble.
   *  Only "provider_unconfigured" gets the rich treatment today; the
   *  other error codes still render via ChatTurn. */
  errorKind?: "provider_unconfigured"
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
  locale: "ko" | "en"
  /** Server action threaded from policies/new/page.tsx. Posts the
   *  current draft to PUT /policies (same path the NL and Raw modes
   *  use). */
  saveAction: (fd: FormData) => Promise<void>
  /** D56b: legacy `/policies/new?mode=nl&nl=<seed>` URLs redirect to
   *  `?mode=conversational&nl=<seed>`. The server page forwards that
   *  `nl=` query param here so the seed actually prefills the input
   *  instead of landing on an empty chat. Empty string = no seed. */
  initialUserMessage?: string
}

export function ConversationalCompose({
  locale, saveAction, initialUserMessage,
}: ConversationalComposeProps) {
  const t: T = useMemo(
    () => (key, vars) => translate(locale, key, vars),
    [locale],
  )
  const [history, setHistory] = useState<HistoryTurn[]>([])
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null)
  const [readyToSave, setReadyToSave] = useState(false)
  const [pending, setPending] = useState(false)
  // D56b: prefill the input with the `?nl=` seed forwarded by the
  // `?mode=nl` backcompat redirect so a bookmarked legacy URL renders
  // the user's saved description instead of an empty chat. useState
  // initializer (not useEffect) avoids the empty→prefilled flash and
  // never clobbers the user's later edits.
  const [input, setInput] = useState(initialUserMessage ?? "")
  const [picks, setPicks] = useState<Record<string, string[]>>({})
  /** Error state surfaces as an assistant bubble per the brief
   *  ("provider_unconfigured surfaces as an assistant bubble with the
   *  actionable copy, not a top-of-page banner"). We keep a flag so
   *  the input can re-enable for retry. */
  const [errored, setErrored] = useState(false)

  const scrollRef = useRef<HTMLDivElement | null>(null)
  const mountedRef = useRef(true)
  /** Monotonic request id. The cleanup branch in sendTurn drops any
   *  response whose id is no longer current. This is the only line
   *  of defense if two events (e.g. accessibility-tool-driven double
   *  pill click) both observe pending=false in the same micro-task
   *  before React 18 flushes the `setPending(true)` update. */
  const reqIdRef = useRef(0)
  const abortRef = useRef<AbortController | null>(null)
  /** IME composition state. The Korean (Hangul) IME signals Enter to
   *  finalize a composition; we MUST NOT send the in-flight message
   *  on that keystroke. Tracked via compositionstart/end + the
   *  KeyboardEvent.isComposing flag. */
  const composingRef = useRef(false)

  useEffect(() => {
    return () => {
      mountedRef.current = false
      if (abortRef.current) abortRef.current.abort()
    }
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
    /** Optional pre-rendered user bubble (e.g. the pill's human label).
     *  Pushed onto history alongside the wire turn so the transcript
     *  records "user picked X" for clicks too. */
    userBubble?: string | null
  }) => {
    // Abort any in-flight request: only the most-recent fetch wins.
    // Without this the LAST-resolved fetch overwrites `draft` /
    // `readyToSave`, even if it carries a stale server view.
    if (abortRef.current) abortRef.current.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl
    const myId = ++reqIdRef.current

    setPending(true)
    setErrored(false)
    // Optimistically render the user's bubble. Functional updater so
    // any interleaved write (future refactor, concurrent events) does
    // not clobber state - we always extend the latest snapshot.
    const bubble = params.userBubble ?? params.userText
    if (bubble) {
      setHistory((prev) => [...prev, { role: "user", content: bubble }])
    }
    let answersSent = false
    try {
      answersSent = !!params.answers
      // Build wire history. The server reconstructs questions
      // deterministically from `draft_so_far`, so we drop our local
      // `questions` metadata. We capture the latest snapshot via a
      // functional setter that also serves as a read; this avoids the
      // closure-capture race entirely.
      let wireHistory: { role: "user" | "assistant"; content: string }[] = []
      setHistory((prev) => {
        wireHistory = prev.map((h) => ({ role: h.role, content: h.content }))
        return prev
      })
      const res = await fetch("/api/policies/compile-interactive", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        signal: ctrl.signal,
        body: JSON.stringify({
          history: wireHistory,
          draft_so_far: draft,
          answers: params.answers,
        }),
      })
      // Drop the response on the floor if a newer request started OR
      // the component unmounted.
      if (!mountedRef.current) return
      if (myId !== reqIdRef.current) return
      if (!res.ok) {
        let code = "upstream"
        try {
          const j = (await res.json()) as { error?: string }
          if (j && typeof j.error === "string") code = j.error
        } catch { /* keep default */ }
        if (myId !== reqIdRef.current) return
        const assistantMsg = errorBubbleText(code, t, locale)
        const errorKind: HistoryTurn["errorKind"] =
          code === "provider_unconfigured" ? "provider_unconfigured" : undefined
        // Carry the previous assistant turn's questions forward onto
        // the error bubble so the user can re-click and retry. The
        // exception is errorKind === "provider_unconfigured": the
        // ProviderUnconfiguredBubble render path renders escape-hatch
        // CTAs + a setup disclosure and never reads h.questions, so
        // carrying pills forward there would silently drop them from
        // view. For that case we leave questions undefined.
        setHistory((prev) => {
          const lastQuestions = errorKind === "provider_unconfigured"
            ? undefined
            : (() => {
              for (let i = prev.length - 1; i >= 0; i--) {
                if (prev[i].role === "assistant") {
                  return prev[i].questions ?? []
                }
              }
              return []
            })()
          return [
            ...prev,
            {
              role: "assistant",
              content: assistantMsg,
              questions: lastQuestions,
              errorKind,
            },
          ]
        })
        setErrored(true)
        return
      }
      const data = (await res.json()) as InteractiveTurnResponse
      if (!mountedRef.current) return
      if (myId !== reqIdRef.current) return
      const assistantMsg = typeof data.assistant_message === "string"
        ? data.assistant_message
        : ""
      const questions = Array.isArray(data.questions) ? data.questions : []
      setHistory((prev) => [
        ...prev,
        { role: "assistant", content: assistantMsg, questions },
      ])
      setDraft(data.draft ?? null)
      setReadyToSave(!!data.ready_to_save)
    } catch (e) {
      // AbortError is the normal cancellation path when a newer turn
      // started; swallow without surfacing an error bubble.
      if ((e as { name?: string } | null)?.name === "AbortError") return
      if (!mountedRef.current) return
      if (myId !== reqIdRef.current) return
      const assistantMsg = errorBubbleText("network", t, locale)
      setHistory((prev) => {
        const lastQuestions = (() => {
          for (let i = prev.length - 1; i >= 0; i--) {
            if (prev[i].role === "assistant") {
              return prev[i].questions ?? []
            }
          }
          return []
        })()
        return [
          ...prev,
          { role: "assistant", content: assistantMsg, questions: lastQuestions },
        ]
      })
      setErrored(true)
    } finally {
      // Only the most-recent turn flips pending=false. An older turn
      // that lost the race must not re-enable the input under the
      // newer turn's feet.
      if (mountedRef.current && myId === reqIdRef.current) {
        setPending(false)
        // Picks for the previous turn are spent regardless of
        // outcome, so a transient 5xx does not leak the old selection
        // into a later turn that happens to reuse the same qid.
        if (answersSent) setPicks({})
      }
    }
  }, [draft, t, locale])

  const onSendInput = useCallback(() => {
    const text = input.trim()
    if (!text || pending) return
    setInput("")
    void sendTurn({ userText: text, answers: null })
  }, [input, pending, sendTurn])

  /** Look up the human label for a picked option so the optimistic
   *  user bubble shows what the user chose ("Block the action") and
   *  not the raw option value ("block"). */
  const labelForOption = useCallback((qid: string, value: string): string => {
    if (lastAssistantIdx < 0) return value
    const q = history[lastAssistantIdx]?.questions?.find((x) => x.id === qid)
    if (!q || !q.options) return value
    const opt = q.options.find((o) => o.value === value)
    return opt?.label ?? value
  }, [history, lastAssistantIdx])

  const onPickSingle = useCallback((qid: string, value: string) => {
    // Single-select pills submit immediately. The answers payload only
    // ever carries the freshly picked option (the server validates
    // against the previous turn's question set). We also push a user
    // bubble using the human label so the transcript records the pick.
    if (pending) return
    const bubble = labelForOption(qid, value)
    void sendTurn({ userText: null, answers: { [qid]: value }, userBubble: bubble })
  }, [pending, sendTurn, labelForOption])

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
    const bubble = cur.map((v) => labelForOption(qid, v)).join(", ")
    void sendTurn({
      userText: null,
      answers: { [qid]: cur.join(",") },
      userBubble: bubble,
    })
  }, [picks, pending, sendTurn, labelForOption])

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
            // We keep the question pills on the live assistant turn
            // even when it surfaced an error: the bubble inherits the
            // previous turn's question set in the error branch above
            // so the user can re-click and retry without losing their
            // place. A non-last assistant turn never shows live pills.
            const questionsForTurn = isLastAssistant
              ? h.questions ?? null
              : null
            // D57a: provider_unconfigured renders a structured bubble
            // (short user-friendly line + escape-hatch CTAs to guided
            // and advanced modes + collapsible setup guide). The plain
            // ChatTurn path is preserved for every other error code.
            if (h.role === "assistant" && h.errorKind === "provider_unconfigured") {
              return (
                <ProviderUnconfiguredBubble
                  key={idx}
                  t={t}
                  message={h.content}
                  testId={`conv-chat-turn-${idx}`}
                />
              )
            }
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
              onCompositionStart={() => { composingRef.current = true }}
              onCompositionEnd={() => { composingRef.current = false }}
              onKeyDown={(e) => {
                // Korean-primary i18n: the Hangul IME uses Enter to
                // finalize a composition. We MUST guard both
                // KeyboardEvent.isComposing and the composition
                // event-driven ref (Safari has historically lied
                // about isComposing on certain Hangul finalizations).
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing &&
                  !composingRef.current
                ) {
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
 *  what env var to set, forbidden names MAGI_CP_ADMIN_API_KEY so an
 *  operator can tell admin-key trouble from cloud trouble. */
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
  if (code === "forbidden") {
    return t("newPolicy.conv.error.forbidden")
  }
  if (code === "server config") {
    return t("newPolicy.conv.error.configError")
  }
  if (code === "network") {
    return t("newPolicy.conv.error.network")
  }
  return t("newPolicy.conv.error.upstream")
}

/** D57a: structured assistant bubble for the provider_unconfigured
 *  error. Two-tier message:
 *    - short user-friendly first line (no env-var jargon),
 *    - two escape-hatch CTAs (guided wizard / advanced IR editor),
 *    - collapsible "Show setup guide" disclosure with the admin
 *      details (env keys + restart cmd + docs link).
 *  Renders inside the chat scroll like an ordinary assistant bubble
 *  so the conversational lens is preserved (no top-of-page banner).
 */
function ProviderUnconfiguredBubble({
  t, message, testId,
}: {
  t: T
  message: string
  testId?: string
}) {
  const [open, setOpen] = useState(false)
  return (
    <div
      data-testid={testId}
      data-role="assistant"
      data-error-kind="provider_unconfigured"
      className="mr-auto flex flex-col gap-3 max-w-[85%]"
    >
      <div
        className={
          "rounded-2xl px-3 py-2 text-sm leading-relaxed whitespace-pre-wrap " +
          "bg-white border border-black/[0.08] text-[var(--color-text-primary)]"
        }
      >
        {message}
      </div>

      <div
        className="flex flex-wrap gap-2"
        data-testid="conv-provider-unconfigured-ctas"
      >
        <Link
          href="/policies/new?mode=guided"
          className={
            "rounded-xl border border-[var(--color-accent)] bg-[var(--color-accent)] " +
            "px-3 py-1.5 text-xs font-medium text-white " +
            "hover:bg-[var(--color-accent)]/90 " +
            "focus-visible:outline-none focus-visible:ring-2 " +
            "focus-visible:ring-offset-2 focus-visible:ring-[var(--color-accent)]"
          }
          data-testid="conv-provider-unconfigured-cta-guided"
        >
          {t("newPolicy.conv.error.providerUnconfigured.ctaGuided")}
        </Link>
        <Link
          href="/policies/new?mode=advanced"
          className={
            "rounded-xl border border-black/[0.12] bg-white px-3 py-1.5 " +
            "text-xs font-medium text-[var(--color-text-primary)] " +
            "hover:border-[var(--color-accent)] hover:bg-[var(--color-accent)]/[0.04] " +
            "focus-visible:outline-none focus-visible:ring-2 " +
            "focus-visible:ring-offset-2 focus-visible:ring-[var(--color-accent)]"
          }
          data-testid="conv-provider-unconfigured-cta-advanced"
        >
          {t("newPolicy.conv.error.providerUnconfigured.ctaAdvanced")}
        </Link>
      </div>

      <div className="flex flex-col gap-2">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls="conv-provider-unconfigured-setup"
          data-testid="conv-provider-unconfigured-setup-toggle"
          className={
            "self-start rounded text-xs font-medium text-[var(--color-text-secondary)] " +
            "hover:text-[var(--color-accent)] " +
            "focus-visible:outline-none focus-visible:ring-2 " +
            "focus-visible:ring-offset-1 focus-visible:ring-[var(--color-accent)]"
          }
        >
          {open
            ? "▾ " + t("newPolicy.conv.error.providerUnconfigured.setupToggleHide")
            : "▸ " + t("newPolicy.conv.error.providerUnconfigured.setupToggle")}
        </button>
        {open && (
          <div
            id="conv-provider-unconfigured-setup"
            data-testid="conv-provider-unconfigured-setup-body"
            className={
              "rounded-xl border border-black/[0.06] bg-gray-50/60 px-3 py-2 " +
              "text-xs leading-relaxed text-[var(--color-text-secondary)] " +
              "flex flex-col gap-2"
            }
          >
            <p className="m-0 whitespace-pre-wrap">
              {t("newPolicy.conv.error.providerUnconfigured.setupBody")}
            </p>
            <a
              href="https://openmagi.ai/docs/install"
              target="_blank"
              rel="noopener noreferrer"
              className={
                "self-start text-xs font-medium text-[var(--color-accent)] " +
                "hover:underline"
              }
              data-testid="conv-provider-unconfigured-docs-link"
            >
              {t("newPolicy.conv.error.providerUnconfigured.docsLink")}
            </a>
          </div>
        )}
      </div>
    </div>
  )
}

export default ConversationalCompose
