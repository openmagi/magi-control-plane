"use client"

import { useRef, useState, useTransition, type MouseEvent } from "react"
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
 *
 * D75 follow-up (setup_required parity): an earlier revision cascaded
 * silently through every member, including setup_required prebuilts
 * (citation-verify, source-allowlist) whose verifier-side config is
 * NOT defaulted. The single-policy PrebuiltToggle already enforces a
 * `setupRequired`/`enableAnyway` confirmation gate for the same case,
 * so the pack cascade now mirrors it: when at least one un-enabled
 * member needs setup, the click reveals an inline callout that lists
 * the affected members and offers Enable Anyway + Cancel.
 *
 * D75 follow-up (partial-state cross-pack reach): when a pack's
 * status is `partial` AND the off-by-default member set is non-empty
 * (some member is enabled by a different pack), the click reveals a
 * different inline callout that names the cross-pack reach
 * ("clicking will also enable N member(s) currently disabled by
 * another pack"). Operator confirms before the cascade fires. The
 * cloud's blunt-cascade contract is documented in pack.py; this UI
 * makes it visible.
 */
export interface PackToggleProps {
  packId: string
  /** Pack-level status — drives the toggle's checked state +
   * decides which cloud verb the action calls. */
  status: "all" | "partial" | "none"
  action: typeof togglePackAction
  labelOn: string
  labelOff: string
  /** D75 follow-up: list of member ids whose prebuilt spec carries
   * `setup_required=true` AND is currently disabled. Triggers a
   * confirmation callout on the OFF->ON transition. Empty by default
   * so existing user-pack code paths (no setup_required members)
   * round-trip unchanged. */
  setupRequiredMembers?: string[]
  /** D75 follow-up: i18n copy bundle for the confirm callout. Pulled
   * from the server component so the client mounts plain strings (no
   * dependency on the dict here). When `null`/undefined no callout
   * renders even if `setupRequiredMembers` is non-empty — used by
   * tests that exercise the post-click flow without the dialog. */
  confirmCopy?: {
    setupRequiredTitle: string
    setupRequiredBody: string
    setupRequiredMembersHeader: string
    partialReachTitle: string
    partialReachBody: string
    enableAnyway: string
    cancel: string
  } | null
}

export function PackToggle({
  packId, status, action, labelOn, labelOff,
  setupRequiredMembers = [],
  confirmCopy = null,
}: PackToggleProps) {
  const [pending, startTransition] = useTransition()
  const [calloutKind, setCalloutKind] = useState<
    null | "setup" | "partial-reach"
  >(null)
  const formRef = useRef<HTMLFormElement>(null)
  // A pack is "checked" when EVERY member is enabled. Partial keeps
  // the visual off so the operator's next click is enable-missing
  // semantics (handled cloud-side via plain enable, which is
  // idempotent on already-enabled members).
  const checkedNow = status === "all"
  const checked = pending ? !checkedNow : checkedNow

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

  const onClick = (e: MouseEvent<HTMLButtonElement>) => {
    e.stopPropagation()
    e.preventDefault()
    if (pending) return
    const nextEnabled = !checkedNow
    // Only intercept OFF->ON transitions: a disable cascade does not
    // need a setup-required confirm because the operator is removing
    // the (already-active) enforcement surface, not adding a new
    // inert one. Same for partial-reach: disabling is "turn the
    // intent off", no cross-pack reach concern.
    if (nextEnabled && confirmCopy) {
      if (setupRequiredMembers.length > 0) {
        setCalloutKind("setup")
        return
      }
      // Partial -> on means the cascade may re-enable members that a
      // sibling pack just disabled. Surface the reach explicitly.
      if (status === "partial") {
        setCalloutKind("partial-reach")
        return
      }
    }
    submit(nextEnabled)
  }

  const onEnableAnyway = (e: MouseEvent<HTMLButtonElement>) => {
    e.preventDefault()
    setCalloutKind(null)
    submit(true)
  }

  const onCancel = (e: MouseEvent<HTMLButtonElement>) => {
    e.preventDefault()
    setCalloutKind(null)
  }

  return (
    <div className="flex flex-col items-end gap-2">
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
      {calloutKind === "setup" && confirmCopy && (
        <div
          role="alertdialog"
          aria-label={confirmCopy.setupRequiredTitle}
          className="w-80 rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900 shadow-sm"
        >
          <p className="font-semibold mb-1">
            {confirmCopy.setupRequiredTitle}
          </p>
          <p className="mb-2 leading-relaxed">
            {confirmCopy.setupRequiredBody}
          </p>
          <p className="font-semibold mb-1">
            {confirmCopy.setupRequiredMembersHeader}
          </p>
          <ul className="mb-2 space-y-0.5 pl-3 list-disc">
            {setupRequiredMembers.map((mid) => (
              <li key={mid} className="font-mono text-[10px]">
                {mid}
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={onEnableAnyway}
              className="rounded-md bg-amber-500 px-2 py-1 font-semibold text-white hover:bg-amber-600"
            >
              {confirmCopy.enableAnyway}
            </button>
            <button
              type="button"
              onClick={onCancel}
              className="rounded-md border border-amber-500/60 bg-white px-2 py-1 font-semibold text-amber-900 hover:bg-amber-100"
            >
              {confirmCopy.cancel}
            </button>
          </div>
        </div>
      )}
      {calloutKind === "partial-reach" && confirmCopy && (
        <div
          role="alertdialog"
          aria-label={confirmCopy.partialReachTitle}
          className="w-80 rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900 shadow-sm"
        >
          <p className="font-semibold mb-1">
            {confirmCopy.partialReachTitle}
          </p>
          <p className="mb-2 leading-relaxed">
            {confirmCopy.partialReachBody}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={onEnableAnyway}
              className="rounded-md bg-amber-500 px-2 py-1 font-semibold text-white hover:bg-amber-600"
            >
              {confirmCopy.enableAnyway}
            </button>
            <button
              type="button"
              onClick={onCancel}
              className="rounded-md border border-amber-500/60 bg-white px-2 py-1 font-semibold text-amber-900 hover:bg-amber-100"
            >
              {confirmCopy.cancel}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
