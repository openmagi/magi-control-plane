"use client"

import { useRef, useState, useTransition, type MouseEvent } from "react"
import type { togglePrebuiltAction } from "../actions"

/**
 * D60 → D82d simplification.
 *
 * Plain on / off switch. The setup-required inline callout that the
 * D60 revision shipped repeatedly came up as confusing UX in
 * screenshot reviews. The setup affordance moved to a separate Setup
 * button on the row (rendered by PrebuiltRow when the prebuilt needs
 * it). This component now only owns the toggle visual + the
 * optimistic submission lifecycle.
 *
 * Visual contract mirrors PolicyToggle (same h-6 w-11 dimensions,
 * same focus / disabled treatment).
 */

export interface PrebuiltToggleProps {
  prebuiltId: string
  enabled: boolean
  action: typeof togglePrebuiltAction
  /** Operator-readable labels for the toggle role=switch. */
  labelOn: string
  labelOff: string
  /** Inline-callout copy (transport error message only after the
   *  setup-required popover was removed). */
  copy: {
    transportError: string
  }
}

export function PrebuiltToggle({
  prebuiltId,
  enabled,
  action,
  labelOn,
  labelOff,
  copy,
}: PrebuiltToggleProps) {
  const [pending, startTransition] = useTransition()
  const [transportError, setTransportError] = useState(false)
  const formRef = useRef<HTMLFormElement>(null)
  const checked = pending ? !enabled : enabled

  const submit = (nextEnabled: boolean) => {
    const form = formRef.current
    if (!form) return
    const enabledInput = form.elements.namedItem("enabled") as
      HTMLInputElement | null
    if (enabledInput) enabledInput.value = nextEnabled.toString()
    setTransportError(false)
    startTransition(async () => {
      const fd = new FormData(form)
      try {
        await action(fd)
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e)
        if (!msg.includes("NEXT_REDIRECT")) {
          setTransportError(true)
        }
      }
    })
  }

  const onToggleClick = (e: MouseEvent<HTMLButtonElement>) => {
    e.stopPropagation()
    e.preventDefault()
    if (pending) return
    submit(!enabled)
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
      {transportError && (
        <p
          role="status"
          aria-live="polite"
          className="max-w-[18rem] text-[11px] text-red-700"
        >
          {copy.transportError}
        </p>
      )}
    </div>
  )
}
