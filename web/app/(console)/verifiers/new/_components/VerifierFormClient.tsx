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

import { useEffect, useId, useMemo, useRef, useState } from "react"
import { getDisplayLabel } from "@/lib/payload-schemas"

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

/** D52d: one (path, check_description) pair the operator adds in the
 * field_checks editor. Mirrors `FieldCheck` in the catalog descriptor +
 * `CustomVerifierFieldCheck` server-side. */
export type FieldCheckRow = { path: string; check_description: string }

/** D57c: input-assembly contract. See lib/verifier-descriptors.ts +
 * custom_verifier_store.InputAssembly for the prose. */
export type InputAssemblyValue = "cc_stdin" | "caller_assembled"

/** Internal row carries a stable id so React identity + DOM ids are
 * tied to the row content, not its position in the array. Stripped
 * before serializing into the payload (`event` + `matcher_class` only). */
type InternalTriggerRow = TriggerRow & { _id: string }

/** D52d: internal row id mirrors InternalTriggerRow. */
type InternalFieldCheckRow = FieldCheckRow & { _id: string }

const MAX_FIELD_CHECK_PATH_LEN = 128
const MAX_FIELD_CHECK_DESC_LEN = 200
/** D57c: caller_assembly_hint character cap. Matches
 * `_MAX_CALLER_ASSEMBLY_HINT_LEN` server-side. */
const MAX_CALLER_ASSEMBLY_HINT_LEN = 500

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
    // D52d: field_checks editor labels.
    fieldChecks: string
    fieldChecksHelper: string
    fieldCheckPath: string
    fieldCheckDescription: string
    fieldCheckAdd: string
    fieldCheckRemove: string
    errFieldChecks: string
    // D57c: input_assembly select + caller_assembly_hint textarea
    // labels.
    inputAssembly: string
    inputAssemblyHelper: string
    inputAssemblyCcStdin: string
    inputAssemblyCcStdinHelper: string
    inputAssemblyCallerAssembled: string
    inputAssemblyCallerAssembledHelper: string
    callerAssemblyHint: string
    callerAssemblyHintHelper: string
    callerAssemblyHintPlaceholder: string
    errCallerAssemblyHint: string
    errCallerAssemblyHintOnCcStdin: string
  }
  initial?: {
    name?: string
    description?: string
    triggers?: TriggerRow[]
    verdict_set?: ReadonlyArray<string>
    field_checks?: FieldCheckRow[]
    input_assembly?: InputAssemblyValue
    caller_assembly_hint?: string
  }
  /** D64: locale used to resolve friendly display labels for the
   * field-check path picker. When the operator types a path the
   * runtime registry knows (`tool_input.command`, `tool_response.output`,
   * …) we render the friendly label below the input so the operator
   * sees the human-readable name they're picking. UNKNOWN paths get
   * no label (the input itself reads as the raw path). Defaults to
   * `en` so older callers without locale wiring still render. */
  locale?: "ko" | "en"
}

