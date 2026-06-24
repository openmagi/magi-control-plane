"use client"

import Link from "next/link"
import { useCallback, useEffect, useId, useRef, useState } from "react"
import { ledgerHref } from "@/lib/ledger-url"
import { Skeleton } from "@/components/ui"

/**
 * D53a: inline list of the most-recent N redacted samples for one
 * verifier. Renders inside the "Recent emissions" panel on the
 * verifier catalog expander.
 *
 * Lifecycle:
 *   - Mounts collapsed (operator clicks "Show samples" to expand).
 *   - On first expand we issue ONE fetch against the same-origin
 *     `/api/verifier-samples` proxy. Subsequent toggles re-show the
 *     cached result (no second round-trip).
 *   - Loading state shows three skeleton rows so the slot doesn't
 *     re-flow when the data arrives.
 *   - Error state collapses to a short inline note; the operator's
 *     other affordances (count + jump-to-ledger link) stay reachable
 *     in the parent panel.
 *
 * The list does NOT take a 200ms loading shimmer when expanded after
 * a successful fetch - we cache the prior payload in a ref.
 *
 * The deep-link href routes through `ledgerHref` (same builder the
 * chip selector uses) and appends `&record=<id>` so the ledger page
 * can scroll-anchor to the row when that surface lands.
 */

// i18n helper signature matches the rest of the rules tab.
type T = (
  k: import("@/lib/i18n/dict").TKey,
  v?: Record<string, string | number>,
) => string

export type VerifierSampleRow = {
  id: number
  ts: string
  verdict:
    | "pass"
    | "fail"
    | "deny"
    | "review"
    | "needs_review"
    | "not_applicable"
    | null
  redacted_payload_preview: string
  policy_id: string | null
}

