"use client"

/**
 * D55b: a single chat turn bubble in the Conversational compose surface.
 *
 * Renders either a user bubble (right-aligned) or an assistant bubble
 * (left-aligned). Assistant turns may also carry question pills, which
 * the parent ConversationalCompose wires through `onPick`.
 *
 * Sub-path imports only ("@/components/ui/..."). The barrel
 * "@/components/ui" pulls a server-only chain into the client bundle.
 */

import ChatTurnPill from "./ChatTurnPill"

export interface QuestionOptionVM {
  value: string
  label: string
  hint?: string
}

export interface QuestionVM {
  id: string
  prompt: string
  kind: "single_select" | "multi_select" | "text"
  /** Empty array for `kind="text"` questions. */
  options: QuestionOptionVM[] | null
  /** Brief: stable field discriminator from D55a. Never rendered. */
  targets_field: string
}

export interface ChatTurnProps {
  role: "user" | "assistant"
  /** Plain-language message. Always pre-scrubbed by the server. */
  message: string
  /** Questions only set on assistant turns AND only on the LAST
   *  assistant turn (older assistants are read-only). */
  questions?: QuestionVM[] | null
  /** Currently-picked option values keyed by question id (multi-select). */
  picks?: Record<string, string[]> | undefined
  /** True when the input chain is in-flight: pills get disabled so the
   *  user does not pile up an unbounded answer queue. */
  pending?: boolean
  onPick?: (qid: string, value: string) => void
  onSubmitPicks?: (qid: string) => void
  /** Optional test id. */
  testId?: string
  /** i18n submit button label for multi-select questions. */
  submitLabel?: string
}

export function ChatTurn({
  role, message, questions, picks, pending, onPick, onSubmitPicks,
  testId, submitLabel,
}: ChatTurnProps) {
  const isUser = role === "user"
  const align = isUser ? "ml-auto" : "mr-auto"
  const bubble = isUser
    ? "bg-[var(--color-accent)]/[0.08] border border-[var(--color-accent)]/20 text-[var(--color-text-primary)]"
    : "bg-white border border-black/[0.08] text-[var(--color-text-primary)]"

  const hasQuestions = !!questions && questions.length > 0

  return (
    <div
      data-testid={testId}
      data-role={role}
      className={`flex flex-col gap-2 max-w-[85%] ${align}`}
    >
      <div
        className={
          `rounded-2xl px-3 py-2 text-sm leading-relaxed whitespace-pre-wrap ${bubble}`
        }
      >
        {message}
      </div>

      {hasQuestions && (
        <div className="flex flex-col gap-3 mt-1">
          {questions!.map((q) => {
            const picked = picks?.[q.id] ?? []
            const isMulti = q.kind === "multi_select"
            const isText = q.kind === "text"
            return (
              <div
                key={q.id}
                data-testid={`chat-question-${q.id}`}
                className="flex flex-col gap-2"
              >
                {q.prompt && (
                  <p className="text-xs font-medium text-[var(--color-text-secondary)] m-0">
                    {q.prompt}
                  </p>
                )}
                {!isText && q.options && q.options.length > 0 && (
                  <div
                    role="list"
                    className="flex flex-wrap gap-2"
                  >
                    {q.options.map((opt) => (
                      <ChatTurnPill
                        key={opt.value}
                        value={opt.value}
                        label={opt.label}
                        hint={opt.hint}
                        pressed={picked.includes(opt.value)}
                        disabled={pending}
                        onPick={(v) => onPick && onPick(q.id, v)}
                        testId={`chat-pill-${q.id}-${opt.value}`}
                      />
                    ))}
                  </div>
                )}
                {isMulti && picked.length > 0 && (
                  <button
                    type="button"
                    onClick={() => onSubmitPicks && onSubmitPicks(q.id)}
                    disabled={pending}
                    data-testid={`chat-submit-multi-${q.id}`}
                    className={
                      "self-start rounded-lg border border-[var(--color-accent)] " +
                      "bg-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-white " +
                      "hover:bg-[var(--color-accent)]/90 disabled:opacity-50"
                    }
                  >
                    {submitLabel ?? "Submit"}
                  </button>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default ChatTurn
