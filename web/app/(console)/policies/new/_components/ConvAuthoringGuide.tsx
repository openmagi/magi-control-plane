"use client"

/**
 * D57b: conversational compose authoring guide.
 *
 * A reborn version of the deleted D52e NlAuthoringGuide tuned for the
 * conversational lens. Where the NL guide spoke admin-shape vocabulary
 * (WHEN / CONDITION / WHAT), this guide speaks the same plain-language
 * questions the assistant asks during a conversation:
 *
 *   - 어떤 시점에서? (When does this rule fire?)
 *   - 어떤 조건? (What triggers it?)
 *   - 어떤 조치? (What action?)
 *
 * Discovery / disclosure:
 *   - Renders ABOVE the chat scroll region.
 *   - Closed by default; a subtle "어떻게 쓰면 좋을까요?" pin sits on
 *     the collapsed header so first-time visitors can spot the
 *     affordance.
 *   - Open / closed state is persisted per-user in localStorage under
 *     the "magi_cp.conv_authoring_guide.expanded" key.
 *
 * Starter pills:
 *   - 5 "이걸 한 번 해보세요" prompts at the bottom of the panel.
 *   - Click writes into the chat input (does NOT auto-send). The
 *     parent passes onFillPrompt(text), which mirrors the existing
 *     starter-pill row inside ConversationalCompose.
 *
 * HARD RULE: this is a conversational surface. The copy MUST NOT name
 * internal terms (regex / shacl / llm_critic / EvidenceReq / matcher /
 * kind / lifecycle / on_missing). Action archetypes are described as
 * "차단 / 승인 요청 / 감사" with a soft note that "출력을 무력화" needs
 * a PostToolUse-with-additionalContext OR block+retry shape, expressed
 * in user words.
 *
 * Sub-path imports ONLY (NOT from "@/components/ui"). The barrel
 * pulls a server-only chain into the client bundle and breaks build.
 */

import { useCallback, useEffect, useMemo, useState } from "react"
import { translate } from "@/lib/i18n/dict"

const LOCAL_STORAGE_KEY = "magi_cp.conv_authoring_guide.expanded"

type T = (
  k: import("@/lib/i18n/dict").TKey,
  v?: Record<string, string | number>,
) => string

interface GuideExample {
  /** i18n key for the literal example phrase (rendered as a quoted span). */
  exampleKey: import("@/lib/i18n/dict").TKey
  /** i18n key for the right-hand soft explanation. */
  explainKey: import("@/lib/i18n/dict").TKey
}

interface GuideSection {
  /** i18n key for the section heading. */
  titleKey: import("@/lib/i18n/dict").TKey
  /** i18n key for the section subtitle (one short phrase). */
  subtitleKey: import("@/lib/i18n/dict").TKey
  examples: GuideExample[]
  /** Optional i18n key for a soft footer note (used by the action
   * section to describe the "출력 무력화" composition without naming
   * internal hooks). */
  footerKey?: import("@/lib/i18n/dict").TKey
}

const SECTIONS: readonly GuideSection[] = [
  {
    titleKey: "convGuide.section.when.title",
    subtitleKey: "convGuide.section.when.subtitle",
    examples: [
      {
        exampleKey: "convGuide.when.ex1",
        explainKey: "convGuide.when.ex1.explain",
      },
      {
        exampleKey: "convGuide.when.ex2",
        explainKey: "convGuide.when.ex2.explain",
      },
      {
        exampleKey: "convGuide.when.ex3",
        explainKey: "convGuide.when.ex3.explain",
      },
    ],
  },
  {
    titleKey: "convGuide.section.condition.title",
    subtitleKey: "convGuide.section.condition.subtitle",
    examples: [
      {
        exampleKey: "convGuide.condition.ex1",
        explainKey: "convGuide.condition.ex1.explain",
      },
      {
        exampleKey: "convGuide.condition.ex2",
        explainKey: "convGuide.condition.ex2.explain",
      },
      {
        exampleKey: "convGuide.condition.ex3",
        explainKey: "convGuide.condition.ex3.explain",
      },
      {
        exampleKey: "convGuide.condition.ex4",
        explainKey: "convGuide.condition.ex4.explain",
      },
    ],
  },
  {
    titleKey: "convGuide.section.action.title",
    subtitleKey: "convGuide.section.action.subtitle",
    examples: [
      {
        exampleKey: "convGuide.action.ex1",
        explainKey: "convGuide.action.ex1.explain",
      },
      {
        exampleKey: "convGuide.action.ex2",
        explainKey: "convGuide.action.ex2.explain",
      },
      {
        exampleKey: "convGuide.action.ex3",
        explainKey: "convGuide.action.ex3.explain",
      },
    ],
    footerKey: "convGuide.action.footer",
  },
]

