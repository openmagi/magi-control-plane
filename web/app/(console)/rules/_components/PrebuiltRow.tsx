"use client"

import { useState, type KeyboardEvent } from "react"
import Link from "next/link"
import type { PrebuiltPolicyEntry } from "@/lib/cloud"
import { Code } from "@/components/ui/Code"
import { PrebuiltToggle } from "./PrebuiltToggle"
import { togglePrebuiltAction } from "../actions"

type TFunc = (
  k: import("@/lib/i18n/dict").TKey,
  v?: Record<string, string | number>,
) => string

/**
 * D82a: prebuilt entry rendered as a single ROW (not a card).
 *
 * Pre-D82a operators saw a grid of cards that took up vertical space
 * out of proportion to the information density (one verifier + one
 * trigger + one action per row). The card body description (a one-
 * sentence summary) was the largest visual block but the operator
 * scanning the page rarely needed to read it for every row.
 *
 * The new row layout is:
 *
 *   [BUILT-IN] [Name + status pill]  verifier · trigger  Action  [toggle]  [Edit before enabling >]  [caret]
 *
 * The summary hides behind a chevron expander on the right of the row.
 * Clicking the row body OR the caret expands; clicking the toggle does
 * NOT propagate (we call stopPropagation on the toggle wrapper). The
 * Edit-before-enabling link is also click-isolated so a click on it
 * navigates without toggling expansion state.
 *
 * Status pill mapping (right after the name):
 *   enabled + setup_required        → "Needs setup" amber
 *   enabled + !setup_required       → "Active"      emerald
 *   !enabled + setup_required       → "Needs setup" amber (same chip,
 *                                                          off-state
 *                                                          renders
 *                                                          identical
 *                                                          framing)
 *   !enabled + !setup_required      → "Off"         neutral
 */
export function PrebuiltRow({
  entry, draftHref, t,
}: {
  entry: PrebuiltPolicyEntry
  draftHref: string
  t: TFunc
}) {
  const [expanded, setExpanded] = useState(false)
  const expandLabelKey = expanded
    ? "rules.prebuilt.row.collapseAria"
    : "rules.prebuilt.row.expandAria"
  const expandLabel = t(expandLabelKey, { title: entry.title })

  function onRowClick(): void {
    setExpanded((v) => !v)
  }
  function onRowKey(ev: KeyboardEvent<HTMLDivElement>): void {
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault()
      setExpanded((v) => !v)
    }
  }
  // Click on the toggle (or any of its inner elements / dialogs) must
  // not bubble up to the row click handler — otherwise flipping the
  // toggle would also expand the description, which is jarring.
  function stop(ev: { stopPropagation(): void }): void {
    ev.stopPropagation()
  }

  return (
    <div
      role="button"
      tabIndex={0}
      aria-expanded={expanded}
      onClick={onRowClick}
      onKeyDown={onRowKey}
      className="group flex flex-col gap-2 px-4 py-3 transition-colors hover:bg-black/[0.02] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]/40"
    >
      <div className="flex flex-wrap items-center gap-3">
        {/* Identity block: badge + name + status pill. */}
        <div className="flex flex-wrap items-center gap-2 min-w-0 flex-1">
          <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-[var(--color-muted-bg,#f3f4f6)] text-[var(--color-muted-fg,#374151)]">
            {t("rules.prebuilt.badge")}
          </span>
          <span className="text-sm font-semibold text-[var(--color-text-primary)] truncate">
            {entry.title}
          </span>
          <PrebuiltStatusPill entry={entry} t={t} />
        </div>

        {/* Meta block: verifier · trigger · action. Single-line, hides
            on narrow widths to give name + toggle room (wrap on tiny
            widths). */}
        <div className="hidden md:flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-[var(--color-text-tertiary)]">
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

        {/* Control block: toggle + edit link + caret. Each control is
            click-isolated so the row expander does not fight it. */}
        <div className="flex items-center gap-2" onClick={stop}>
          <PrebuiltToggle
            prebuiltId={entry.id}
            enabled={entry.enabled}
            setupRequired={entry.setup_required}
            setupHint={entry.setup_hint}
            action={togglePrebuiltAction}
            labelOn={t("rules.prebuilt.disable", { title: entry.title })}
            labelOff={t("rules.prebuilt.enable", { title: entry.title })}
            copy={{
              setupRequired: t("rules.prebuilt.setupRequired"),
              setupUnconfigurableHere: t(
                "rules.prebuilt.setupHint.unconfigurableHere",
              ),
              enableAnyway: t("rules.prebuilt.enableAnyway"),
              cancel: t("rules.prebuilt.cancel"),
              transportError: t("rules.prebuilt.transportError"),
            }}
          />
          <Link
            href={draftHref}
            aria-label={t("rules.prebuilt.editBeforeAria", { title: entry.title })}
            onClick={stop}
            className="text-[11px] font-medium text-[var(--color-accent-light)] hover:underline whitespace-nowrap"
          >
            {t("rules.prebuilt.editBefore")}
          </Link>
          <Caret
            expanded={expanded}
            label={expandLabel}
          />
        </div>
      </div>

      {/* Meta meta (narrow widths) — verifier/trigger/action wraps below
          the row controls on mobile so the row body never overflows. */}
      <div className="md:hidden flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-[var(--color-text-tertiary)]">
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

      {expanded && (
        <p className="mt-1 text-xs text-[var(--color-text-secondary)] leading-relaxed">
          {entry.summary}
        </p>
      )}
    </div>
  )
}

function PrebuiltStatusPill({
  entry, t,
}: {
  entry: PrebuiltPolicyEntry
  t: TFunc
}) {
  // D60 — leaves the active emerald pill when on; "needs setup" amber
  // surfaces whenever the setup_required bit is set (regardless of
  // enabled state) because operator may have used Enable Anyway and
  // the policy is still inert.
  if (entry.setup_required) {
    return (
      <span
        className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-amber-100 text-amber-800"
        title={entry.setup_hint}
      >
        {t("rules.prebuilt.row.statusNeedsSetup")}
      </span>
    )
  }
  if (entry.enabled) {
    return (
      <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-emerald-100 text-emerald-800">
        {t("rules.prebuilt.row.statusActive")}
      </span>
    )
  }
  return (
    <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-gray-100 text-gray-700">
      {t("rules.prebuilt.row.statusOff")}
    </span>
  )
}

function Caret({ expanded, label }: { expanded: boolean; label: string }) {
  // Visually a chevron — rotates 90 degrees when expanded. No button
  // element: the entire row is the toggle target so a nested button
  // would create a duplicate (and overlapping) click target. The
  // aria-hidden span is for screen readers (the row carries
  // aria-expanded; this caret is decorative).
  return (
    <span
      aria-hidden="true"
      title={label}
      className={`inline-block h-3 w-3 shrink-0 text-[var(--color-text-tertiary)] transition-transform duration-150 ${expanded ? "rotate-90" : ""}`}
    >
      <svg viewBox="0 0 12 12" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M4 2 L8 6 L4 10" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </span>
  )
}
