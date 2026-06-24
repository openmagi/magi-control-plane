"use client"

/**
 * D52e: NL compose mode authoring guide.
 *
 * The /policies/new NL textarea hands raw prose to the LLM compiler.
 * Authors who never wrote a policy IR have no scaffolding for what a
 * "good" sentence looks like, so they retry, get clarifying questions
 * back, then bounce off. This guide is the disclosure layer:
 *
 *   - A collapsible "What can I write?" panel with three sections
 *     (WHEN / CONDITION / WHAT) of ✓ allowed and ✗ disallowed examples,
 *     calibrated to the control-plane IR (3 lifecycles, 4 actions, the
 *     evidence kinds the compiler actually accepts).
 *   - A "TRY ONE OF THESE" pill row with 5-6 example queries that fill
 *     the textarea on click (no auto-submit; the author still reviews
 *     and hits compile).
 *   - A yellow ambiguity callout below the pills that explains the
 *     compiler's clarifying-question behavior when phrasing is missing
 *     a lifecycle, scope, or condition.
 *
 * Discovery / disclosure:
 *   - Closed by default. A subtle "AUTHORING GUIDE" pin sits on the
 *     right of the collapsed header so authors who have never seen it
 *     can spot the affordance.
 *   - Open / closed state is persisted per-user in localStorage under
 *     the "magi_cp.nl_authoring_guide.expanded" key. Once a power user
 *     opens it, it stays open across visits.
 *
 * Only mounted on the NL compose mode. Guided and Raw IR modes do NOT
 * get this guide. they are structured already.
 *
 * Pill click writes the textarea value through a controlled
 * `dispatchEvent('input')` so other client islands on the page (none
 * today on the NL textarea, but PayloadFieldChipsClient uses the same
 * pattern on the wizard) stay in sync.
 */

import { useCallback, useEffect, useMemo, useState } from "react"

const LOCAL_STORAGE_KEY = "magi_cp.nl_authoring_guide.expanded"

type T = (
  k: import("@/lib/i18n/dict").TKey,
  v?: Record<string, string | number>,
) => string

type Tone = "ok" | "no"

interface GuideExample {
  tone: Tone
  /** i18n key for the literal example phrase (rendered as a quoted span). */
  exampleKey: import("@/lib/i18n/dict").TKey
  /** i18n key for the right-hand explanation (e.g. "→ before_tool_use"). */
  explainKey: import("@/lib/i18n/dict").TKey
}

interface GuideSection {
  /** i18n key for the section heading (WHEN / CONDITION / WHAT). */
  titleKey: import("@/lib/i18n/dict").TKey
  /** i18n key for the section subtitle (one short phrase). */
  subtitleKey: import("@/lib/i18n/dict").TKey
  examples: GuideExample[]
}

