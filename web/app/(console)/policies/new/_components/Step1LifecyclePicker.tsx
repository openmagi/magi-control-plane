"use client"

/**
 * D61: Step 1 lifecycle picker with layered disclosure.
 *
 * The Step 1 lifecycle surface used to scream 30 hook events into the
 * operator's face. 95% of authoring picks one of four: PreToolUse,
 * PostToolUse, UserPromptSubmit, Stop. The other 26 dominated the
 * screen and added cognitive load.
 *
 * This client island renders:
 *
 *   - A search input ("Search lifecycle moments...") above the grid.
 *     Typing filters by event name OR plain-language label substring
 *     across both Common and Advanced. Matching groups auto-expand;
 *     non-matching groups stay collapsed (or auto-collapse when their
 *     children all hide).
 *   - A default-expanded "Common" group with the 4 recommended events.
 *     PreToolUse carries a "recommended" / "추천" badge identical to
 *     the original wizard surface (visual parity with prior D58/D59
 *     behaviour).
 *   - Collapsed-by-default "Advanced" groups for the remaining 26
 *     events. Each group header shows its plain label, child count,
 *     and a caret. Click toggles expand. Multiple groups can be open
 *     simultaneously.
 *
 * Persistence:
 *   - localStorage key `magi_cp.step1_advanced_open` stores the set of
 *     currently-open Advanced group keys between sessions. Empty
 *     search input resets to default view (Common expanded, Advanced
 *     groups respect persisted set).
 *   - The search input itself does NOT persist; every fresh session
 *     starts empty.
 *
 * Why a client island:
 *   - Search filtering + group expand/collapse are interactive; the
 *     server has no way to express this without a per-keystroke
 *     round-trip.
 *   - The surrounding StepShell + <form action={advanceAction}> still
 *     own server actions; this component just renders the radio inputs
 *     within that form. The form's submit reads whatever radio is
 *     `:checked` at the time, which works regardless of which group is
 *     expanded (collapsed groups still mount the inputs; we hide the
 *     row container, not the input).
 *
 * Sub-path imports ONLY (NOT from "@/components/ui") so the barrel
 * does not yank a server-only chain into the client bundle.
 *
 * Brief: NEVER expose internal terms. Plain user vocabulary in labels
 * and search placeholder.
 */

import { useCallback, useEffect, useMemo, useState } from "react"
import { translate, type TKey } from "@/lib/i18n/dict"
import {
  ADVANCED_GROUPS,
  ADVANCED_OPEN_STORAGE_KEY,
  COMMON_GROUP,
  matchesQuery,
  normalizeQuery,
  type LifecycleLabels,
  type LifecycleSlug,
} from "./step1-lifecycle-groups"

// Re-exports so this client component remains a single import for
// consumers, even though the pure data + helpers live in a sibling
// module so the test loader does not need to import React + i18n.
export {
  COMMON_GROUP,
  ADVANCED_GROUPS,
  ADVANCED_OPEN_STORAGE_KEY,
  matchesQuery,
} from "./step1-lifecycle-groups"
export type {
  LifecycleSlug,
  LifecycleLabels,
  LifecycleGroup,
} from "./step1-lifecycle-groups"

/** Read the persisted set of open Advanced group keys. Returns an
 * empty set on first visit or any parse failure (defensive: a
 * corrupted localStorage entry must NOT crash the page). */
function readPersistedOpen(): Set<string> {
  if (typeof window === "undefined") return new Set()
  try {
    const raw = window.localStorage.getItem(ADVANCED_OPEN_STORAGE_KEY)
    if (!raw) return new Set()
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return new Set()
    const valid = ADVANCED_GROUPS.map((g) => g.key)
    return new Set(
      parsed.filter((x: unknown): x is string =>
        typeof x === "string" && valid.includes(x),
      ),
    )
  } catch {
    return new Set()
  }
}

function writePersistedOpen(next: Set<string>): void {
  if (typeof window === "undefined") return
  try {
    window.localStorage.setItem(
      ADVANCED_OPEN_STORAGE_KEY,
      JSON.stringify([...next]),
    )
  } catch {
    /* quota / private-mode noop */
  }
}

export interface Step1LifecyclePickerProps {
  locale: "ko" | "en"
  /** Currently-selected lifecycle slug. Drives `defaultChecked` on the
   * radio inputs. The form action consumes the submitted `lifecycle`
   * radio value, not this prop, so a re-render after a fresh URL state
   * is the source of truth. */
  currentLifecycle: LifecycleSlug
  /** Per-locale label + sub-copy for every lifecycle slug. The server
   * parent builds this via `lifecycleCardCopy(locale)` so the dict
   * stays a single source of truth in page.tsx. */
  labels: LifecycleLabels
  /** Optional click hook for analytics / parent-driven side effects
   * the moment a radio is changed. The wizard does NOT need this for
   * navigation (the surrounding <form action={advanceAction}> still
   * owns submission), but the test suite exercises it to confirm the
   * radio change fires and to assert downstream parent reactions
   * without driving the form. */
  onPick?: (slug: LifecycleSlug) => void
}