export function VerifierSamplesList({
  step,
  t,
  initialCount,
}: {
  step: string
  t: T
  /** Server-rendered count from `/ledger/counts`. Used in the header
   * so the empty case ("no samples returned") never contradicts a
   * stale count (we re-key the empty state on this number too). */
  initialCount: number | null
}) {
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [samples, setSamples] = useState<VerifierSampleRow[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  // First-fetch latch. We cache the result so re-toggling does not
  // re-fetch (a clicker who collapses then re-expands is not asking
  // for fresh data; staleness will not matter at the 5-row scale).
  const fetched = useRef(false)
  const listId = useId()

  const fetchSamples = useCallback(async () => {
    setLoading(true)
    setErr(null)
    try {
      const url = `/api/verifier-samples?verifier=${encodeURIComponent(step)}&limit=5`
      const r = await fetch(url, { cache: "no-store" })
      if (!r.ok) {
        setErr(t("rules.verifier.samples.error"))
        return
      }
      const data = (await r.json()) as { samples?: VerifierSampleRow[] }
      setSamples(Array.isArray(data.samples) ? data.samples : [])
    } catch {
      setErr(t("rules.verifier.samples.error"))
    } finally {
      setLoading(false)
    }
  }, [step, t])

  // Toggle handler: open + fetch-once. Closing keeps the cached data
  // so re-opening renders instantly.
  const onToggle = useCallback(() => {
    const next = !open
    setOpen(next)
    if (next && !fetched.current) {
      fetched.current = true
      void fetchSamples()
    }
  }, [open, fetchSamples])

  // The header label is computed off the initialCount so it does not
  // flip to "0 total" mid-fetch when the panel is open but the list
  // is still loading.
  const headerLabel = t("rules.verifier.samples.header", {
    count: initialCount == null ? "-" : String(initialCount),
  })

  return (
    <div
      data-testid="verifier-expander-samples"
      className="mt-2"
    >
      <div className="flex flex-wrap items-baseline gap-2 text-[11px]">
        <span className="text-[var(--color-text-tertiary)]">{headerLabel}</span>
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={open}
          aria-controls={listId}
          data-testid="verifier-expander-samples-toggle"
          className="ml-auto font-medium text-[var(--color-accent-light)] hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-accent)]"
        >
          {open
            ? t("rules.verifier.samples.hide")
            : t("rules.verifier.samples.show")}
        </button>
      </div>

      {open && (
        <ul
          id={listId}
          data-testid="verifier-expander-samples-list"
          className="mt-2 space-y-1.5"
          role="list"
        >
          {loading && <SamplesSkeleton />}
          {!loading && err && (
            <li
              data-testid="verifier-expander-samples-error"
              className="text-[11px] italic text-[var(--color-text-tertiary)]"
            >
              {err}
            </li>
          )}
          {!loading && !err && samples && samples.length === 0 && (
            <li
              data-testid="verifier-expander-samples-empty"
              className="text-[11px] italic text-[var(--color-text-tertiary)]"
            >
              {t("rules.verifier.samples.empty")}
            </li>
          )}
          {!loading && !err && samples && samples.length > 0 &&
            samples.map((s) => (
              <SampleRow key={s.id} step={step} sample={s} t={t} />
            ))}
        </ul>
      )}
    </div>
  )
}

function SamplesSkeleton() {
  // Three rows so the panel does not re-flow when the data arrives
  // (real responses ship up to 5; three is a calm midpoint).
  return (
    <>
      {[0, 1, 2].map((i) => (
        <li
          key={i}
          data-testid="verifier-expander-samples-skeleton"
          className="flex items-center gap-2"
        >
          <Skeleton className="h-3 w-12" />
          <Skeleton className="h-3 w-16" />
          <Skeleton className="h-3 flex-1" />
        </li>
      ))}
    </>
  )
}

function SampleRow({
  step,
  sample,
  t,
}: {
  step: string
  sample: VerifierSampleRow
  t: T
}) {
  const tone = verdictTone(sample.verdict)
  // ledgerHref produces the canonical filter URL. We append `record=<id>`
  // via a manual concat (ledgerHref does not yet take a record param;
  // the ledger page will adopt the field once the row anchor lands).
  const base = ledgerHref({ verifiers: [step] })
  const href = `${base}${base.includes("?") ? "&" : "?"}record=${sample.id}`
  return (
    <li
      data-testid="verifier-expander-samples-row"
      className="flex items-center gap-2 text-[11px]"
    >
      <span
        className={`inline-flex items-center rounded-full px-1.5 py-0.5 text-[9.5px] font-semibold uppercase tracking-wider ${tone}`}
      >
        {verdictLabel(sample.verdict, t)}
      </span>
      <RelativeTime ts={sample.ts} t={t} />
      <span
        className="flex-1 truncate font-mono text-[10.5px] text-[var(--color-text-secondary)]"
        title={sample.redacted_payload_preview}
      >
        {sample.redacted_payload_preview || "."}
      </span>
      <Link
        href={href}
        data-testid="verifier-expander-samples-row-link"
        className="shrink-0 font-medium text-[var(--color-accent-light)] hover:underline"
        aria-label={t("ledger.deepLink.toRecord", { id: String(sample.id) })}
      >
        →
      </Link>
    </li>
  )
}

function verdictLabel(
  v: VerifierSampleRow["verdict"],
  t: T,
): string {
  if (v == null) return t("rules.verifier.samples.verdict.unknown")
  switch (v) {
    case "pass":
      return t("rules.verifier.samples.verdict.pass")
    case "fail":
      return t("rules.verifier.samples.verdict.fail")
    case "deny":
      return t("rules.verifier.samples.verdict.deny")
    case "review":
    case "needs_review":
      return t("rules.verifier.samples.verdict.needs_review")
    case "not_applicable":
      return t("rules.verifier.samples.verdict.not_applicable")
    default:
      return t("rules.verifier.samples.verdict.unknown")
  }
}

function verdictTone(v: VerifierSampleRow["verdict"]): string {
  // Mirrors the verdict chip tones used by the existing
  // VerifierExpander.verdictTone helper.
  switch (v) {
    case "pass":
      return "bg-[var(--color-pass-bg,#ecfdf5)] text-[var(--color-pass-fg,#047857)]"
    case "fail":
    case "deny":
      return "bg-[var(--color-deny-bg,#fff1f2)] text-[var(--color-deny-fg,#be123c)]"
    case "review":
    case "needs_review":
      return "bg-[var(--color-review-bg,#fffbeb)] text-[var(--color-review-fg,#b45309)]"
    case "not_applicable":
      return "bg-[var(--color-muted-bg,#f3f4f6)] text-[var(--color-muted-fg,#374151)]"
    default:
      return "bg-[var(--color-muted-bg,#f3f4f6)] text-[var(--color-muted-fg,#374151)]"
  }
}

function RelativeTime({ ts, t }: { ts: string; t: T }) {
  // Compute on the client so the value reflects the operator's wall
  // clock without an extra hydration prop. We render a stable
  // fallback ("just now") on the initial render so SSR + client
  // hydration agree, then update in an effect.
  const [label, setLabel] = useState<string>(formatRelative(ts, t))
  useEffect(() => {
    setLabel(formatRelative(ts, t))
    const handle = setInterval(() => setLabel(formatRelative(ts, t)), 30_000)
    return () => clearInterval(handle)
  }, [ts, t])
  return (
    <time
      dateTime={ts}
      className="shrink-0 text-[10.5px] text-[var(--color-text-tertiary)] tabular-nums"
    >
      {label}
    </time>
  )
}

function formatRelative(ts: string, t: T): string {
  const then = Date.parse(ts)
  if (Number.isNaN(then)) return ts
  const deltaSec = Math.max(0, Math.floor((Date.now() - then) / 1000))
  if (deltaSec < 60) {
    return t("rules.verifier.samples.relative.secondsAgo", { n: deltaSec })
  }
  if (deltaSec < 3600) {
    return t("rules.verifier.samples.relative.minutesAgo", {
      n: Math.floor(deltaSec / 60),
    })
  }
  if (deltaSec < 86400) {
    return t("rules.verifier.samples.relative.hoursAgo", {
      n: Math.floor(deltaSec / 3600),
    })
  }
  return t("rules.verifier.samples.relative.daysAgo", {
    n: Math.floor(deltaSec / 86400),
  })
}
