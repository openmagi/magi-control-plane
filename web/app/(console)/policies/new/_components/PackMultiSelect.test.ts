import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"
import { suggestPackFromText } from "./PackMultiSelect"
import type { PolicyPackEntry } from "@/lib/cloud"

function pack(id: string, name: string, isFloor = false): PolicyPackEntry {
  return {
    id, name, description: "", policy_ids: [], source: "user",
    status: "none", member_count: 0, enabled_count: 0,
    is_floor: isFloor,
  }
}

describe("suggestPackFromText extractor (P4)", () => {
  const packs = [
    pack("user-pack/floor", "Floor", true),
    pack("user-pack/research-mode", "Research Mode"),
    pack("user-pack/coding-safety", "Coding Safety"),
  ]

  it("returns null for empty text", () => {
    expect(suggestPackFromText("", packs)).toBeNull()
    expect(suggestPackFromText("   ", packs)).toBeNull()
  })

  it("matches a Korean work-context keyword to the research pack", () => {
    expect(suggestPackFromText("리서치 세션 시작할게", packs)).toBe(
      "user-pack/research-mode",
    )
  })

  it("matches an English work-context keyword to the coding pack", () => {
    expect(suggestPackFromText("set up coding safety guardrails", packs)).toBe(
      "user-pack/coding-safety",
    )
  })

  it("matches a direct pack-name mention", () => {
    expect(suggestPackFromText("please add to Research Mode", packs)).toBe(
      "user-pack/research-mode",
    )
  })

  it("returns null when nothing matches", () => {
    expect(suggestPackFromText("hello world", packs)).toBeNull()
  })
})

describe("PackMultiSelect is built once + reused (P4)", () => {
  const componentSrc = readFileSync(
    path.join(__dirname, "PackMultiSelect.tsx"), "utf-8",
  )

  it("writes the shared hidden pack_ids field", () => {
    expect(componentSrc).toContain('name="pack_ids"')
  })

  it("orders the floor pack first with an ALWAYS-ON chip", () => {
    expect(componentSrc).toContain("is_floor")
    expect(componentSrc).toContain("alwaysOn")
  })

  const surfaces = [
    // conversational compose (via IrDraftPane)
    "IrDraftPane.tsx",
    // raw / advanced editor
    "AdvancedAuthoring.tsx",
  ]
  for (const file of surfaces) {
    it(`is imported (not re-implemented) by ${file}`, () => {
      const src = readFileSync(path.join(__dirname, file), "utf-8")
      expect(src).toContain("PackMultiSelect")
    })
  }

  it("is imported by the guided wizard page", () => {
    const src = readFileSync(path.join(__dirname, "../page.tsx"), "utf-8")
    expect(src).toContain("PackMultiSelect")
  })
})

describe("G3 (IF-05): built-in packs are non-selectable", () => {
  const src = readFileSync(
    path.join(__dirname, "PackMultiSelect.tsx"), "utf-8",
  )

  it("disables the checkbox for a pack/ (built-in) id", () => {
    expect(src).toContain('pack.id.startsWith("pack/")')
    expect(src).toContain("disabled={isBuiltin}")
    // never let a built-in be checked/toggled (it always 400s on join)
    expect(src).toContain("checked={isSelected && !isBuiltin}")
    expect(src).toMatch(/if \(!isBuiltin\) toggle/)
  })
})
