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
import ConvAuthoringGuide from "./ConvAuthoringGuide"
import { decodeSeed } from "./handoff-seed"
import IrDraftPane from "./IrDraftPane"

type T = (
  k: import("@/lib/i18n/dict").TKey,
  v?: Record<string, string | number>,
) => string

/** PR-6: Discriminated union for the feasibility alternatives the server
 *  may append to a turn. `keep_for_cc` is a static chip; `magi_agent_handoff`
 *  carries a localized CTA and an optional deep-link route. */
type FeasibilityAlternative =
  | { kind: "keep_for_cc" }
  | {
      kind: "magi_agent_handoff"
      route: string | null
      intent_summary: string
      cta: string
    }

/** PR-6: Wire shape of the per-turn feasibility object. Absent or null when
 *  the policy compiles natively on the selected runtime (class = "native").
 *  Non-null when the runtime cannot enforce the policy fully. */
interface FeasibilityVM {
  runtime_id: "claude-code" | "codex"
  class: "native" | "degraded" | "silent_noop" | "magi-agent-only" | "not-expressible"
  code: string
  explanation: string
  alternatives: FeasibilityAlternative[]
}

/** Mirror of D55a's wire response shape. */
interface InteractiveTurnResponse {
  assistant_message: string
  draft: Record<string, unknown> | null
  missing_fields: string[]
  questions: QuestionVM[]
  needs_more: boolean
  ready_to_save: boolean
  /** PR-6: runtime-gated feasibility. Null or absent when the policy
   *  compiles natively on the selected runtime (nothing to surface). */
  feasibility?: FeasibilityVM | null
}

/** G2 (IF-04): the server caps history at 16 turns (MAX_HISTORY_TURNS) and
 *  400s beyond it. The client keeps the full transcript for the operator but
 *  sends only the most recent turns on the wire so a long conversation never
 *  trips the cap (which previously bricked the flow with a generic error).
 *  Kept below 16 to leave headroom for the user turn appended this send. */
const MAX_WIRE_TURNS = 14

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
  /** D57g: handoff seed forwarded from the wizard / raw editor's
   *  "Continue in conversation" link. Base64-encoded JSON of
   *  `{wizard_state, draft_ir, origin?}`. When present we POST to
   *  /api/policies/handoff-context on mount and render the response as
   *  the first assistant turn instead of the canned intro. */
  initialSeed?: string
  /** P4 legacy-guard: forwarded to IrDraftPane so the pack-membership
   *  picker only renders under the pack-centric runtime. Default off
   *  keeps the legacy per-policy enabled path unambiguous. */
  packCentric?: boolean
}

/**
 * abortRef ownership contract (single in-flight fetch invariant):
 *
 *   - The seed-mount effect (handoff seed) uses `seedAbortRef`.
 *   - `sendTurn` (every subsequent user turn) uses `sendAbortRef`.
 *   - Unmount aborts BOTH refs.
 *   - The two paths never interleave with each other in practice
 *     because `pending` flips to true on the seed mount and the input
 *     is disabled until the seed turn resolves. Splitting the refs
 *     enforces the contract structurally so a future refactor cannot
 *     silently overwrite a live AbortController by writing to the
 *     wrong slot.
 *
 *   - The monotonic `reqIdRef` is the tiebreak the same path uses to
 *     drop stale responses; sendTurn and the seed-mount each get
 *     their own (sendReqIdRef / seedReqIdRef) so a late seed
 *     response cannot clobber a fresher sendTurn response.
 */

