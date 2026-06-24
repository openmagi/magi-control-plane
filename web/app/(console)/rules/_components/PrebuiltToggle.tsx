"use client"

import { useRef, useState, useTransition, type MouseEvent } from "react"
import type { togglePrebuiltAction } from "../actions"

/**
 * D60: large on/off toggle on each prebuilt card.
 *
 * Two modes:
 *
 *   - simple (setupRequired = false) — clicking the toggle directly
 *     flips the prebuilt enable/disable.
 *
 *   - setup-required (setupRequired = true, currently OFF) — clicking
 *     the toggle reveals an inline callout that gives the operator a
 *     "Configure" link (jumps to wizard) and an "Enable anyway"
 *     button (proceeds with the toggle). This prevents the
 *     citation_verify / source_allowlist trap where the toggle goes
 *     on but the policy is inert because the operator-supplied knobs
 *     are blank.
 *
 * The inline callout intentionally does not block disable: a
 * setup-required prebuilt that is already ON disables with one click,
 * since the setup-required gate is a "first time" check.
 *
 * Visual contract mirrors PolicyToggle (same h-6 w-11 dimensions,
 * same focus/disabled treatment) so the section reads as one
 * consistent control surface.
 */

export interface PrebuiltToggleProps {
  prebuiltId: string
  enabled: boolean
  setupRequired: boolean
  setupHint: string
  configureHref: string
  action: typeof togglePrebuiltAction
  /** Operator-readable labels for the toggle role=switch. */
  labelOn: string
  labelOff: string
  /** Inline-callout copy. */
  copy: {
    setupRequired: string
    configure: string
    enableAnyway: string
  }
}

export function PrebuiltToggle({
  prebuiltId,
  enabled,
  setupRequired,
  setupHint,
  configureHref,
  action,
  labelOn,
  labelOff,
  copy,
}: PrebuiltToggleProps) {
  const [pending, startTransition] = useTransition()
  const [calloutOpen, setCalloutOpen] = useState(false)
  const formRef = useRef<HTMLFormElement>(null)
  // Optimistic UI: while the request is in flight, render the target
  // state so the operator sees instant feedback. The server action
  // revalidates `/rules` on completion so the authoritative state
  // overwrites the optimistic one on the next paint.
  const checked = pending ? !enabled : enabled

  const submit = (nextEnabled: boolean) => {
    const form = formRef.current
    if (!form) return
    const enabledInput = form.elements.namedItem("enabled") as
      HTMLInputElement | null
    if (enabledInput) enabledInput.value = nextEnabled.toString()
    startTransition(async () => {
      const fd = new FormData(form)
      await action(fd)
    })
  }

  const onToggleClick = (e: MouseEvent<HTMLButtonElement>) => {
    e.stopPropagation()
    e.preventDefault()
    if (pending) return
    // Setup-required gate: only fire on the OFF -> ON transition.
    // Disabling stays one-click.
    if (setupRequired && !enabled) {
      setCalloutOpen(true)
      return
    }
    submit(!enabled)
  }

  const onEnableAnyway = (e: MouseEvent<HTMLButtonElement>) => {
    e.preventDefault()
    setCalloutOpen(false)
    submit(true)
  }

  return (
    <div className="flex flex-col items-end gap-2">
      <form ref={formRef} className="hidden">
        <input type="hidden" name="id" value={prebuiltId} />
        <input type="hidden" name="enabled" value={(!enabled).toString()} />
      </form>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={checked ? labelOn : labelOff}
        aria-busy={pending || undefined}
        disabled={pending}
        onClick={onToggleClick}
        className={`relative inline-block h-6 w-11 shrink-0 rounded-full transition-colors duration-200 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]/45 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60 ${
          checked ? "bg-[var(--color-accent)]" : "bg-gray-300"
        }`}
      >
        <span
          aria-hidden="true"
          style={{
            transform: `translateX(${checked ? "20px" : "0"})`,
            boxShadow: "0 1px 2px rgba(15,23,42,0.18)",
          }}
          className="absolute top-0.5 left-0.5 inline-block h-5 w-5 rounded-full bg-white transition-transform duration-200 ease-out"
        />
      </button>
      {calloutOpen && setupRequired && (
        <div
          role="alertdialog"
          aria-label={copy.setupRequired}
          className="w-72 rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900 shadow-sm"
        >
          <p className="font-semibold mb-1">{copy.setupRequired}</p>
          <p className="mb-2 leading-relaxed">{setupHint}</p>
          <div className="flex flex-wrap items-center gap-2">
            <a
              href={configureHref}
              className="rounded-md border border-amber-500/60 bg-white px-2 py-1 font-semibold text-amber-900 hover:bg-amber-100"
            >
              {copy.configure}
            </a>
            <button
              type="button"
              onClick={onEnableAnyway}
              className="rounded-md bg-amber-500 px-2 py-1 font-semibold text-white hover:bg-amber-600"
            >
              {copy.enableAnyway}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
