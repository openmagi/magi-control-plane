"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import Link from "next/link"
import type { PrebuiltPolicyEntry } from "@/lib/cloud"
import { Code } from "@/components/ui/Code"
import { PrebuiltToggle } from "./PrebuiltToggle"
import { PrebuiltSourceDialog } from "./PrebuiltSourceDialog"
import { togglePrebuiltAction } from "../actions"
import { translate, type Locale, type TKey } from "@/lib/i18n/dict"

type TFunc = (
  k: TKey,
  v?: Record<string, string | number>,
) => string

/**
 * D82e: density-reduced prebuilt row.
 *
 * Screenshot review flagged the D82d row layout as UI-overwhelming:
 * per-row it surfaced badge + title + status + verifier + trigger +
 * action + two full lines of description + toggle + View source +
 * Setup / Edit before enabling — five distinct meta labels + three
 * action controls on ONE row. With five rows on-screen the whole
 * Prebuilts section read as noise.
 *
 * The new layout collapses everything except identity + status +
 * toggle behind a kebab (`⋯`) menu. Description is line-clamped to
 * one line; the operator opens the details drawer (from the kebab)
 * or the source dialog when they want to know more. The kebab menu
 * groups the three secondary actions:
 *   - Details (verifier / trigger / action + full summary)
 *   - View source (JSON IR)
 *   - Setup / Edit before enabling
 *
 * Toggle stays as the row's PRIMARY control (visible without a
 * click) because operators scanning the list are looking for
 * on/off state and want to flip it in place.
 */
