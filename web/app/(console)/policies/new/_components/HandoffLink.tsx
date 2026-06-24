"use client"

/**
 * D57g: "Continue in conversation" handoff link.
 *
 * Renders a small text link in the top-right of each authoring screen
 * (wizard step 1-6, raw editor, Step 6 review). Click serializes the
 * current authoring state into a base64 URL param and redirects to
 * /policies/new?mode=conversational&seed=<...>. The conversational
 * client reads ?seed= on mount, POSTs to /api/policies/handoff-context,
 * and mounts the response as the first assistant turn instead of the
 * canned intro.
 *
 * Brief: NEVER expose internal terms (the link's label is plain user
 * vocabulary). The serialised seed itself is opaque base64 — internal
 * field names ride through the wire but never reach the rendered chat.
 *
 * Why this is a client component:
 *   - The wizard chrome (page.tsx) is a server component but the
 *     wizard's URL state is the source of truth for what gets
 *     serialised. Reading URL state from the client (via
 *     `useSearchParams`) keeps the seed in sync with whatever the
 *     operator just edited, even after a step transition that has
 *     not yet rewritten the URL via the form action's redirect.
 *   - The advanced (raw editor) mode does NOT round-trip its draft
 *     through the URL. The HandoffLink in that view reads from the
 *     parent-supplied `getDraft` callback so the live in-memory edit
 *     is captured at click time.
 *   - Base64 encoding lives in `btoa`, which is a browser primitive.
 *
 * Sub-path imports ONLY (NOT from "@/components/ui"). The barrel pulls
 * a server-only chain into the client bundle and breaks build.
 */

import Link from "next/link"
import { useCallback, useMemo } from "react"
import { useSearchParams } from "next/navigation"
import { ChatBubbleLeftRightIcon } from "@heroicons/react/24/outline"
import { translate } from "@/lib/i18n/dict"
import { encodeSeed, decodeSeed } from "./handoff-seed"
export { encodeSeed, decodeSeed } from "./handoff-seed"

export type HandoffLinkProps = {
  locale: "ko" | "en"
  /** Where this link is rendered. Used for analytics + the seed
   *  payload so the backend serializer can pick the right summary
   *  framing. Optional; defaults to "guided" when reading the URL
   *  state. */
  origin?: "guided" | "advanced" | "review"
  /** Read-only access to the current raw editor draft. Only used by
   *  the advanced-mode link where the draft lives in client state,
   *  not on the URL. The guided wizard reads from the URL directly
   *  via `useSearchParams`, so this can be left undefined there. */
  getDraft?: () => Record<string, unknown> | null
  /** Optional extra className for layout. */
  className?: string
  /** Test hook. */
  testId?: string
}

/** Subset of URL params the guided wizard ferries between steps. We
 *  list them explicitly so a future URL key cannot silently smuggle a
 *  non-wizard field into the seed payload.
 *
 *  D66 widened the list with the run_command archetype keys (Step 4b)
 *  so a wizard mid-flight on a RunCommandPolicy actually carries its
 *  body / runtime / args / timeout / fail_closed / script_id /
 *  scriptName into the conversational seed. Without these the
 *  backend serializer would receive `action=run_command` with no
 *  body fields and the assistant would have to re-ask for the
 *  command from scratch. */
const WIZARD_URL_KEYS = [
  "lifecycle", "conditionKind", "toolScope",
  "fetchDomain", "allowlist", "pattern", "llmCriterion",
  "evidence_refs", "shaclTtl", "action",
  "injectTemplate", "injectLabelKo", "injectLabelEn",
  "rewriterKind", "rewriterField", "rewriterPrefix",
  "rewriterStripRepeat", "rewriterFrom", "rewriterTo",
  "rewriterPattern", "rewriterReplacement", "rewriterCount",
  // D66: run_command archetype URL keys (Step4bRunCommandFields).
  "runCommandMode", "runCommandRuntime", "runCommandBody",
  "runCommandScriptId", "runCommandScriptName",
  "runCommandArgs", "runCommandTimeoutMs", "runCommandFailClosed",
  "id", "description",
] as const

export default function HandoffLink({
  locale, origin, getDraft, className, testId,
}: HandoffLinkProps) {
  const t = useMemo(
    () => (key: import("@/lib/i18n/dict").TKey) => translate(locale, key),
    [locale],
  )
  const params = useSearchParams()

  const buildHref = useCallback((): string => {
    // 1. wizard state: read every recognised URL key. Unknown / empty
    //    values are skipped so a non-wizard URL param (e.g. `?msg=...`)
    //    cannot ride along into the seed.
    const wizard: Record<string, unknown> = {}
    if (params) {
      for (const key of WIZARD_URL_KEYS) {
        const v = params.get(key)
        if (v && v.trim()) {
          // `evidence_refs` is a CSV in the URL; the serializer accepts
          // both shapes ("a,b,c" string or ["a","b","c"] array), but
          // canonicalising to an array here matches the
          // `_draft_from_wizard_state` expectation.
          if (key === "evidence_refs") {
            const parts = v.split(",").map((s) => s.trim()).filter(Boolean)
            if (parts.length > 0) {
              wizard.evidenceRefs = parts
            }
            continue
          }
          wizard[key] = v
        }
      }
    }
    // 2. raw editor draft (advanced mode only).
    let draftIr: Record<string, unknown> | null = null
    if (getDraft) {
      try {
        draftIr = getDraft()
      } catch {
        draftIr = null
      }
    }
    const payload: {
      wizard_state: Record<string, unknown> | null
      draft_ir: Record<string, unknown> | null
      origin?: string
    } = {
      wizard_state: Object.keys(wizard).length > 0 ? wizard : null,
      draft_ir: draftIr && Object.keys(draftIr).length > 0 ? draftIr : null,
    }
    if (origin) payload.origin = origin
    const seed = encodeSeed(payload)
    return `/policies/new?mode=conversational&seed=${encodeURIComponent(seed)}`
  }, [getDraft, origin, params])

  return (
    <Link
      href={buildHref()}
      data-testid={testId ?? "handoff-continue-in-chat"}
      data-handoff-origin={origin ?? "guided"}
      aria-label={t("newPolicy.handoff.continueInChat.aria")}
      className={
        "inline-flex items-center gap-1 text-sm text-[var(--color-text-secondary)] " +
        "hover:text-[var(--color-accent)] transition-colors " +
        (className ?? "")
      }
    >
      <ChatBubbleLeftRightIcon className="h-3.5 w-3.5" />
      {t("newPolicy.handoff.continueInChat")}
    </Link>
  )
}
