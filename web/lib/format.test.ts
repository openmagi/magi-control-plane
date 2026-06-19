import { describe, it, expect } from "vitest"
import { fmtUtc, clampNonNegInt } from "./format"

describe("fmtUtc", () => {
  it("renders ISO with Z suffix", () => {
    const s = fmtUtc(0)
    expect(s).toMatch(/^1970-01-01 00:00:00Z$/)
  })

  it("returns em-dash on undefined", () => {
    expect(fmtUtc(undefined)).toBe("—")
  })

  it("returns em-dash on NaN", () => {
    expect(fmtUtc(Number.NaN)).toBe("—")
  })
})

describe("clampNonNegInt", () => {
  it("clamps NaN to fallback", () => {
    expect(clampNonNegInt("abc", 0)).toBe(0)
    expect(clampNonNegInt(undefined, 7)).toBe(7)
  })

  it("clamps negative to fallback", () => {
    expect(clampNonNegInt("-5", 0)).toBe(0)
  })

  it("floors fractional", () => {
    expect(clampNonNegInt("3.7", 0)).toBe(3)
  })

  it("passes valid positive int", () => {
    expect(clampNonNegInt("42", 0)).toBe(42)
  })
})