export default function VerifierFormClient({ labels, initial, locale = "en" }: Props) {
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
  // D52d: field_checks state. Seed to one empty row so the operator
  // sees the editor immediately; the row is "incomplete" until the
  // path + description are filled in, and the submit button stays
  // disabled until at least one row is complete.
  const [fieldChecks, setFieldChecks] = useState<InternalFieldCheckRow[]>(() => {
    const seed: FieldCheckRow[] =
      initial?.field_checks && initial.field_checks.length > 0
        ? [...initial.field_checks]
        : [{ path: "", check_description: "" }]
    return seed.map((r) => ({ ...r, _id: _genRowId() }))
  })
  // D57c: input_assembly + caller_assembly_hint state. Defaults to
  // cc_stdin so authors of a standalone verifier do not need to
  // touch this section at all; switching to caller_assembled
  // surfaces the hint textarea inline (a recipe-driven verifier
  // needs the explainer to be useful).
  const [inputAssembly, setInputAssembly] = useState<InputAssemblyValue>(
    initial?.input_assembly ?? "cc_stdin",
  )
  const [callerAssemblyHint, setCallerAssemblyHint] = useState<string>(
    initial?.caller_assembly_hint ?? "",
  )
  // D57c follow-up: useId-scoped name for the input-assembly radio
  // group. The form previously hardcoded `name="input-assembly"`,
  // which would merge two radio groups into one if the form ever
  // mounted twice on the same page (a draft + a fresh form, or a
  // wizard with a preview). useId() gives every mount a stable,
  // unique name so the radio invariants hold per-instance.
  const reactId = useId()
  const inputAssemblyRadioName = `input-assembly-${reactId}`

  // D52d follow-up (a11y, WCAG 2.4.3 + 2.4.7): after the operator
  // clicks "Add check" or "Add trigger", focus has to move INTO the
  // newly inserted row's first input. Otherwise focus sits on the
  // (now visually-below-the-row) Add button and a keyboard / SR user
  // has to tab through every existing row's 2-3 inputs + remove
  // button to reach the new row. The pending id is set inside the
  // Add onClick; the matching useEffect resolves it once React has
  // committed the new row to the DOM, focuses the path/event input,
  // and clears the flag. The remove path restores focus to the Add
  // button if the last removable row was deleted, otherwise to the
  // adjacent row's first input (so a "delete the wrong row" undo
  // path lands near the deletion point).
  const [pendingFocusFieldCheckId, setPendingFocusFieldCheckId] = useState<string | null>(null)
  const [pendingFocusTriggerId, setPendingFocusTriggerId] = useState<string | null>(null)
  const fieldChecksAddBtnRef = useRef<HTMLButtonElement | null>(null)
  const triggersAddBtnRef = useRef<HTMLButtonElement | null>(null)

  useEffect(() => {
    if (!pendingFocusFieldCheckId) return
    const el = document.getElementById(`field-check-path-${pendingFocusFieldCheckId}`)
    if (el && typeof (el as HTMLInputElement).focus === "function") {
      (el as HTMLInputElement).focus()
    }
    setPendingFocusFieldCheckId(null)
  }, [pendingFocusFieldCheckId])

  useEffect(() => {
    if (!pendingFocusTriggerId) return
    const el = document.getElementById(`trigger-event-${pendingFocusTriggerId}`)
    if (el && typeof (el as HTMLSelectElement).focus === "function") {
      (el as HTMLSelectElement).focus()
    }
    setPendingFocusTriggerId(null)
  }, [pendingFocusTriggerId])

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
  // D52d: field_checks seeds to one empty row, so its error is
  // structurally "incomplete row" rather than "missing array", gate
  // visibility behind submitAttempted so the operator does not see a
  // red error the moment they open the page.
  const showFieldChecksError = submitAttempted

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

  // D52d: validate the field_checks rows. Server enforces the same
  // contract (>=1 row, non-empty path, non-empty + <=200 char desc).
  const fieldChecksError = useMemo(() => {
    if (fieldChecks.length === 0) return labels.errFieldChecks
    for (const fc of fieldChecks) {
      const path = fc.path.trim()
      const desc = fc.check_description.trim()
      if (!path || path.length > MAX_FIELD_CHECK_PATH_LEN) return labels.errFieldChecks
      if (!desc || desc.length > MAX_FIELD_CHECK_DESC_LEN) return labels.errFieldChecks
    }
    return null
  }, [fieldChecks, labels])

  // D57c: caller_assembled rows MUST carry a 1-500 char explainer.
  // The error visibility gate behaves like the other inputs: surface
  // on blur / first edit / submit attempt.
  //
  // D57c follow-up (data-loss): when cc_stdin is selected the typed
  // hint stays in component state (so switching back to
  // caller_assembled restores it) but is excluded from the wire
  // payload. The local "must leave blank" check is therefore not
  // useful — the payload always satisfies the server invariant for
  // cc_stdin rows. Keeping the `errCallerAssemblyHintOnCcStdin` label
  // in `Props.labels` for back-compat; the server still raises if a
  // hand-rolled client posts a hint while picking cc_stdin.
  const [touchedHint, setTouchedHint] = useState(false)
  const callerAssemblyHintError = useMemo(() => {
    if (inputAssembly !== "caller_assembled") return null
    const trimmed = callerAssemblyHint.trim()
    if (!trimmed) return labels.errCallerAssemblyHint
    if (callerAssemblyHint.length > MAX_CALLER_ASSEMBLY_HINT_LEN) {
      return labels.errCallerAssemblyHint
    }
    return null
  }, [inputAssembly, callerAssemblyHint, labels])
  const showCallerAssemblyHintError =
    (touchedHint || submitAttempted) && !!callerAssemblyHintError

  const canSubmit = !nameError && !descriptionError && !triggersError
    && !verdictError && !fieldChecksError && !callerAssemblyHintError

  const payload = JSON.stringify({
    name,
    description: description.trim(),
    // Strip the client-only _id before serializing — the cloud only
    // accepts {event, matcher_class} (extra='forbid' on the Pydantic
    // model rejects anything else).
    triggers: triggers.map(({ event, matcher_class }) => ({ event, matcher_class })),
    verdict_set: ALLOWED_VERDICTS.filter((v) => verdicts.has(v)),
    body_type: "preview",
    // D52d: strip _id, trim path/desc, but keep the row order the
    // operator authored. Server re-validates and dedupes.
    field_checks: fieldChecks.map(({ path, check_description }) => ({
      path: path.trim(),
      check_description: check_description.trim(),
    })),
    // D57c: forward the (input_assembly, caller_assembly_hint) pair.
    // The hint is trimmed to match the server invariant (cc_stdin
    // rows MUST leave it blank, not "blank with whitespace").
    //
    // D57c follow-up (data-loss): when cc_stdin is selected we keep
    // the typed hint in component state (so switching back to
    // caller_assembled restores it) but EXCLUDE it from the wire
    // payload, which preserves the server invariant without forcing
    // the operator to retype 500 chars if they bounce the radio.
    input_assembly: inputAssembly,
    caller_assembly_hint:
      inputAssembly === "caller_assembled" ? callerAssemblyHint.trim() : "",
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
            ref={triggersAddBtnRef}
            type="button"
            onClick={() => {
              // D52d follow-up (a11y): generate the id up-front so the
              // useEffect can find the row right after React commits
              // it. Using a server-stable id keeps the focus path the
              // same regardless of the order setState's batched
              // updates resolve.
              const newId = _genRowId()
              setTriggers((rows) => [
                ...rows,
                { event: "PreToolUse", matcher_class: "tool", _id: newId },
              ])
              setPendingFocusTriggerId(newId)
            }}
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
                  onClick={() => {
                    // D52d follow-up (a11y): restore focus after a
                    // remove so the operator does not get dropped at
                    // the document root. Prefer the previous row's
                    // event input; fall back to the Add button when
                    // the removed row was the first (or only) one.
                    setTriggers((rows) => {
                      const removeIdx = rows.findIndex((r) => r._id === tr._id)
                      const next = rows.filter((r) => r._id !== tr._id)
                      const restore = removeIdx > 0 ? next[removeIdx - 1] : next[0]
                      // Defer the focus restore until after commit.
                      queueMicrotask(() => {
                        if (restore) {
                          const el = document.getElementById(`trigger-event-${restore._id}`)
                          if (el && typeof (el as HTMLSelectElement).focus === "function") {
                            (el as HTMLSelectElement).focus()
                            return
                          }
                        }
                        triggersAddBtnRef.current?.focus()
                      })
                      return next
                    })
                  }}
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

      {/* D52d: field_checks editor.
          Multi-add rows: [path] [check description]. >=1 required.
          Path placeholder hints at the CC stdin vocabulary
          (tool_input.url, tool_response.output, transcript_path); the
          backend does NOT enforce a path enum so the operator can
          describe domain-specific MCP tool paths. */}
      <div
        className="space-y-1.5"
        data-testid="field-checks-section"
        role="group"
        aria-labelledby="field-checks-label"
        aria-describedby={[
          "field-checks-helper",
          showFieldChecksError && fieldChecksError ? "field-checks-error" : null,
        ].filter(Boolean).join(" ")}
      >
        <div className="flex items-baseline justify-between gap-2">
          <span
            id="field-checks-label"
            className="block text-xs font-semibold text-[var(--color-text-secondary)]"
          >
            {labels.fieldChecks}
            <span aria-hidden className="ml-1 text-[var(--color-deny-fg)]">*</span>
          </span>
          <button
            ref={fieldChecksAddBtnRef}
            type="button"
            onClick={() => {
              const newId = _genRowId()
              setFieldChecks((rows) => [
                ...rows,
                { path: "", check_description: "", _id: newId },
              ])
              setPendingFocusFieldCheckId(newId)
            }}
            className="rounded-md border border-[var(--color-border-strong)] bg-white px-2 py-1 text-[11px] font-semibold text-[var(--color-text-secondary)] hover:bg-black/[0.02] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-accent)]"
          >
            {labels.fieldCheckAdd}
          </button>
        </div>
        <p id="field-checks-helper" className="text-[11px] text-[var(--color-text-tertiary)]">
          {labels.fieldChecksHelper}
        </p>
        <div className="space-y-2">
          {fieldChecks.map((fc) => {
            // D52d follow-up (WCAG 3.3.1): per-row validity. The
            // group-level error ("Add at least one (path, description)
            // check. …") cannot tell the operator WHICH of N rows is
            // the offender. We compute per-field invalidity and bind
            // aria-invalid + aria-describedby to a row-local <p> so
            // SR users tabbing into the offending input hear "invalid
            // entry, <row reason>" rather than a silent input. The
            // group-level error stays as a summary.
            const pathTrim = fc.path.trim()
            const descTrim = fc.check_description.trim()
            const pathInvalid = !pathTrim || pathTrim.length > MAX_FIELD_CHECK_PATH_LEN
            const descInvalid = !descTrim || descTrim.length > MAX_FIELD_CHECK_DESC_LEN
            const showRowError = submitAttempted && (pathInvalid || descInvalid)
            const rowErrorId = `field-check-${fc._id}-error`
            let rowErrorMsg: string | null = null
            if (showRowError) {
              if (pathInvalid && descInvalid) rowErrorMsg = labels.errFieldChecks
              else if (pathInvalid)
                rowErrorMsg = pathTrim.length > MAX_FIELD_CHECK_PATH_LEN
                  ? `path must be <= ${MAX_FIELD_CHECK_PATH_LEN} chars`
                  : "path is required"
              else
                rowErrorMsg = descTrim.length > MAX_FIELD_CHECK_DESC_LEN
                  ? `description must be <= ${MAX_FIELD_CHECK_DESC_LEN} chars`
                  : "description is required"
            }
            return (
              <div
                key={fc._id}
                data-testid="field-check-row"
                className="flex flex-wrap items-end gap-2 rounded-md border border-black/[0.06] bg-[var(--color-surface-1,#fafafa)]/40 p-2"
              >
                <div className="flex-1 min-w-[180px]">
                  <label
                    htmlFor={`field-check-path-${fc._id}`}
                    className="block text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]"
                  >
                    {labels.fieldCheckPath}
                  </label>
                  {(() => {
                    const trimmedPath = fc.path.trim()
                    const friendly = trimmedPath
                      ? getDisplayLabel(trimmedPath, locale)
                      : trimmedPath
                    const hasFriendly = !!trimmedPath && friendly !== trimmedPath
                    const friendlyId = `field-check-path-display-label-${fc._id}`
                    // Compose aria-describedby so SR users hear BOTH the
                    // friendly resolution (when present) and the row error
                    // (when the row fails validation). D64 polish: parity
                    // with the chip / tree-row surfaces that already name
                    // the friendly label to SR users.
                    const describedBy = [
                      hasFriendly ? friendlyId : null,
                      showRowError && pathInvalid ? rowErrorId : null,
                    ]
                      .filter((x): x is string => !!x)
                      .join(" ") || undefined
                    return (
                      <>
                        <input
                          id={`field-check-path-${fc._id}`}
                          type="text"
                          value={fc.path}
                          maxLength={MAX_FIELD_CHECK_PATH_LEN}
                          placeholder="tool_input.url"
                          aria-invalid={showRowError && pathInvalid ? "true" : undefined}
                          aria-describedby={describedBy}
                          onChange={(e) =>
                            setFieldChecks((rows) =>
                              rows.map((r) =>
                                r._id === fc._id ? { ...r, path: e.target.value } : r,
                              ),
                            )
                          }
                          className="mt-1 block w-full rounded-md border border-[var(--color-border-strong)] bg-white px-2 py-1.5 text-xs font-mono focus:border-[var(--color-border-focus)] focus:outline-none focus:ring-2 focus:ring-[var(--color-border-focus)]/40"
                        />
                        {hasFriendly && (
                          // D64 polish: the friendly resolution participates
                          // in the input's accessible description (wired via
                          // aria-describedby above) so SR users hear the
                          // friendly label after the input value, matching
                          // the chip / tree-row / expander surfaces. Visible
                          // helper text is decorative for sighted users.
                          <p
                            id={friendlyId}
                            data-testid={`field-check-path-display-label-${fc._id}`}
                            data-field-path={trimmedPath}
                            className="mt-1 text-[10.5px] text-[var(--color-text-tertiary)]"
                          >
                            {friendly}
                          </p>
                        )}
                      </>
                    )
                  })()}
                </div>
                <div className="flex-[2] min-w-[220px]">
                  <label
                    htmlFor={`field-check-desc-${fc._id}`}
                    className="block text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]"
                  >
                    {labels.fieldCheckDescription}
                  </label>
                  <input
                    id={`field-check-desc-${fc._id}`}
                    type="text"
                    value={fc.check_description}
                    maxLength={MAX_FIELD_CHECK_DESC_LEN}
                    placeholder="hostname is in allowlist"
                    aria-invalid={showRowError && descInvalid ? "true" : undefined}
                    aria-describedby={showRowError && descInvalid ? rowErrorId : undefined}
                    onChange={(e) =>
                      setFieldChecks((rows) =>
                        rows.map((r) =>
                          r._id === fc._id
                            ? { ...r, check_description: e.target.value }
                            : r,
                        ),
                      )
                    }
                    className="mt-1 block w-full rounded-md border border-[var(--color-border-strong)] bg-white px-2 py-1.5 text-xs focus:border-[var(--color-border-focus)] focus:outline-none focus:ring-2 focus:ring-[var(--color-border-focus)]/40"
                  />
                </div>
                {fieldChecks.length > 1 && (
                  <button
                    type="button"
                    aria-label={labels.fieldCheckRemove}
                    onClick={() => {
                      setFieldChecks((rows) => {
                        const removeIdx = rows.findIndex((r) => r._id === fc._id)
                        const next = rows.filter((r) => r._id !== fc._id)
                        const restore = removeIdx > 0 ? next[removeIdx - 1] : next[0]
                        queueMicrotask(() => {
                          if (restore) {
                            const el = document.getElementById(`field-check-path-${restore._id}`)
                            if (el && typeof (el as HTMLInputElement).focus === "function") {
                              (el as HTMLInputElement).focus()
                              return
                            }
                          }
                          fieldChecksAddBtnRef.current?.focus()
                        })
                        return next
                      })
                    }}
                    className="rounded-md border border-[var(--color-border-strong)] bg-white px-2 py-1.5 text-[11px] text-[var(--color-text-secondary)] hover:bg-black/[0.02] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-accent)]"
                  >
                    <span aria-hidden="true">×</span>
                  </button>
                )}
                {showRowError && rowErrorMsg && (
                  <p
                    id={rowErrorId}
                    role="alert"
                    className="w-full text-[11px] text-[var(--color-deny-fg)]"
                  >
                    {rowErrorMsg}
                  </p>
                )}
              </div>
            )
          })}
        </div>
        {showFieldChecksError && fieldChecksError && (
          <p
            id="field-checks-error"
            role="alert"
            className="text-[11px] text-[var(--color-deny-fg)]"
          >
            {fieldChecksError}
          </p>
        )}
      </div>

      {/* D57c: input_assembly select + caller_assembly_hint textarea.
          Two radio-card options (cc_stdin / caller_assembled) so the
          contract is visually distinguished from a generic enum
          dropdown — the brief explicitly calls this out as a "How
          does this verifier get its input?" question, not a
          plumbing-tax field. caller_assembled reveals the explainer
          textarea immediately so the operator does not have to find
          a separate spot for the prose. */}
      <div
        className="space-y-1.5"
        data-testid="input-assembly-section"
        role="group"
        aria-labelledby="input-assembly-label"
        aria-describedby="input-assembly-helper"
      >
        <span
          id="input-assembly-label"
          className="block text-xs font-semibold text-[var(--color-text-secondary)]"
        >
          {labels.inputAssembly}
          <span aria-hidden className="ml-1 text-[var(--color-deny-fg)]">*</span>
        </span>
        <p id="input-assembly-helper" className="text-[11px] text-[var(--color-text-tertiary)]">
          {labels.inputAssemblyHelper}
        </p>
        <div className="space-y-2" role="radiogroup" aria-labelledby="input-assembly-label">
          <InputAssemblyOption
            value="cc_stdin"
            name={inputAssemblyRadioName}
            selected={inputAssembly === "cc_stdin"}
            label={labels.inputAssemblyCcStdin}
            helper={labels.inputAssemblyCcStdinHelper}
            onSelect={() => {
              // D57c follow-up (data-loss): keep the typed hint in
              // component state across the switch (excluded from the
              // wire payload while cc_stdin is selected). The
              // operator can switch back to caller_assembled and
              // their prose is intact, no manual retype.
              //
              // D57c follow-up (a11y / WCAG 3.3.1): do NOT preempt
              // touchedHint here. The textarea's own onBlur /
              // onChange and submitAttempted already cover the
              // visibility gate; toggling touchedHint on a radio
              // click would render the "hint required" error the
              // instant the textarea is revealed by the sibling
              // option's first click.
              setInputAssembly("cc_stdin")
            }}
          />
          <InputAssemblyOption
            value="caller_assembled"
            name={inputAssemblyRadioName}
            selected={inputAssembly === "caller_assembled"}
            label={labels.inputAssemblyCallerAssembled}
            helper={labels.inputAssemblyCallerAssembledHelper}
            onSelect={() => {
              setInputAssembly("caller_assembled")
            }}
          />
        </div>
        {inputAssembly === "caller_assembled" && (
          <div
            data-testid="caller-assembly-hint-row"
            className="mt-2 space-y-1.5 rounded-md border border-[var(--color-review-fg,#b45309)]/30 bg-[var(--color-review-bg,#fffbeb)]/60 p-3"
          >
            <label
              htmlFor="caller-assembly-hint"
              className="block text-xs font-semibold text-[var(--color-text-secondary)]"
            >
              {labels.callerAssemblyHint}
              <span aria-hidden className="ml-1 text-[var(--color-deny-fg)]">*</span>
            </label>
            <textarea
              id="caller-assembly-hint"
              value={callerAssemblyHint}
              maxLength={MAX_CALLER_ASSEMBLY_HINT_LEN}
              rows={3}
              onChange={(e) => {
                setCallerAssemblyHint(e.target.value)
                if (!touchedHint) setTouchedHint(true)
              }}
              onBlur={() => setTouchedHint(true)}
              aria-invalid={showCallerAssemblyHintError ? "true" : undefined}
              aria-describedby={[
                showCallerAssemblyHintError ? "caller-assembly-hint-error" : null,
                "caller-assembly-hint-helper",
              ].filter(Boolean).join(" ")}
              placeholder={labels.callerAssemblyHintPlaceholder}
              className="block w-full rounded-md border border-[var(--color-border-strong)] bg-[var(--color-surface-input)] px-3 py-2 text-sm text-[var(--color-text-primary)] focus:border-[var(--color-border-focus)] focus:outline-none focus:ring-2 focus:ring-[var(--color-border-focus)]/40"
            />
            <p
              id="caller-assembly-hint-helper"
              className="text-[11px] text-[var(--color-text-tertiary)]"
            >
              {labels.callerAssemblyHintHelper} ({callerAssemblyHint.length}/{MAX_CALLER_ASSEMBLY_HINT_LEN})
            </p>
            {showCallerAssemblyHintError && callerAssemblyHintError && (
              <p
                id="caller-assembly-hint-error"
                role="alert"
                className="text-[11px] text-[var(--color-deny-fg)]"
              >
                {callerAssemblyHintError}
              </p>
            )}
          </div>
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

/** D57c: one of the two `input_assembly` radio cards. Renders as a
 * label-wrapped radio so a click anywhere on the card selects the
 * option; the visually-hidden native radio is the keyboard-focusable
 * element so a screen reader announces "radiogroup, X selected" /
 * "Y not selected" as expected.
 *
 * D57c follow-up: `name` is threaded in from the parent's useId()
 * so two instances of VerifierFormClient on the same page don't
 * merge into one radio group (a draft + a fresh form, or a wizard
 * with a preview). */
function InputAssemblyOption({
  value, name, selected, label, helper, onSelect,
}: {
  value: InputAssemblyValue
  name: string
  selected: boolean
  label: string
  helper: string
  onSelect: () => void
}) {
  return (
    <label
      data-testid={`input-assembly-option-${value}`}
      className={`block cursor-pointer rounded-md border p-2.5 focus-within:ring-2 focus-within:ring-[var(--color-border-focus)] focus-within:ring-offset-1 ${
        selected
          ? "border-[var(--color-accent)] bg-[var(--color-accent)]/[0.06]"
          : "border-[var(--color-border-strong)] bg-white hover:bg-black/[0.02]"
      }`}
    >
      <span className="flex items-baseline gap-2">
        <input
          type="radio"
          name={name}
          value={value}
          checked={selected}
          onChange={onSelect}
          className="sr-only"
        />
        <span
          aria-hidden
          className={`inline-block h-3 w-3 flex-shrink-0 translate-y-[1px] rounded-full border-2 ${
            selected
              ? "border-[var(--color-accent)] bg-[var(--color-accent)]"
              : "border-[var(--color-border-strong)] bg-white"
          }`}
        />
        <span className="block">
          <span className="block text-xs font-semibold text-[var(--color-text-primary)]">
            {label}
          </span>
          <span className="mt-0.5 block text-[11px] text-[var(--color-text-secondary)] leading-relaxed">
            {helper}
          </span>
        </span>
      </span>
    </label>
  )
}