/** Tiny caret SVG (no @heroicons import to keep the client bundle
 * minimal; the icon is purely decorative). */
function Caret({ open }: { open: boolean }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 12 12"
      className={
        "h-3 w-3 transition-transform duration-150 " +
        (open ? "rotate-90" : "rotate-0")
      }
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M4 2l4 4-4 4" />
    </svg>
  )
}

/** Badge ("recommended" / "추천") rendered next to PreToolUse only.
 * Inline so the file stays self-contained and we don't pull in the
 * server-side <Badge> from page.tsx into a client bundle. */
function RecommendedBadge({ locale }: { locale: "ko" | "en" }) {
  const text = locale === "ko" ? "추천" : "recommended"
  return (
    <span
      data-testid="step1-recommended-badge"
      className="inline-flex items-center rounded-full bg-emerald-100 px-2 py-[1px] text-[10px] font-semibold uppercase tracking-wider text-emerald-700"
    >
      {text}
    </span>
  )
}

/** One lifecycle row. Visually mirrors the original server <RadioCard>
 * (border + selected-state + sub-copy) but mounts the radio input
 * unconditionally so the surrounding <form> always sees the picked
 * value, even when a group is collapsed (collapse hides the row
 * container, not the input). */
function LifecycleRow({
  slug,
  label,
  sub,
  defaultChecked,
  showBadge,
  locale,
  onPick,
  hidden,
}: {
  slug: LifecycleSlug
  label: string
  sub: string
  defaultChecked: boolean
  showBadge: boolean
  locale: "ko" | "en"
  onPick?: (slug: LifecycleSlug) => void
  hidden: boolean
}) {
  return (
    <label
      data-testid={`step1-row-${slug}`}
      data-lifecycle={slug}
      data-hidden={hidden ? "true" : "false"}
      className={
        "block cursor-pointer " + (hidden ? "hidden" : "")
      }
    >
      <input
        type="radio"
        name="lifecycle"
        value={slug}
        defaultChecked={defaultChecked}
        onChange={() => onPick?.(slug)}
        className="peer sr-only"
        required
      />
      <span
        className={
          "block rounded-xl border bg-white p-4 transition-colors " +
          "border-black/[0.08] hover:border-[var(--color-accent)]/40 " +
          "peer-checked:border-[var(--color-accent)] peer-checked:bg-[var(--color-accent)]/[0.05]"
        }
      >
        <span className="flex items-center justify-between gap-2 mb-1">
          <span className="text-sm font-semibold text-[var(--color-text-primary)]">
            {label}
          </span>
          {showBadge && <RecommendedBadge locale={locale} />}
        </span>
        <span className="block text-xs text-[var(--color-text-secondary)] leading-relaxed">
          {sub}
        </span>
      </span>
    </label>
  )
}

