"use client"

import { useCallback, useEffect, useId, useRef, useState } from "react"
import { Button } from "@/components/ui/Button"

/**
 * D53b: "Dry-run on last 24h" client panel.
 *
 * Lives on /policies/new (NL + Guided + Raw modes) and the
 * /policies/[id] edit page. The button is gated on a passed-in
 * `disabled` prop so the parent (which knows whether the IR is
 * currently valid / compiled clean) decides when the button is
 * usable. Click flow:
 *
 *   1. POST /api/policies/dry-run (same-origin proxy; key stays on
 *      the server).
 *   2. Render the headline ("If you enabled this policy, in the
 *      last 24h it would have <action>'d M of N tool calls.") plus
 *      a by_verdict pill row.
 *   3. "Show 3 sample matches" toggle reveals the redacted preview
 *      rows. Samples are already redacted on the server (D50);
 *      raw payloads never reach the client.
 *
 * Brief constraints:
 *   - Loading state shows "Replaying last 24h…" + spinner.
 *   - Error state shows "Dry-run failed: <reason>"; never blocks
 *     the parent's save action.
 *   - i18n keys: newPolicy.dryRun.*
 *
 * a11y:
 *   - The result region is keyed by aria-live="polite" so screen
 *     readers announce the headline when it lands.
 *   - The "show samples" toggle is a button with aria-expanded
 *     wired into the controlled sample list (aria-controls).
 */

// i18n helper signature matches the rest of the policies/* tree.
type T = (
  k: import("@/lib/i18n/dict").TKey,
  v?: Record<string, string | number>,
) => string

export type DryRunSampleRow = {
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
}

export type DryRunResult = {
  total_records: number
  matched: number
  /** Follow-up: rows where >=1 requires entry was offline-unevaluable
   *  (llm_critic / shacl / regex without payload snapshot). Optional
   *  so older cloud versions that don't yet emit the field continue
   *  to deserialize. */
  indeterminate?: number
  by_verdict: Record<string, number>
  by_action: Record<string, number>
  sample_matched: DryRunSampleRow[]
  skipped_reason: string | null
  /** Follow-up: subset of {"llm_critic","shacl","regex"} listing the
   *  requires-entry kinds that produced indeterminate results during
   *  the replay. Used to surface a per-requires disclosure on the
   *  headline. */
  skipped_kinds?: string[]
  since: "24h" | "7d"
  limit: number
}

type ActionArchetype = "block" | "ask" | "audit" | "strip"

