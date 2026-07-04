"use client"
import { useMemo, useState } from "react"

import type {
  LedgerAggregateBucket, LedgerAggregateResponse, OverviewActionKey,
} from "@/lib/cloud"

/**
 * D76: hand-rolled SVG stacked-bar chart for the /overview 24h
 * emission view. The dashboard ships no chart lib (recharts is not in
 * `web/package.json`); a hand-rolled SVG keeps the bundle small and
 * dodges the "react server component vs client" hydration issues a
 * library would drag in.
 *
 * Visual contract:
 *   - One column per bucket. Columns stack action segments bottom-up
 *     in the order the response gives us (block / ask / audit /
 *     inject_context / run_command / input_rewrite).
 *   - Y axis: 0 → `yMax` (computed). Zero-data case degenerates to a
 *     centered empty-state caption (no NaN axis, no fake gridlines).
 *   - X axis ticks every Nth bucket so a 24-bar layout still labels.
 *   - Hover (mouse) or tap (touch) reveals the per-bucket detail
 *     panel. Tapping the same column a second time dismisses it.
 *
 * Accessibility:
 *   - `role="img"` + descriptive `aria-label` so screen readers get a
 *     summary even though the visual encoding is rich.
 *   - A parallel hidden <table> below the chart gives SR users a
 *     row-by-row reading path the SVG <g tabIndex> trick does not
 *     reliably provide across Safari/VoiceOver and Chrome/TalkBack.
 *   - The floating detail panel is rendered as a plain styled box
 *     (not `role="status" aria-live`) so mouse-scrub across 24 bars
 *     does not flood screen readers — meaningful announcements come
 *     from the table + the parent OverviewLive headline.
 *   - Bar fills use Tailwind 600-series hues chosen for separation;
 *     not formally CVD-validated. Each segment also carries a
 *     redundant `<title>` so SR / hover reads the action by label,
 *     not by hue alone.
 */

type Props = {
  data: LedgerAggregateResponse
  /** Localized labels for each action key. */
  actionLabel: Record<OverviewActionKey, string>
  /** Localized "no data" empty state body. */
  emptyBody: string
  /** Locale-aware Intl.NumberFormat instance. */
  nf: Intl.NumberFormat
  /** Optional explicit locale tag for date formatting. Falls back to
   *  `nf.resolvedOptions().locale` so the chart's hour labels and the
   *  tooltip date string never diverge from each other or from the
   *  parent surface's number formatting. */
  locale?: string
}

const ACTION_COLORS: Record<OverviewActionKey, string> = {
  block: "var(--color-overview-action-block, #DC2626)",          // red-600
  ask: "var(--color-overview-action-ask, #D97706)",              // amber-600
  audit: "var(--color-overview-action-audit, #2563EB)",          // blue-600
  inject_context: "var(--color-overview-action-inject, #7C3AED)", // violet-600
  run_command: "var(--color-overview-action-run, #059669)",      // emerald-600
  input_rewrite: "var(--color-overview-action-rewrite, #DB2777)", // pink-600
}

// SVG view box. We size in CSS via 100% width and viewBox-driven aspect.
const VIEW_W = 720
const VIEW_H = 220
const PAD_L = 32
const PAD_R = 8
const PAD_T = 8
const PAD_B = 28
const PLOT_W = VIEW_W - PAD_L - PAD_R
const PLOT_H = VIEW_H - PAD_T - PAD_B

function bucketStackTotal(b: LedgerAggregateBucket): number {
  return Object.values(b.by_action).reduce((a, n) => a + n, 0)
}

function resolveLocale(locale: string | undefined, nf: Intl.NumberFormat): string {
  if (locale && locale.length > 0) return locale
  return nf.resolvedOptions().locale ?? "en-US"
}

function formatHourLabel(tsStart: number, locale: string): string {
  // Render the hour label in the operator's TZ. Use Intl.DateTimeFormat
  // with the resolved locale so the X-axis ticks, the tooltip date,
  // and the hidden SR table all use the same locale source.
  const d = new Date(tsStart * 1000)
  const dtf = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit" })
  return dtf.format(d)
}

function formatBucketTimestamp(tsStart: number, locale: string): string {
  return new Intl.DateTimeFormat(locale, {
    dateStyle: "short", timeStyle: "short",
  }).format(new Date(tsStart * 1000))
}

