"use client"

import { useTransition, type MouseEvent } from "react"
import { Switch } from "@/components/ui/Switch"

export interface PolicyToggleProps {
  policyId: string
  enabled: boolean
  /** Server action — takes (id, enabled) and PUTs the new state. */
  action: (formData: FormData) => Promise<void>
  labelOn: string
  labelOff: string
}

/**
 * Bridge between the FormData-based policy toggle server action and
 * the (next: boolean) => Promise<void> shape that <Switch> takes.
 *
 * We keep this here (not in /components/ui/) because it's specific to
 * the policies page's toggleEnabled action contract.
 */
export function PolicyToggle({
  policyId, enabled, action, labelOn, labelOff,
}: PolicyToggleProps) {
  return (
    <Switch
      checked={enabled}
      labelOn={labelOn}
      labelOff={labelOff}
      onToggle={async (next: boolean) => {
        const fd = new FormData()
        fd.set("id", policyId)
        fd.set("enabled", next ? "true" : "false")
        await action(fd)
      }}
    />
  )
}