interface TryPill {
  /** i18n key for the pill label. */
  labelKey: import("@/lib/i18n/dict").TKey
  /** i18n key for the text dropped into the chat input on click. */
  fillKey: import("@/lib/i18n/dict").TKey
}

const PILLS: readonly TryPill[] = [
  {
    labelKey: "convGuide.pill.blockAwsKey.label",
    fillKey: "convGuide.pill.blockAwsKey.fill",
  },
  {
    labelKey: "convGuide.pill.askRmRf.label",
    fillKey: "convGuide.pill.askRmRf.fill",
  },
  {
    labelKey: "convGuide.pill.auditWebFetch.label",
    fillKey: "convGuide.pill.auditWebFetch.fill",
  },
  {
    labelKey: "convGuide.pill.flagWeakCitations.label",
    fillKey: "convGuide.pill.flagWeakCitations.fill",
  },
  {
    labelKey: "convGuide.pill.blockPiiAfterTool.label",
    fillKey: "convGuide.pill.blockPiiAfterTool.fill",
  },
]

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

export interface ConvAuthoringGuideProps {
  locale: "ko" | "en"
  /** Drop the pill's prompt into the chat input. The parent's input
   *  state owns the value, so this is a one-way write; the user can
   *  still edit before sending. */
  onFillPrompt: (text: string) => void
  /** If true the pills are visually inert (parent has an in-flight
   *  turn). The toggle button stays usable so a reader can still flip
   *  the guide open while the assistant is typing. */
  pending?: boolean
}