export function EmissionChart({
  data, actionLabel, emptyBody, nf, locale,
}: Props) {
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null)
  const { buckets, action_buckets } = data
  const localeTag = resolveLocale(locale, nf)

  const yMax = useMemo(() => {
    let m = 0
    for (const b of buckets) {
      const total = bucketStackTotal(b)
      if (total > m) m = total
    }
    return m
  }, [buckets])

  const isEmpty = yMax === 0

  // Pre-compute bar geometry. When there are 0 buckets we still render
  // an empty grid so the layout doesn't pop in once data arrives.
  const n = Math.max(1, buckets.length)
  const colW = PLOT_W / n
  // For very narrow chart configurations (n <= 2) widen the bars so a
  // single bucket reads as a deliberate solo column rather than a
  // misaligned 28px glyph floating in a 680px gutter.
  const barW = n <= 2
    ? Math.max(2, Math.min(colW * 0.6, 120))
    : Math.max(2, Math.min(colW - 2, 28))
  const yScale = (v: number) => (isEmpty ? 0 : (v / yMax) * PLOT_H)

  // X tick stride. With 24 buckets we want ~6 labels.
  const tickStride = Math.max(1, Math.ceil(n / 6))

  // Build per-bucket segments + a summary ariaLabel.
  const ariaLabel = isEmpty
    ? emptyBody
    : buckets
        .map(b => `${formatHourLabel(b.ts_start, localeTag)}: ${nf.format(b.count)}`)
        .join("; ")

  // Selection handlers. A second tap on the same column dismisses the
  // detail panel (the only way to "untap" on touch).
  const selectColumn = (i: number) => {
    setSelectedIdx(prev => (prev === i ? null : i))
  }
  const clearSelection = () => setSelectedIdx(null)

  return (
    <div className="relative w-full">
      <svg
        role="img"
        aria-label={ariaLabel}
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        preserveAspectRatio="xMidYMid meet"
        className="w-full h-auto"
        // Tap on empty SVG space (i.e. not on a column) clears the
        // detail panel so the operator can dismiss without a second
        // tap on the active column.
        onClick={(ev) => {
          if (ev.target === ev.currentTarget) clearSelection()
        }}
      >
        {/* Y-axis baseline + faint horizontal gridlines.
            When the chart is empty we render only the baseline so the
            25/50/75/100% dashes don't imply a scale that doesn't exist. */}
        <g stroke="var(--color-border-subtle, rgba(0,0,0,0.08))" strokeWidth={1}>
          {isEmpty ? (
            <line
              x1={PAD_L} x2={VIEW_W - PAD_R}
              y1={PAD_T + PLOT_H}
              y2={PAD_T + PLOT_H}
            />
          ) : (
            [0.25, 0.5, 0.75, 1].map(f => (
              <line
                key={f}
                x1={PAD_L} x2={VIEW_W - PAD_R}
                y1={PAD_T + PLOT_H * (1 - f)}
                y2={PAD_T + PLOT_H * (1 - f)}
                strokeDasharray="2 3"
              />
            ))
          )}
        </g>

        {/* Y-axis ticks: 0 and max */}
        <g
          fontSize={10}
          fill="var(--color-text-tertiary, #6B7280)"
          textAnchor="end"
        >
          <text x={PAD_L - 4} y={PAD_T + PLOT_H + 4}>0</text>
          {!isEmpty && (
            <text x={PAD_L - 4} y={PAD_T + 10}>
              {nf.format(yMax)}
            </text>
          )}
        </g>

        {/* Empty-state copy in-SVG so sighted users see why the chart
            is blank rather than reading a flat axis as "chart broke". */}
        {isEmpty && (
          <text
            x={PAD_L + PLOT_W / 2}
            y={PAD_T + PLOT_H / 2}
            textAnchor="middle"
            fontSize={12}
            fill="var(--color-text-secondary, #6B7280)"
            data-testid="overview-chart-empty"
          >
            {emptyBody}
          </text>
        )}

        {/* Stacked bars */}
        {!isEmpty && buckets.map((b, i) => {
          const cx = PAD_L + colW * i + colW / 2
          const x = cx - barW / 2
          let runningBottom = PAD_T + PLOT_H
          return (
            <g
              key={b.ts_start}
              onMouseEnter={() => setSelectedIdx(i)}
              onMouseLeave={() =>
                // Only clear if the column being left is the
                // currently-selected one and selection arose from
                // hover (no second tap to dismiss yet). The tap path
                // owns dismissal via toggling.
                setSelectedIdx(prev => (prev === i ? null : prev))
              }
              onClick={(ev) => {
                ev.stopPropagation()
                selectColumn(i)
              }}
              style={{ cursor: "pointer" }}
            >
              {/* Click target: a transparent rect spanning the column
                  so hover / tap doesn't require pixel-perfect aim on
                  a thin bar. */}
              <rect
                x={PAD_L + colW * i} y={PAD_T}
                width={colW} height={PLOT_H}
                fill="transparent"
              />
              {action_buckets.map((a) => {
                const v = b.by_action[a] ?? 0
                if (v <= 0) return null
                const h = yScale(v)
                runningBottom -= h
                return (
                  <rect
                    key={a}
                    x={x} y={runningBottom}
                    width={barW} height={h}
                    fill={ACTION_COLORS[a]}
                    rx={2}
                  >
                    <title>{`${actionLabel[a]}: ${nf.format(v)}`}</title>
                  </rect>
                )
              })}
            </g>
          )
        })}

        {/* X-axis labels */}
        {!isEmpty && (
          <g
            fontSize={10}
            fill="var(--color-text-tertiary, #6B7280)"
            textAnchor="middle"
          >
            {buckets.map((b, i) => {
              if (i % tickStride !== 0) return null
              const cx = PAD_L + colW * i + colW / 2
              return (
                <text key={b.ts_start} x={cx} y={VIEW_H - 8}>
                  {formatHourLabel(b.ts_start, localeTag)}
                </text>
              )
            })}
          </g>
        )}

        {/* Hover indicator */}
        {selectedIdx !== null && !isEmpty && (
          <rect
            x={PAD_L + colW * selectedIdx} y={PAD_T}
            width={colW} height={PLOT_H}
            fill="var(--color-bg-hover, rgba(0,0,0,0.04))"
            pointerEvents="none"
          />
        )}
      </svg>

      {/* Detail panel — placed near the active column (left half =>
          left-aligned, right half => right-aligned) so the operator's
          gaze doesn't have to shuttle across the chart to read it. */}
      {selectedIdx !== null && !isEmpty && buckets[selectedIdx] && (
        <ChartDetailPanel
          bucket={buckets[selectedIdx]}
          actionLabel={actionLabel}
          actionOrder={action_buckets}
          nf={nf}
          locale={localeTag}
          alignRight={selectedIdx >= buckets.length / 2}
        />
      )}

      {/* Legend */}
      <ChartLegend actionOrder={action_buckets} actionLabel={actionLabel} />

      {/* Hidden SR-only parallel table. Screen readers (Safari +
          VoiceOver, Chrome + TalkBack) can navigate a table reliably
          where they cannot navigate a tabIndex'd SVG <g>. This is the
          canonical SR reading path for the chart's values. */}
      <SrBucketTable
        buckets={buckets}
        actionLabel={actionLabel}
        actionOrder={action_buckets}
        nf={nf}
        locale={localeTag}
      />
    </div>
  )
}