const SECTIONS: readonly GuideSection[] = [
  {
    titleKey: "nlGuide.section.when.title",
    subtitleKey: "nlGuide.section.when.subtitle",
    examples: [
      { tone: "ok", exampleKey: "nlGuide.when.ok1.ex", explainKey: "nlGuide.when.ok1.ex.explain" },
      { tone: "ok", exampleKey: "nlGuide.when.ok2.ex", explainKey: "nlGuide.when.ok2.ex.explain" },
      { tone: "ok", exampleKey: "nlGuide.when.ok3.ex", explainKey: "nlGuide.when.ok3.ex.explain" },
      { tone: "ok", exampleKey: "nlGuide.when.ok4.ex", explainKey: "nlGuide.when.ok4.ex.explain" },
      { tone: "no", exampleKey: "nlGuide.when.no1.ex", explainKey: "nlGuide.when.no1.ex.explain" },
      { tone: "no", exampleKey: "nlGuide.when.no2.ex", explainKey: "nlGuide.when.no2.ex.explain" },
    ],
  },
  {
    titleKey: "nlGuide.section.condition.title",
    subtitleKey: "nlGuide.section.condition.subtitle",
    examples: [
      { tone: "ok", exampleKey: "nlGuide.condition.ok1.ex", explainKey: "nlGuide.condition.ok1.ex.explain" },
      { tone: "ok", exampleKey: "nlGuide.condition.ok2.ex", explainKey: "nlGuide.condition.ok2.ex.explain" },
      { tone: "ok", exampleKey: "nlGuide.condition.ok3.ex", explainKey: "nlGuide.condition.ok3.ex.explain" },
      { tone: "ok", exampleKey: "nlGuide.condition.ok4.ex", explainKey: "nlGuide.condition.ok4.ex.explain" },
      { tone: "ok", exampleKey: "nlGuide.condition.ok5.ex", explainKey: "nlGuide.condition.ok5.ex.explain" },
      { tone: "ok", exampleKey: "nlGuide.condition.ok6.ex", explainKey: "nlGuide.condition.ok6.ex.explain" },
      { tone: "ok", exampleKey: "nlGuide.condition.ok7.ex", explainKey: "nlGuide.condition.ok7.ex.explain" },
    ],
  },
  {
    titleKey: "nlGuide.section.what.title",
    subtitleKey: "nlGuide.section.what.subtitle",
    examples: [
      { tone: "ok", exampleKey: "nlGuide.what.ok1.ex", explainKey: "nlGuide.what.ok1.ex.explain" },
      { tone: "ok", exampleKey: "nlGuide.what.ok2.ex", explainKey: "nlGuide.what.ok2.ex.explain" },
      { tone: "ok", exampleKey: "nlGuide.what.ok3.ex", explainKey: "nlGuide.what.ok3.ex.explain" },
      { tone: "ok", exampleKey: "nlGuide.what.ok4.ex", explainKey: "nlGuide.what.ok4.ex.explain" },
    ],
  },
]

/** Action archetypes drive the pill color so the author sees the
 * intended verdict shape at a glance. */
type PillTone = "block" | "ask" | "audit" | "strip"

interface TryPill {
  /** i18n key for the visible pill label. */
  labelKey: import("@/lib/i18n/dict").TKey
  /** i18n key for the actual textarea fill text. Separated from the
   * label so the pill stays short while the seed text is full prose. */
  fillKey: import("@/lib/i18n/dict").TKey
  tone: PillTone
}

const PILLS: readonly TryPill[] = [
  { labelKey: "nlGuide.pill.blockFetch.label", fillKey: "nlGuide.pill.blockFetch.fill", tone: "block" },
  { labelKey: "nlGuide.pill.denyShell.label", fillKey: "nlGuide.pill.denyShell.fill", tone: "block" },
  { labelKey: "nlGuide.pill.askMissingSource.label", fillKey: "nlGuide.pill.askMissingSource.fill", tone: "ask" },
  { labelKey: "nlGuide.pill.auditAwsKey.label", fillKey: "nlGuide.pill.auditAwsKey.fill", tone: "audit" },
  { labelKey: "nlGuide.pill.auditWeakCitations.label", fillKey: "nlGuide.pill.auditWeakCitations.fill", tone: "audit" },
  { labelKey: "nlGuide.pill.stripPii.label", fillKey: "nlGuide.pill.stripPii.fill", tone: "strip" },
]

/** Light-theme color tokens per action archetype. Matches the wizard's
 * action cards so the pill set looks part of the same vocabulary. */
function pillClasses(tone: PillTone): string {
  switch (tone) {
    case "block":
      return "border-red-300 bg-red-50 text-red-900 hover:bg-red-100 hover:border-red-400"
    case "ask":
      return "border-amber-300 bg-amber-50 text-amber-900 hover:bg-amber-100 hover:border-amber-400"
    case "audit":
      return "border-blue-300 bg-blue-50 text-blue-900 hover:bg-blue-100 hover:border-blue-400"
    case "strip":
      return "border-purple-300 bg-purple-50 text-purple-900 hover:bg-purple-100 hover:border-purple-400"
  }
}

function readExpanded(): boolean {
  if (typeof window === "undefined") return false
  try {
    return window.localStorage.getItem(LOCAL_STORAGE_KEY) === "1"
  } catch {
    return false
  }
}

