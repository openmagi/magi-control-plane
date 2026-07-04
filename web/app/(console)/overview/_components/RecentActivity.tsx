import Link from "next/link"

import { Card } from "@/components/ui"
import type { LedgerEntry } from "@/lib/cloud"

/** D76: closed-set verdict vocabulary the /overview surface renders.
 * Mirrors `_VERDICT_BUCKETS_ORDER` in src/magi_cp/cloud/metrics.py.
 * Raw producer-emitted variants (`deny`, `review`) are projected onto
 * this set by `toRecentActivityRows` so the chart's stacked-column
 * counts and the per-row badges below speak the same vocabulary. */
type Verdict =
  | "pass" | "fail" | "needs_review" | "not_applicable"

type Row = {
  id: number
  ts: number
  /** Action label pulled from `body.action` (may be missing on legacy
   *  rows; we render "—" in that case). */
  action: string | null
  verdict: Verdict | null
  policyId: string | null
}

/**
 * D76: last 5 ledger rows, rendered as a compact table on /overview
 * with a link to /ledger for the full view. Receives already-
 * shaped rows from the server component so we never expose the raw
 * `body` blob to the renderer (PII / signed-token bytes stay on the
 * server).
 */
type Props = {
  rows: Row[]
  /** Localized "no recent activity" body. */
  emptyBody: string
  /** Localized "View all in ledger" link label. */
  ctaLabel: string
  /** Localized column headers + verdict / action labels. */
  labels: {
    when: string
    action: string
    verdict: string
    policy: string
    pass: string
    fail: string
    needsReview: string
    notApplicable: string
    unknown: string
  }
  /** Locale-aware Intl.DateTimeFormat. */
  dtf: Intl.DateTimeFormat
}

// Verdict chip colors. These mirror the canonical `_ds/Badge` variant
// tokens (ok/deny/review/muted) so the row badge speaks the exact same
// palette as every other verdict surface. Do not invent `--color-bg-*`
// names here: only the `--color-pass/review/deny-*` trio and the muted
// border treatment exist in `_ds/tokens.css`.
function verdictBadgeClass(v: Verdict | null): string {
  if (v === "pass") {
    return "bg-[var(--color-pass-bg)] text-[var(--color-pass-fg)]"
  }
  if (v === "fail") {
    return "bg-[var(--color-deny-bg)] text-[var(--color-deny-fg)]"
  }
  if (v === "needs_review") {
    return "bg-[var(--color-review-bg)] text-[var(--color-review-fg)]"
  }
  return "border border-[var(--color-border-subtle)] text-[var(--color-text-tertiary)]"
}

function verdictLabel(v: Verdict | null, labels: Props["labels"]): string {
  if (v === "pass") return labels.pass
  if (v === "fail") return labels.fail
  if (v === "needs_review") return labels.needsReview
  if (v === "not_applicable") return labels.notApplicable
  return labels.unknown
}

/** Project a producer-supplied verdict onto the dashboard's closed
 * vocabulary. Mirrors `_project_verdict` in
 * src/magi_cp/cloud/metrics.py so the per-row badge below the chart
 * speaks the same word the chart's stacked-column count does. */
function projectVerdict(raw: unknown): Verdict | null {
  if (typeof raw !== "string") return null
  if (raw === "pass") return "pass"
  if (raw === "fail" || raw === "deny") return "fail"
  if (raw === "review" || raw === "needs_review") return "needs_review"
  if (raw === "not_applicable") return "not_applicable"
  return null
}

export function RecentActivity({ rows, emptyBody, ctaLabel, labels, dtf }: Props) {
  if (rows.length === 0) {
    return (
      <Card>
        <p className="text-sm text-[var(--color-text-secondary)]">
          {emptyBody}
        </p>
      </Card>
    )
  }
  return (
    <Card>
      <ul className="divide-y divide-[var(--color-border-subtle)]">
        {rows.map(r => (
          <li
            key={r.id}
            className="flex flex-wrap items-center gap-3 py-2 text-sm"
          >
            <span className="text-xs text-[var(--color-text-tertiary)] w-32 shrink-0">
              {dtf.format(new Date(r.ts * 1000))}
            </span>
            <span
              className={
                "inline-flex items-center rounded-sm px-1.5 py-0.5 "
                + "text-xs font-medium " + verdictBadgeClass(r.verdict)
              }
            >
              {verdictLabel(r.verdict, labels)}
            </span>
            <span className="font-mono text-xs text-[var(--color-text-primary)]">
              {r.action ?? "—"}
            </span>
            <span className="font-mono text-xs text-[var(--color-text-secondary)] truncate">
              {r.policyId ?? ""}
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-xs">
        <Link
          href="/ledger"
          className="font-medium text-[var(--color-accent-light)] hover:underline"
        >
          {ctaLabel}
        </Link>
      </p>
    </Card>
  )
}

export type { Row as RecentActivityRow }
export default RecentActivity

// Helper consumed by the page server component so it can build `Row[]`
// from raw ledger entries without exporting the body shape.
export function toRecentActivityRows(
  entries: Array<LedgerEntry & { body?: Record<string, unknown> | undefined }>,
  limit: number,
): Row[] {
  const rows: Row[] = []
  for (const e of entries.slice(0, limit)) {
    const body = (e.body ?? {}) as Record<string, unknown>
    const action = typeof body.action === "string"
      ? body.action as string
      : (typeof body.step === "string" ? body.step as string : null)
    const policyId = typeof body.policy_id === "string"
      ? body.policy_id as string
      : null
    rows.push({
      id: e.id,
      ts: e.ts,
      action,
      // Project on egress so the chart's `by_verdict.fail` bar above
      // and this row's badge speak the same vocabulary for the same
      // ledger row (raw `deny` → `fail`, raw `review` →
      // `needs_review`). See `_project_verdict` in
      // src/magi_cp/cloud/metrics.py for the cloud-side mirror.
      verdict: projectVerdict(body.verdict),
      policyId,
    })
  }
  return rows
}
