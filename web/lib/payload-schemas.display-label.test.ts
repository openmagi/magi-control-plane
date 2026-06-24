import { describe, it, expect } from "vitest"
import {
  allSchemas,
  availableFields,
  getDisplayLabel,
} from "./payload-schemas"

/**
 * D64: web mirror of the friendly display-label table. Same invariants
 * as `tests/test_payload_schemas_display_label.py` so a follow-up rename
 * has to update both sides or fail the gate.
 */

const KNOWN_PATHS = [
  "tool_input.command",
  "tool_input.url",
  "tool_input.file_path",
  "tool_input.old_string",
  "tool_input.new_string",
  "tool_input.content",
  "tool_input.cwd",
  "tool_input.timeout",
  "tool_input.description",
  "tool_input.prompt",
  "tool_input.offset",
  "tool_input.limit",
  "tool_input",
  "tool_response.output",
  "tool_response.is_error",
  "tool_response.duration_ms",
  "final_message",
  "prompt",
  "transcript_path",
  "transcript",
  "session_id",
  "tool_use_id",
  "tool_name",
  "cwd",
  "citations[].quote",
  "citations[].ref",
] as const

describe("getDisplayLabel — known paths render friendly labels", () => {
  for (const p of KNOWN_PATHS) {
    it(`returns a non-empty EN label for ${p}`, () => {
      const label = getDisplayLabel(p, "en")
      expect(label).not.toBe(p)
      expect(label.length).toBeGreaterThan(0)
    })
    it(`returns a non-empty KO label for ${p}`, () => {
      const label = getDisplayLabel(p, "ko")
      expect(label).not.toBe(p)
      expect(label.length).toBeGreaterThan(0)
    })
  }
})

describe("getDisplayLabel — brief-pinned canonical examples", () => {
  it("Bash command (KO + EN) match the brief", () => {
    expect(getDisplayLabel("tool_input.command", "en")).toBe("Bash command")
    expect(getDisplayLabel("tool_input.command", "ko")).toBe("Bash 명령어")
  })

  it("Fetched URL", () => {
    expect(getDisplayLabel("tool_input.url", "en")).toBe("Fetched URL")
    expect(getDisplayLabel("tool_input.url", "ko")).toBe("요청 URL")
  })

  it("File path", () => {
    expect(getDisplayLabel("tool_input.file_path", "en")).toBe("File path")
    expect(getDisplayLabel("tool_input.file_path", "ko")).toBe("파일 경로")
  })

  it("Tool output / error flag / duration", () => {
    expect(getDisplayLabel("tool_response.output", "en")).toBe("Tool output")
    expect(getDisplayLabel("tool_response.output", "ko")).toBe("도구 출력")
    expect(getDisplayLabel("tool_response.is_error", "en")).toBe("Tool error flag")
    expect(getDisplayLabel("tool_response.is_error", "ko")).toBe("도구 오류 여부")
    expect(getDisplayLabel("tool_response.duration_ms", "en")).toBe("Tool duration (ms)")
    expect(getDisplayLabel("tool_response.duration_ms", "ko")).toBe("도구 실행 시간(ms)")
  })

  it("Final answer / user prompt / transcript path", () => {
    expect(getDisplayLabel("final_message", "en")).toBe("Agent final answer")
    expect(getDisplayLabel("final_message", "ko")).toBe("에이전트 최종 답변")
    expect(getDisplayLabel("prompt", "en")).toBe("User prompt")
    expect(getDisplayLabel("prompt", "ko")).toBe("사용자 입력")
    expect(getDisplayLabel("transcript_path", "en")).toBe("Conversation transcript path")
    expect(getDisplayLabel("transcript_path", "ko")).toBe("대화 기록 경로")
  })

  it("Session / tool IDs", () => {
    expect(getDisplayLabel("session_id", "en")).toBe("Session ID")
    expect(getDisplayLabel("tool_use_id", "en")).toBe("Tool call ID")
    expect(getDisplayLabel("tool_name", "en")).toBe("Tool name")
    expect(getDisplayLabel("tool_name", "ko")).toBe("도구 이름")
  })

  it("Citation nested fields", () => {
    expect(getDisplayLabel("citations[].quote", "en")).toBe("Cited quote")
    expect(getDisplayLabel("citations[].quote", "ko")).toBe("인용 본문")
    expect(getDisplayLabel("citations[].ref", "en")).toBe("Citation reference id")
    expect(getDisplayLabel("citations[].ref", "ko")).toBe("인용 ref id")
  })
})

describe("getDisplayLabel — fallbacks", () => {
  it("UNKNOWN path → raw path verbatim (back-compat)", () => {
    // Operator-typed MCP slug. The UI never invents a friendly name.
    expect(getDisplayLabel("mcp__court__file.docket_id", "en")).toBe(
      "mcp__court__file.docket_id",
    )
    expect(getDisplayLabel("mcp__court__file.docket_id", "ko")).toBe(
      "mcp__court__file.docket_id",
    )
  })

  it("empty path returns empty", () => {
    expect(getDisplayLabel("", "en")).toBe("")
    expect(getDisplayLabel("", "ko")).toBe("")
  })

  it("no locale arg defaults to English", () => {
    expect(getDisplayLabel("tool_input.command")).toBe("Bash command")
  })
})

describe("availableFields carries display_label_* on every descriptor", () => {
  it("PreToolUse + Bash chip row gets friendly labels", () => {
    const fields = availableFields("PreToolUse", "Bash")
    expect(fields.length).toBeGreaterThan(0)
    for (const f of fields) {
      expect(f.display_label_ko).toBeTruthy()
      expect(f.display_label_en).toBeTruthy()
    }
    const cmd = fields.find((f) => f.path === "tool_input.command")
    expect(cmd?.display_label_en).toBe("Bash command")
    expect(cmd?.display_label_ko).toBe("Bash 명령어")
  })

  it("PostToolUse exposes friendly tool_response labels", () => {
    const fields = availableFields("PostToolUse", "Bash")
    const out = fields.find((f) => f.path === "tool_response.output")
    expect(out?.display_label_en).toBe("Tool output")
    expect(out?.display_label_ko).toBe("도구 출력")
  })

  it("Stop event exposes friendly final_message label", () => {
    const fields = availableFields("Stop")
    const fin = fields.find((f) => f.path === "final_message")
    expect(fin?.display_label_en).toBe("Agent final answer")
    expect(fin?.display_label_ko).toBe("에이전트 최종 답변")
  })

  it("UserPromptSubmit exposes friendly prompt label", () => {
    const fields = availableFields("UserPromptSubmit")
    const pr = fields.find((f) => f.path === "prompt")
    expect(pr?.display_label_en).toBe("User prompt")
    expect(pr?.display_label_ko).toBe("사용자 입력")
  })
})

describe("allSchemas REST contract carries display labels", () => {
  it("every schema field has both display_label_ko + _en", () => {
    const schemas = allSchemas()
    expect(schemas.length).toBeGreaterThan(0)
    for (const s of schemas) {
      for (const f of s.fields) {
        expect(f.display_label_ko, `missing ko on ${s.event}/${f.path}`).toBeTruthy()
        expect(f.display_label_en, `missing en on ${s.event}/${f.path}`).toBeTruthy()
      }
    }
  })
})