export function DryRunPanel({
  t,
  /** The policy IR draft as a JSON-serializable object. Required.
   *  The parent passes either the compile result (NL mode), the
   *  built draft (Guided / Raw / Edit), or the stored row (the
   *  existing-policy detail page).
   *  Passing `null` keeps the button disabled. */
  ir,
  /** When true, the button is disabled and a hover-tooltip hint
   *  explains why (e.g. compile not yet run). The parent uses this
   *  to gate on IR validity. */
  disabled = false,
  /** Optional: action archetype this draft will emit. Used to
   *  color the headline pill. Default `audit` (the most
   *  conservative pill color). */
  action,
}: {
  t: T
  ir: Record<string, unknown> | null
  disabled?: boolean
  action?: ActionArchetype
}) {
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<DryRunResult | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [samplesOpen, setSamplesOpen] = useState(false)
  const samplesId = useId()
  const resultId = useId()
  // P1 #1: abort-on-navigation. The cloud's replay can take up to
  // ~30s for a 10_000-row window; without a client-side abort the
  // fetch keeps the threadpool slot warm AND its resolution races
  // against unmount, producing "state update on unmounted component"
  // warnings + wasted work. We hold an AbortController in a ref so
  // unmount and a fresh click both abort the in-flight request.
  const abortRef = useRef<AbortController | null>(null)
  const mountedRef = useRef(true)
  useEffect(() => {
    return () => {
      mountedRef.current = false
      abortRef.current?.abort()
    }
  }, [])

  const onRun = useCallback(async () => {
    if (ir == null) return
    // Cancel any previous in-flight replay so a fast-clicker does not
    // pin two threadpool slots. The cloud route is async + uses
    // asyncio.to_thread so the abort frees the slot promptly.
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    setLoading(true)
    setErr(null)
    setSamplesOpen(false)
    try {
      const r = await fetch("/api/policies/dry-run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({ ir, since: "24h" }),
        signal: controller.signal,
      })
      if (controller.signal.aborted || !mountedRef.current) return
      if (!r.ok) {
        // Pull a short, friendly reason. The proxy returns
        // `{error: "<short>"}` on every failure path.
        let reason = "upstream"
        try {
          const j = (await r.json()) as { error?: string }
          if (j && typeof j.error === "string") reason = j.error
        } catch { /* keep default */ }
        if (controller.signal.aborted || !mountedRef.current) return
        setErr(t("newPolicy.dryRun.failed", { reason }))
        setResult(null)
        return
      }
      const data = (await r.json()) as DryRunResult
      if (controller.signal.aborted || !mountedRef.current) return
      setResult(data)
    } catch (e) {
      // AbortError lands here on navigation-cancel; we deliberately
      // swallow it because the unmount cleanup already cleared
      // state. Anything else surfaces as a recoverable error.
      if (
        e instanceof DOMException && e.name === "AbortError"
      ) return
      if (controller.signal.aborted || !mountedRef.current) return
      setErr(t("newPolicy.dryRun.failed", { reason: "network" }))
      setResult(null)
    } finally {
      if (mountedRef.current && abortRef.current === controller) {
        setLoading(false)
      }
    }
  }, [ir, t])

  const resolvedAction: ActionArchetype = action ?? "audit"
  const actionToneClass = actionTone(resolvedAction)

  return (
    <div
      data-testid="dry-run-panel"
      className="mt-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-subtle,#fafafa)] p-3"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={onRun}
          disabled={disabled || loading || ir == null}
          aria-controls={resultId}
          data-testid="dry-run-button"
        >
          {loading
            ? t("newPolicy.dryRun.loading")
            : t("newPolicy.dryRun.button")}
        </Button>
        {disabled && !loading && (
          <span className="text-[11px] italic text-[var(--color-text-tertiary)]">
            {t("newPolicy.dryRun.disabledHint")}
          </span>
        )}
      </div>

      <div
        id={resultId}
        aria-live="polite"
        aria-busy={loading}
        className="mt-3"
      >
        {loading && (
          <p
            data-testid="dry-run-loading"
            className="text-xs italic text-[var(--color-text-tertiary)]"
          >
            {/* P2 follow-up: long-replay copy with an explicit
                up-to-30s expectation so the operator does not
                interpret a multi-second wait as a hang and retry
                (which would double the threadpool load). The
                button label uses the shorter `loading` key; this
                paragraph uses the longer `loadingLong` key so they
                don't read as a duplicate paragraph bug. */}
            {t("newPolicy.dryRun.loadingLong")}
          </p>
        )}
        {!loading && err && (
          <p
            data-testid="dry-run-error"
            className="text-xs text-[var(--color-deny-fg)]"
            role="alert"
          >
            {err}
          </p>
        )}
        {!loading && !err && result && (
          <DryRunResultBlock
            t={t}
            result={result}
            action={resolvedAction}
            actionToneClass={actionToneClass}
            samplesOpen={samplesOpen}
            setSamplesOpen={setSamplesOpen}
            samplesId={samplesId}
          />
        )}
      </div>
    </div>
  )
}