function writeExpanded(expanded: boolean): void {
  if (typeof window === "undefined") return
  try {
    if (expanded) {
      window.localStorage.setItem(LOCAL_STORAGE_KEY, "1")
    } else {
      window.localStorage.removeItem(LOCAL_STORAGE_KEY)
    }
  } catch {
    // private-mode Safari etc.: fail open; the next reload will reset
    // to closed (the default), no data lost.
  }
}

/** Find the NL textarea on the page (id="nl") and overwrite its value.
 * Fires an 'input' event so any client island also bound to the field
 * stays in sync. */
function fillTextarea(targetId: string, text: string): void {
  if (typeof document === "undefined") return
  const el = document.getElementById(targetId)
  if (!el || !(el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement)) {
    return
  }
  // Use the native value setter to defeat React's controlled input
  // shadow value (the Textarea ships uncontrolled but this stays safe
  // if it's later wrapped). dispatchEvent then notifies any listeners.
  const proto = Object.getPrototypeOf(el)
  const desc = Object.getOwnPropertyDescriptor(proto, "value")
  if (desc?.set) {
    desc.set.call(el, text)
  } else {
    el.value = text
  }
  el.dispatchEvent(new Event("input", { bubbles: true }))
  // Focus + cursor at end so the author can immediately edit / submit.
  el.focus()
  try {
    const end = el.value.length
    if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) {
      el.setSelectionRange(end, end)
    }
  } catch {
    // ignore
  }
}

interface Props {
  t: T
  /** id of the NL <textarea> that pills should fill. */
  targetTextareaId: string
}

