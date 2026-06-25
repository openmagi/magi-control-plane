"use client"

import { useCallback, useState } from "react"
import Link from "next/link"
import type { PrebuiltPolicyEntry } from "@/lib/cloud"
import { Code } from "@/components/ui/Code"
import { PrebuiltToggle } from "./PrebuiltToggle"
import { togglePrebuiltAction } from "../actions"
import { translate, type Locale, type TKey } from "@/lib/i18n/dict"

type TFunc = (
  k: TKey,
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
 * The summary hides behind a real <button> chevron expander on the
 * right of the row. The outer row is a plain <div> (NOT role=button) —
 * WAI-ARIA disallows interactive descendants (PrebuiltToggle's switch,
 * the Edit link) inside role=button, and the original outer-role-button
 * form announced as one giant button to AT and broke focus order.
 *
 * D82a follow-up: the row also no longer uses an instant DOM swap for
 * the summary; the summary is rendered unconditionally inside a
 * grid-template-rows transition wrapper so the row height eases from
 * 0fr <-> 1fr over 150ms (matching the caret rotation). In the row-
 * density scenario (5+ rows in the first viewport) clicking a row no
 * longer abruptly pushes the rows below off-screen.
 *
 * Status pill mapping (right after the name):
 *   enabled + setup_required        -> "Needs setup" amber
 *   enabled + !setup_required       -> "Active"      emerald
 *   !enabled + setup_required       -> "Needs setup" amber (same chip,
 *                                                          off-state
 *                                                          renders
 *                                                          identical
 *                                                          framing)
 *   !enabled + !setup_required      -> "Off"         neutral
 */
export function PrebuiltRow({
  entry, draftHref, locale,
}: {
  entry: PrebuiltPolicyEntry
  draftHref: string
  /** D82a hotfix: take locale instead of t closure so this client
   * component does not violate the RSC boundary. Rebuild t locally
   * via the pure translate() from dict.ts. */
  locale: Locale
}) {
  const t: TFunc = useCallback(
    (key, vars) => translate(locale, key, vars),
    [locale],
  )
  const [expanded, setExpanded] = useState(false)
  const expandLabelKey = expanded
    ? "rules.prebuilt.row.collapseAria"
    : "rules.prebuilt.row.expandAria"
  const expandLabel = t(expandLabelKey, { title: entry.title })
  // D82a follow-up: aria-controls target id for the summary region so
  // AT users hear WHAT is being expanded, not just that "expanded" is
  // true on an unrelated element.
  const summaryId = `prebuilt-row-${entry.id}-summary`

  return (
    <div className="group flex flex-col gap-2 px-4 py-3 transition-colors hover:bg-black/[0.02]">
      <div className="flex flex-wrap items-center gap-3">
        {/* Identity block: badge + name + status pill. Mouse users can
            still click anywhere in this block to toggle expansion; the
            click target is a real <button> wrapping the identity row so
            AT users see one interactive control with a clear label. */}
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={summaryId}
          aria-label={expandLabel}
          onClick={() => setExpanded((v) => !v)}
          className="flex flex-wrap items-center gap-2 min-w-0 flex-1 cursor-pointer text-left bg-transparent border-0 p-0 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]/40 rounded-md"
        >
          <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-[var(--color-muted-bg,#f3f4f6)] text-[var(--color-muted-fg,#374151)]">
            {t("rules.prebuilt.badge")}
          </span>
          <span className="text-sm font-semibold text-[var(--color-text-primary)] truncate">
            {entry.title}
          </span>
          <PrebuiltStatusPill entry={entry} t={t} />
        </button>

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

        {/* Control block: toggle + edit link + caret. The toggle and
            link are siblings (not descendants of an outer role=button)
            so WAI-ARIA's "no interactive descendants inside button"
            rule is satisfied. No click-bubble guards are needed because
            no parent listens for the click. */}
        <div className="flex items-center gap-2">
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
            className="text-[11px] font-medium text-[var(--color-accent-light)] hover:underline whitespace-nowrap"
          >
            {t("rules.prebuilt.editBefore")}
          </Link>
          {/* Caret as a sibling <button> — keeps a discoverable click
              target on the right of the row even when the identity
              block is hard to read (no focus target overlap with the
              identity button because both buttons toggle the same
              state; only one of them needs focus at a time). */}
          <button
            type="button"
            aria-expanded={expanded}
            aria-controls={summaryId}
            aria-label={expandLabel}
            onClick={() => setExpanded((v) => !v)}
            className="inline-flex h-6 w-6 items-center justify-center rounded-md text-[var(--color-text-tertiary)] hover:bg-black/[0.04] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]/40"
          >
            <svg
              viewBox="0 0 12 12"
              className={`h-3 w-3 transition-transform duration-150 ${expanded ? "rotate-90" : ""}`}
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              aria-hidden="true"
            >
              <path d="M4 2 L8 6 L4 10" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
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

      {/* D82a follow-up: animated expander. Render the summary
          unconditionally inside a grid-template-rows transition so the
          height eases from 0fr <-> 1fr over 150ms (matching the caret
          rotation). The inner <div overflow-hidden> hides the text
          while the wrapper is collapsed; aria-hidden when collapsed so
          AT doesn't read invisible content. */}
      <div
        id={summaryId}
        className="grid transition-[grid-template-rows] duration-150 ease-out"
        style={{ gridTemplateRows: expanded ? "1fr" : "0fr" }}
        aria-hidden={!expanded}
      >
        <div className="overflow-hidden">
          <p className="mt-1 text-xs text-[var(--color-text-secondary)] leading-relaxed">
            {entry.summary}
          </p>
        </div>
      </div>
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

