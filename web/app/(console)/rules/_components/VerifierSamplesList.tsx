"use client"

import Link from "next/link"
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react"
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
 *   - Loading state shows five skeleton rows (the default request
 *     limit) so the loaded list contracts by at most a few rows,
 *     never expands the panel.
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
  /** Reserved in the wire contract; the cloud does NOT project this
   * field today (fail-closed: no producer + no redaction contract
   * means no value reaches the browser). The type stays nullable so a
   * future producer can populate it without a frontend type change. */
  policy_id?: string | null
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

  // Single "now" tick shared by every SampleRow's relative-time label.
  // Hoisting the interval here (instead of one per row) keeps the DOM
  // timer count at 1 regardless of how many samples render, and avoids
  // orphaning intervals when a parent re-render swaps row keys.
  const [now, setNow] = useState<number>(() => Date.now())
  useEffect(() => {
    if (!open) return
    const handle = setInterval(() => setNow(Date.now()), 30_000)
    return () => clearInterval(handle)
  }, [open])

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

      {/* The controlled <ul> is rendered unconditionally so the
          toggle's aria-controls={listId} always resolves to a real
          element in the DOM. Visibility flips via the `hidden`
          attribute. aria-busy + aria-live signal loading state to
          assistive tech (WCAG 4.1.3). */}
      <ul
        id={listId}
        data-testid="verifier-expander-samples-list"
        className="mt-2 space-y-1.5"
        role="list"
        hidden={!open}
        aria-busy={open && loading}
        aria-live="polite"
      >
        {open && loading && (
          <>
            <li className="sr-only">
              {t("rules.verifier.samples.loading")}
            </li>
            <SamplesSkeleton />
          </>
        )}
        {open && !loading && err && (
          <li
            data-testid="verifier-expander-samples-error"
            className="text-[11px] italic text-[var(--color-text-tertiary)]"
          >
            {err}
          </li>
        )}
        {open && !loading && !err && samples && samples.length === 0 && (
          <li
            data-testid="verifier-expander-samples-empty"
            className="text-[11px] italic text-[var(--color-text-tertiary)]"
          >
            {t("rules.verifier.samples.empty")}
          </li>
        )}
        {open && !loading && !err && samples && samples.length > 0 &&
          samples.map((s) => (
            <SampleRow key={s.id} step={step} sample={s} t={t} now={now} />
          ))}
      </ul>
    </div>
  )
}

function SamplesSkeleton() {
  // Five rows match the default request limit, so the loaded list
  // contracts by at most a few rows when fewer samples come back,
  // never expands. The per-row geometry mirrors SampleRow's (chip +
  // time + truncated mono + arrow) so the skeleton -> loaded swap
  // doesn't reflow horizontally either. aria-hidden lets SR users
  // skip the placeholder shapes; the parent <ul aria-busy aria-live>
  // + sr-only "loading" line carry the perceivable state.
  return (
    <>
      {[0, 1, 2, 3, 4].map((i) => (
        <li
          key={i}
          aria-hidden="true"
          data-testid="verifier-expander-samples-skeleton"
          className="flex items-center gap-2"
        >
          <Skeleton className="h-3 w-12" />
          <Skeleton className="h-3 w-16" />
          <Skeleton className="h-3 flex-1" />
          <Skeleton className="h-3 w-4" />
        </li>
      ))}
    </>
  )
}

function SampleRow({
  step,
  sample,
  t,
  now,
}: {
  step: string
  sample: VerifierSampleRow
  t: T
  /** Single shared "now" tick from the parent list (see
   * VerifierSamplesList). Lets each row recompute its relative label
   * without each instantiating its own setInterval. */
  now: number
}) {
  const tone = verdictTone(sample.verdict)
  // ledgerHref produces the canonical filter URL. We append `record=<id>`
  // via a manual concat (ledgerHref does not yet take a record param;
  // the ledger page will adopt the field once the row anchor lands).
  const base = ledgerHref({ verifiers: [step] })
  const href = `${base}${base.includes("?") ? "&" : "?"}record=${sample.id}`
  const hasPreview = !!sample.redacted_payload_preview
  const deepLinkLabel = t("ledger.deepLink.toRecord", { id: String(sample.id) })
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
      <RelativeTime ts={sample.ts} t={t} now={now} />
      {hasPreview ? (
        // The preview is fully readable by SR users via textContent
        // and by sighted users on focus / hover (the row wraps the
        // truncated mono span). No `title=` here: per the parent
        // expander's a11y contract (VerifierExpander.test.ts line
        // 48-54) `title=` is mouse-only and inaccessible to keyboard
        // / SR; full-text inspection happens in the linked ledger
        // record view.
        <span
          data-testid="verifier-expander-samples-row-preview"
          className="flex-1 truncate font-mono text-[10.5px] text-[var(--color-text-secondary)]"
        >
          {sample.redacted_payload_preview}
        </span>
      ) : (
        <span
          data-testid="verifier-expander-samples-row-preview-empty"
          className="flex-1 truncate italic text-[10.5px] text-[var(--color-text-tertiary)]"
        >
          {t("rules.verifier.samples.previewUnavailable")}
        </span>
      )}
      <Link
        href={href}
        data-testid="verifier-expander-samples-row-link"
        // `title={href}` surfaces the destination on mouse hover
        // (the brief's "deep-link previews href on hover (no surprise
        // navigation)") while aria-label carries the SR-friendly
        // "Open ledger record #N" copy. Visible "Open" text + arrow
        // gives keyboard users a destination affordance beyond the
        // focus ring; mirrors the brief's no-surprise-navigation
        // expectation.
        title={href}
        className="inline-flex shrink-0 items-center gap-0.5 font-medium text-[var(--color-accent-light)] hover:underline"
        aria-label={deepLinkLabel}
      >
        <span aria-hidden="true">{deepLinkLabel}</span>
        <span aria-hidden="true">→</span>
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

function RelativeTime({ ts, t, now }: { ts: string; t: T; now: number }) {
  // The component is only mounted post-fetch on the client (the
  // parent <ul> is hidden until the operator opens the expander, and
  // every sample row comes from a client-side fetch); SSR hydration
  // never sees this subtree. The label recomputes whenever `now`
  // changes (the parent ticks it every 30s while the expander is
  // open) so we don't need an effect or local timer here.
  const label = useMemo(() => formatRelative(ts, t, now), [ts, t, now])
  return (
    <time
      dateTime={ts}
      className="shrink-0 text-[10.5px] text-[var(--color-text-tertiary)] tabular-nums"
    >
      {label}
    </time>
  )
}

function formatRelative(ts: string, t: T, now: number): string {
  const then = Date.parse(ts)
  if (Number.isNaN(then)) return ts
  const deltaSec = Math.max(0, Math.floor((now - then) / 1000))
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
