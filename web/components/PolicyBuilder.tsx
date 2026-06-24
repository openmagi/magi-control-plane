"use client"
import { useEffect, useMemo, useState, useTransition } from "react"
import {
  DEFAULT_DRAFT, validateDraft, previewManagedSettings,
  type PolicyDraft, type EventKind, type Action,
} from "@/lib/policy-builder"
import { Button } from "@/components/ui/Button"
import { Card, CardHeader } from "@/components/ui/Card"
import { CodeBlock } from "@/components/ui/Code"
import { Input, Textarea } from "@/components/ui/Input"
import { Select } from "@/components/ui/Select"

type Props = {
  submitAction: (formData: FormData) => Promise<void> | void
  initial?: PolicyDraft | null
  /** Known wired verifier steps (datalist). drives compiler-step guessing UX
   * AND P8 authoring-time fail-closed check — a step not in this list (and
   * not in `vendorSteps` either) gets a red inline error before submit. */
  wiredSteps?: string[]
  /** P8: vendor catalog step names (preview-only, no live verifier). When
   * an author types one of these without the `preview:` prefix the inline
   * error tells them to enable it under /presets or use the prefix —
   * matches the backend 422 the cloud would return. */
  vendorSteps?: string[]
  labels: {
    irFields: string
    compiledPreview: string
    compiledPreviewHint: string
    id: string
    description: string
    triggerEvent: string
    triggerMatcher: string
    onMissing: string
    sentinelRe: string
    sentinelReHint: string
    requires: string
    addRequirement: string
    removeRequirement: string
    source: string
    save: string
    saving: string
    fixIssueOne: string   // singular: "Fix 1 validation issue"
    fixIssueMany: string  // plural template, `{n}` interpolated: "Fix {n} validation issues"
    unsavedWarning: string
    placeholderId: string
    placeholderMatcher: string
  }
}

