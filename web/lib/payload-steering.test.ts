import { describe, it, expect } from "vitest"
import {
  detectCumulativeSteering,
  activeTextForKind,
} from "./payload-steering"

describe("detectCumulativeSteering — gating", () => {
  it("returns false for non-payload kinds (evidence_ref already correct)", () => {
    const r = detectCumulativeSteering({
      conditionKind: "evidence_ref",
      text: "all tests passed",
    })
    expect(r.shouldSteer).toBe(false)
  })

  it("returns false for kind=none", () => {
    const r = detectCumulativeSteering({
      conditionKind: "none",
      text: "cumulative history of citations",
    })
    expect(r.shouldSteer).toBe(false)
  })

  it("returns false when conditionKind is undefined", () => {
    const r = detectCumulativeSteering({
      conditionKind: undefined,
      text: "all tests passed",
    })
    expect(r.shouldSteer).toBe(false)
  })

  it("returns false for empty / whitespace-only text", () => {
    expect(detectCumulativeSteering({ conditionKind: "llm_critic", text: "" }).shouldSteer).toBe(false)
    expect(detectCumulativeSteering({ conditionKind: "llm_critic", text: "   " }).shouldSteer).toBe(false)
    expect(detectCumulativeSteering({ conditionKind: "llm_critic", text: undefined }).shouldSteer).toBe(false)
  })

  it("returns false for kind=regex even on cumulative phrasing", () => {
    // Regex pattern syntax is not natural language; running NL
    // heuristics over regex source overreaches. The wizard still lets
    // authors switch kinds from the kind picker.
    const r = detectCumulativeSteering({
      conditionKind: "regex",
      text: "tests\\s+passed",
    })
    expect(r.shouldSteer).toBe(false)
  })
})

describe("detectCumulativeSteering — English cumulative phrases", () => {
  const cases: Array<[string, string]> = [
    ["test passed phrase fires", "Does the output prove that all tests passed?"],
    ["cumulative literal fires", "Reject any answer where cumulative coverage is below 80%"],
    ["cross-turn 'all turns' fires", "Verify all turns produced a clean trace"],
    ["previous turn fires", "Check that the previous turn included evidence"],
    ["conversation history fires", "Block if the conversation history shows an unsourced claim"],
    ["evidence chain fires", "Make sure the evidence chain remains intact"],
    ["citations plural fires", "Did the agent produce citations for each fact?"],
    ["verified token fires", "Has every fact been verified end-to-end?"],
    // P9 D49 additions — cumulative quantifier × outcome:
    ["'all verifiers must pass' fires", "All verifiers must pass before the final answer."],
    ["'every prior step succeeded' fires", "Each prior step succeeded without retry."],
    ["'so far' fires", "Has the agent stayed under budget so far?"],
    ["'audit trail' fires", "Maintain an audit trail of every shell command."],
    ["'running total' fires", "The running total of failed tools should be zero."],
  ]
  for (const [name, text] of cases) {
    it(name, () => {
      const r = detectCumulativeSteering({ conditionKind: "llm_critic", text })
      expect(r.shouldSteer).toBe(true)
      expect(r.matched.length).toBeGreaterThan(0)
    })
  }
})

describe("detectCumulativeSteering — Korean cumulative phrases", () => {
  const cases: Array<[string, string]> = [
    ["누적 fires", "누적 통과율이 80% 이상이어야 한다"],
    ["이전 턴 fires", "이전 턴에서 인용을 제시했는지 검사"],
    ["지금까지 검증된 fires", "지금까지 검증된 결과만 반영"],
    ["모두 통과 fires", "모든 도구가 모두 통과해야 한다"],
    ["통과율 fires", "전체 통과율을 보고하라"],
    ["이전 검증 fires", "이전에 검증된 주장만 반복하는가?"],
    ["감사 로그 fires", "감사 로그에 비밀이 노출되었는가?"],
  ]
  for (const [name, text] of cases) {
    it(name, () => {
      const r = detectCumulativeSteering({ conditionKind: "shacl", text })
      expect(r.shouldSteer).toBe(true)
      expect(r.matched.length).toBeGreaterThan(0)
    })
  }
})

describe("detectCumulativeSteering — snapshot phrasings stay quiet", () => {
  // Plain single-turn critic / SHACL spec → no steering. (Regex never
  // fires after the D49 P9 fix — see the gating block above.)
  const cases: Array<[string, string, string]> = [
    ["LLM snapshot critic EN", "llm_critic", "Does the output contain a guess the user did not ask for?"],
    ["LLM snapshot critic about command args", "llm_critic", "Does the command attempt to delete files outside the workspace?"],
    ["SHACL property shape", "shacl", "[ sh:path magi:tool_input.command ; sh:datatype xsd:string ]"],
    ["KO snapshot critic", "llm_critic", "이번 응답에 비속어가 포함되어 있는가?"],
  ]
  for (const [name, kind, text] of cases) {
    it(name, () => {
      const r = detectCumulativeSteering({ conditionKind: kind, text })
      expect(r.shouldSteer).toBe(false)
      expect(r.matched).toEqual([])
    })
  }
})

