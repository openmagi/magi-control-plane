"use client"

/**
 * P9 (issue #1 D49): client island that owns the per-kind text input
 * and dynamically reruns the cumulative-judgment heuristic on each
 * keystroke.
 *
 * Why a client island (and not the original server-side defaultValue
 * input + server-rendered tip): the server tip only flipped state
 * AFTER the user submitted Step 3, so authors typing "all verifiers
 * must pass…" never saw the tip until they had walked through Action /
 * Name / Review. The dynamic surfacing is the whole point of P9 — the
 * tip is informative cheap noise if it appears 4 steps later.
 *
 * Responsibilities:
 *
 *   - Render the active textarea / input for one of
 *     regex / llm_critic / shacl, controlled by local state. The
 *     server form still picks up the value via the input's `name`
 *     attribute (the form submit reads the live DOM value, no hidden
 *     mirror needed).
 *   - Rerun detectCumulativeSteering on each keystroke (debounced
 *     ~150ms to avoid layout thrash).
 *   - Show the steering tip when the heuristic fires AND the per-kind
 *     dismissal flag in sessionStorage is not set.
 *   - When the user clicks "Switch to evidence_ref" / "Switch to
 *     pre_final + evidence_ref", rebuild the href from the LIVE local
 *     text (the in-flight value would otherwise be lost). When they
 *     click "Got it, keep payload-kind", set the sessionStorage flag
 *     so the tip stays dismissed within this tab/session and rerender.
 *
 * The wizard URL no longer carries `keepKind=1`; the suppression is
 * session-scoped to a single tab.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import Link from "next/link"
import {
  detectCumulativeSteering,
  type SteerableConditionKind,
} from "@/lib/payload-steering"

/** Snapshot of the wizard state that the steering tip needs to build
 * the same-page switch hrefs. We accept a plain serialized snapshot
 * (no server-only types) so this can live in a client island. */
export interface WizardSnapshot {
  lifecycle?: string
  toolScope?: string
  conditionKind?: string
  fetchDomain?: string
  allowlist?: string
  pattern?: string
  llmCriterion?: string
  evidenceRefs?: string[]
  shaclTtl?: string
  action?: string
  id?: string
  description?: string
}

interface CommonProps {
  kind: SteerableConditionKind
  locale: "ko" | "en"
  state: WizardSnapshot
  /** True when evidence_ref is a legal kind under the current
   * lifecycle. (CONDITION_KINDS_BY_LIFECYCLE check lives in the server
   * parent; we pass the result down because the lookup table is server-
   * authoritative.) */
  evidenceAllowed: boolean
  /** Server-built fallback href that swaps the kind in URL state but
   * does NOT include in-flight text. Used as the starting point for
   * the per-keystroke rebuild. */
  baseSwitchHref: string
  baseSwitchPreFinalHref: string
  /** DOM id of the input/textarea (kept stable so PayloadFieldChips
   * can keep splicing into it). */
  inputId: string
  /** Initial value seeded from URL state at server render. */
  initialValue: string
  /** Input chrome — provided by the parent so we don't duplicate
   * styles. */
  className?: string
  /** Field-specific props for the actual element. */
  fieldElement: "textarea" | "input"
  rows?: number
  placeholder?: string
  maxLength?: number
  monospace?: boolean
  name: "pattern" | "llmCriterion" | "shaclTtl"
}

const DEBOUNCE_MS = 150

function sessionKey(kind: SteerableConditionKind): string {
  return `magi-cp:steering-dismissed:${kind}`
}

function readDismissed(kind: SteerableConditionKind): boolean {
  if (typeof window === "undefined") return false
  try {
    return window.sessionStorage.getItem(sessionKey(kind)) === "1"
  } catch {
    // sessionStorage can throw in private-mode Safari etc. — fail
    // open: show the tip.
    return false
  }
}

function writeDismissed(kind: SteerableConditionKind, dismissed: boolean): void {
  if (typeof window === "undefined") return
  try {
    if (dismissed) {
      window.sessionStorage.setItem(sessionKey(kind), "1")
    } else {
      window.sessionStorage.removeItem(sessionKey(kind))
    }
  } catch {
    // ignore
  }
}

/** Rebuild a wizard href with the live text spliced into the right
 * field. The server gave us a base URL that already has every OTHER
 * URL param right; we just need to overwrite the one that maps to the
 * current input. */
function rebuildHrefWithLiveText(
  baseHref: string,
  name: "pattern" | "llmCriterion" | "shaclTtl",
  liveText: string,
): string {
  try {
    // baseHref is a relative URL like "/policies/new?…". URL needs an
    // origin; use a throwaway one and emit the path+search at the end.
    const u = new URL(baseHref, "http://localhost")
    if (liveText) {
      u.searchParams.set(name, liveText)
    } else {
      u.searchParams.delete(name)
    }
    return u.pathname + (u.search ? u.search : "")
  } catch {
    return baseHref
  }
}

