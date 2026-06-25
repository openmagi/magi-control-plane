"use client"

import { useRef, useTransition, type MouseEvent } from "react"
import type { togglePackAction } from "../actions"

/**
 * D75: large on/off toggle on each policy-pack card. Visual + behaviour
 * mirror PolicyToggle / PrebuiltToggle so the section reads as one
 * consistent control surface.
 *
 * One-click semantics: clicking the switch posts to either
 * `enablePack` (current status `none` or `partial`) or `disablePack`
 * (current status `all`). The cloud's enable handler is idempotent so
 * a double-click while the request is in flight is safe. Per-member
 * cascade errors land in the response envelope; the dashboard reads
 * them after the revalidate refresh.
 */
export interface PackToggleProps {
  packId: string
  /** Pack-level status — drives the toggle's checked state +
   * decides which cloud verb the action calls. */
  status: "all" | "partial" | "none"
  action: typeof togglePackAction
  labelOn: string
  labelOff: string
}

export function PackToggle({
  packId, status, action, labelOn, labelOff,
}: PackToggleProps) {
  const [pending, startTransition] = useTransition()
  const formRef = useRef<HTMLFormElement>(null)
  // A pack is "checked" when EVERY member is enabled. Partial keeps
  // the visual off so the operator's next click is enable-missing
  // semantics (handled cloud-side via plain enable, which is
  // idempotent on already-enabled members).
  const checkedNow = status === "all"
  const checked = pending ? !checkedNow : checkedNow

  const onClick = (e: MouseEvent<HTMLButtonElement>) => {
    e.stopPropagation()
    e.preventDefault()
    if (pending) return
    const form = formRef.current
    if (!form) return
    const next = (!checkedNow).toString()
    const enabledInput = form.elements.namedItem("enabled") as
      HTMLInputElement | null
    if (enabledInput) enabledInput.value = next
    startTransition(async () => {
      const fd = new FormData(form)
      await action(fd)
    })
  }

  return (
    <>
      <form ref={formRef} className="hidden">
        <input type="hidden" name="id" value={packId} />
        <input
          type="hidden"
          name="enabled"
          value={(!checkedNow).toString()}
        />
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
