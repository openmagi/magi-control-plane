"use client"

import { useTransition } from "react"
import { cn } from "@/lib/cn"
import type { togglePresetAction } from "../actions"

export interface PresetToggleProps {
  presetId: string
  enabled: boolean
  /** Server action — receives the preset id, flips the cookie set. */
  action: typeof togglePresetAction
  labelOn: string
  labelOff: string
}

/**
 * iOS-style switch. Optimistic local state via useTransition so the
 * thumb slides immediately on tap; server action updates the cookie
 * + triggers a path revalidate to keep the SSR HTML truthful.
 */
export function PresetToggle({
  presetId, enabled, action, labelOn, labelOff,
}: PresetToggleProps) {
  const [pending, startTransition] = useTransition()
  // Optimistic — render the inverse while the action is in-flight.
  const renderEnabled = pending ? !enabled : enabled
  return (
    <button
      type="button"
      role="switch"
      aria-checked={renderEnabled}
      aria-label={renderEnabled ? labelOn : labelOff}
      disabled={pending}
      onClick={() => {
        startTransition(async () => {
          await action(presetId)
        })
      }}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full",
        "transition-colors duration-150 cursor-pointer",
        "outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-border-focus)]/30 focus-visible:ring-offset-2",
        renderEnabled
          ? "bg-[var(--color-accent)]"
          : "bg-gray-300 hover:bg-gray-400",
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          "inline-block h-4 w-4 rounded-full bg-white shadow",
          "transition-transform duration-150",
          renderEnabled ? "translate-x-[18px]" : "translate-x-0.5",
        )}
      />
    </button>
  )
}