describe("detectCumulativeSteering — high-frequency single-turn nouns stay quiet bare", () => {
  // P9 D49 issue #5/#7: these used to over-fire because bare 'history' /
  // 'citation' singular / '검증' / '통과' / 'verify' triggered the
  // detector on legitimate single-turn snapshot checks. Now each
  // requires a cumulative qualifier nearby.
  const cases: Array<[string, string, string]> = [
    ["bare 'history' (git command output)", "llm_critic", "Does the output contain a git history shown as raw lines?"],
    ["bare 'history' (browser command dump)", "llm_critic", "Block any output that mentions browser history command output"],
    ["bare singular 'citation' (single-turn check)", "llm_critic", "Block any output that fabricates a citation to a journal"],
    ["bare 'verify' verb", "llm_critic", "Verify the output does not leak AWS keys"],
    ["bare KO '검증' (single command)", "llm_critic", "sudo 명령어 검증 결과를 출력하는가?"],
    ["bare KO '통과' (single-turn build)", "llm_critic", "이번 빌드 통과 여부 출력에 포함되어 있는가?"],
    ["bare KO '통과' (literal-word check)", "llm_critic", "출력에 통과라는 단어가 포함되어 있는가?"],
  ]
  for (const [name, kind, text] of cases) {
    it(name, () => {
      const r = detectCumulativeSteering({ conditionKind: kind, text })
      expect(r.shouldSteer).toBe(false)
      expect(r.matched).toEqual([])
    })
  }
})

describe("detectCumulativeSteering — qualified nouns fire", () => {
  // The flip side of the bare-noun cases: when the qualifier is present
  // the heuristic should still surface a tip.
  const cases: Array<[string, string]> = [
    ["'every citation' fires", "Verify that every citation in the run is from a trusted domain"],
    ["'all citations' fires", "Make sure all citations across the answer are reachable URLs"],
    ["'previously verified' fires", "Only repeat claims that were previously verified"],
  ]
  for (const [name, text] of cases) {
    it(name, () => {
      const r = detectCumulativeSteering({ conditionKind: "llm_critic", text })
      expect(r.shouldSteer).toBe(true)
      expect(r.matched.length).toBeGreaterThan(0)
    })
  }
})

describe("detectCumulativeSteering — snapshot anchor suppresses cumulative match", () => {
  // Author wrote a cumulative-shaped word but scoped to one turn — we
  // trust the scope and stay quiet.
  it("suppresses 'verified' when 'this output' is present (EN)", () => {
    const r = detectCumulativeSteering({
      conditionKind: "llm_critic",
      text: "Does this output claim something has been verified without proof?",
    })
    expect(r.shouldSteer).toBe(false)
  })

  it("suppresses 'citations' when 'in the answer' is present (EN)", () => {
    const r = detectCumulativeSteering({
      conditionKind: "llm_critic",
      text: "Are the citations in the answer from a domain we don't trust?",
    })
    expect(r.shouldSteer).toBe(false)
  })

  it("suppresses 'tests passed' when 'contain the phrase' is present (literal-string check)", () => {
    const r = detectCumulativeSteering({
      conditionKind: "llm_critic",
      text: "Does the output contain the phrase 'tests passed' without showing the actual test command?",
    })
    expect(r.shouldSteer).toBe(false)
  })

  it("suppresses '검증' when '이번 응답' is present (KO)", () => {
    const r = detectCumulativeSteering({
      conditionKind: "llm_critic",
      text: "이번 응답이 누적 검증되었다고 주장만 하고 근거가 없는가?",
    })
    expect(r.shouldSteer).toBe(false)
  })
})

describe("detectCumulativeSteering — token boundary safety", () => {
  it("does not fire 'verify' inside 'preverify' or unrelated compounds", () => {
    const r = detectCumulativeSteering({
      conditionKind: "llm_critic",
      text: "Does the output mention preverification step names?",
    })
    expect(r.shouldSteer).toBe(false)
  })

  it("matches the 'verified' token only as a whole word", () => {
    // 'unverifiedness' should not trigger 'verified'
    const r = detectCumulativeSteering({
      conditionKind: "llm_critic",
      text: "Reject answers using the term unverifiedness without a source",
    })
    expect(r.shouldSteer).toBe(false)
  })
})

describe("detectCumulativeSteering — dedup matched", () => {
  it("returns each matched phrase at most once", () => {
    const r = detectCumulativeSteering({
      conditionKind: "llm_critic",
      text: "all tests passed, all tests passed, all tests passed",
    })
    expect(r.shouldSteer).toBe(true)
    expect(r.matched.filter((m) => m === "all tests").length).toBe(1)
  })
})

describe("activeTextForKind", () => {
  const state = {
    pattern: "AKIA[A-Z0-9]+",
    llmCriterion: "Does the output cite a source?",
    shaclTtl: "@prefix sh: <…> .",
  }
  it("returns pattern for regex", () => {
    expect(activeTextForKind("regex", state)).toBe(state.pattern)
  })
  it("returns llmCriterion for llm_critic", () => {
    expect(activeTextForKind("llm_critic", state)).toBe(state.llmCriterion)
  })
  it("returns shaclTtl for shacl", () => {
    expect(activeTextForKind("shacl", state)).toBe(state.shaclTtl)
  })
  it("returns empty string for non-payload kinds", () => {
    expect(activeTextForKind("evidence_ref", state)).toBe("")
    expect(activeTextForKind("none", state)).toBe("")
    expect(activeTextForKind(undefined, state)).toBe("")
  })
  it("tolerates missing per-kind fields", () => {
    expect(activeTextForKind("regex", {})).toBe("")
    expect(activeTextForKind("llm_critic", {})).toBe("")
    expect(activeTextForKind("shacl", {})).toBe("")
  })
})