export function ConversationalCompose({
  locale, saveAction, initialUserMessage, initialSeed, packCentric = false,
}: ConversationalComposeProps) {
  const t: T = useMemo(
    () => (key, vars) => translate(locale, key, vars),
    [locale],
  )
  const [history, setHistory] = useState<HistoryTurn[]>([])
  /** G2 (IF-03): sendTurn is memoized on [draft, t, locale] and does NOT
   *  recreate when `history` changes, so its closure captured a stale
   *  history. After an error turn, the next send rebuilt state from the
   *  pre-error history, deleting the errored user + error bubbles from the
   *  UI AND the wire body. This ref always mirrors the committed history so
   *  the turn builder reads the current transcript regardless of closure. */
  const historyRef = useRef<HistoryTurn[]>([])
  historyRef.current = history
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null)
  const [readyToSave, setReadyToSave] = useState(false)
  // Policy-integrity review verdict, fetched once a draft is ready. The
  // review is ADVISORY: it surfaces "does this implement your intent?"
  // (an orphan gate, a non-enforcing action, an intent mismatch) next to
  // the Save CTA, but never blocks the save.
  const [review, setReview] = useState<{
    ok: boolean
    summaryCode: string
    checked: string[]
    issues: {
      severity: string; code: string; message: string
      params: Record<string, unknown>; source: string
    }[]
  } | null>(null)
  const [reviewPending, setReviewPending] = useState(false)
  // F2: distinguish "review couldn't run" (neutral) from "no review". Null
  // review + reviewError=true renders a neutral "couldn't check" row instead
  // of silently hiding the trust surface (which reads as "no feature").
  const [reviewError, setReviewError] = useState(false)
  // Q102: track the server's canonical missing-field set so the
  // IrDraftPane can render the "still missing: ..." footer and
  // surface NAMED placeholders per row instead of an empty stub.
  const [missingFields, setMissingFields] = useState<string[]>([])
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
  /** PR-6: session-scoped runtime override. Sent with every turn so the
   *  cloud can compute feasibility for the operator's chosen runtime.
   *  Defaults to the primary runtime (claude-code). */
  const [runtimeOverride, setRuntimeOverride] = useState<"claude-code" | "codex">("claude-code")
  /** PR-6: last turn's feasibility verdict. Null when the policy compiles
   *  natively (class = "native" or absent) or a new turn is in flight. */
  const [feasibility, setFeasibility] = useState<FeasibilityVM | null>(null)

  // Fetch the policy-integrity review whenever a draft becomes ready.
  // Clears the verdict as soon as the draft is edited back to not-ready
  // so a stale "looks good" can't linger over a changed draft.
  useEffect(() => {
    if (!readyToSave || !draft) {
      setReview(null)
      setReviewError(false)
      return
    }
    // F3 (IF-11/UX-17): compose the intent from ALL user turns (bounded),
    // not just the first, so a conversation that pivots ("actually block,
    // not audit") is reviewed against what the operator actually asked.
    const userTurns = history.filter((h) => h.role === "user").map((h) => h.content)
    if (userTurns.length === 0 && initialUserMessage) userTurns.push(initialUserMessage)
    const intent = userTurns.join("\n").slice(0, 4000)
    let cancelled = false
    setReviewPending(true)
    setReviewError(false)
    fetch("/api/policies/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ draft, intent, locale }),
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (cancelled) return
        if (
          data && typeof data.ok === "boolean" && Array.isArray(data.issues)
        ) {
          setReview({
            ok: data.ok,
            summaryCode: String(data.summary_code ?? ""),
            checked: Array.isArray(data.checked) ? data.checked : [],
            issues: data.issues,
          })
          setReviewError(false)
        } else {
          setReview(null)
          setReviewError(true)
        }
      })
      .catch(() => {
        if (!cancelled) { setReview(null); setReviewError(true) }
      })
      .finally(() => {
        if (!cancelled) setReviewPending(false)
      })
    return () => {
      cancelled = true
    }
  }, [readyToSave, draft, history, initialUserMessage, locale])

  const scrollRef = useRef<HTMLDivElement | null>(null)
  const mountedRef = useRef(true)
  /** Monotonic request id. The cleanup branch in sendTurn drops any
   *  response whose id is no longer current. This is the only line
   *  of defense if two events (e.g. accessibility-tool-driven double
   *  pill click) both observe pending=false in the same micro-task
   *  before React 18 flushes the `setPending(true)` update. */
  const reqIdRef = useRef(0)
  /** D57g code review P2: split AbortController refs so the seed-mount
   *  and sendTurn paths cannot accidentally overwrite each other's
   *  controllers. See file-level abortRef ownership contract above. */
  const sendAbortRef = useRef<AbortController | null>(null)
  const seedAbortRef = useRef<AbortController | null>(null)
  /** Alias kept for the source-level invariant test that greps for
   *  `abortRef`. Points at `sendAbortRef` because that is the
   *  controller every user-driven turn writes to; the seed mount has
   *  its own ref. */
  const abortRef = sendAbortRef
  /** IME composition state. The Korean (Hangul) IME signals Enter to
   *  finalize a composition; we MUST NOT send the in-flight message
   *  on that keystroke. Tracked via compositionstart/end + the
   *  KeyboardEvent.isComposing flag. */
  const composingRef = useRef(false)

  useEffect(() => {
    // R4-01: restore on (re)mount. React StrictMode double-invokes
    // effects, so the cleanup's `mountedRef.current = false` runs first
    // and the next mount must flip it back to true. Without this every
    // sendTurn / seed response bails at the `!mountedRef.current` guard
    // and `pending` sticks true (permanent spinner in `npm run dev`).
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      // The aliased view (`abortRef`) is what the source-level test
      // greps for; we call .abort() on it as the contract pin, and
      // also abort the seed path explicitly.
      if (abortRef.current) abortRef.current.abort()
      if (seedAbortRef.current) seedAbortRef.current.abort()
    }
  }, [])

  // D57g: seed-handling on mount. When a `?seed=` param is forwarded by
  // the "Continue in conversation" link, decode it, POST to
  // /api/policies/handoff-context, and mount the response as the FIRST
  // assistant turn (replacing the canned intro). The seed-applied ref
  // is consulted on every render so a remount (HMR, locale switch)
  // never re-fires the handoff. On error the ref is reset to false
  // (R4-07: this allows re-seeding if the initialSeed PROP changes,
  // but does NOT auto-retry within the same session; see inline note).
  const seedAppliedRef = useRef(false)
  useEffect(() => {
    if (seedAppliedRef.current) return
    if (!initialSeed) return
    const decoded = decodeSeed(initialSeed)
    if (!decoded) {
      // Bad seed — silently fall through to the canned intro. The
      // seed payload is opaque to the operator so surfacing a parse
      // failure would be more confusing than helpful.
      seedAppliedRef.current = true
      return
    }
    seedAppliedRef.current = true
    const ctrl = new AbortController()
    seedAbortRef.current = ctrl
    const myId = ++reqIdRef.current
    setPending(true)
    void (async () => {
      try {
        const res = await fetch("/api/policies/handoff-context", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          cache: "no-store",
          signal: ctrl.signal,
          body: JSON.stringify({
            wizard_state: decoded.wizard_state ?? null,
            draft_ir: decoded.draft_ir ?? null,
            // D57g code review P2: forward `origin` end-to-end so the
            // cloud serialiser can vary the summary head ("Picking up
            // from the review screen" vs "Continuing from the rule
            // editor"). The proxy validates the allow-set
            // (guided / advanced / review).
            ...(typeof decoded.origin === "string"
              ? { origin: decoded.origin } : {}),
            // D57g code review P2: forward the dashboard's locale so a
            // Korean-locale operator authoring an English-only policy
            // still gets a Korean seed (the cloud's draft-content
            // heuristic alone would pick English here).
            locale,
          }),
        })
        if (!mountedRef.current) return
        if (myId !== reqIdRef.current) return
        if (!res.ok) {
          // Map the proxy's error code to the same bubble copy
          // sendTurn uses so the operator sees a consistent surface.
          let code = "upstream"
          try {
            const j = (await res.json()) as { error?: string }
            if (j && typeof j.error === "string") code = j.error
          } catch {
            // keep default
          }
          if (!mountedRef.current) return
          if (myId !== reqIdRef.current) return
          // Render a localized error bubble as the FIRST assistant
          // turn. seedAppliedRef stays true to avoid spam, but the
          // errored flag re-enables the input so the operator can
          // either type from scratch or click Retry (we expose the
          // bubble's "try again" path via the standard error
          // affordance — same as sendTurn's error branch).
          const errMsg = handoffErrorBubbleText(code, t)
          setHistory((prev) =>
            prev.length === 0
              ? [{ role: "assistant", content: errMsg }]
              : prev,
          )
          setErrored(true)
          // R4-07 (documented intentional): resetting seedAppliedRef
          // allows a re-seed if the `initialSeed` PROP changes (which
          // re-runs this effect). It does NOT trigger a retry within the
          // same session because the effect's deps are [initialSeed];
          // an unchanged prop will not re-run the effect. Recovery within
          // the same session requires a page reload with a fresh seed URL.
          seedAppliedRef.current = false
          return
        }
        const data = await res.json() as {
          assistant_message?: string
          draft?: Record<string, unknown> | null
          questions?: QuestionVM[]
          ready_to_save?: boolean
          missing_fields?: unknown
        }
        if (!mountedRef.current) return
        if (myId !== reqIdRef.current) return
        const msg = typeof data.assistant_message === "string"
          ? data.assistant_message : ""
        const questions = Array.isArray(data.questions) ? data.questions : []
        // Functional setter (matches the rest of the component) so an
        // interleaved write does not clobber the seed turn.
        setHistory((prev) =>
          prev.length === 0
            ? [{ role: "assistant", content: msg, questions }]
            : prev,
        )
        setDraft(data.draft ?? null)
        setReadyToSave(!!data.ready_to_save)
        // Q102: defensively narrow missing_fields to string[] for
        // IrDraftPane; the seed-mount endpoint's wire shape is loosely
        // typed (the proxy passes the cloud payload through) so we
        // can't trust the field's element type at the boundary.
        setMissingFields(
          Array.isArray(data.missing_fields)
            ? data.missing_fields.filter((f): f is string => typeof f === "string")
            : [],
        )
      } catch (e) {
        if ((e as { name?: string } | null)?.name === "AbortError") return
        // Network throw / parse error — surface the same bubble path
        // so the operator is not staring at the canned intro thinking
        // the handoff worked.
        if (!mountedRef.current) return
        if (myId !== reqIdRef.current) return
        const errMsg = handoffErrorBubbleText("network", t)
        setHistory((prev) =>
          prev.length === 0
            ? [{ role: "assistant", content: errMsg }]
            : prev,
        )
        setErrored(true)
        // R4-07: same intentional-inert note as the HTTP-error branch above.
        seedAppliedRef.current = false
      } finally {
        if (mountedRef.current && myId === reqIdRef.current) {
          setPending(false)
        }
      }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialSeed])

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
    if (sendAbortRef.current) sendAbortRef.current.abort()
    const ctrl = new AbortController()
    sendAbortRef.current = ctrl
    const myId = ++reqIdRef.current

    setPending(true)
    setErrored(false)
    setFeasibility(null)
    // Build the next-history snapshot synchronously over the current
    // closure-captured `history`, then push the same array into BOTH
    // the optimistic setState AND the fetch wire body. The prior
    // pattern split this into two setState calls — first to add the
    // user bubble, then a second functional updater that read `prev`
    // to capture the wire body. React 18 batches functional updaters
    // and runs them deferred, so the second updater's `prev` arrived
    // BEFORE the first updater's bubble was applied. The result: the
    // wire body landed with an empty `history`, so the server's
    // `_latest_user_turn(history)` returned "" and the #100
    // deterministic intent extractor had nothing to scan. Building
    // `nextHistory` synchronously here removes the race.
    // G2 (IF-03): build from the REF, not the closure `history`, so an
    // error turn is preserved on the next send.
    const bubble = params.userBubble ?? params.userText
    const nextHistory: HistoryTurn[] = bubble
      ? [...historyRef.current, { role: "user", content: bubble }]
      : historyRef.current
    setHistory(nextHistory)
    let answersSent = false
    try {
      answersSent = !!params.answers
      // G2 (IF-04): window the wire history to the last MAX_WIRE_TURNS so a
      // long conversation never trips the server's 16-turn cap (which 400'd
      // with a generic error and could never recover, since history only
      // grows). The server is stateless over draft_so_far and only reads the
      // LATEST user turn, so dropping the oldest turns is safe.
      const wireHistory: { role: "user" | "assistant"; content: string }[] =
        nextHistory.slice(-MAX_WIRE_TURNS).map((h) => ({ role: h.role, content: h.content }))
      const res = await fetch("/api/policies/compile-interactive", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        signal: ctrl.signal,
        body: JSON.stringify({
          history: wireHistory,
          draft_so_far: draft,
          answers: params.answers,
          runtime_id: runtimeOverride,
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
      // Q102: thread the canonical missing-field set through to the
      // IrDraftPane so its named placeholders + "still missing"
      // footer track each turn's server view. InteractiveTurnResponse
      // already pins missing_fields: string[], so the wire shape is
      // sound; we still defensively coerce to handle a malformed
      // server response without crashing the right column.
      setMissingFields(
        Array.isArray(data.missing_fields)
          ? data.missing_fields.filter((f): f is string => typeof f === "string")
          : [],
      )
      // PR-6: update feasibility from the server's per-turn wire object.
      // Null or absent = native runtime (render nothing); any other class
      // surfaces the banner + alternatives.
      const fw = data.feasibility
      if (fw != null && fw.class !== "native") {
        setFeasibility(fw)
      } else {
        setFeasibility(null)
      }
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
  }, [draft, t, locale, runtimeOverride])

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
        {/* D57b: "What can I write?" guide. Sits above the chat scroll
         *  region so first-time visitors discover the affordance before
         *  the assistant turn renders. Collapsed by default; the open
         *  state is persisted per-user via the guide's own localStorage
         *  key (parent does not need to wire it). The pill row writes
         *  prompts into the chat input via setInput, mirroring the
         *  existing in-scroll starter pills (which only render on
         *  empty-history). */}
        <div className="px-4 pt-4">
          <ConvAuthoringGuide
            locale={locale}
            onFillPrompt={(text) => setInput(text)}
            pending={pending}
          />
        </div>

        {/* PR-6: session-scoped runtime override selector */}
        <div className="flex items-center gap-2 border-b border-black/[0.06] px-4 py-2">
          <span className="text-[11px] font-medium text-[var(--color-text-secondary)]">
            {t("newPolicy.conv.runtimeOverride.label")}
          </span>
          <select
            data-testid="conv-runtime-select"
            value={runtimeOverride}
            onChange={(e) => {
              // R4-03: clear the stale feasibility banner from the prior
              // runtime immediately; the next turn will fetch a fresh one.
              setFeasibility(null)
              setRuntimeOverride(e.target.value as "claude-code" | "codex")
            }}
            // R4-03: freeze the select while a turn is in flight so the
            // operator cannot switch runtimes mid-request (which would
            // send the new runtime but show the old runtime's feasibility).
            disabled={pending}
            aria-label={t("newPolicy.conv.runtimeOverride.label")}
            className={
              "rounded-md border border-black/[0.08] bg-white px-2 py-0.5 " +
              "text-[11px] text-[var(--color-text-primary)] " +
              "focus:border-[var(--color-accent)] focus:outline-none " +
              "disabled:cursor-not-allowed disabled:opacity-50"
            }
          >
            <option value="claude-code">{t("runtime.name.claude-code")}</option>
            <option value="codex">{t("runtime.name.codex")}</option>
          </select>
        </div>

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

          {/* PR-6: feasibility banner — surfaces when the current runtime
           *  cannot fully enforce the compiled policy. Null when native or
           *  when a new turn is in flight (cleared in sendTurn). */}
          {feasibility && (
            <FeasibilityBanner feasibility={feasibility} t={t} />
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
        missingFields={missingFields}
        packCentric={packCentric}
        review={review}
        reviewPending={reviewPending}
        reviewError={reviewError}
        // R4-02: gate Save while a correction turn is in-flight so the
        // operator cannot persist a pre-correction (superseded) draft.
        pending={pending}
        // P4: feed the pack picker's extractor the operator's first
        // message so a named work context ("리서치", "coding safety")
        // pre-selects the matching pack.
        suggestedPackText={
          history.find((h) => h.role === "user")?.content
          ?? initialUserMessage
          ?? null
        }
      />
    </div>
  )
}

/**
 * PR-6: Feasibility banner.
 *
 * Renders below the last assistant turn when the server reports that the
 * current runtime cannot fully enforce the compiled policy. Never renders
 * for class "native" (that is the happy path — null feasibility state).
 *
 * Layout:
 *   - Colored border: amber for non-enforcing paths (silent_noop,
 *     not-expressible); verdigris for informational (degraded,
 *     magi-agent-only).
 *   - `explanation` from the server, rendered verbatim (already localized
 *     server-side).
 *   - `alternatives` as chips below the explanation:
 *     - `keep_for_cc` -> static i18n chip
 *     - `magi_agent_handoff` -> `cta` text; anchor when `route` is
 *       non-null (opens in new tab), plain span otherwise.
 */
function FeasibilityBanner({ feasibility, t }: {
  feasibility: FeasibilityVM
  t: T
}) {
  const isWarning =
    feasibility.class === "silent_noop" ||
    feasibility.class === "not-expressible"
  const bannerClass = isWarning
    ? "border-amber-300 bg-amber-50 text-amber-900"
    : "border-[var(--color-accent)]/30 bg-[var(--color-accent)]/[0.04] text-[var(--color-text-primary)]"

  return (
    <div
      data-testid="feasibility-banner"
      data-class={feasibility.class}
      className={`rounded-lg border p-3 text-xs leading-relaxed ${bannerClass}`}
    >
      <p className="m-0 whitespace-pre-wrap">{feasibility.explanation}</p>

      {feasibility.alternatives.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2">
          {feasibility.alternatives.map((alt, i) => {
            if (alt.kind === "keep_for_cc") {
              return (
                <span
                  key={i}
                  data-testid="feasibility-alt-keep-for-cc"
                  className={
                    "rounded-xl border border-black/[0.10] bg-white px-2.5 py-1 " +
                    "text-[11px] font-medium text-[var(--color-text-primary)]"
                  }
                >
                  {t("newPolicy.conv.feasibility.keepForCC")}
                </span>
              )
            }
            // magi_agent_handoff
            return (
              <span key={i} className="flex flex-col gap-0.5">
                {alt.route ? (
                  <a
                    data-testid="feasibility-alt-handoff-link"
                    href={alt.route}
                    target="_blank"
                    rel="noreferrer"
                    className={
                      "rounded-xl border border-[var(--color-accent)]/50 " +
                      "bg-[var(--color-accent)]/10 px-2.5 py-1 " +
                      "text-[11px] font-medium text-[var(--color-accent)] " +
                      "hover:bg-[var(--color-accent)]/20"
                    }
                  >
                    {alt.cta}
                  </a>
                ) : (
                  <span
                    data-testid="feasibility-alt-handoff-text"
                    className="text-[11px] text-[var(--color-text-secondary)]"
                  >
                    {alt.cta}
                  </span>
                )}
                <span
                  data-testid="feasibility-alt-intent-summary"
                  className="px-1 text-[10px] text-[var(--color-text-tertiary)]"
                >
                  {alt.intent_summary}
                </span>
              </span>
            )
          })}
        </div>
      )}
    </div>
  )
}

/** D57g code review P2: error bubble for the seed-mount path. The
 *  handoff fetch (POST /api/policies/handoff-context) maps proxy /
 *  cloud failures onto the same code vocabulary sendTurn uses
 *  (provider_unconfigured, invalid_input, forbidden, server config,
 *  network, upstream). When the seed mount errors we still want to
 *  render an actionable assistant bubble (not silently fall through
 *  to the canned intro), but we want a HANDOFF-specific opening line
 *  so the operator knows their wizard / raw-editor context didn't
 *  load — they can either type from scratch or refresh to retry. */
function handoffErrorBubbleText(
  code: string,
  t: T,
): string {
  // Lead with the localized handoff-failed copy so the operator knows
  // "your handoff didn't load" before the standard error detail.
  const lead = t("newPolicy.handoff.failed")
  const tail = errorBubbleText(code, t, "ko")
  return `${lead}\n\n${tail}`
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
