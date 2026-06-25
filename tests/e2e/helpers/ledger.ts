/**
 * D73 — ledger polling helper.
 *
 * The cloud's /ledger endpoint is the contract checkpoint for every
 * scenario that crosses the dashboard → cloud boundary. The runtime
 * gate (local/gate.py) writes evidence rows there too; we poll until
 * a row matching a predicate shows up, or until a timeout trips.
 *
 * The polling helper keeps cursor state between rounds so the same
 * scenario can re-use the helper for chained assertions.
 */
import { ledger, type LedgerEntry } from "./cloud"

export type LedgerPredicate = (row: LedgerEntry) => boolean

export async function waitForLedgerRow(
  predicate: LedgerPredicate,
  opts: {
    timeoutMs?: number
    intervalMs?: number
    startSinceId?: number
  } = {},
): Promise<LedgerEntry> {
  const start = Date.now()
  const timeoutMs = opts.timeoutMs ?? 30_000
  const intervalMs = opts.intervalMs ?? 1_500
  let cursor = opts.startSinceId ?? 0
  while (Date.now() - start < timeoutMs) {
    const page = await ledger(cursor, 200).catch(() => null)
    if (page) {
      for (const row of page.entries) {
        if (predicate(row)) return row
      }
      if (page.entries.length > 0) {
        cursor = page.next_since_id || cursor
      }
    }
    await new Promise((r) => setTimeout(r, intervalMs))
  }
  throw new Error(
    `waitForLedgerRow: no matching row within ${timeoutMs}ms (cursor=${cursor})`,
  )
}

/** Return the current next_since_id without waiting. Useful when a
 *  scenario wants to record the cursor BEFORE firing claude so the
 *  polling phase only scans new rows. */
export async function currentLedgerCursor(): Promise<number> {
  const page = await ledger(0, 1)
  return page.next_since_id ?? 0
}
