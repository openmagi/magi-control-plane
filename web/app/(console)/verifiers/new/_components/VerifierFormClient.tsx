"use client"

/**
 * D52b: client island for the /verifiers/new authoring form.
 *
 * The form fields (name / description / triggers / verdict_set /
 * body_type) all need either dynamic add-row UX (triggers, verdicts) or
 * live regex validation (name slug). A pure server-component form would
 * have to round-trip every "add trigger" click through a server action.
 * The client island handles in-flight state; the encompassing <form>
 * still posts to the server action via the action prop the parent
 * passes in.
 *
 * On submit the island serializes `name`, `description`, the trigger
 * rows, the chosen verdicts, and the (locked) `body_type=preview` into
 * a single hidden input (`payload`) the server action reads. This
 * matches the policies/new pattern (one JSON blob into the FormData
 * envelope, server validates, redirects).
 */

import { useMemo, useState } from "react"

export type T = (k: string) => string

const EVENTS: ReadonlyArray<string> = [
  "PreToolUse",
  "PostToolUse",
  "UserPromptSubmit",
  "Stop",
  "SubagentStop",
  "SessionStart",
  "SessionEnd",
  "PreCompact",
]

const MATCHER_CLASSES: ReadonlyArray<{ value: "tool" | "no_tool" | "final"; label: string }> = [
  { value: "tool", label: "tool" },
  { value: "no_tool", label: "no_tool" },
  { value: "final", label: "final" },
]

const ALLOWED_VERDICTS: ReadonlyArray<"pass" | "fail" | "needs_review" | "not_applicable"> = [
  "pass",
  "fail",
  "needs_review",
  "not_applicable",
]

const NAME_RE = /^[a-z][a-z0-9_]*$/

export type TriggerRow = { event: string; matcher_class: "tool" | "no_tool" | "final" }

interface Props {
  labels: {
    name: string
    nameHelper: string
    description: string
    descriptionHelper: string
    triggers: string
    triggersHelper: string
    triggerEvent: string
    triggerMatcher: string
    triggerAdd: string
    triggerRemove: string
    verdictSet: string
    verdictSetHelper: string
    bodyType: string
    bodyTypePreview: string
    submit: string
    submitPending: string
    errName: string
    errNameSlug: string
    errDescription: string
    errTriggers: string
    errVerdicts: string
  }
  initial?: {
    name?: string
    description?: string
    triggers?: TriggerRow[]
    verdict_set?: ReadonlyArray<string>
  }
}