export function PrebuiltRow({
  entry, draftHref, locale,
}: {
  entry: PrebuiltPolicyEntry
  draftHref: string
  locale: Locale
}) {
  const t: TFunc = useCallback(
    (key, vars) => translate(locale, key, vars),
    [locale],
  )

  const viewSourceTriggerRef = useRef<HTMLButtonElement>(null)
  const [sourceOpen, setSourceOpen] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [detailsOpen, setDetailsOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement | null>(null)
  const menuButtonRef = useRef<HTMLButtonElement | null>(null)

  // Close menu on outside click + Escape.
  useEffect(() => {
    if (!menuOpen) return
    function onDoc(e: MouseEvent) {
      const root = menuRef.current
      if (root && !root.contains(e.target as Node)) setMenuOpen(false)
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setMenuOpen(false)
        menuButtonRef.current?.focus()
      }
    }
    document.addEventListener("mousedown", onDoc)
    document.addEventListener("keydown", onKey)
    return () => {
      document.removeEventListener("mousedown", onDoc)
      document.removeEventListener("keydown", onKey)
    }
  }, [menuOpen])

  return (
    <div className="flex flex-col gap-1 px-4 py-2.5 transition-colors hover:bg-black/[0.02]">
      <div className="flex items-center gap-3">
        {/* Identity: badge + title + status pill. */}
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-[var(--color-muted-bg,#f3f4f6)] text-[var(--color-muted-fg,#374151)] shrink-0">
            {t("rules.prebuilt.badge")}
          </span>
          <span className="text-sm font-semibold text-[var(--color-text-primary)] truncate">
            {entry.title}
          </span>
          <PrebuiltStatusPill entry={entry} t={t} />
        </div>

        {/* Primary control: toggle. */}
        <PrebuiltToggle
          prebuiltId={entry.id}
          enabled={entry.enabled}
          action={togglePrebuiltAction}
          labelOn={t("rules.prebuilt.disable", { title: entry.title })}
          labelOff={t("rules.prebuilt.enable", { title: entry.title })}
          copy={{
            transportError: t("rules.prebuilt.transportError"),
          }}
        />

        {/* Kebab menu: secondary actions. */}
        <div ref={menuRef} className="relative">
          <button
            ref={menuButtonRef}
            type="button"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            aria-label={t("rules.prebuilt.moreAria", { title: entry.title })}
            onClick={() => setMenuOpen((v) => !v)}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-[var(--color-text-tertiary)] hover:bg-black/[0.04] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]/40"
          >
            <svg viewBox="0 0 20 20" className="h-4 w-4" fill="currentColor" aria-hidden="true">
              <circle cx="10" cy="4" r="1.5" />
              <circle cx="10" cy="10" r="1.5" />
              <circle cx="10" cy="16" r="1.5" />
            </svg>
          </button>
          {menuOpen && (
            <div
              role="menu"
              className="absolute right-0 top-full mt-1 z-20 min-w-[200px] rounded-lg border border-black/[0.08] bg-white py-1 shadow-lg"
            >
              <button
                type="button"
                role="menuitem"
                onClick={() => { setDetailsOpen((v) => !v); setMenuOpen(false) }}
                className="flex w-full items-center px-3 py-2 text-left text-xs text-[var(--color-text-primary)] hover:bg-black/[0.03]"
              >
                {detailsOpen ? t("rules.prebuilt.hideDetails") : t("rules.prebuilt.showDetails")}
              </button>
              <button
                ref={viewSourceTriggerRef}
                type="button"
                role="menuitem"
                onClick={() => { setSourceOpen(true); setMenuOpen(false) }}
                className="flex w-full items-center px-3 py-2 text-left text-xs text-[var(--color-text-primary)] hover:bg-black/[0.03]"
              >
                {t("rules.prebuilt.viewSource")}
              </button>
              {entry.setup_required ? (
                <Link
                  href={setupDocsHref(entry.id)}
                  role="menuitem"
                  onClick={() => setMenuOpen(false)}
                  className="flex w-full items-center px-3 py-2 text-left text-xs font-medium text-amber-900 hover:bg-amber-50"
                >
                  {t("rules.prebuilt.setup")}
                  <span aria-hidden className="ml-1">→</span>
                </Link>
              ) : (
                <Link
                  href={draftHref}
                  role="menuitem"
                  onClick={() => setMenuOpen(false)}
                  className="flex w-full items-center px-3 py-2 text-left text-xs text-[var(--color-accent-light)] hover:bg-black/[0.03]"
                >
                  {t("rules.prebuilt.editBefore")}
                </Link>
              )}
            </div>
          )}
        </div>
      </div>

      {/* One-line summary (line-clamped). Full summary lives in the
       *  Details drawer below. */}
      {entry.summary ? (
        <p className="text-xs text-[var(--color-text-secondary)] truncate">
          {entry.summary}
        </p>
      ) : null}

      {/* Details drawer: verifier · trigger · action + full summary.
       *  Hidden by default; opened via the kebab menu. */}
      {detailsOpen ? (
        <div className="mt-2 rounded-lg border border-black/[0.06] bg-black/[0.02] px-3 py-2.5">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-[var(--color-text-tertiary)]">
            <span>
              {t("rules.prebuilt.verifier")}: <Code>{entry.verifier_step}</Code>
            </span>
            {entry.ir.trigger ? (
              <span>
                {t("rules.prebuilt.row.trigger")}:{" "}
                <Code>{entry.ir.trigger.event}</Code>{" · "}
                <Code>{entry.ir.trigger.matcher}</Code>
              </span>
            ) : null}
            {entry.ir.action ? (
              <span>
                {t("rules.prebuilt.action")}: <Code>{entry.ir.action}</Code>
              </span>
            ) : null}
          </div>
          {entry.summary ? (
            <p className="mt-2 text-xs text-[var(--color-text-secondary)] leading-relaxed">
              {entry.summary}
            </p>
          ) : null}
        </div>
      ) : null}

      <PrebuiltSourceDialog
        entry={entry}
        open={sourceOpen}
        onClose={() => setSourceOpen(false)}
        locale={locale}
        triggerRef={viewSourceTriggerRef}
      />
    </div>
  )
}

/** D82d: per-prebuilt docs anchor for the Setup button. Q96 moved the
 *  docs to markdown under `<repo>/docs/*.md`. The setup hint links to
 *  the operator runbook by default; per-prebuilt landings can be added
 *  later as individual markdown files without code changes here. */
function setupDocsHref(prebuiltId: string): string {
  const slug = prebuiltId.replace(/^prebuilt\//, "")
  return `/docs/operator#${slug}`
}

function PrebuiltStatusPill({
  entry, t,
}: {
  entry: PrebuiltPolicyEntry
  t: TFunc
}) {
  if (entry.setup_required) {
    return (
      <span
        className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-amber-100 text-amber-800 shrink-0"
        title={entry.setup_hint}
      >
        {t("rules.prebuilt.row.statusNeedsSetup")}
      </span>
    )
  }
  if (entry.enabled) {
    return (
      <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-emerald-100 text-emerald-800 shrink-0">
        {t("rules.prebuilt.row.statusActive")}
      </span>
    )
  }
  return (
    <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-gray-100 text-gray-700 shrink-0">
      {t("rules.prebuilt.row.statusOff")}
    </span>
  )
}