export default function NlAuthoringGuide({ t, targetTextareaId }: Props): JSX.Element {
  const [expanded, setExpanded] = useState<boolean>(false)
  const [hydrated, setHydrated] = useState<boolean>(false)

  // Hydrate persisted state on mount. We can't read localStorage during
  // SSR so we render closed first and flip on the next tick when the
  // key is set.
  useEffect(() => {
    setExpanded(readExpanded())
    setHydrated(true)
  }, [])

  const onToggle = useCallback(() => {
    setExpanded((prev) => {
      const next = !prev
      writeExpanded(next)
      return next
    })
  }, [])

  const onPill = useCallback(
    (fillKey: import("@/lib/i18n/dict").TKey) => {
      fillTextarea(targetTextareaId, t(fillKey))
    },
    [targetTextareaId, t],
  )

  const sectionsId = useMemo(
    () => `${targetTextareaId}-authoring-guide-sections`,
    [targetTextareaId],
  )

  return (
    <section
      data-testid="nl-authoring-guide"
      // suppressHydrationWarning: the persisted-expanded state can
      // diverge between SSR (always closed) and client (read from
      // localStorage). We accept the one-tick flicker because hiding
      // the panel until hydrate would mean the affordance is invisible
      // on the slower paint.
      suppressHydrationWarning
      className="mb-3 rounded-xl border border-black/[0.08] bg-white shadow-sm"
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={hydrated ? expanded : false}
        aria-controls={sectionsId}
        data-testid="nl-authoring-guide-toggle"
        className="flex w-full items-center justify-between gap-3 rounded-xl px-4 py-2.5 text-left transition-colors hover:bg-black/[0.025] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]/40"
      >
        <span className="flex items-center gap-2">
          <span
            aria-hidden
            className={
              "inline-block text-[var(--color-text-tertiary)] transition-transform " +
              (expanded ? "rotate-90" : "rotate-0")
            }
          >
            ▸
          </span>
          <span className="text-sm font-semibold text-[var(--color-text-primary)]">
            {t("nlGuide.header.title")}
          </span>
          <span className="text-xs text-[var(--color-text-tertiary)]">
            {t("nlGuide.header.subtitle")}
          </span>
        </span>
        <span
          aria-hidden
          data-testid="nl-authoring-guide-pin"
          className="rounded-md border border-purple-300 bg-purple-50 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-[0.14em] text-purple-800"
        >
          {t("nlGuide.header.pin")}
        </span>
      </button>

      {expanded && (
        <div
          id={sectionsId}
          data-testid="nl-authoring-guide-body"
          className="border-t border-black/[0.06] px-4 pb-4 pt-3"
        >
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            {SECTIONS.map((s) => (
              <NlGuideSection key={s.titleKey} t={t} section={s} />
            ))}
          </div>

          <div className="mt-5">
            <p
              className="mb-2 text-[10px] font-bold uppercase tracking-[0.16em] text-[var(--color-text-tertiary)]"
              id={`${targetTextareaId}-try-one-label`}
            >
              {t("nlGuide.tryOne.title")}
            </p>
            <ul
              role="list"
              aria-labelledby={`${targetTextareaId}-try-one-label`}
              data-testid="nl-authoring-guide-pills"
              className="flex flex-wrap gap-2"
            >
              {PILLS.map((p) => (
                <li key={p.labelKey} className="inline-flex">
                  <NlTryExamplePill
                    t={t}
                    pill={p}
                    onPick={() => onPill(p.fillKey)}
                  />
                </li>
              ))}
            </ul>

            <div
              role="note"
              data-testid="nl-authoring-guide-ambiguity"
              className="mt-3 rounded-lg border border-amber-400/40 bg-amber-50 p-3 text-xs leading-relaxed text-amber-900"
            >
              <p className="m-0 font-semibold">
                {t("nlGuide.ambiguity.title")}
              </p>
              <p className="m-0 mt-1">
                {t("nlGuide.ambiguity.body")}
              </p>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}

function NlGuideSection({
  t,
  section,
}: {
  t: T
  section: GuideSection
}): JSX.Element {
  return (
    <div
      data-testid="nl-guide-section"
      className="rounded-lg border border-black/[0.06] bg-[var(--color-accent)]/[0.015] px-3 py-2.5"
    >
      <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-[var(--color-accent)]">
        {t(section.titleKey)}
      </p>
      <p className="mt-0.5 text-[11px] text-[var(--color-text-tertiary)] leading-snug">
        {t(section.subtitleKey)}
      </p>
      <ul role="list" className="mt-2 space-y-1.5">
        {section.examples.map((ex) => (
          <li
            key={ex.exampleKey}
            data-testid={ex.tone === "ok" ? "nl-guide-example-ok" : "nl-guide-example-no"}
          >
            <NlGuideExample t={t} example={ex} />
          </li>
        ))}
      </ul>
    </div>
  )
}

function NlGuideExample({
  t,
  example,
}: {
  t: T
  example: GuideExample
}): JSX.Element {
  const isOk = example.tone === "ok"
  // SR users get a prefix word so they hear "allowed" or "disallowed"
  // instead of just the check / cross glyph.
  const srPrefix = isOk ? t("nlGuide.sr.allowed") : t("nlGuide.sr.disallowed")
  return (
    <div className="flex items-start gap-1.5 text-[11.5px] leading-snug">
      <span
        aria-hidden
        className={
          "mt-[1px] inline-block w-3 select-none font-bold " +
          (isOk ? "text-emerald-600" : "text-red-600")
        }
      >
        {isOk ? "✓" : "✗"}
      </span>
      <span className="sr-only">{srPrefix}</span>
      <span className="text-[var(--color-text-primary)]">
        <span className="font-mono text-[11px] text-[var(--color-text-secondary)]">
          {"“"}
          {t(example.exampleKey)}
          {"”"}
        </span>{" "}
        <span className="text-[var(--color-text-tertiary)]">
          {t(example.explainKey)}
        </span>
      </span>
    </div>
  )
}

function NlTryExamplePill({
  t,
  pill,
  onPick,
}: {
  t: T
  pill: TryPill
  onPick: () => void
}): JSX.Element {
  return (
    <button
      type="button"
      onClick={onPick}
      data-testid="nl-try-pill"
      data-tone={pill.tone}
      className={
        "cursor-pointer rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]/40 " +
        pillClasses(pill.tone)
      }
      aria-label={t(pill.labelKey)}
      title={t(pill.fillKey)}
    >
      {t(pill.labelKey)}
    </button>
  )
}
