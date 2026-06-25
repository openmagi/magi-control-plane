import { describe, it, expect } from "vitest"
import { ENV_REFERENCE, groupEntries } from "./env-reference"

describe("D78 env-reference", () => {
  it("includes every MAGI_CP_* entry expected by the docs", () => {
    // Spot-check the names that the doc pages explicitly cite. If any
    // disappear, the doc would link to a missing row.
    const REQUIRED = [
      "MAGI_CP_API_KEY",
      "MAGI_CP_ADMIN_API_KEY",
      "MAGI_CP_HITL_API_KEY",
      "MAGI_CP_CLOUD_URL",
      "MAGI_CP_ALLOW_RUN_COMMAND",
      "MAGI_CP_REQUIRE_SIGNED_RUN_COMMAND_SPEC",
      "MAGI_CP_SCRIPT_STORE_DIR",
      "MAGI_CP_RUN_COMMAND_LEDGER",
      "MAGI_CP_LLM_COMPILER",
      "MAGI_CP_LLM_REVIEWER",
      "MAGI_CP_CONTEXT_TEMPLATES_DIR",
    ]
    const names = ENV_REFERENCE.map((e) => e.name)
    for (const r of REQUIRED) {
      expect(names, `missing required env-reference entry: ${r}`).toContain(r)
    }
  })

  it("every entry has both ko and en one-liners", () => {
    for (const e of ENV_REFERENCE) {
      expect(e.ko, `${e.name} missing ko`).toBeTruthy()
      expect(e.en, `${e.name} missing en`).toBeTruthy()
    }
  })

  it("groupEntries() partitions the full reference into four buckets", () => {
    const g = groupEntries()
    const total = g.cloud.length + g.local.length + g.dashboard.length + g.provider.length
    expect(total).toBe(ENV_REFERENCE.length)
  })

  it("no duplicate names", () => {
    const seen = new Set<string>()
    for (const e of ENV_REFERENCE) {
      expect(seen.has(e.name), `duplicate entry ${e.name}`).toBe(false)
      seen.add(e.name)
    }
  })
})
