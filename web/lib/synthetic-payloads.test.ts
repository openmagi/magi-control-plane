import { describe, it, expect } from "vitest"
import {
  SYNTHETIC_PAYLOAD_TEMPLATES, templateById,
} from "./synthetic-payloads"

/**
 * D77: synthetic CC hook payload template catalog. The cloud
 * simulator does not enforce template selection; the catalog is a UX
 * scaffolding so a first-time operator can get to a passing test
 * with one click. We pin a few invariants so a future edit cannot
 * silently land a template that breaks the panel.
 */
describe("synthetic-payloads catalog", () => {
  it("has at least one template per important hook event", () => {
    const events = new Set(
      SYNTHETIC_PAYLOAD_TEMPLATES.map((t) => t.event),
    )
    expect(events.has("PreToolUse")).toBe(true)
    expect(events.has("PostToolUse")).toBe(true)
    expect(events.has("Stop")).toBe(true)
    expect(events.has("UserPromptSubmit")).toBe(true)
    expect(events.has("SessionStart")).toBe(true)
  })

  it("every template carries both ko + en display labels", () => {
    for (const t of SYNTHETIC_PAYLOAD_TEMPLATES) {
      expect(t.displayLabel.ko.length).toBeGreaterThan(0)
      expect(t.displayLabel.en.length).toBeGreaterThan(0)
      expect(t.hint.ko.length).toBeGreaterThan(0)
      expect(t.hint.en.length).toBeGreaterThan(0)
    }
  })

  it("every template carries a hook_event_name in the payload", () => {
    for (const t of SYNTHETIC_PAYLOAD_TEMPLATES) {
      expect(t.payload.hook_event_name).toBe(t.event)
    }
  })

  it("template ids are unique", () => {
    const ids = SYNTHETIC_PAYLOAD_TEMPLATES.map((t) => t.id)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it("templateById returns the matching template or undefined", () => {
    const first = SYNTHETIC_PAYLOAD_TEMPLATES[0]
    expect(templateById(first.id)?.id).toBe(first.id)
    expect(templateById("never-exists")).toBeUndefined()
  })

  it("Bash rm -rf template demonstrates a block-worthy command", () => {
    const tpl = SYNTHETIC_PAYLOAD_TEMPLATES.find(
      (t) => t.id === "pre-bash-rmrf",
    )
    expect(tpl).toBeDefined()
    const ti = (tpl!.payload as { tool_input?: { command?: string } })
      .tool_input
    expect(ti?.command).toContain("rm -rf")
  })
})
