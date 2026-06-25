/**
 * D73. ledger polling helper.
 *
 * The cloud's /ledger endpoint is the contract checkpoint for every
 * scenario that crosses the dashboard to cloud boundary. The runtime
 * gate (local/gate.py) writes evidence rows there too. we poll until
 * a row matching a predicate shows up, or until a timeout trips.
 *
 * The polling helper keeps cursor state between rounds so the same
 * scenario can re-use the helper for chained assertions.
 *
 * D73 follow-up: tracks every polled row in a bounded buffer so a
 * timeout error carries a useful diagnostic (rows seen but unmatched)
 * AND attaches them to the test report when a `test.info()` handle
 * is provided.
 */
import type { TestInfo } from "@playwright/test"
import { ledger, type LedgerEntry } from "./cloud"

export type LedgerPredicate = (row: LedgerEntry) => boolean

const MAX_SEEN = 200

export async function waitForLedgerRow(
  predicate: LedgerPredicate,
  opts: {
    timeoutMs?: number
    intervalMs?: number
    startSinceId?: number
    /** Optional test info handle. When provided, polled rows are
     *  attached on timeout under `ledger-rows-on-timeout` so the
     *  curated report carries them under scenario.ledger_rows. */
    testInfo?: TestInfo
  } = {},
): Promise<LedgerEntry> {
  const start = Date.now()
  const timeoutMs = opts.timeoutMs ?? 30_000
  const intervalMs = opts.intervalMs ?? 1_500
  let cursor = opts.startSinceId ?? 0
  const seen: LedgerEntry[] = []
  while (Date.now() - start < timeoutMs) {
    const page = await ledger(cursor, 200).catch(() => null)
    if (page) {
      for (const row of page.entries) {
        if (seen.length < MAX_SEEN) seen.push(row)
        if (predicate(row)) {
          if (opts.testInfo) {
            await opts.testInfo.attach("ledger-rows", {
              body: JSON.stringify([row]),
              contentType: "application/json",
            }).catch(() => {})
          }
          return row
        }
      }
      if (page.entries.length > 0) {
        cursor = page.next_since_id || cursor
      }
    }
    await new Promise((r) => setTimeout(r, intervalMs))
  }
  if (opts.testInfo) {
    await opts.testInfo.attach("ledger-rows-on-timeout", {
      body: JSON.stringify(seen),
      contentType: "application/json",
    }).catch(() => {})
  }
  const sampleSubjects = [...new Set(seen.map((r) => r.subject))].slice(0, 5)
  throw new Error(
    `waitForLedgerRow: no matching row within ${timeoutMs}ms (cursor=${cursor}, ` +
    `saw ${seen.length} unrelated rows, sample subjects=[${sampleSubjects.join(", ")}])`,
  )
}

/** Return the current next_since_id without waiting. Useful when a
 *  scenario wants to record the cursor BEFORE firing claude so the
 *  polling phase only scans new rows. */
export async function currentLedgerCursor(): Promise<number> {
  const page = await ledger(0, 1)
  return page.next_since_id ?? 0
}
