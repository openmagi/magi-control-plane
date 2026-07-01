"use client"

import { useEffect, useMemo, useState } from "react"
import type { PolicyPackEntry } from "@/lib/cloud"

/**
 * P4 (pack-centric runtime): the ONE pack-membership picker.
 *
 * Built once and reused by all three authoring surfaces (Guided wizard
 * Step 1 "When", Raw / IR editor, Conversational compose handoff card).
 * Do NOT copy-paste variants — every surface renders this component and
 * relies on its hidden `pack_ids` input, which the surface's save server
 * action reads via `_parsePackIds(formData)`.
 *
 * Semantics (design doc "policies/new" section):
 *   - 0..n packs selectable. Empty selection = orphan (the policy is
 *     authored but joins no activated pack — a legitimate "wire up
 *     later" state).
 *   - The floor pack always renders at the TOP with an ALWAYS-ON chip so
 *     an operator who wants the policy to fire everywhere can select it
 *     directly.
 *   - `suggestedPackId` (from the conversational compose extractor) is
 *     PRE-SELECTED but never auto-committed — the operator confirms by
 *     leaving it checked before saving.
 *
 * The selected ids are serialized as a JSON array into a hidden input so
 * the server action reads a single stable field regardless of surface.
 */
/**
 * P4 conversational-compose extractor hook (pure + testable).
 *
 * When the operator's freeform text names a work context ("리서치",
 * "coding safety", "compliance audit"), suggest the matching pack id so
 * the picker can PRE-SELECT it (never auto-commit). Matching is a simple
 * case-insensitive substring scan of each pack's name + id against the
 * text; the first floor-then-order match wins. Returns null when nothing
 * matches. Deliberately conservative — a false suggestion the operator
 * has to uncheck is worse than no suggestion.
 */
export function suggestPackFromText(
  text: string,
  packs: PolicyPackEntry[],
): string | null {
  const t = (text || "").toLowerCase()
  if (!t.trim()) return null
  // Keyword aliases → substrings that commonly name a pack context.
  const aliases: Record<string, string[]> = {
    research: ["research", "리서치", "연구"],
    coding: ["coding", "code safety", "코딩"],
    compliance: ["compliance", "audit", "규정", "컴플라이언스"],
  }
  for (const pack of packs) {
    const hay = `${pack.name} ${pack.id}`.toLowerCase()
    // Direct name/id mention.
    const nameToken = pack.name.toLowerCase().trim()
    if (nameToken && t.includes(nameToken)) return pack.id
    // Alias mention: the text names a context AND the pack's name/id
    // reflects that context.
    for (const [ctx, words] of Object.entries(aliases)) {
      if (words.some((w) => t.includes(w)) && hay.includes(ctx)) {
        return pack.id
      }
    }
  }
  return null
}

export function PackMultiSelect({
  locale = "ko",
  suggestedPackId = null,
  suggestedPackText = null,
  labels,
}: {
  locale?: "ko" | "en"
  /** Extractor suggestion to pre-select (conversational compose). */
  suggestedPackId?: string | null
  /** Freeform text (e.g. the operator's first message in conversational
   *  compose) the picker runs through `suggestPackFromText` once the
   *  pack list loads, to derive a suggestion when no explicit id is
   *  passed. Pre-selects but never auto-commits. */
  suggestedPackText?: string | null
  labels: {
    heading: string
    hint: string
    search: string
    alwaysOn: string
    orphan: string
    loading: string
    empty: string
    suggested: string
  }
}) {
  const [packs, setPacks] = useState<PolicyPackEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState("")
  const [resolvedSuggestion, setResolvedSuggestion] = useState<string | null>(
    suggestedPackId,
  )
  const [touched, setTouched] = useState(false)
  const [selected, setSelected] = useState<string[]>(
    suggestedPackId ? [suggestedPackId] : [],
  )

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const r = await fetch(`/api/packs?locale=${locale}`, {
          cache: "no-store",
        })
        const data = (await r.json()) as { items?: PolicyPackEntry[] }
        const items = Array.isArray(data.items) ? data.items : []
        if (cancelled) return
        setPacks(items)
        // Extractor hook: derive a suggestion from freeform text once
        // the pack list is known, but only pre-select when the operator
        // has not yet touched the picker and no explicit id was passed.
        if (!suggestedPackId && suggestedPackText) {
          const derived = suggestPackFromText(suggestedPackText, items)
          if (derived) {
            setResolvedSuggestion(derived)
            setTouched((wasTouched) => {
              if (!wasTouched) setSelected((prev) =>
                prev.includes(derived) ? prev : [...prev, derived],
              )
              return wasTouched
            })
          }
        }
      } catch {
        if (!cancelled) setPacks([])
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [locale, suggestedPackId, suggestedPackText])

  // Floor pack first, then everything else. Search filters by name/id.
  const ordered = useMemo(() => {
    const sorted = [...packs].sort((a, b) => {
      const af = a.is_floor ? 0 : 1
      const bf = b.is_floor ? 0 : 1
      return af - bf
    })
    const q = query.trim().toLowerCase()
    if (!q) return sorted
    return sorted.filter(
      (p) =>
        p.name.toLowerCase().includes(q) || p.id.toLowerCase().includes(q),
    )
  }, [packs, query])

  function toggle(id: string) {
    setTouched(true)
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    )
  }

  return (
    <div className="rounded-lg border border-[var(--color-border-subtle)] p-3">
      <input type="hidden" name="pack_ids" value={JSON.stringify(selected)} />
      <p className="text-sm font-semibold text-[var(--color-text-primary)]">
        {labels.heading}
      </p>
      <p className="mt-0.5 text-xs text-[var(--color-text-tertiary)]">
        {labels.hint}
      </p>

      <input
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder={labels.search}
        aria-label={labels.search}
        className="mt-2 w-full rounded-md border border-[var(--color-border)] bg-transparent px-2.5 py-1.5 text-sm"
      />

      {loading ? (
        <p className="mt-3 text-xs text-[var(--color-text-tertiary)]">
          {labels.loading}
        </p>
      ) : ordered.length === 0 ? (
        <p className="mt-3 text-xs text-[var(--color-text-tertiary)]">
          {labels.empty}
        </p>
      ) : (
        <ul className="mt-2 max-h-56 space-y-1 overflow-y-auto">
          {ordered.map((pack) => {
            const isSelected = selected.includes(pack.id)
            const isSuggested = pack.id === resolvedSuggestion
            return (
              <li key={pack.id}>
                <label className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-[var(--color-surface-overlay)]">
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => toggle(pack.id)}
                    aria-label={pack.name}
                  />
                  <span className="font-medium text-[var(--color-text-primary)]">
                    {pack.name}
                  </span>
                  {pack.is_floor && (
                    <span className="rounded-full bg-emerald-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-emerald-800">
                      {labels.alwaysOn}
                    </span>
                  )}
                  {isSuggested && (
                    <span className="rounded-full bg-[var(--color-info-bg)] px-1.5 py-0.5 text-[10px] font-semibold text-[var(--color-info-fg)]">
                      {labels.suggested}
                    </span>
                  )}
                </label>
              </li>
            )
          })}
        </ul>
      )}

      {selected.length === 0 && !loading && (
        <p className="mt-2 text-[11px] text-[var(--color-text-tertiary)]">
          {labels.orphan}
        </p>
      )}
    </div>
  )
}
