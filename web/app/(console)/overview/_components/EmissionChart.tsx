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
 *     flat baseline (no NaN axis, no division by zero).
 *   - X axis ticks every Nth bucket so a 24-bar layout still labels.
 *   - Hover surfaces a tooltip with the bucket counts; touch users
 *     also see the same panel after a tap (no hover-only affordance).
 *
 * Accessibility:
 *   - `role="img"` + descriptive `aria-label` so screen readers get a
 *     summary even though the visual encoding is rich.
 *   - Color-blind safe palette (HSL hues spaced for distinguishability
 *     across the protan / deutan / tritan axes; not exhaustive, but a
 *     baseline above arbitrary picks).
 */

type Props = {
  data: LedgerAggregateResponse
  /** Localized labels for each action key. */
  actionLabel: Record<OverviewActionKey, string>
  /** Localized "no data" empty state body. */
  emptyBody: string
  /** Locale-aware Intl.NumberFormat instance. */
  nf: Intl.NumberFormat
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

function formatHourLabel(tsStart: number, locale: Intl.NumberFormat): string {
  // Render the hour label in the operator's TZ. Use Date directly;
  // Intl.NumberFormat gives us the locale tag for hour-cycle
  // selection.
  const d = new Date(tsStart * 1000)
  const lang = (locale.resolvedOptions().locale ?? "en-US")
  const dtf = new Intl.DateTimeFormat(lang, { hour: "2-digit", minute: "2-digit" })
  return dtf.format(d)
}

export function EmissionChart({ data, actionLabel, emptyBody, nf }: Props) {
  const [hoverIdx, setHoverIdx] = useState<number | null>(null)
  const { buckets, action_buckets } = data

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
  const barW = Math.max(2, Math.min(colW - 2, 28))
  const yScale = (v: number) => (isEmpty ? 0 : (v / yMax) * PLOT_H)

  // X tick stride. With 24 buckets we want ~6 labels.
  const tickStride = Math.max(1, Math.ceil(n / 6))

  // Build per-bucket segments + a summary ariaLabel.
  const ariaLabel = isEmpty
    ? emptyBody
    : buckets
        .map(b => `${formatHourLabel(b.ts_start, nf)}: ${nf.format(b.count)}`)
        .join("; ")

  return (
    <div className="relative w-full">
      <svg
        role="img"
        aria-label={ariaLabel}
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        preserveAspectRatio="xMidYMid meet"
        className="w-full h-auto"
      >
        {/* Y-axis baseline + faint horizontal gridlines */}
        <g stroke="var(--color-border-subtle, rgba(0,0,0,0.08))" strokeWidth={1}>
          {[0.25, 0.5, 0.75, 1].map(f => (
            <line
              key={f}
              x1={PAD_L} x2={VIEW_W - PAD_R}
              y1={PAD_T + PLOT_H * (1 - f)}
              y2={PAD_T + PLOT_H * (1 - f)}
              strokeDasharray="2 3"
            />
          ))}
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

        {/* Stacked bars */}
        {buckets.map((b, i) => {
          const cx = PAD_L + colW * i + colW / 2
          const x = cx - barW / 2
          let runningBottom = PAD_T + PLOT_H
          return (
            <g
              key={b.ts_start}
              onMouseEnter={() => setHoverIdx(i)}
              onMouseLeave={() => setHoverIdx(prev => (prev === i ? null : prev))}
              onFocus={() => setHoverIdx(i)}
              onBlur={() => setHoverIdx(prev => (prev === i ? null : prev))}
              tabIndex={isEmpty ? -1 : 0}
              style={{ cursor: isEmpty ? "default" : "pointer" }}
            >
              {/* Click target: a transparent rect spanning the column
                  so hover doesn't require pixel-perfect aim on a thin
                  bar. */}
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
                {formatHourLabel(b.ts_start, nf)}
              </text>
            )
          })}
        </g>

        {/* Hover indicator */}
        {hoverIdx !== null && !isEmpty && (
          <rect
            x={PAD_L + colW * hoverIdx} y={PAD_T}
            width={colW} height={PLOT_H}
            fill="var(--color-bg-hover, rgba(0,0,0,0.04))"
            pointerEvents="none"
          />
        )}
      </svg>

      {/* Tooltip (DOM, not SVG-foreignObject, for accessibility) */}
      {hoverIdx !== null && !isEmpty && buckets[hoverIdx] && (
        <ChartTooltip
          bucket={buckets[hoverIdx]}
          actionLabel={actionLabel}
          actionOrder={action_buckets}
          nf={nf}
        />
      )}

      {/* Legend */}
      <ChartLegend actionOrder={action_buckets} actionLabel={actionLabel} />
    </div>
  )
}

function ChartTooltip({
  bucket, actionLabel, actionOrder, nf,
}: {
  bucket: LedgerAggregateBucket
  actionLabel: Record<OverviewActionKey, string>
  actionOrder: OverviewActionKey[]
  nf: Intl.NumberFormat
}) {
  const total = bucketStackTotal(bucket)
  return (
    <div
      className="pointer-events-none absolute top-2 right-2 rounded-md
                 border border-[var(--color-border-default)]
                 bg-[var(--color-bg-elevated)] px-3 py-2 text-xs
                 shadow-sm"
      role="status"
      aria-live="polite"
    >
      <div className="font-medium mb-1">
        {new Date(bucket.ts_start * 1000).toLocaleString()}
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

export default EmissionChart
