"use client"

import { useCallback, useRef, useState } from "react"
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
 * D82d: flattened prebuilt row.
 *
 * Earlier revisions wrapped the row in an outer chevron-expander button
 * with a collapsible summary block, and showed a yellow "Verifier-side
 * setup required" callout when toggling a setup_required prebuilt. Both
 * showed up in screenshot review as confusing UI ("the empty button on
 * the far right", "the UI is weird"). The row now:
 *
 *   - badge + title + status pill (NOT a toggle target — caret expander
 *     gone, the summary is rendered inline as quieter tertiary copy)
 *   - meta (verifier · trigger · action)
 *   - toggle (plain on/off, no setup-required popover)
 *   - secondary action: either "Setup →" (setup_required) → docs page
 *     that explains how to configure the verifier knob, or
 *     "Edit before enabling →" → wizard step 6 prefilled with the IR
 *
 * Setup-required prebuilts surface their config requirement via a
 * dedicated button on the row, not via a popover sprung from the
 * toggle. Operators who genuinely need to configure first take the
 * Setup → docs path; operators who already configured (CLI override)
 * toggle directly without an interstitial gate.
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

  // Q94: per-row view-source dialog state. Each row owns its own
  // dialog instance keyed by the trigger ref so focus restoration on
  // close lands back on the exact button the operator clicked.
  const viewSourceTriggerRef = useRef<HTMLButtonElement>(null)
  const [sourceOpen, setSourceOpen] = useState(false)

  return (
    <div className="flex flex-col gap-2 px-4 py-3 transition-colors hover:bg-black/[0.02]">
      <div className="flex flex-wrap items-center gap-3">
        {/* Identity block: badge + name + status pill. Plain inline
            row, no outer interactive wrapper. */}
        <div className="flex flex-wrap items-center gap-2 min-w-0 flex-1">
          <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider bg-[var(--color-muted-bg,#f3f4f6)] text-[var(--color-muted-fg,#374151)]">
            {t("rules.prebuilt.badge")}
          </span>
          <span className="text-sm font-semibold text-[var(--color-text-primary)] truncate">
            {entry.title}
          </span>
          <PrebuiltStatusPill entry={entry} t={t} />
        </div>

        {/* Meta block: verifier · trigger · action. Hides on narrow
            widths; reappears under the row on mobile. */}
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

        {/* Control block: toggle + secondary action link. */}
        <div className="flex items-center gap-3">
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
          {/* Q94: View source sits next to Setup / Edit. Opens a
              modal with the prebuilt's underlying Policy IR JSON so
              operators can inspect what the prebuilt actually does
              before flipping the toggle. */}
          <button
            ref={viewSourceTriggerRef}
            type="button"
            onClick={() => setSourceOpen(true)}
            aria-label={t("rules.prebuilt.viewSourceAria", { title: entry.title })}
            className="text-[11px] font-medium text-[var(--color-text-secondary)] hover:underline whitespace-nowrap"
          >
            {t("rules.prebuilt.viewSource")}
          </button>
          {entry.setup_required ? (
            <Link
              href={setupDocsHref(entry.id)}
              aria-label={t("rules.prebuilt.setupAria", { title: entry.title })}
              className="inline-flex items-center gap-1 rounded-md bg-amber-50 px-2 py-1 text-[11px] font-semibold text-amber-900 hover:bg-amber-100 whitespace-nowrap"
              title={entry.setup_hint || undefined}
            >
              {t("rules.prebuilt.setup")}
              <span aria-hidden>→</span>
            </Link>
          ) : (
            <Link
              href={draftHref}
              aria-label={t("rules.prebuilt.editBeforeAria", { title: entry.title })}
              className="text-[11px] font-medium text-[var(--color-accent-light)] hover:underline whitespace-nowrap"
            >
              {t("rules.prebuilt.editBefore")}
            </Link>
          )}
        </div>
      </div>

      <PrebuiltSourceDialog
        entry={entry}
        open={sourceOpen}
        onClose={() => setSourceOpen(false)}
        locale={locale}
        triggerRef={viewSourceTriggerRef}
      />

      {/* Meta meta (narrow widths) — verifier/trigger/action wraps below
          the row controls on mobile. */}
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

      {/* Inline summary as quieter tertiary copy, always visible. The
          earlier collapsible expander caused more confusion than it
          saved vertical space. */}
      {entry.summary ? (
        <p className="text-xs text-[var(--color-text-secondary)] leading-relaxed">
          {entry.summary}
        </p>
      ) : null}
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
