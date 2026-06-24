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
import { translate, type Locale, type TKey } from "@/lib/i18n/dict"

const LOCAL_STORAGE_KEY = "magi_cp.nl_authoring_guide.expanded"

type T = (
  k: TKey,
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
    // D52e follow-up: WHEN absorbs the matcher / fetch-domain scope
    // refinements that used to sit (incorrectly) under CONDITION. Tool
    // name match + fetch domain are Trigger.matcher concerns in the IR
    // (ir.py:53), not EvidenceReq concerns.
    titleKey: "nlGuide.section.when.title",
    subtitleKey: "nlGuide.section.when.subtitle",
    examples: [
      { tone: "ok", exampleKey: "nlGuide.when.ok1.ex", explainKey: "nlGuide.when.ok1.ex.explain" },
      { tone: "ok", exampleKey: "nlGuide.when.ok2.ex", explainKey: "nlGuide.when.ok2.ex.explain" },
      { tone: "ok", exampleKey: "nlGuide.when.ok3.ex", explainKey: "nlGuide.when.ok3.ex.explain" },
      { tone: "ok", exampleKey: "nlGuide.when.ok4.ex", explainKey: "nlGuide.when.ok4.ex.explain" },
      { tone: "ok", exampleKey: "nlGuide.when.ok5.ex", explainKey: "nlGuide.when.ok5.ex.explain" },
      { tone: "ok", exampleKey: "nlGuide.when.ok6.ex", explainKey: "nlGuide.when.ok6.ex.explain" },
      { tone: "no", exampleKey: "nlGuide.when.no1.ex", explainKey: "nlGuide.when.no1.ex.explain" },
      { tone: "no", exampleKey: "nlGuide.when.no2.ex", explainKey: "nlGuide.when.no2.ex.explain" },
    ],
  },
  {
    // D52e follow-up: CONDITION is now strictly EvidenceReq prose
    // (verifier ref / SHACL / LLM critic / regex). Matcher constraints
    // moved to WHEN; unconditional-audit moved to WHAT.
    titleKey: "nlGuide.section.condition.title",
    subtitleKey: "nlGuide.section.condition.subtitle",
    examples: [
      { tone: "ok", exampleKey: "nlGuide.condition.ok1.ex", explainKey: "nlGuide.condition.ok1.ex.explain" },
      { tone: "ok", exampleKey: "nlGuide.condition.ok2.ex", explainKey: "nlGuide.condition.ok2.ex.explain" },
      { tone: "ok", exampleKey: "nlGuide.condition.ok3.ex", explainKey: "nlGuide.condition.ok3.ex.explain" },
      { tone: "ok", exampleKey: "nlGuide.condition.ok4.ex", explainKey: "nlGuide.condition.ok4.ex.explain" },
    ],
  },
  {
    // D52e follow-up: WHAT gains the unconditional-audit archetype
    // (action: audit, requires=[]) that used to sit under CONDITION.
    titleKey: "nlGuide.section.what.title",
    subtitleKey: "nlGuide.section.what.subtitle",
    examples: [
      { tone: "ok", exampleKey: "nlGuide.what.ok1.ex", explainKey: "nlGuide.what.ok1.ex.explain" },
      { tone: "ok", exampleKey: "nlGuide.what.ok2.ex", explainKey: "nlGuide.what.ok2.ex.explain" },
      { tone: "ok", exampleKey: "nlGuide.what.ok3.ex", explainKey: "nlGuide.what.ok3.ex.explain" },
      { tone: "ok", exampleKey: "nlGuide.what.ok4.ex", explainKey: "nlGuide.what.ok4.ex.explain" },
      { tone: "ok", exampleKey: "nlGuide.what.ok5.ex", explainKey: "nlGuide.what.ok5.ex.explain" },
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

/** Outcome of a pill fill attempt. Used by the parent island to flash
 * a hint when the author's existing draft would otherwise be wiped. */
type FillOutcome = "filled" | "blocked-nonempty" | "not-found"

/** Find the NL textarea on the page (id="nl") and seed its value with
 * the example prose ONLY when the field is empty / whitespace-only.
 * If the author already typed something, return "blocked-nonempty" so
 * the caller can surface a "clear the field to load an example" hint
 * instead of silently destroying the draft. Fires an 'input' event so
 * any client island also bound to the field stays in sync. */
function fillTextarea(targetId: string, text: string): FillOutcome {
  if (typeof document === "undefined") return "not-found"
  const el = document.getElementById(targetId)
  if (!el || !(el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement)) {
    return "not-found"
  }
  // D52e follow-up: pill click was destructive. It overwrote any
  // unsaved prose the author had typed. Treat the textarea as a
  // single-author surface: only seed when blank.
  if (el.value.trim().length > 0) {
    el.focus()
    return "blocked-nonempty"
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
  return "filled"
}

interface Props {
  /** Resolved locale from the server. The client rebuilds `t` locally
   * via the pure `translate()` exported from dict.ts so we do not
   * pass the closure across the server/client boundary (Next.js 14
   * RSC forbids passing functions to client components). */
  locale: Locale
  /** id of the NL <textarea> that pills should fill. */
  targetTextareaId: string
}

export default function NlAuthoringGuide({ locale, targetTextareaId }: Props): JSX.Element {
  const t: T = useCallback(
    (key, vars) => translate(locale, key, vars),
    [locale],
  )
  const [expanded, setExpanded] = useState<boolean>(false)
  const [hydrated, setHydrated] = useState<boolean>(false)
  // D52e follow-up: pill click is non-destructive. When the textarea
  // already has prose, we surface a small hint instead of overwriting.
  // The hint auto-clears after a few seconds so it does not stick
  // around once the author empties the field.
  const [blockedHint, setBlockedHint] = useState<boolean>(false)
  const [previewKey, setPreviewKey] = useState<
    import("@/lib/i18n/dict").TKey | null
  >(null)

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
      const outcome = fillTextarea(targetTextareaId, t(fillKey))
      if (outcome === "blocked-nonempty") {
        setBlockedHint(true)
      } else if (outcome === "filled") {
        setBlockedHint(false)
      }
    },
    [targetTextareaId, t],
  )

  // Auto-clear the "clear field to load an example" hint after a few
  // seconds. The hint persists if the author keeps clicking pills
  // (each click re-arms it).
  useEffect(() => {
    if (!blockedHint) return
    const timer = window.setTimeout(() => setBlockedHint(false), 4000)
    return () => window.clearTimeout(timer)
  }, [blockedHint])

  const sectionsId = useMemo(
    () => `${targetTextareaId}-authoring-guide-sections`,
    [targetTextareaId],
  )
  const previewId = `${targetTextareaId}-pill-preview`
  const hintId = `${targetTextareaId}-pill-hint`

  // D52e follow-up: render the body unconditionally and animate the
  // open / close via a CSS-only grid-row trick (grid-template-rows
  // 0fr → 1fr). Mount/unmount used to make the panel snap; this also
  // keeps `aria-controls={sectionsId}` pointing at a real element in
  // both states (NVDA + Firefox warn otherwise). We only opt the body
  // INTO the transition after first hydration so the SSR → CSR flip
  // for power users (persisted-open) doesn't animate from closed to
  // open as a jarring late motion.
  const bodyHidden = !expanded
  const transitionClass = hydrated
    ? "transition-[grid-template-rows] duration-200 ease-out"
    : ""

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

      <div
        id={sectionsId}
        data-testid="nl-authoring-guide-body"
        // Wrap in a grid whose single row transitions between 0fr and
        // 1fr; the inner child overflows hidden so the content height
        // animates smoothly. overflow-hidden on the grid track keeps
        // the panel from leaking pixels in the collapsed state.
        className={
          "grid overflow-hidden " +
          transitionClass +
          " " +
          (expanded ? "grid-rows-[1fr]" : "grid-rows-[0fr]")
        }
        aria-hidden={bodyHidden}
        // `inert` removes the subtree from focus order + AT when the
        // panel is collapsed. The attribute is supported across all
        // modern browsers; older targets simply ignore it (the
        // aria-hidden + display still gate semantics for SR).
        // @ts-expect-error inert is a valid HTML attribute (React 19
        // types add it; older @types/react may not).
        inert={bodyHidden ? "" : undefined}
      >
        <div className="min-h-0">
          <div className="border-t border-black/[0.06] px-4 pb-4 pt-3">
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
                      previewId={previewId}
                      onPick={() => onPill(p.fillKey)}
                      onPreview={(open) =>
                        setPreviewKey(open ? p.fillKey : null)
                      }
                    />
                  </li>
                ))}
              </ul>

              {/* Shared preview slot: the prose that WOULD be seeded
                  when the author clicks the focused / hovered pill.
                  Visible on both hover AND focus, so keyboard users
                  get the same affordance as mouse users (native
                  `title` does not). */}
              <p
                id={previewId}
                data-testid="nl-authoring-guide-pill-preview"
                role="status"
                aria-live="polite"
                className={
                  "mt-2 text-[11px] italic leading-snug text-[var(--color-text-tertiary)] transition-opacity " +
                  (previewKey ? "opacity-100" : "opacity-0")
                }
              >
                {previewKey ? `“${t(previewKey)}”` : " "}
              </p>

              {blockedHint && (
                <p
                  id={hintId}
                  data-testid="nl-authoring-guide-pill-blocked-hint"
                  role="status"
                  aria-live="polite"
                  className="mt-2 text-[11px] leading-snug text-[var(--color-text-secondary)]"
                >
                  {t("nlGuide.pill.blockedHint")}
                </p>
              )}

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
        </div>
      </div>
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
  previewId,
  onPick,
  onPreview,
}: {
  t: T
  pill: TryPill
  previewId: string
  onPick: () => void
  /** Fire `true` on hover / focus enter and `false` on hover / focus
   * leave so the parent shared-preview slot can render the seed prose
   * for the currently-active pill. */
  onPreview: (open: boolean) => void
}): JSX.Element {
  // D52e follow-up: replace the native `title` attribute (mouse-only,
  // delayed, hidden behind cursor) with a hover-AND-focus preview that
  // routes through the parent's shared preview slot. Keyboard users
  // get the same preview as mouse users.
  return (
    <button
      type="button"
      onClick={onPick}
      onMouseEnter={() => onPreview(true)}
      onMouseLeave={() => onPreview(false)}
      onFocus={() => onPreview(true)}
      onBlur={() => onPreview(false)}
      data-testid="nl-try-pill"
      data-tone={pill.tone}
      className={
        "cursor-pointer rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]/40 " +
        pillClasses(pill.tone)
      }
      aria-label={t(pill.labelKey)}
      aria-describedby={previewId}
    >
      {t(pill.labelKey)}
    </button>
  )
}