export default function SteeringAwareField(props: CommonProps): JSX.Element {
  const {
    kind, locale, state, evidenceAllowed,
    baseSwitchHref, baseSwitchPreFinalHref,
    inputId, initialValue, className, fieldElement,
    rows, placeholder, maxLength, monospace, name,
  } = props

  const ko = locale === "ko"

  const [text, setText] = useState<string>(initialValue ?? "")
  const [dismissed, setDismissed] = useState<boolean>(false)
  // Debounced version of `text` — drives the heuristic only.
  const [debouncedText, setDebouncedText] = useState<string>(initialValue ?? "")

  // Read sessionStorage on mount. We can't read during SSR so this
  // hydrates dismissed=true on the next tick when applicable.
  useEffect(() => {
    setDismissed(readDismissed(kind))
  }, [kind])

  // Debounce text changes into the detector input.
  useEffect(() => {
    if (text === debouncedText) return
    const id = window.setTimeout(() => {
      setDebouncedText(text)
    }, DEBOUNCE_MS)
    return () => window.clearTimeout(id)
  }, [text, debouncedText])

  // External splice from PayloadFieldChips: it fires an 'input' event
  // on the textarea. React doesn't see it (we control the value), so
  // listen on the DOM element directly and pull el.value into state.
  const elRef = useRef<HTMLTextAreaElement | HTMLInputElement | null>(null)
  useEffect(() => {
    const el = elRef.current
    if (!el) return
    const onInput = (): void => {
      if (el.value !== text) setText(el.value)
    }
    el.addEventListener("input", onInput)
    return () => el.removeEventListener("input", onInput)
  }, [text])

  const det = useMemo(
    () => detectCumulativeSteering({ conditionKind: kind, text: debouncedText }),
    [kind, debouncedText],
  )

  const switchHref = useMemo(
    () => rebuildHrefWithLiveText(baseSwitchHref, name, text),
    [baseSwitchHref, name, text],
  )
  const switchPreFinalHref = useMemo(
    () => rebuildHrefWithLiveText(baseSwitchPreFinalHref, name, text),
    [baseSwitchPreFinalHref, name, text],
  )

  const onKeep = useCallback((e: React.MouseEvent<HTMLButtonElement>) => {
    e.preventDefault()
    writeDismissed(kind, true)
    setDismissed(true)
  }, [kind])

  const onChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement | HTMLInputElement>) => {
      setText(e.target.value)
    },
    [],
  )

  const inputCommon = {
    id: inputId,
    name,
    "data-name": name, // for the wizard test wiring
    value: text,
    onChange,
    placeholder,
    spellCheck: false,
    autoComplete: "off",
    maxLength,
    className: monospace
      ? (className ?? "") + " font-mono"
      : className,
  } as const

  const showTip = det.shouldSteer && !dismissed
  const matchedPreview = det.matched.slice(0, 3).join(", ")

  return (
    <>
      {fieldElement === "textarea" ? (
        <textarea
          ref={(el) => {
            elRef.current = el
          }}
          rows={rows ?? 3}
          {...inputCommon}
        />
      ) : (
        <input
          ref={(el) => {
            elRef.current = el
          }}
          {...inputCommon}
        />
      )}
      {/* Live region: announces the tip when it appears dynamically so
          screen-reader users notice. polite to avoid interrupting. */}
      <div
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className="sr-only"
      >
        {showTip
          ? (ko
              ? "이 조건이 누적적 판단으로 보입니다. evidence_ref로 전환을 고려하세요."
              : "This criterion looks like an accumulated judgment. Consider switching to evidence_ref.")
          : ""}
      </div>
      {showTip && (
        <div
          role="note"
          className="mt-2 rounded-lg border border-amber-400/40 bg-amber-50 p-3 text-xs leading-relaxed text-amber-900"
          data-testid={`steering-tip-${kind}`}
        >
          <p className="m-0 font-semibold">
            {ko
              ? "이 조건이 누적적 판단으로 보입니다"
              : "This looks like an accumulated judgment"}
          </p>
          <p className="mt-1 m-0">
            {ko
              ? "이 정책의 기준은 여러 턴에 걸쳐 누적된 결과(테스트 통과, 인용 검증, 이전 턴 등)를 보는 것 같습니다. 페이로드-종류 조건(LLM critic / SHACL)은 한 번의 호출만 봅니다. 턴을 가로질러 살아남으려면 step verifier(evidence_ref)로 작성하세요."
              : "Your criteria seem to judge something cumulative across turns (test results, citation history, previous turns). Payload-kind conditions only see ONE turn at a time. Use a step verifier (evidence_ref) so the judgment survives across turns."}
          </p>
          {matchedPreview && (
            <p className="mt-1 m-0 text-[10.5px] text-amber-800/80">
              {ko ? "감지된 키워드: " : "Triggered by: "}
              <code className="font-mono">{matchedPreview}</code>
            </p>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {evidenceAllowed ? (
              <Link
                href={switchHref}
                className="inline-flex items-center rounded-md bg-amber-600 px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-amber-700 hover:no-underline"
                data-testid={`steering-switch-${kind}`}
              >
                {ko ? "evidence_ref로 전환" : "Switch to evidence_ref"}
              </Link>
            ) : (
              <Link
                href={switchPreFinalHref}
                className="inline-flex items-center rounded-md bg-amber-600 px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-amber-700 hover:no-underline"
                data-testid={`steering-switch-prefinal-${kind}`}
              >
                {ko
                  ? "pre_final + evidence_ref로 전환"
                  : "Switch to pre_final + evidence_ref"}
              </Link>
            )}
            <button
              type="button"
              onClick={onKeep}
              className="inline-flex items-center rounded-md border border-amber-400/60 bg-white px-2.5 py-1 text-[11px] font-semibold text-amber-900 hover:bg-amber-100 hover:no-underline cursor-pointer"
              data-testid={`steering-dismiss-${kind}`}
            >
              {ko ? "이해함, 페이로드-종류 유지" : "Got it, keep payload-kind"}
            </button>
          </div>
        </div>
      )}
    </>
  )
}
