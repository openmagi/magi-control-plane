/**
 * Time formatting for audit surfaces.
 *
 * Why ISO + explicit UTC: server-rendered `toLocaleString()` uses the SERVER
 * timezone, which is ambiguous for regulators/partners across regions. ISO 8601
 * with a 'Z' suffix is unambiguous and matches what evidence reviewers expect.
 */
export function fmtUtc(epochSeconds: number | undefined): string {
  if (typeof epochSeconds !== "number" || !Number.isFinite(epochSeconds)) return "—"
  return new Date(epochSeconds * 1000).toISOString().replace("T", " ").replace(".000Z", "Z")
}

export function clampNonNegInt(v: unknown, fallback = 0): number {
  const n = Number(v)
  if (!Number.isFinite(n) || n < 0) return fallback
  return Math.floor(n)
}

export const LEDGER_PAGE_SIZE = 50