export default function ConvAuthoringGuide({
  locale,
  onFillPrompt,
  pending = false,
}: ConvAuthoringGuideProps): JSX.Element {
  const t: T = useMemo(
    () => (key, vars) => translate(locale, key, vars),
    [locale],
  )
  const [expanded, setExpanded] = useState<boolean>(false)

  // Hydrate persisted state on mount. We can't read localStorage during
  // SSR so we render closed first and flip on the next tick when the
  // key is set.
  useEffect(() => {
    setExpanded(readExpanded())
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
      if (pending) return
      onFillPrompt(t(fillKey))
    },
    [pending, onFillPrompt, t],
  )

  const bodyId = "conv-authoring-guide-body"

  return (
    <section
      data-testid="conv-authoring-guide"
      className="mb-3 rounded-xl border border-black/[0.08] bg-white shadow-sm"
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        aria-controls={bodyId}
        data-testid="conv-authoring-guide-toggle"
        className={
          "flex w-full items-center justify-between gap-3 rounded-xl px-4 py-2.5 " +
          "text-left transition-colors hover:bg-black/[0.025] " +
          "focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]/40"
        }
      >
        <span className="flex items-center gap-2">
          <span
            aria-hidden
            className={
              "inline-block text-[var(--color-text-tertiary)] transition-transform " +
              (expanded ? "rotate-90" : "rotate-0")
            }
          >
            {"▸"}
          </span>
          <span className="text-sm font-semibold text-[var(--color-text-primary)]">
            {t("convGuide.header.title")}
          </span>
          <span className="text-xs text-[var(--color-text-tertiary)]">
            {t("convGuide.header.subtitle")}
          </span>
        </span>
        <span
          aria-hidden
          data-testid="conv-authoring-guide-pin"
          className={
            "rounded-md border border-purple-300 bg-purple-50 px-1.5 py-0.5 " +
            "text-[10px] font-bold uppercase tracking-[0.14em] text-purple-800"
          }
        >
          {t("convGuide.header.pin")}
        </span>
      </button>

      {expanded && (
        <div
          id={bodyId}
          data-testid="conv-authoring-guide-body"
          className="border-t border-black/[0.06] px-4 pb-4 pt-3"
        >
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            {SECTIONS.map((s) => (
              <ConvGuideSection key={s.titleKey} t={t} section={s} />
            ))}
          </div>

          <div className="mt-5">
            <p
              className={
                "mb-2 text-[10px] font-bold uppercase tracking-[0.16em] " +
                "text-[var(--color-text-tertiary)]"
              }
              id="conv-authoring-guide-try-label"
            >
              {t("convGuide.tryOne.title")}
            </p>
            <ul
              role="list"
              aria-labelledby="conv-authoring-guide-try-label"
              data-testid="conv-authoring-guide-pills"
              className="flex flex-wrap gap-2"
            >
              {PILLS.map((p) => (
                <li key={p.labelKey} className="inline-flex">
                  <button
                    type="button"
                    onClick={() => onPill(p.fillKey)}
                    disabled={pending}
                    data-testid={`conv-authoring-guide-pill-${p.labelKey}`}
                    aria-label={t(p.labelKey)}
                    title={t(p.fillKey)}
                    className={
                      "cursor-pointer rounded-full border border-black/[0.08] " +
                      "bg-white px-2.5 py-1 text-[11px] font-medium " +
                      "text-[var(--color-text-primary)] transition-colors " +
                      "hover:border-[var(--color-accent)] " +
                      "hover:bg-[var(--color-accent)]/[0.04] " +
                      "focus:outline-none focus-visible:ring-2 " +
                      "focus-visible:ring-[var(--color-accent)]/40 " +
                      "disabled:cursor-not-allowed disabled:opacity-50"
                    }
                  >
                    {t(p.labelKey)}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </section>
  )
}

function ConvGuideSection({
  t,
  section,
}: {
  t: T
  section: GuideSection
}): JSX.Element {
  return (
    <div
      data-testid="conv-guide-section"
      className={
        "rounded-lg border border-black/[0.06] " +
        "bg-[var(--color-accent)]/[0.015] px-3 py-2.5"
      }
    >
      <p
        className={
          "text-[10px] font-bold uppercase tracking-[0.14em] " +
          "text-[var(--color-accent)]"
        }
      >
        {t(section.titleKey)}
      </p>
      <p
        className={
          "mt-0.5 text-[11px] leading-snug text-[var(--color-text-tertiary)]"
        }
      >
        {t(section.subtitleKey)}
      </p>
      <ul role="list" className="mt-2 space-y-1.5">
        {section.examples.map((ex) => (
          <li key={ex.exampleKey} data-testid="conv-guide-example">
            <div className="flex items-start gap-1.5 text-[11.5px] leading-snug">
              <span
                aria-hidden
                className={
                  "mt-[1px] inline-block w-3 select-none font-bold text-emerald-600"
                }
              >
                {"✓"}
              </span>
              <span className="text-[var(--color-text-primary)]">
                <span
                  className={
                    "font-mono text-[11px] text-[var(--color-text-secondary)]"
                  }
                >
                  {"“"}
                  {t(ex.exampleKey)}
                  {"”"}
                </span>{" "}
                <span className="text-[var(--color-text-tertiary)]">
                  {t(ex.explainKey)}
                </span>
              </span>
            </div>
          </li>
        ))}
      </ul>
      {section.footerKey && (
        <p
          data-testid="conv-guide-section-footer"
          className={
            "mt-2 rounded border border-amber-400/40 bg-amber-50 px-2 py-1.5 " +
            "text-[10.5px] leading-snug text-amber-900"
          }
        >
          {t(section.footerKey)}
        </p>
      )}
    </div>
  )
}
