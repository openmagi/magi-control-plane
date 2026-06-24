import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

/**
 * KO/EN drift gate.
 *
 * dict.ts has KO_RAW and EN object literals that the TS type system *would*
 * keep in sync. `EN: Record<keyof typeof KO_RAW, string>` would refuse to
 * compile if an EN key were missing. But the inverse direction (an EN-only
 * key) is silently allowed by the type checker because the index signature
 * only goes one way.
 *
 * This test parses the source file and asserts the two objects have the
 * exact same keys, in the same order (so reviewers can eyeball diffs by
 * line number). It also flags duplicate keys, which TS *does* warn about
 * at build time but only as duplicate-key warnings. not failures.
 */
describe("i18n dict drift gate", () => {
  const src = readFileSync(path.join(__dirname, "dict.ts"), "utf-8")

  function extractKeysBetween(startMarker: string, endMarker: string): string[] {
    const startIdx = src.indexOf(startMarker)
    const endIdx = src.indexOf(endMarker, startIdx + 1)
    if (startIdx < 0 || endIdx < 0) {
      throw new Error(`markers not found: ${startMarker} … ${endMarker}`)
    }
    const block = src.slice(startIdx, endIdx)
    // Match "key.path.parts": (greedy line capture); only at the start of a line.
    const keys: string[] = []
    for (const line of block.split("\n")) {
      const m = line.match(/^\s*"([^"]+)"\s*:/)
      if (m) keys.push(m[1])
    }
    return keys
  }

  it("KO_RAW and EN have the same set of keys", () => {
    const ko = extractKeysBetween("const KO_RAW = {", "} as const")
    const en = extractKeysBetween("const EN:", "\nconst DICT")
    const koOnly = ko.filter(k => !en.includes(k))
    const enOnly = en.filter(k => !ko.includes(k))
    expect(koOnly, `keys in KO but missing in EN:\n${koOnly.join("\n")}`).toEqual([])
    expect(enOnly, `keys in EN but missing in KO:\n${enOnly.join("\n")}`).toEqual([])
  })

  it("KO_RAW has no duplicate keys", () => {
    const ko = extractKeysBetween("const KO_RAW = {", "} as const")
    const seen = new Set<string>()
    const dups: string[] = []
    for (const k of ko) {
      if (seen.has(k)) dups.push(k)
      seen.add(k)
    }
    expect(dups, `duplicate KO keys:\n${dups.join("\n")}`).toEqual([])
  })

  it("EN has no duplicate keys", () => {
    const en = extractKeysBetween("const EN:", "\nconst DICT")
    const seen = new Set<string>()
    const dups: string[] = []
    for (const k of en) {
      if (seen.has(k)) dups.push(k)
      seen.add(k)
    }
    expect(dups, `duplicate EN keys:\n${dups.join("\n")}`).toEqual([])
  })

  /**
   * RulesDescription splice contract.
   *
   * web/app/(console)/rules/page.tsx splices a /ledger Link into the
   * translated "rules.description" by substituting a marker into the
   * {ledger} placeholder and splitting on it. The render only stays
   * inline if both translations contain the literal {ledger} token AND
   * the "rules.description.ledgerLink" key exists in both locales. The
   * key-set drift gate above would not catch the case where both KO+EN
   * simultaneously lost the placeholder (or both lost the link key), so
   * we lock the contract here.
   */
  function extractValueFor(startMarker: string, endMarker: string, key: string): string | null {
    const startIdx = src.indexOf(startMarker)
    const endIdx = src.indexOf(endMarker, startIdx + 1)
    if (startIdx < 0 || endIdx < 0) return null
    const block = src.slice(startIdx, endIdx)
    const re = new RegExp(`^\\s*"${key.replace(/\./g, "\\.")}"\\s*:\\s*"((?:[^"\\\\]|\\\\.)*)"`, "m")
    const m = block.match(re)
    return m ? m[1] : null
  }

  it("KO rules.description contains the {ledger} placeholder", () => {
    const koDesc = extractValueFor("const KO_RAW = {", "} as const", "rules.description")
    expect(koDesc, "rules.description missing from KO_RAW").not.toBeNull()
    expect(koDesc).toContain("{ledger}")
  })

  it("EN rules.description contains the {ledger} placeholder", () => {
    const enDesc = extractValueFor("const EN:", "\nconst DICT", "rules.description")
    expect(enDesc, "rules.description missing from EN").not.toBeNull()
    expect(enDesc).toContain("{ledger}")
  })

  it("rules.description.ledgerLink exists in both KO and EN", () => {
    const koLink = extractValueFor("const KO_RAW = {", "} as const", "rules.description.ledgerLink")
    const enLink = extractValueFor("const EN:", "\nconst DICT", "rules.description.ledgerLink")
    expect(koLink, "rules.description.ledgerLink missing from KO_RAW").not.toBeNull()
    expect(enLink, "rules.description.ledgerLink missing from EN").not.toBeNull()
    expect(koLink!.length).toBeGreaterThan(0)
    expect(enLink!.length).toBeGreaterThan(0)
  })
})