function DryRunResultBlock({
  t,
  result,
  action,
  actionToneClass,
  samplesOpen,
  setSamplesOpen,
  samplesId,
}: {
  t: T
  result: DryRunResult
  action: ActionArchetype
  actionToneClass: string
  samplesOpen: boolean
  setSamplesOpen: (b: boolean) => void
  samplesId: string
}) {
  // Skipped paths surface a friendly inline explanation rather than
  // a misleading "0 of N would have blocked" line.
  if (result.skipped_reason === "archetype-not-dry-runnable") {
    return (
      <p
        data-testid="dry-run-skipped-archetype"
        className="text-xs italic text-[var(--color-text-tertiary)]"
      >
        {t("newPolicy.dryRun.empty.archetype")}
      </p>
    )
  }
  if (result.skipped_reason === "multi-requires-not-replayable") {
    // P1 #3: surface the multi-requires limitation so an operator
    // doesn't read "0 would have blocked" as "policy too narrow"
    // when in fact the replay refused to fan out per-payload.
    return (
      <p
        data-testid="dry-run-skipped-multi-requires"
        className="text-xs italic text-[var(--color-text-tertiary)]"
      >
        {t("newPolicy.dryRun.empty.multiRequires")}
      </p>
    )
  }
  if (result.skipped_reason === "requires-indeterminate") {
    // P1 #4: every requires entry was offline-unevaluable. The
    // headline number would be a literal zero; surface the
    // limitation instead.
    return (
      <p
        data-testid="dry-run-skipped-indeterminate"
        className="text-xs italic text-[var(--color-text-tertiary)]"
      >
        {t("newPolicy.dryRun.empty.requiresIndeterminate")}
      </p>
    )
  }
  if (result.skipped_reason === "no-frame-metadata-on-rows") {
    return (
      <p
        data-testid="dry-run-skipped-no-frame"
        className="text-xs italic text-[var(--color-text-tertiary)]"
      >
        {t("newPolicy.dryRun.empty.noFrameMetadata")}
      </p>
    )
  }
  if (
    result.skipped_reason === "no-records-in-trigger-frame"
    || result.total_records === 0
  ) {
    return (
      <p
        data-testid="dry-run-empty"
        className="text-xs italic text-[var(--color-text-tertiary)]"
      >
        {t("newPolicy.dryRun.empty")}
      </p>
    )
  }
  // P1 #2: dedicated "policy would not have fired on any of the last
  // N tool calls in scope" branch. The prior code fell through to
  // the regular headline rendering ("would have ...'d 0 of N"), which
  // looks like a successful match summary at a glance. This branch
  // makes the matched=0-but-total>0 case explicitly visible.
  if (result.matched === 0 && result.total_records > 0) {
    return (
      <div className="space-y-2">
        <p
          data-testid="dry-run-no-match"
          className="text-sm leading-relaxed text-[var(--color-text-secondary)]"
        >
          {t("newPolicy.dryRun.noMatch", { total: result.total_records })}
        </p>
        {(result.indeterminate ?? 0) > 0 && (
          <p
            data-testid="dry-run-indeterminate-note"
            className="text-[11px] italic text-[var(--color-text-tertiary)]"
          >
            {t("newPolicy.dryRun.indeterminateNote", {
              n: result.indeterminate ?? 0,
              kinds: (result.skipped_kinds ?? []).join(", "),
            })}
          </p>
        )}
        <VerdictPillRow t={t} byVerdict={result.by_verdict} />
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <p
        data-testid="dry-run-headline"
        className="text-sm leading-relaxed text-[var(--color-text-secondary)]"
      >
        {t("newPolicy.dryRun.result", {
          action: t(actionLabelKey(action)),
          matched: result.matched,
          total: result.total_records,
        })}
        <span
          className={`ml-2 inline-flex items-center rounded-full px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${actionToneClass}`}
        >
          {t(actionLabelKey(action))}
        </span>
      </p>

      {(result.indeterminate ?? 0) > 0 && (
        <p
          data-testid="dry-run-indeterminate-note"
          className="text-[11px] italic text-[var(--color-text-tertiary)]"
        >
          {t("newPolicy.dryRun.indeterminateNote", {
            n: result.indeterminate ?? 0,
            kinds: (result.skipped_kinds ?? []).join(", "),
          })}
        </p>
      )}

      <VerdictPillRow t={t} byVerdict={result.by_verdict} />

      {result.sample_matched.length > 0 && (
        <>
          <button
            type="button"
            onClick={() => setSamplesOpen(!samplesOpen)}
            aria-expanded={samplesOpen}
            aria-controls={samplesId}
            data-testid="dry-run-samples-toggle"
            className="text-[11px] font-medium text-[var(--color-accent-light)] hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-accent)]"
          >
            {samplesOpen
              ? t("newPolicy.dryRun.hideSamples")
              : t("newPolicy.dryRun.showSamples")}
          </button>
          <ul
            id={samplesId}
            role="list"
            hidden={!samplesOpen}
            data-testid="dry-run-samples"
            className="mt-1 space-y-1"
          >
            {result.sample_matched.map((s) => (
              <li
                key={s.id}
                data-testid="dry-run-sample-row"
                className="flex items-center gap-2 text-[11px]"
              >
                <span
                  className={`inline-flex items-center rounded-full px-1.5 py-0.5 text-[9.5px] font-semibold uppercase tracking-wider ${verdictPillTone(verdictKey(s.verdict))}`}
                >
                  {t(verdictPillLabel(verdictKey(s.verdict)))}
                </span>
                <time
                  dateTime={s.ts}
                  className="shrink-0 text-[10.5px] text-[var(--color-text-tertiary)] tabular-nums"
                >
                  {s.ts}
                </time>
                <span
                  data-testid="dry-run-sample-preview"
                  className="flex-1 truncate font-mono text-[10.5px] text-[var(--color-text-secondary)]"
                >
                  {s.redacted_payload_preview}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

function VerdictPillRow({
  t,
  byVerdict,
}: {
  t: T
  byVerdict: Record<string, number>
}) {
  return (
    <ul
      data-testid="dry-run-by-verdict"
      className="flex flex-wrap gap-1.5"
      role="list"
      aria-label={t("newPolicy.dryRun.byVerdict.label")}
    >
      {(["pass", "fail", "deny", "review", "needs_review",
         "not_applicable", "unknown"] as const).map((v) => {
        const n = byVerdict[v] ?? 0
        if (n === 0) return null
        return (
          <li
            key={v}
            data-testid={`dry-run-pill-${v}`}
            className={`inline-flex items-center rounded-full px-1.5 py-0.5 text-[10px] font-medium ${verdictPillTone(v)}`}
          >
            {t(verdictPillLabel(v))}: {n}
          </li>
        )
      })}
    </ul>
  )
}

// ── helpers ─────────────────────────────────────────────────────────

function actionTone(a: ActionArchetype): string {
  switch (a) {
    case "block":
      return "bg-[var(--color-deny-bg,#fff1f2)] text-[var(--color-deny-fg,#be123c)]"
    case "ask":
      return "bg-[var(--color-review-bg,#fffbeb)] text-[var(--color-review-fg,#b45309)]"
    case "audit":
      return "bg-[var(--color-pass-bg,#ecfdf5)] text-[var(--color-pass-fg,#047857)]"
    case "strip":
      return "bg-[var(--color-muted-bg,#f3f4f6)] text-[var(--color-muted-fg,#374151)]"
  }
}

function actionLabelKey(
  a: ActionArchetype,
): import("@/lib/i18n/dict").TKey {
  switch (a) {
    case "block": return "newPolicy.dryRun.action.block"
    case "ask": return "newPolicy.dryRun.action.ask"
    case "audit": return "newPolicy.dryRun.action.audit"
    case "strip": return "newPolicy.dryRun.action.strip"
  }
}

type VerdictKey =
  | "pass" | "fail" | "deny" | "review"
  | "needs_review" | "not_applicable" | "unknown"

function verdictKey(v: DryRunSampleRow["verdict"]): VerdictKey {
  if (v == null) return "unknown"
  return v
}

function verdictPillTone(v: VerdictKey): string {
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
    case "unknown":
      return "bg-[var(--color-muted-bg,#f3f4f6)] text-[var(--color-muted-fg,#374151)]"
  }
}

function verdictPillLabel(v: VerdictKey): import("@/lib/i18n/dict").TKey {
  switch (v) {
    case "pass": return "newPolicy.dryRun.verdict.pass"
    case "fail": return "newPolicy.dryRun.verdict.fail"
    case "deny": return "newPolicy.dryRun.verdict.deny"
    case "review":
    case "needs_review":
      return "newPolicy.dryRun.verdict.needsReview"
    case "not_applicable":
      return "newPolicy.dryRun.verdict.notApplicable"
    case "unknown":
      return "newPolicy.dryRun.verdict.unknown"
  }
}
