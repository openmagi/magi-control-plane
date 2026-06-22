"use client"

import { useRef, useTransition, type MouseEvent } from "react"
import type { togglePolicyAction } from "../actions"

export interface PolicyToggleProps {
  policyId: string
  enabled: boolean
  action: typeof togglePolicyAction
  labelOn: string
  labelOff: string
}

/**
 * Toggle for a tenant policy row on the /rules page. Same visual spec
 * as VerifierToggle; calls `togglePolicyAction(formData)`.
 */
export function PolicyToggle({
  policyId, enabled, action, labelOn, labelOff,
}: PolicyToggleProps) {
  const [pending, startTransition] = useTransition()
  const formRef = useRef<HTMLFormElement>(null)
  const checked = pending ? !enabled : enabled

  const onClick = (e: MouseEvent<HTMLButtonElement>) => {
    e.stopPropagation()
    e.preventDefault()
    const form = formRef.current
    if (!form) return
    const next = (!enabled).toString()
    const enabledInput = form.elements.namedItem("enabled") as HTMLInputElement | null
    if (enabledInput) enabledInput.value = next
    startTransition(async () => {
      const fd = new FormData(form)
      await action(fd)
    })
  }

  return (
    <>
      <form ref={formRef} className="hidden">
        <input type="hidden" name="id" value={policyId} />
        <input type="hidden" name="enabled" value={(!enabled).toString()} />
      </form>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={checked ? labelOn : labelOff}
        aria-busy={pending || undefined}
        disabled={pending}
        onClick={onClick}
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
    </>
  )
}