export default function Step1LifecyclePicker({
  locale,
  currentLifecycle,
  labels,
  onPick,
}: Step1LifecyclePickerProps) {
  const t = useCallback(
    (key: TKey, vars?: Record<string, string | number>) =>
      translate(locale, key, vars),
    [locale],
  )

  // Live search query (in-memory, not persisted).
  const [query, setQuery] = useState("")
  // Persisted Advanced-group open set. Read from localStorage on
  // mount; SSR / first paint use the empty set so the initial server
  // markup matches.
  const [openSet, setOpenSet] = useState<Set<string>>(new Set())
  useEffect(() => {
    setOpenSet(readPersistedOpen())
  }, [])

  const toggleGroup = useCallback((groupKey: string) => {
    setOpenSet((prev) => {
      const next = new Set(prev)
      if (next.has(groupKey)) next.delete(groupKey)
      else next.add(groupKey)
      writePersistedOpen(next)
      return next
    })
  }, [])

  // Precompute per-row visibility under the current query.
  const visibilityByGroup = useMemo(() => {
    const all = [COMMON_GROUP, ...ADVANCED_GROUPS]
    const map = new Map<string, { anyMatch: boolean; visibleRows: Set<LifecycleSlug> }>()
    for (const group of all) {
      const visibleRows = new Set<LifecycleSlug>()
      let anyMatch = false
      for (const slug of group.members) {
        const meta = labels[slug]
        if (matchesQuery(slug, meta.label, query)) {
          visibleRows.add(slug)
          anyMatch = true
        }
      }
      map.set(group.key, { anyMatch, visibleRows })
    }
    return map
  }, [labels, query])

  const queryActive = normalizeQuery(query) !== ""
  // Sum of all match counts under the active query (across every
  // group). Used to render an empty-state hint when nothing matches.
  const totalMatches = useMemo(() => {
    let n = 0
    for (const v of visibilityByGroup.values()) n += v.visibleRows.size
    return n
  }, [visibilityByGroup])

  return (
    <div className="space-y-5" data-testid="step1-lifecycle-picker">
      <div>
        <label htmlFor="step1-search" className="sr-only">
          {t("newPolicy.wizard.step1.search.aria")}
        </label>
        <input
          id="step1-search"
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("newPolicy.wizard.step1.search.placeholder")}
          aria-label={t("newPolicy.wizard.step1.search.aria")}
          data-testid="step1-search-input"
          // The search input is NOT part of the wizard form payload;
          // a stray `name="search"` would post to advanceWizard.
          // Omit `name` deliberately.
          autoComplete="off"
          spellCheck={false}
          className="w-full rounded-xl border border-black/[0.08] bg-white px-4 py-2.5 text-sm leading-6 text-[var(--color-text-primary)] focus:border-[var(--color-accent)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]/20"
        />
      </div>

      {/* Common group: always expanded, no toggle, no caret. */}
      {(() => {
        const groupVis = visibilityByGroup.get(COMMON_GROUP.key)!
        // Under an active query, hide the whole Common group if no
        // row matches; otherwise it always renders.
        if (queryActive && !groupVis.anyMatch) return null
        return (
          <section
            key={COMMON_GROUP.key}
            data-testid={`step1-group-${COMMON_GROUP.key}`}
            data-group-kind="common"
            data-group-open="true"
            className="space-y-2"
          >
            <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--color-text-tertiary)] m-0">
              {t(COMMON_GROUP.key as TKey)}
            </p>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {COMMON_GROUP.members.map((slug) => {
                const meta = labels[slug]
                const hidden = queryActive && !groupVis.visibleRows.has(slug)
                return (
                  <LifecycleRow
                    key={slug}
                    slug={slug}
                    label={meta.label}
                    sub={meta.sub}
                    defaultChecked={currentLifecycle === slug}
                    showBadge={slug === "before_tool_use"}
                    locale={locale}
                    onPick={onPick}
                    hidden={hidden}
                  />
                )
              })}
            </div>
          </section>
        )
      })()}

      {/* Advanced groups: collapsible, persisted, auto-expand on
          query match. */}
      {ADVANCED_GROUPS.map((group) => {
        const groupVis = visibilityByGroup.get(group.key)!
        // When the query is active, hide groups with zero matches
        // entirely; auto-expand the ones with matches regardless of
        // persisted state.
        if (queryActive && !groupVis.anyMatch) return null
        const persistedOpen = openSet.has(group.key)
        const effectivelyOpen = queryActive ? true : persistedOpen
        return (
          <section
            key={group.key}
            data-testid={`step1-group-${group.key}`}
            data-group-kind="advanced"
            data-group-open={effectivelyOpen ? "true" : "false"}
            data-group-persisted-open={persistedOpen ? "true" : "false"}
            className="space-y-2"
          >
            <button
              type="button"
              onClick={() => toggleGroup(group.key)}
              aria-expanded={effectivelyOpen}
              aria-controls={`step1-group-rows-${group.key}`}
              aria-label={effectivelyOpen
                ? t("newPolicy.wizard.step1.collapseGroup")
                : t("newPolicy.wizard.step1.expandGroup")}
              data-testid={`step1-group-toggle-${group.key}`}
              className="flex w-full items-center gap-2 rounded-md px-1 py-1 text-left transition-colors hover:bg-black/[0.02]"
            >
              <Caret open={effectivelyOpen} />
              <span className="flex-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--color-text-tertiary)]">
                {t(group.key as TKey)}
              </span>
              <span className="text-[11px] font-mono text-[var(--color-text-tertiary)]">
                {t("newPolicy.wizard.step1.advancedCount", {
                  count: queryActive ? groupVis.visibleRows.size : group.members.length,
                })}
              </span>
            </button>
            <div
              id={`step1-group-rows-${group.key}`}
              className={
                "grid grid-cols-1 gap-2 sm:grid-cols-2 " +
                (effectivelyOpen ? "" : "hidden")
              }
            >
              {group.members.map((slug) => {
                const meta = labels[slug]
                const hidden = queryActive && !groupVis.visibleRows.has(slug)
                return (
                  <LifecycleRow
                    key={slug}
                    slug={slug}
                    label={meta.label}
                    sub={meta.sub}
                    defaultChecked={currentLifecycle === slug}
                    showBadge={false}
                    locale={locale}
                    onPick={onPick}
                    hidden={hidden}
                  />
                )
              })}
            </div>
          </section>
        )
      })}

      {queryActive && totalMatches === 0 && (
        <p
          data-testid="step1-search-empty"
          className="text-xs text-[var(--color-text-tertiary)]"
        >
          {t("newPolicy.wizard.step1.search.empty")}
        </p>
      )}
    </div>
  )
}