function ChartDetailPanel({
  bucket, actionLabel, actionOrder, nf, locale, alignRight,
}: {
  bucket: LedgerAggregateBucket
  actionLabel: Record<OverviewActionKey, string>
  actionOrder: OverviewActionKey[]
  nf: Intl.NumberFormat
  locale: string
  alignRight: boolean
}) {
  const total = bucketStackTotal(bucket)
  // No `role="status" aria-live`: a hover-driven panel re-announces on
  // every column scrub, which floods SR users. SR access to bucket
  // values comes from `SrBucketTable` below.
  const positionClass = alignRight ? "top-2 right-2" : "top-2 left-2"
  return (
    <div
      className={
        "pointer-events-none absolute rounded-md "
        + "border border-[var(--color-border-strong)] "
        + "bg-[var(--color-surface-raised)] px-3 py-2 text-xs shadow-sm "
        + positionClass
      }
      data-testid="overview-chart-detail"
    >
      <div className="font-medium mb-1">
        {formatBucketTimestamp(bucket.ts_start, locale)}
      </div>
      <div className="text-[var(--color-text-secondary)]">
        Total: {nf.format(total)}
      </div>
      {actionOrder.map(a => {
        const v = bucket.by_action[a] ?? 0
        if (v <= 0) return null
        return (
          <div key={a} className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className="inline-block w-2 h-2 rounded-sm"
              style={{ background: ACTION_COLORS[a] }}
            />
            <span>{actionLabel[a]}: {nf.format(v)}</span>
          </div>
        )
      })}
    </div>
  )
}

function ChartLegend({
  actionOrder, actionLabel,
}: {
  actionOrder: OverviewActionKey[]
  actionLabel: Record<OverviewActionKey, string>
}) {
  return (
    <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs
                   text-[var(--color-text-secondary)]">
      {actionOrder.map(a => (
        <li key={a} className="flex items-center gap-2">
          <span
            aria-hidden="true"
            className="inline-block w-2 h-2 rounded-sm"
            style={{ background: ACTION_COLORS[a] }}
          />
          <span>{actionLabel[a]}</span>
        </li>
      ))}
    </ul>
  )
}

function SrBucketTable({
  buckets, actionLabel, actionOrder, nf, locale,
}: {
  buckets: LedgerAggregateBucket[]
  actionLabel: Record<OverviewActionKey, string>
  actionOrder: OverviewActionKey[]
  nf: Intl.NumberFormat
  locale: string
}) {
  // Visually hidden but still in the accessibility tree. `sr-only`
  // isn't a configured Tailwind utility here so we hand-roll the
  // clip-path technique inline.
  const srOnly: React.CSSProperties = {
    position: "absolute",
    width: "1px",
    height: "1px",
    padding: 0,
    margin: "-1px",
    overflow: "hidden",
    clip: "rect(0,0,0,0)",
    whiteSpace: "nowrap",
    border: 0,
  }
  return (
    <table style={srOnly} data-testid="overview-chart-sr-table">
      <caption>Emission counts by bucket</caption>
      <thead>
        <tr>
          <th scope="col">Bucket start</th>
          <th scope="col">Total</th>
          {actionOrder.map(a => (
            <th key={a} scope="col">{actionLabel[a]}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {buckets.map(b => (
          <tr key={b.ts_start}>
            <th scope="row">{formatBucketTimestamp(b.ts_start, locale)}</th>
            <td>{nf.format(bucketStackTotal(b))}</td>
            {actionOrder.map(a => (
              <td key={a}>{nf.format(b.by_action[a] ?? 0)}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export default EmissionChart
