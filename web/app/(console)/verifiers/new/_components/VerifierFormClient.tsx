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

/**
 * Stable id generator for trigger rows. crypto.randomUUID is in the
 * standard DOM lib but server snapshots from Next 14 do not run this
 * file (it is "use client"); we still guard with a fallback so an
 * unusually old runtime / a vitest jsdom environment that lacks the
 * API never throws when adding a row.
 */
function _genRowId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID()
  }
  return `row-${Date.now()}-${Math.floor(Math.random() * 1e9)}`
}

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

/** Internal row carries a stable id so React identity + DOM ids are
 * tied to the row content, not its position in the array. Stripped
 * before serializing into the payload (`event` + `matcher_class` only). */
type InternalTriggerRow = TriggerRow & { _id: string }

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
  const [triggers, setTriggers] = useState<InternalTriggerRow[]>(() => {
    const seed: TriggerRow[] =
      initial?.triggers && initial.triggers.length > 0
        ? [...initial.triggers]
        : [{ event: "PreToolUse", matcher_class: "tool" }]
    return seed.map((r) => ({ ...r, _id: _genRowId() }))
  })
  const initialVerdicts = useMemo(() => {
    const seed = initial?.verdict_set ?? ["pass", "fail"]
    return new Set(seed.filter((v) => (ALLOWED_VERDICTS as ReadonlyArray<string>).includes(v)))
  }, [initial?.verdict_set])
  const [verdicts, setVerdicts] = useState<Set<string>>(initialVerdicts)

  // Touched state: only show "X is required" once the operator has
  // engaged with the field (focus → blur OR first edit) or has
  // attempted to submit. Prevents screen readers from announcing
  // "name is required" the moment the page mounts (WCAG 3.3.1).
  const [touched, setTouched] = useState({
    name: false,
    description: false,
  })
  const [submitAttempted, setSubmitAttempted] = useState(false)
  const showNameError = touched.name || submitAttempted
  const showDescriptionError = touched.description || submitAttempted
  // Triggers + verdicts seed to non-empty defaults so their error never
  // shows on initial mount. We still gate the trigger error visibility
  // behind submitAttempted as well, to align with the explicit-engage
  // model the name + description follow.
  const showTriggersError = submitAttempted
  const showVerdictsError = submitAttempted

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
    // Strip the client-only _id before serializing — the cloud only
    // accepts {event, matcher_class} (extra='forbid' on the Pydantic
    // model rejects anything else).
    triggers: triggers.map(({ event, matcher_class }) => ({ event, matcher_class })),
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
          onChange={(e) => {
            setName(e.target.value)
            if (!touched.name) setTouched((t) => ({ ...t, name: true }))
          }}
          onBlur={() => setTouched((t) => ({ ...t, name: true }))}
          aria-invalid={showNameError && nameError ? "true" : undefined}
          aria-describedby={[
            showNameError && nameError ? "custom-verifier-name-error" : null,
            "custom-verifier-name-helper",
          ].filter(Boolean).join(" ")}
          className="block w-full rounded-md border border-[var(--color-border-strong)] bg-[var(--color-surface-input)] px-3 py-2 text-sm text-[var(--color-text-primary)] focus:border-[var(--color-border-focus)] focus:outline-none focus:ring-2 focus:ring-[var(--color-border-focus)]/40"
          placeholder="my_custom_check"
        />
        <p id="custom-verifier-name-helper" className="text-[11px] text-[var(--color-text-tertiary)]">
          {labels.nameHelper}
        </p>
        {showNameError && nameError && (
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
          onChange={(e) => {
            setDescription(e.target.value)
            if (!touched.description) setTouched((t) => ({ ...t, description: true }))
          }}
          onBlur={() => setTouched((t) => ({ ...t, description: true }))}
          aria-invalid={showDescriptionError && descriptionError ? "true" : undefined}
          // Keep the helper id in aria-describedby even when an error is
          // present so screen readers continue to announce the live
          // character counter ({n}/500) as the operator approaches the
          // 500-char cap. Otherwise the counter would silently vanish
          // from the SR experience the moment validation fired.
          aria-describedby={[
            showDescriptionError && descriptionError ? "custom-verifier-description-error" : null,
            "custom-verifier-description-helper",
          ].filter(Boolean).join(" ")}
          className="block w-full rounded-md border border-[var(--color-border-strong)] bg-[var(--color-surface-input)] px-3 py-2 text-sm text-[var(--color-text-primary)] focus:border-[var(--color-border-focus)] focus:outline-none focus:ring-2 focus:ring-[var(--color-border-focus)]/40"
          placeholder="..."
        />
        <p
          id="custom-verifier-description-helper"
          className="text-[11px] text-[var(--color-text-tertiary)]"
        >
          {labels.descriptionHelper} ({description.length}/500)
        </p>
        {showDescriptionError && descriptionError && (
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
      <div
        className="space-y-1.5"
        data-testid="triggers-section"
        role="group"
        aria-labelledby="triggers-label"
        aria-describedby={[
          "triggers-helper",
          showTriggersError && triggersError ? "triggers-error" : null,
        ].filter(Boolean).join(" ")}
      >
        <div className="flex items-baseline justify-between gap-2">
          <span
            id="triggers-label"
            className="block text-xs font-semibold text-[var(--color-text-secondary)]"
          >
            {labels.triggers}
            <span aria-hidden className="ml-1 text-[var(--color-deny-fg)]">*</span>
          </span>
          <button
            type="button"
            onClick={() =>
              setTriggers((rows) => [
                ...rows,
                { event: "PreToolUse", matcher_class: "tool", _id: _genRowId() },
              ])
            }
            className="rounded-md border border-[var(--color-border-strong)] bg-white px-2 py-1 text-[11px] font-semibold text-[var(--color-text-secondary)] hover:bg-black/[0.02] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-accent)]"
          >
            {labels.triggerAdd}
          </button>
        </div>
        <p id="triggers-helper" className="text-[11px] text-[var(--color-text-tertiary)]">
          {labels.triggersHelper}
        </p>
        <div className="space-y-2">
          {triggers.map((tr, idx) => (
            <div
              key={tr._id}
              data-testid="trigger-row"
              className="flex flex-wrap items-end gap-2 rounded-md border border-black/[0.06] bg-[var(--color-surface-1,#fafafa)]/40 p-2"
            >
              <div className="flex-1 min-w-[140px]">
                <label
                  htmlFor={`trigger-event-${tr._id}`}
                  className="block text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]"
                >
                  {labels.triggerEvent}
                </label>
                <select
                  id={`trigger-event-${tr._id}`}
                  value={tr.event}
                  onChange={(e) =>
                    setTriggers((rows) =>
                      rows.map((r) => (r._id === tr._id ? { ...r, event: e.target.value } : r)),
                    )
                  }
                  className="mt-1 block w-full rounded-md border border-[var(--color-border-strong)] bg-white px-2 py-1.5 text-xs focus:border-[var(--color-border-focus)] focus:outline-none focus:ring-2 focus:ring-[var(--color-border-focus)]/40"
                >
                  {EVENTS.map((ev) => (
                    <option key={ev} value={ev}>{ev}</option>
                  ))}
                </select>
              </div>
              <div className="flex-1 min-w-[120px]">
                <label
                  htmlFor={`trigger-matcher-${tr._id}`}
                  className="block text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]"
                >
                  {labels.triggerMatcher}
                </label>
                <select
                  id={`trigger-matcher-${tr._id}`}
                  value={tr.matcher_class}
                  onChange={(e) =>
                    setTriggers((rows) =>
                      rows.map((r) =>
                        r._id === tr._id
                          ? { ...r, matcher_class: e.target.value as TriggerRow["matcher_class"] }
                          : r,
                      ),
                    )
                  }
                  className="mt-1 block w-full rounded-md border border-[var(--color-border-strong)] bg-white px-2 py-1.5 text-xs focus:border-[var(--color-border-focus)] focus:outline-none focus:ring-2 focus:ring-[var(--color-border-focus)]/40"
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
                  onClick={() => setTriggers((rows) => rows.filter((r) => r._id !== tr._id))}
                  className="rounded-md border border-[var(--color-border-strong)] bg-white px-2 py-1.5 text-[11px] text-[var(--color-text-secondary)] hover:bg-black/[0.02] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-accent)]"
                >
                  {/* aria-hidden so SR keys on the aria-label only; the
                      visible glyph stays for sighted users. */}
                  <span aria-hidden="true">×</span>
                </button>
              )}
            </div>
          ))}
        </div>
        {showTriggersError && triggersError && (
          <p
            id="triggers-error"
            role="alert"
            className="text-[11px] text-[var(--color-deny-fg)]"
          >
            {triggersError}
          </p>
        )}
      </div>

      {/* verdict set */}
      <div
        className="space-y-1.5"
        role="group"
        aria-labelledby="verdict-set-label"
        aria-describedby={[
          "verdict-set-helper",
          showVerdictsError && verdictError ? "verdict-set-error" : null,
        ].filter(Boolean).join(" ")}
      >
        <span
          id="verdict-set-label"
          className="block text-xs font-semibold text-[var(--color-text-secondary)]"
        >
          {labels.verdictSet}
          <span aria-hidden className="ml-1 text-[var(--color-deny-fg)]">*</span>
        </span>
        <p id="verdict-set-helper" className="text-[11px] text-[var(--color-text-tertiary)]">
          {labels.verdictSetHelper}
        </p>
        <div className="flex flex-wrap gap-2">
          {ALLOWED_VERDICTS.map((v) => {
            const selected = verdicts.has(v)
            return (
              <label
                key={v}
                className={`inline-flex cursor-pointer items-center gap-1.5 rounded-md border px-2 py-1 text-xs focus-within:ring-2 focus-within:ring-[var(--color-border-focus)] focus-within:ring-offset-1 ${
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
        {showVerdictsError && verdictError && (
          <p
            id="verdict-set-error"
            role="alert"
            className="text-[11px] text-[var(--color-deny-fg)]"
          >
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
          <span className="inline-flex items-center rounded-full bg-[var(--color-review-bg,#fffbeb)] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-[var(--color-review-fg,#b45309)]">
            preview
          </span>
          <span>{labels.bodyTypePreview}</span>
        </div>
      </div>

      <div className="pt-2">
        <button
          type="submit"
          disabled={!canSubmit}
          onClick={() => {
            // Force-show every field's error once the user attempts to
            // submit, even if they have not visited each individual
            // field. Mirrors the WCAG 3.3.1 "error visible on submit"
            // expectation while keeping the initial-render silent.
            if (!submitAttempted) setSubmitAttempted(true)
          }}
          className="rounded-md bg-[var(--color-accent)] px-4 py-2 text-sm font-semibold text-white hover:bg-[var(--color-accent)]/90 disabled:cursor-not-allowed disabled:opacity-55 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-accent)]"
        >
          {labels.submit}
        </button>
      </div>
    </div>
  )
}