export default function VerifierFormClient({ labels, initial }: Props) {
  const [name, setName] = useState(initial?.name ?? "")
  const [description, setDescription] = useState(initial?.description ?? "")
  const [triggers, setTriggers] = useState<TriggerRow[]>(
    initial?.triggers && initial.triggers.length > 0
      ? initial.triggers
      : [{ event: "PreToolUse", matcher_class: "tool" }],
  )
  const initialVerdicts = useMemo(() => {
    const seed = initial?.verdict_set ?? ["pass", "fail"]
    return new Set(seed.filter((v) => (ALLOWED_VERDICTS as ReadonlyArray<string>).includes(v)))
  }, [initial?.verdict_set])
  const [verdicts, setVerdicts] = useState<Set<string>>(initialVerdicts)

  // Live validation. The server action re-runs these; surfacing them in
  // the client keeps the operator from round-tripping a doomed POST.
  const nameError = useMemo(() => {
    if (!name) return labels.errName
    if (name.length > 64) return labels.errNameSlug
    if (!NAME_RE.test(name)) return labels.errNameSlug
    return null
  }, [name, labels])

  const descriptionError = useMemo(() => {
    if (!description.trim()) return labels.errDescription
    if (description.length > 500) return labels.errDescription
    return null
  }, [description, labels])

  const triggersError = useMemo(() => {
    if (triggers.length === 0) return labels.errTriggers
    for (const t of triggers) {
      if (!t.event) return labels.errTriggers
      if (!(MATCHER_CLASSES.map((m) => m.value) as ReadonlyArray<string>).includes(t.matcher_class)) {
        return labels.errTriggers
      }
    }
    return null
  }, [triggers, labels])

  const verdictError = useMemo(
    () => (verdicts.size === 0 ? labels.errVerdicts : null),
    [verdicts, labels],
  )

  const canSubmit = !nameError && !descriptionError && !triggersError && !verdictError

  const payload = JSON.stringify({
    name,
    description: description.trim(),
    triggers,
    verdict_set: ALLOWED_VERDICTS.filter((v) => verdicts.has(v)),
    body_type: "preview",
  })

  return (
    <div className="space-y-5">
      <input type="hidden" name="payload" value={payload} />

      {/* name */}
      <div className="space-y-1.5">
        <label
          htmlFor="custom-verifier-name"
          className="block text-xs font-semibold text-[var(--color-text-secondary)]"
        >
          {labels.name}
          <span aria-hidden className="ml-1 text-[var(--color-deny-fg)]">*</span>
        </label>
        <input
          id="custom-verifier-name"
          name="name_visible"
          type="text"
          value={name}
          maxLength={64}
          onChange={(e) => setName(e.target.value)}
          aria-invalid={nameError ? "true" : undefined}
          aria-describedby={nameError ? "custom-verifier-name-error" : "custom-verifier-name-helper"}
          className="block w-full rounded-md border border-[var(--color-border-strong)] bg-[var(--color-surface-input)] px-3 py-2 text-sm text-[var(--color-text-primary)] focus:border-[var(--color-border-focus)] focus:outline-none focus:ring-2 focus:ring-[var(--color-border-focus)]/40"
          placeholder="my_custom_check"
        />
        <p id="custom-verifier-name-helper" className="text-[11px] text-[var(--color-text-tertiary)]">
          {labels.nameHelper}
        </p>
        {nameError && (
          <p
            id="custom-verifier-name-error"
            role="alert"
            className="text-[11px] text-[var(--color-deny-fg)]"
          >
            {nameError}
          </p>
        )}
      </div>

      {/* description */}
      <div className="space-y-1.5">
        <label
          htmlFor="custom-verifier-description"
          className="block text-xs font-semibold text-[var(--color-text-secondary)]"
        >
          {labels.description}
          <span aria-hidden className="ml-1 text-[var(--color-deny-fg)]">*</span>
        </label>
        <textarea
          id="custom-verifier-description"
          name="description_visible"
          value={description}
          maxLength={500}
          rows={3}
          onChange={(e) => setDescription(e.target.value)}
          aria-invalid={descriptionError ? "true" : undefined}
          aria-describedby={descriptionError ? "custom-verifier-description-error" : "custom-verifier-description-helper"}
          className="block w-full rounded-md border border-[var(--color-border-strong)] bg-[var(--color-surface-input)] px-3 py-2 text-sm text-[var(--color-text-primary)] focus:border-[var(--color-border-focus)] focus:outline-none focus:ring-2 focus:ring-[var(--color-border-focus)]/40"
          placeholder="..."
        />
        <p
          id="custom-verifier-description-helper"
          className="text-[11px] text-[var(--color-text-tertiary)]"
        >
          {labels.descriptionHelper} ({description.length}/500)
        </p>
        {descriptionError && (
          <p
            id="custom-verifier-description-error"
            role="alert"
            className="text-[11px] text-[var(--color-deny-fg)]"
          >
            {descriptionError}
          </p>
        )}
      </div>

      {/* triggers */}
      <div className="space-y-1.5" data-testid="triggers-section">
        <div className="flex items-baseline justify-between gap-2">
          <span className="block text-xs font-semibold text-[var(--color-text-secondary)]">
            {labels.triggers}
            <span aria-hidden className="ml-1 text-[var(--color-deny-fg)]">*</span>
          </span>
          <button
            type="button"
            onClick={() =>
              setTriggers((rows) => [...rows, { event: "PreToolUse", matcher_class: "tool" }])
            }
            className="rounded-md border border-[var(--color-border-strong)] bg-white px-2 py-1 text-[11px] font-semibold text-[var(--color-text-secondary)] hover:bg-black/[0.02] focus-visible:outline-2 focus-visible:outline-[var(--color-accent)]"
          >
            {labels.triggerAdd}
          </button>
        </div>
        <p className="text-[11px] text-[var(--color-text-tertiary)]">{labels.triggersHelper}</p>
        <div className="space-y-2">
          {triggers.map((tr, idx) => (
            <div
              key={idx}
              data-testid="trigger-row"
              className="flex flex-wrap items-end gap-2 rounded-md border border-black/[0.06] bg-[var(--color-surface-1,#fafafa)]/40 p-2"
            >
              <div className="flex-1 min-w-[140px]">
                <label
                  htmlFor={`trigger-event-${idx}`}
                  className="block text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]"
                >
                  {labels.triggerEvent}
                </label>
                <select
                  id={`trigger-event-${idx}`}
                  value={tr.event}
                  onChange={(e) =>
                    setTriggers((rows) =>
                      rows.map((r, i) => (i === idx ? { ...r, event: e.target.value } : r)),
                    )
                  }
                  className="mt-1 block w-full rounded-md border border-[var(--color-border-strong)] bg-white px-2 py-1.5 text-xs"
                >
                  {EVENTS.map((ev) => (
                    <option key={ev} value={ev}>{ev}</option>
                  ))}
                </select>
              </div>
              <div className="flex-1 min-w-[120px]">
                <label
                  htmlFor={`trigger-matcher-${idx}`}
                  className="block text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]"
                >
                  {labels.triggerMatcher}
                </label>
                <select
                  id={`trigger-matcher-${idx}`}
                  value={tr.matcher_class}
                  onChange={(e) =>
                    setTriggers((rows) =>
                      rows.map((r, i) =>
                        i === idx
                          ? { ...r, matcher_class: e.target.value as TriggerRow["matcher_class"] }
                          : r,
                      ),
                    )
                  }
                  className="mt-1 block w-full rounded-md border border-[var(--color-border-strong)] bg-white px-2 py-1.5 text-xs"
                >
                  {MATCHER_CLASSES.map((m) => (
                    <option key={m.value} value={m.value}>{m.label}</option>
                  ))}
                </select>
              </div>
              {triggers.length > 1 && (
                <button
                  type="button"
                  aria-label={labels.triggerRemove}
                  onClick={() => setTriggers((rows) => rows.filter((_, i) => i !== idx))}
                  className="rounded-md border border-[var(--color-border-strong)] bg-white px-2 py-1.5 text-[11px] text-[var(--color-text-secondary)] hover:bg-black/[0.02]"
                >
                  ×
                </button>
              )}
            </div>
          ))}
        </div>
        {triggersError && (
          <p role="alert" className="text-[11px] text-[var(--color-deny-fg)]">
            {triggersError}
          </p>
        )}
      </div>

      {/* verdict set */}
      <div className="space-y-1.5">
        <span className="block text-xs font-semibold text-[var(--color-text-secondary)]">
          {labels.verdictSet}
          <span aria-hidden className="ml-1 text-[var(--color-deny-fg)]">*</span>
        </span>
        <p className="text-[11px] text-[var(--color-text-tertiary)]">{labels.verdictSetHelper}</p>
        <div className="flex flex-wrap gap-2">
          {ALLOWED_VERDICTS.map((v) => {
            const selected = verdicts.has(v)
            return (
              <label
                key={v}
                className={`inline-flex cursor-pointer items-center gap-1.5 rounded-md border px-2 py-1 text-xs ${
                  selected
                    ? "border-[var(--color-accent)] bg-[var(--color-accent)]/[0.06] text-[var(--color-text-primary)]"
                    : "border-[var(--color-border-strong)] bg-white text-[var(--color-text-secondary)]"
                }`}
              >
                <input
                  type="checkbox"
                  className="sr-only"
                  checked={selected}
                  onChange={(e) =>
                    setVerdicts((prev) => {
                      const next = new Set(prev)
                      if (e.target.checked) next.add(v)
                      else next.delete(v)
                      return next
                    })
                  }
                />
                <span>{v}</span>
              </label>
            )
          })}
        </div>
        {verdictError && (
          <p role="alert" className="text-[11px] text-[var(--color-deny-fg)]">
            {verdictError}
          </p>
        )}
      </div>

      {/* body type (locked to preview in v1) */}
      <div className="space-y-1.5">
        <span className="block text-xs font-semibold text-[var(--color-text-secondary)]">
          {labels.bodyType}
        </span>
        <div className="inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border-strong)] bg-white px-2 py-1 text-xs text-[var(--color-text-secondary)]">
          <span className="inline-flex items-center rounded-full bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-amber-700">
            preview
          </span>
          <span>{labels.bodyTypePreview}</span>
        </div>
      </div>

      <div className="pt-2">
        <button
          type="submit"
          disabled={!canSubmit}
          className="rounded-md bg-[var(--color-accent)] px-4 py-2 text-sm font-semibold text-white hover:bg-[var(--color-accent)]/90 disabled:cursor-not-allowed disabled:opacity-55"
        >
          {labels.submit}
        </button>
      </div>
    </div>
  )
}
