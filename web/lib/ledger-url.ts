/** D52c follow-up: single authoritative URL contract for `/ledger`.
 *
 * The chip selector on `/ledger` and the `View in ledger` jump-link
 * on `/rules` both write `?verifier=<step>` (and the page cursor
 * `?since=<id>`). Centralising the encoder + decoder here:
 *   - keeps `URLSearchParams` (with its `%20` encoding for space) as
 *     the only producer, so the rules-tab jump-link never desyncs
 *     from the chip selector's own emissions (history-back collapses
 *     cleanly),
 *   - makes future param additions one-place changes (e.g.
 *     `since_secs`, `subject_prefix`),
 *   - centralises the empty-string filter rule (`?verifier=` is "no
 *     filter") so the parser matches the backend (`if v` filter on
 *     `/ledger?verifier=`).
 */

/** Normalise `?verifier=...` into a clean string[].
 *
 * Next.js delivers a repeated query param as `string[]`
 * (`?verifier=a&verifier=b`) and a single occurrence as `string`. We
 * also strip empty values so `?verifier=` (no value) is treated as
 * "no filter" (matches the backend's `if v` filter). Duplicate
 * values are deduped via `new Set`.
 *
 * NOTE on canonical form: a URL with duplicate values
 * (`?verifier=a&verifier=a&verifier=b`) parses correctly (chips
 * render `a + b` ON, filter applies correctly) but the URL is left
 * in its dirty form on the first render. The dedupe takes effect
 * only after the next chip toggle (which rewrites the URL via
 * `ledgerHref(... selected ...)`). Deliberate trade-off: server-side
 * redirect to canonicalise would cost an extra round-trip on every
 * deep-link, and duplicates are user-induced (no internal code
 * paths emit them). The functional behaviour is correct on render
 * one; only the URL bar is non-canonical until the next click.
 */
export function parseVerifierParam(
  raw: string | string[] | undefined,
): string[] {
  if (raw == null) return []
  const arr = Array.isArray(raw) ? raw : [raw]
  return Array.from(new Set(arr.filter(Boolean)))
}

/** Build a `/ledger?...` href with the given verifier filter applied.
 *
 * `since` is preserved only when > 0 (cursor 0 is the natural first
 * page; we elide it for the cleanest URL). Verifier values are
 * appended in the order supplied so toggle deltas stay diff-stable
 * across renders.
 */
export function ledgerHref(opts: {
  since?: number
  verifiers?: string[]
}): string {
  const params = new URLSearchParams()
  if (opts.since && opts.since > 0) params.set("since", String(opts.since))
  for (const v of opts.verifiers ?? []) {
    if (v) params.append("verifier", v)
  }
  const qs = params.toString()
  return qs ? `/ledger?${qs}` : "/ledger"
}
