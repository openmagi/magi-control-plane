"use client"

import { useRef, useTransition, type MouseEvent } from "react"
import type {
  toggleBuiltinVerifierAction,
  toggleCustomVerifierAction,
} from "../actions"

export interface VerifierToggleProps {
  presetId: string
  step: string | null
  isCustom: boolean
  enabled: boolean
  builtinAction: typeof toggleBuiltinVerifierAction
  customAction: typeof toggleCustomVerifierAction
  labelOn: string
  labelOff: string
}

/**
 * Same visual spec as PresetToggle (24×44 track, 20×20 thumb pinned
 * absolutely, translateX inline) — wired to two different actions:
 *
 *   built-in: cookie-only disabled set (toggleBuiltinVerifierAction)
 *   custom:   FormData → /tenants/verifiers/{step}/enabled endpoint
 *             (toggleCustomVerifierAction)
 *
 * The custom path uses a hidden form so the server action receives
 * FormData (matching the action signature) without re-architecting the
 * existing builtin cookie path.
 */
export function VerifierToggle({
  presetId, step, isCustom, enabled,
  builtinAction, customAction,
  labelOn, labelOff,
}: VerifierToggleProps) {
  const [pending, startTransition] = useTransition()
  const formRef = useRef<HTMLFormElement>(null)
  const checked = pending ? !enabled : enabled

  const onClick = (e: MouseEvent<HTMLButtonElement>) => {
    e.stopPropagation()
    e.preventDefault()
    if (isCustom && step != null) {
      const form = formRef.current
      if (!form) return
      const next = (!enabled).toString()
      const enabledInput = form.elements.namedItem("enabled") as HTMLInputElement | null
      if (enabledInput) enabledInput.value = next
      startTransition(async () => {
        const fd = new FormData(form)
        await customAction(fd)
      })
      return
    }
    startTransition(async () => {
      await builtinAction(presetId)
    })
  }

  return (
    <>
      {isCustom && step != null && (
        <form ref={formRef} className="hidden">
          <input type="hidden" name="step" value={step} />
          <input type="hidden" name="enabled" value={(!enabled).toString()} />
        </form>
      )}
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