export default function PolicyBuilder({
  submitAction, initial, wiredSteps = [], vendorSteps = [], labels,
}: Props) {
  const [draft, setDraft] = useState<PolicyDraft>(initial ?? DEFAULT_DRAFT)
  const [submitted, setSubmitted] = useState(false)
  const [pending, startTransition] = useTransition()

  const errors = useMemo(
    () => validateDraft(draft, {
      availableSteps: wiredSteps, vendorStepSet: vendorSteps,
    }),
    [draft, wiredSteps, vendorSteps],
  )
  const errorByField = useMemo(() => {
    const m = new Map<string, string>()
    for (const e of errors) m.set(e.field, e.message)
    return m
  }, [errors])

  const preview = useMemo(() => {
    try { return JSON.stringify(previewManagedSettings(draft), null, 2) }
    catch { return "(preview error)" }
  }, [draft])

  // Warn before navigating away with unsaved changes
  const dirty = useMemo(
    () => JSON.stringify(draft) !== JSON.stringify(initial ?? DEFAULT_DRAFT),
    [draft, initial],
  )
  useEffect(() => {
    if (!dirty || submitted) return
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = labels.unsavedWarning
      return labels.unsavedWarning
    }
    window.addEventListener("beforeunload", handler)
    return () => window.removeEventListener("beforeunload", handler)
  }, [dirty, submitted, labels.unsavedWarning])

  function update<K extends keyof PolicyDraft>(k: K, v: PolicyDraft[K]) {
    setDraft(d => ({ ...d, [k]: v }))
  }
  function updateTrigger<K extends keyof PolicyDraft["trigger"]>(
    k: K, v: PolicyDraft["trigger"][K],
  ) {
    setDraft(d => ({ ...d, trigger: { ...d.trigger, [k]: v } }))
  }

  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setSubmitted(true)
    if (errors.length > 0) {
      // focus first error
      const first = errors[0]?.field
      if (first) {
        const el = document.getElementById(`pb-${first}`)
        if (el) (el as HTMLElement).focus()
      }
      return
    }
    const fd = new FormData(e.currentTarget)
    fd.set("draft_json", JSON.stringify(draft))
    startTransition(() => { submitAction(fd) })
  }

  return (
    <form
      onSubmit={onSubmit}
      className="grid grid-cols-1 lg:grid-cols-2 gap-4"
    >
      <Card className="space-y-4">
        <h2 className="text-md font-semibold m-0">{labels.irFields}</h2>

        <Input
          id="pb-id"
          label={labels.id}
          value={draft.id}
          onChange={e => update("id", e.target.value)}
          placeholder={labels.placeholderId}
          required
          maxLength={128}
          spellCheck={false}
          autoComplete="off"
          error={errorByField.get("id")}
        />

        <Input
          id="pb-description"
          label={labels.description}
          value={draft.description}
          onChange={e => update("description", e.target.value)}
          maxLength={2000}
          autoComplete="off"
        />

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <Select
            id="pb-trigger-event"
            label={labels.triggerEvent}
            value={draft.trigger.event}
            onChange={e => updateTrigger("event", e.target.value as EventKind)}
            options={[
              { value: "PreToolUse",  label: "PreToolUse"  },
              { value: "PostToolUse", label: "PostToolUse" },
              { value: "Stop",        label: "Stop"        },
            ]}
          />
          <Input
            id="pb-trigger-matcher"
            label={labels.triggerMatcher}
            value={draft.trigger.matcher}
            onChange={e => updateTrigger("matcher", e.target.value)}
            placeholder={labels.placeholderMatcher}
            required
            spellCheck={false}
            autoComplete="off"
            error={errorByField.get("matrix")}
          />
        </div>

        <Select
          id="pb-action"
          label={labels.onMissing}
          value={draft.action}
          onChange={e => update("action", e.target.value as Action)}
          options={[
            { value: "block", label: "block" },
            { value: "ask",   label: "ask"   },
            { value: "audit", label: "audit" },
          ]}
          error={errorByField.get("matrix")}
        />

        <Textarea
          id="pb-sentinel_re"
          label={labels.sentinelRe}
          helper={labels.sentinelReHint}
          rows={3}
          value={draft.sentinel_re ?? ""}
          onChange={e => update("sentinel_re", e.target.value || null)}
          maxLength={2000}
          spellCheck={false}
          autoComplete="off"
          monospace
          error={errorByField.get("sentinel_re")}
        />

        <fieldset className="border border-[var(--color-border-subtle)] rounded-md p-3">
          <legend className="text-xs text-[var(--color-text-tertiary)] px-1">
            {labels.requires}
          </legend>
          <div className="space-y-2">
            {draft.requires.map((r, i) => {
              // PolicyBuilder edits step-kind rows inline. Non-step kinds
              // (regex/llm_critic/shacl) are authored via the Guided
              // wizard or raw JSON IR. we just show a read-only chip.
              const kind = ("kind" in r ? r.kind : "step")
              const step = ("step" in r ? r.step : "")
              const verdict = ("verdict" in r ? r.verdict : "pass")
              if (kind !== "step") {
                return (
                  <div key={i} className="rounded-md border border-black/[0.08] bg-gray-50 px-3 py-2 text-xs text-[var(--color-text-secondary)]">
                    <span className="font-mono">{kind}</span>. edit via the Guided wizard or raw IR mode.
                  </div>
                )
              }
              const stepErr = errorByField.get(`requires[${i}].step`)
              return (
              <div key={i} className="space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                <input
                  list="pb-wired-steps"
                  className="h-9 px-3 text-sm rounded-md min-w-[160px] flex-1 bg-[var(--color-surface-input)] border border-[var(--color-border-strong)] text-[var(--color-text-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--color-border-focus)]/40"
                  type="text"
                  value={step}
                  placeholder="step"
                  spellCheck={false}
                  autoComplete="off"
                  aria-invalid={stepErr ? true : undefined}
                  onChange={e => {
                    const next = [...draft.requires]
                    next[i] = { kind: "step", step: e.target.value, verdict }
                    update("requires", next)
                  }}
                />
                <input
                  className="h-9 px-3 text-sm rounded-md w-24 bg-[var(--color-surface-input)] border border-[var(--color-border-strong)] text-[var(--color-text-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--color-border-focus)]/40"
                  type="text"
                  value={verdict}
                  placeholder="verdict"
                  spellCheck={false}
                  autoComplete="off"
                  onChange={e => {
                    const next = [...draft.requires]
                    next[i] = { kind: "step", step, verdict: e.target.value }
                    update("requires", next)
                  }}
                />
                {draft.requires.length > 1 && (
                  <Button
                    type="button"
                    variant="danger"
                    size="sm"
                    aria-label={`${labels.removeRequirement} ${i + 1}`}
                    onClick={() => {
                      const next = draft.requires.filter((_, j) => j !== i)
                      update("requires", next)
                    }}
                  >
                    ×
                  </Button>
                )}
                </div>
                {stepErr && (
                  <p role="alert" className="text-xs text-[var(--color-deny-fg)]">
                    {stepErr}
                  </p>
                )}
              </div>
              )
            })}
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() =>
                update("requires",
                  [...draft.requires, { kind: "step", step: "", verdict: "pass" }])
              }
            >
              + {labels.addRequirement}
            </Button>
            {wiredSteps.length > 0 && (
              <datalist id="pb-wired-steps">
                {wiredSteps.map(s => <option key={s} value={s} />)}
              </datalist>
            )}
            {errorByField.get("requires") && (
              <p role="alert" className="text-xs text-[var(--color-deny-fg)]">
                {errorByField.get("requires")}
              </p>
            )}
          </div>
        </fieldset>

        <Select
          id="pb-source"
          name="source"
          label={labels.source}
          defaultValue="org"
          options={[
            { value: "platform", label: "platform" },
            { value: "org",      label: "org"      },
            { value: "bot",      label: "bot"      },
            { value: "user",     label: "user"     },
            { value: "session",  label: "session"  },
          ]}
        />

        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="submit"
            variant="primary"
            disabled={pending}
            aria-busy={pending}
          >
            {pending ? `${labels.saving}…` : labels.save}
          </Button>
          {submitted && errors.length > 0 && (
            <span role="status" className="text-xs text-[var(--color-deny-fg)]">
              {errors.length === 1 ? labels.fixIssueOne : labels.fixIssueMany.replace("{n}", String(errors.length))}
            </span>
          )}
        </div>
      </Card>

      <Card>
        <CardHeader
          title={labels.compiledPreview}
          subtitle={labels.compiledPreviewHint}
        />
        <CodeBlock maxHeight="60vh">{preview}</CodeBlock>
      </Card>
    </form>
  )
}
