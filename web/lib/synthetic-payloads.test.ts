import { describe, it, expect } from "vitest"
import {
  SUPPORTED_EVENTS,
  SYNTHETIC_PAYLOAD_TEMPLATES, coveredEvents, templateById,
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

  // P1 review fix: lockstep gate against the EventKind union. Adds the
  // same regression-gate the Python side uses
  // (tests/test_policy_matrix.py) so a future EventKind member added
  // without a template fires a test failure.
  it("every EventKind member has at least one starter template", () => {
    const covered = coveredEvents()
    const missing: string[] = []
    for (const ev of SUPPORTED_EVENTS) {
      if (!covered.has(ev)) missing.push(ev)
    }
    expect(missing, `missing templates for: ${missing.join(", ")}`).toEqual([])
  })

  it("ships at least 30 templates (full event matrix)", () => {
    // Sanity floor; we expect at least one per EventKind plus the
    // custom-empty fallback.
    expect(SYNTHETIC_PAYLOAD_TEMPLATES.length).toBeGreaterThanOrEqual(
      SUPPORTED_EVENTS.length,
    )
  })

  it("every template carries both ko + en display labels", () => {
    for (const t of SYNTHETIC_PAYLOAD_TEMPLATES) {
      expect(t.displayLabel.ko.length).toBeGreaterThan(0)
      expect(t.displayLabel.en.length).toBeGreaterThan(0)
      expect(t.hint.ko.length).toBeGreaterThan(0)
      expect(t.hint.en.length).toBeGreaterThan(0)
    }
  })

  it("every template carries a hook_event_name field in the payload", () => {
    for (const t of SYNTHETIC_PAYLOAD_TEMPLATES) {
      // custom-empty deliberately ships an empty hook_event_name so
      // the operator types it. Every other template MUST pin the
      // event name to match its declared event so the simulator's
      // trigger-frame check passes.
      if (t.id === "custom-empty") {
        expect(t.payload.hook_event_name).toBe("")
      } else {
        expect(t.payload.hook_event_name).toBe(t.event)
      }
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

  // P2 review fix: realistic /etc shape so the operator's regex
  // policies aren't trained on space-joined fixture data that
  // doesn't match real CC output.
  it("post-bash-ls-etc ships newline-delimited /etc output", () => {
    const tpl = SYNTHETIC_PAYLOAD_TEMPLATES.find(
      (t) => t.id === "post-bash-ls-etc",
    )
    expect(tpl).toBeDefined()
    const out = (tpl!.payload as {
      tool_response?: { output?: string }
    }).tool_response?.output
    expect(out).toBeDefined()
    expect(out).toContain("passwd")
    expect(out).toContain("\n")
  })

  // P2 review fix: mcp tool template carries a clearly-placeholder
  // slug + hint copy that tells the operator to swap for a real
  // mcp__<server>__<tool> name.
  it("pre-mcp-tool template uses a placeholder slug + explanatory hint", () => {
    const tpl = SYNTHETIC_PAYLOAD_TEMPLATES.find(
      (t) => t.id === "pre-mcp-tool",
    )
    expect(tpl).toBeDefined()
    const tn = (tpl!.payload as { tool_name?: string }).tool_name
    expect(tn).toContain("mcp__example__")
    expect(tpl!.hint.en.toLowerCase()).toContain("replace")
  })

  // P2 review fix: custom-empty leaves hook_event_name blank so the
  // operator types it; the simulator surfaces no-event-supplied
  // until they do.
  it("custom-empty template ships empty hook_event_name", () => {
    const tpl = SYNTHETIC_PAYLOAD_TEMPLATES.find(
      (t) => t.id === "custom-empty",
    )
    expect(tpl).toBeDefined()
    expect(tpl!.payload.hook_event_name).toBe("")
    // Hint must tell the operator the event must be filled in first.
    expect(tpl!.hint.en.toLowerCase()).toContain("hook_event_name")
  })

  // P2 review fix: Notification template specifically requested - is
  // a high-traffic audit-only hook the operator is likely to author
  // against.
  it("has a Notification template", () => {
    const tpl = SYNTHETIC_PAYLOAD_TEMPLATES.find(
      (t) => t.event === "Notification",
    )
    expect(tpl).toBeDefined()
    expect(tpl!.payload.hook_event_name).toBe("Notification")
  })
})
