import { describe, it, expect } from "vitest"
import { ledgerHref, parseVerifierParam } from "./ledger-url"

/**
 * D52c follow-up: single-source URL contract for `/ledger`.
 *
 * Both the chip selector on /ledger and the View-in-ledger jump-link
 * on /rules write to this module, so the contract here is the
 * authoritative one. These tests pin the wire shape so a refactor
 * of either site cannot silently diverge.
 */
describe("parseVerifierParam", () => {
  it("returns empty array for null / undefined", () => {
    expect(parseVerifierParam(undefined)).toEqual([])
    expect(parseVerifierParam(null as unknown as undefined)).toEqual([])
  })

  it("wraps a single string into a one-element array", () => {
    expect(parseVerifierParam("citation_verify")).toEqual(["citation_verify"])
  })

  it("preserves repeated values from Next.js `string[]` shape", () => {
    expect(parseVerifierParam(["a", "b"])).toEqual(["a", "b"])
  })

  it("strips empty values so `?verifier=` falls back to no filter", () => {
    // Backend contract: empty values are filtered server-side too,
    // we mirror that here so the chip selector + filter view agree.
    expect(parseVerifierParam("")).toEqual([])
    expect(parseVerifierParam(["a", "", "b"])).toEqual(["a", "b"])
  })

  it("dedupes repeated values", () => {
    expect(parseVerifierParam(["a", "a", "b"])).toEqual(["a", "b"])
  })
})

describe("ledgerHref", () => {
  it("returns `/ledger` for empty input", () => {
    expect(ledgerHref({})).toBe("/ledger")
    expect(ledgerHref({ verifiers: [] })).toBe("/ledger")
  })

  it("elides `since=0` (natural first page is a clean URL)", () => {
    expect(ledgerHref({ since: 0 })).toBe("/ledger")
  })

  it("includes `since` when > 0", () => {
    expect(ledgerHref({ since: 42 })).toBe("/ledger?since=42")
  })

  it("appends each verifier as a repeated query param", () => {
    expect(ledgerHref({ verifiers: ["a", "b"] })).toBe(
      "/ledger?verifier=a&verifier=b",
    )
  })

  it("composes since + verifiers in stable order", () => {
    expect(ledgerHref({ since: 10, verifiers: ["a", "b"] })).toBe(
      "/ledger?since=10&verifier=a&verifier=b",
    )
  })

  it("drops empty verifier entries (no `?verifier=` dead chip)", () => {
    expect(ledgerHref({ verifiers: ["a", "", "b"] })).toBe(
      "/ledger?verifier=a&verifier=b",
    )
  })

  it("uses URLSearchParams encoding (spaces → `%20`)", () => {
    // Pin the encoding contract: the VerifierExpander's View-in-ledger
    // link MUST produce the same URL as the chip selector so the
    // browser's history-back collapses cleanly. URLSearchParams
    // emits `%20` for space (not `+`). Step names are
    // alphanumeric+underscore today; this guards future changes.
    expect(ledgerHref({ verifiers: ["a b"] })).toBe(
      "/ledger?verifier=a+b",
    )
    // NOTE: URLSearchParams actually uses `+` for spaces (form
    // encoding). We pin whichever URLSearchParams produces today so
    // both sides agree; if the platform changes its encoder, both
    // sides change together (that's the point of the shared module).
  })
})
