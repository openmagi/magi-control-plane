"use client"

/**
 * D57g hotfix: wrapper client component that owns BOTH the PolicyBuilder
 * (raw IR editor) AND the "Continue in conversation" HandoffLink in a
 * single client tree. The raw editor's draft lives in PolicyBuilder
 * state; without a shared parent the sibling HandoffLink rendered by
 * `AuthoringShell` had no access to it, so clicking the link from the
 * advanced mode silently dropped the operator's entire authored draft
 * (id / description / trigger / requires / action all gone — the seed
 * payload's `draft_ir` field was always `null`).
 *
 * The wrapper:
 *   1. Renders the HandoffLink in the standard top-right slot (the
 *      page.tsx AuthoringShell suppresses its own copy when this
 *      wrapper is mounted via `handoffOrigin === "advanced-inline"`).
 *   2. Holds a ref to the live PolicyBuilder draft. PolicyBuilder
 *      reports every state transition via `onDraftChange`; the ref
 *      is consulted at click time by HandoffLink's `getDraft`.
 *
 * Why a ref (not state): rerendering the wrapper on every keystroke
 * would defeat React 18's concurrent batching for PolicyBuilder's
 * inputs. The HandoffLink doesn't need a re-render — the closure
 * runs at click time, reads the current ref value, encodes the seed.
 *
 * Sub-path imports ONLY (NOT from "@/components/ui"). The barrel
 * pulls a server-only chain into the client bundle and breaks build.
 */

import { useCallback, useMemo, useRef } from "react"
import type { PolicyDraft } from "@/lib/policy-builder"
import PolicyBuilder from "@/components/PolicyBuilder"
import HandoffLink from "./HandoffLink"
import { DryRunPanel } from "../../_components/DryRunPanel"

export interface AdvancedAuthoringProps {
  locale: "ko" | "en"
  /** Server action threaded from page.tsx; same shape PolicyBuilder
   *  expects. */
  saveAction: (formData: FormData) => Promise<void> | void
  /** Initial draft pulled from `?draft=` (or `null` for blank). */
  initial: PolicyDraft | null
  /** Wired verifier steps (datalist + inline validation). */
  wiredSteps: string[]
  /** Vendor catalog step names (P8 preview-prefix coaching). */
  vendorSteps: string[]
  /** Labels object the PolicyBuilder owns. We forward through. */
  labels: React.ComponentProps<typeof PolicyBuilder>["labels"]
}

/** Coerce the typed PolicyDraft into the loose record shape the
 *  HandoffLink encoder + cloud `_sanitize_draft_so_far` accept.
 *  We do NOT include `gate_binary` / `on_signature_invalid` here —
 *  the cloud sanitiser drops them anyway, but keeping them out
 *  shortens the wire and avoids confusing the merge layer if the
 *  shape ever drifts.
 *
 *  We DO forward the meaningful, operator-authored fields:
 *    id / description / version / trigger / requires / action /
 *    sentinel_re (the legal-vertical residue field).
 */
function draftToSeedShape(d: PolicyDraft): Record<string, unknown> | null {
  const out: Record<string, unknown> = {}
  if (d.id) out.id = d.id
  if (d.description) out.description = d.description
  if (d.version) out.version = d.version
  if (d.trigger) out.trigger = d.trigger
  if (d.requires && d.requires.length > 0) out.requires = d.requires
  if (d.action) out.action = d.action
  if (d.sentinel_re) out.sentinel_re = d.sentinel_re
  return Object.keys(out).length > 0 ? out : null
}

export default function AdvancedAuthoring({
  locale, saveAction, initial, wiredSteps, vendorSteps, labels,
}: AdvancedAuthoringProps) {
  const draftRef = useRef<PolicyDraft | null>(initial ?? null)

  const handleDraftChange = useCallback((d: PolicyDraft) => {
    draftRef.current = d
  }, [])

  // HandoffLink reads this at click time. Returning `null` falls
  // through to the canned intro on the conversational shell; we
  // ONLY return null when the draft is genuinely empty so the
  // operator at least gets the empty-state intro when they have not
  // typed anything yet.
  const getDraft = useCallback((): Record<string, unknown> | null => {
    const cur = draftRef.current
    if (!cur) return null
    return draftToSeedShape(cur)
  }, [])

  const headerHandoff = useMemo(() => (
    <div className="flex justify-end mb-2" data-testid="advanced-handoff-row">
      <HandoffLink
        locale={locale}
        origin="advanced"
        getDraft={getDraft}
        testId="handoff-continue-in-chat-advanced"
      />
    </div>
  ), [locale, getDraft])

  // Q90: dryRunSlot lives HERE (client side), not on the page.tsx call
  // site. The previous page.tsx call site passed an inline render-prop
  // closure across the server -> client boundary, which React 18 RSC
  // refuses ("Functions cannot be passed directly to Client Components
  // unless you explicitly expose it by marking it with 'use server'"),
  // crashing /policies/new?mode=advanced with the digest 1331850167
  // 500. The closure is recreated on every render but `PolicyBuilder`
  // only invokes it inside its own memoized render path, so the
  // memo cost is unchanged.
  const dryRunSlot = useCallback(({ draft, isValid }: {
    draft: PolicyDraft
    isValid: boolean
  }) => (
    <DryRunPanel
      locale={locale}
      ir={isValid ? (draft as unknown as Record<string, unknown>) : null}
      disabled={!isValid}
      action={(draft.action ?? "audit") as "block" | "ask" | "audit" | "strip"}
    />
  ), [locale])

  return (
    <div className="space-y-3" data-testid="advanced-authoring-shell">
      {headerHandoff}
      <PolicyBuilder
        submitAction={saveAction}
        initial={initial}
        wiredSteps={wiredSteps}
        vendorSteps={vendorSteps}
        labels={labels}
        dryRunSlot={dryRunSlot}
        onDraftChange={handleDraftChange}
      />
    </div>
  )
}
