/**
 * P9 (issue #1): input-domain steering for the policy wizard.
 *
 * The CC hook payload-kind conditions (regex / llm_critic / shacl) see
 * ONE turn's tool input / output / final answer at a time. A judgment
 * like "all unit tests passed" or "every previous citation was verified"
 * is inherently cross-turn / cumulative — phrasing it as a payload-kind
 * check is a silent fail-open: the gate sees one turn, can't see the
 * history, and the regex / critic / shape simply never fires the way
 * the author imagined.
 *
 * The correct backing for cumulative judgments is `evidence_ref` — a
 * preset verifier that the runtime drives across turns and folds into a
 * durable evidence ledger. Step verifiers see the full chain; payload
 * conditions do not.
 *
 * This module is a heuristic detector, NOT an LLM call. It scans the
 * author's free-text criterion / pattern / shape for keywords that
 * strongly suggest cumulative reasoning, and the Step 3 UI then offers
 * a tip card with a "Switch to evidence_ref" affordance.
 *
 * Heuristic, not authoritative: a payload-kind condition with one of
 * these keywords is still legal (the user might genuinely mean a
 * single-turn check, e.g. "block any answer that uses the word 'verified'").
 * The tip is suppressible.
 *
 * P9 fix cycle (D49):
 *   - Skip the detector entirely for regex (pattern syntax is not NL).
 *   - High-frequency single-turn nouns ('history', bare 'citation',
 *     bare 'verify', bare KO '검증' / '통과') only fire when paired with
 *     an explicit cumulative qualifier (quantifier × time-range × past
 *     participle).
 *   - Added 'cumulative quantifier × outcome' phrase set so
 *     'all verifiers must pass' / 'every prior step succeeded' fire
 *     without needing the bare noun.
 *   - Added literal-string-detection snapshot anchors
 *     ('contain the phrase', 'the literal', 'mentions', …) so authors
 *     who are looking for a substring in this turn's output are not
 *     pushed into evidence_ref.
 */

export type SteerableConditionKind = "regex" | "llm_critic" | "shacl"

/** Unambiguously cumulative English phrases. Lowercased; substring
 * matched on the lowercased input.
 *
 * Keep this list tight — every phrase here should be a near-zero
 * false-positive on single-turn snapshot checks. High-frequency nouns
 * like bare 'history' / 'citation' / 'verify' are intentionally NOT
 * here; they live in EN_QUALIFIED_* sets that require a cumulative
 * qualifier to fire. */
const EN_PHRASES: readonly string[] = [
  "all tests",
  "tests passed",
  "test passed",
  "test results",
  "all turns",
  "every turn",
  "previous turn",
  "previous turns",
  "previous turn(s)",
  "earlier turn",
  "earlier turns",
  "earlier turn(s)",
  "prior turn",
  "prior turns",
  "prior turn(s)",
  "each prior step",
  "every prior step",
  "every prior",
  "each prior",
  "across turns",
  "across the session",
  "across the conversation",
  "over the session",
  "over time",
  "throughout the session",
  "throughout",
  "cumulative",
  "evidence chain",
  "verified earlier",
  "verified previously",
  "previously verified",
  "previous citations",
  "earlier citations",
  // cumulative quantifier × outcome — these alone are enough.
  "must pass",
  "all pass",
  "all passed",
  "every pass",
  "every passes",
  "every passed",
  "all succeed",
  "all succeeded",
  "every succeed",
  "every succeeded",
  "each succeed",
  "each succeeded",
  "end-to-end success",
  "so far",
  "up to now",
  "to date",
  // audit / aggregate vocabulary
  "audit trail",
  "audit log",
  "compliance",
  "aggregate",
  "aggregated",
  "in aggregate",
  "running total",
  "summary of",
  // 'history' is only cumulative when scoped to the dialogue/run.
  "conversation history",
  "turn history",
  "message history",
  "chat history",
]

/** Single-word English tokens. Whole-token match on \b boundaries to
 * avoid e.g. "preverify" eating "verify". */
const EN_TOKENS: readonly string[] = [
  "verified",
  "accumulated",
  // Plural 'citations' / 'verifiers' are cumulative-sounding even alone
  // (a single-turn check usually phrases as 'cite a source' / 'the
  // citation' / 'a verifier' singular). Bare singular 'citation' and
  // bare 'verify' are NOT in this set — they require a qualifier (see
  // EN_QUALIFIED_*).
  "citations",
  "verifiers",
  "verifier",
]

/** High-frequency English nouns that overreach when matched bare. Each
 * one only fires when at least one cumulative qualifier from
 * EN_QUALIFIERS appears in the same input. */
const EN_QUALIFIED_NOUNS: readonly string[] = [
  "citation", // singular — needs qualifier (plural 'citations' is fine on its own)
  "verify",   // verb — needs qualifier (past-participle 'verified' is fine)
  "cited",
]

/** Phrases that strongly imply cumulative scope. Pairing one of these
 * with a high-frequency noun upgrades the noun match to a real fire. */
const EN_QUALIFIERS: readonly string[] = [
  "every",
  "all ",
  "all the ",
  "all of ",
  "each ",
  "previous",
  "prior",
  "earlier",
  "previously",
  "across",
  "throughout",
  "cumulative",
  "so far",
  "up to now",
  "to date",
  "over the session",
  "over time",
  "history",
]

/** Korean phrase set — unambiguously cumulative. Substring-matched on
 * the lowercased input (Hangul is unaffected by lowercasing).
 *
 * Bare '통과' / '검증' fire on too many single-turn critics ('이번 빌드
 * 통과 여부', '출력에 통과라는 단어 포함') and have been moved to
 * KO_QUALIFIED_NOUNS. Past-participle '검증되었' / '검증된' need a
 * prior-time qualifier from KO_QUALIFIERS to fire. */
const KO_PHRASES: readonly string[] = [
  "누적",
  "이전 턴",
  "이전턴",
  "지금까지",
  "전체 턴",
  "모든 턴",
  "모든 호출",
  "이전 호출",
  "이전 응답들",
  "이전 인용",
  "이전 까지",
  "이전까지",
  "전 구간",
  "통과율",
  "모두 통과",
  "전부 통과",
  "누적 통과",
  "모두 검증",
  "전부 검증",
  "누적 검증",
  "감사 로그",
  "감사 추적",
  "컴플라이언스",
  "준수 여부",
  "누적된",
]

/** Korean high-frequency nouns that overreach bare. Each fires only
 * when paired with a qualifier from KO_QUALIFIERS. */
const KO_QUALIFIED_NOUNS: readonly string[] = [
  "검증",
  "통과",
  "인용",
]

/** Korean prior-time / scope qualifiers. */
const KO_QUALIFIERS: readonly string[] = [
  "이전",
  "이전에",
  "이전까지",
  "지금까지",
  "앞서",
  "모두",
  "전부",
  "각",
  "각각",
  "누적",
  "이번까지",
  "전체",
  "모든",
  "전 구간",
]

/** Korean past-participles that imply prior verification. They only
 * fire when paired with a prior-time qualifier (otherwise '이 답이 이미
 * 검증된' could be talking about this single answer in isolation). */
const KO_PAST_PARTICIPLES: readonly string[] = [
  "검증되었",
  "검증된",
]

/** Snapshot-style keywords that argue AGAINST a steering tip even when
 * a cumulative phrase appears. If the input contains both a cumulative
 * keyword AND a snapshot-style anchor like "this turn" / "this call" /
 * "in the output", we suppress the tip — the author has explicitly
 * scoped to one turn.
 *
 * EN_SNAPSHOT phrases match substring on the lowercased input. */
const EN_SNAPSHOT: readonly string[] = [
  "this turn",
  "this call",
  "this output",
  "this answer",
  "in the output",
  "in this output",
  "in the answer",
  "in this answer",
  "current tool",
  "current call",
  "tool args",
  "the args",
  "the command",
  // Literal-string-detection phrasings — these are textbook snapshot
  // checks even when they mention 'passed' / 'verified' / 'citation' /
  // …  See P9 issue #6.
  "contain the phrase",
  "contains the phrase",
  "the phrase",
  "the literal",
  "the string",
  "the substring",
  "mentions",
  "appears in",
  "present in the output",
  "without showing",
  "without proof",
]

const KO_SNAPSHOT: readonly string[] = [
  "이번 턴",
  "이번턴",
  "이번 호출",
  "이번 응답",
  "이번응답",
  "이번 빌드",
  "이번 결과",
  "이번 출력",
  "현재 출력",
  "현재 응답",
  "이 답변",
  "이 출력",
  "이 응답",
  "라는 단어",
  "라는 문구",
  "문구가 포함",
  "단어가 포함",
  "포함되어 있는가",
  "포함되어",
  "포함하는가",
]

export interface SteeringInput {
  /** Active condition kind on Step 3. Steering only ever applies to
   * payload-kind conditions; pre-final `evidence_ref` is already the
   * right answer and produces no tip. */
  conditionKind: SteerableConditionKind | string | undefined
  /** Free-text spec for the active kind:
   *   - regex      → pattern
   *   - llm_critic → llmCriterion
   *   - shacl      → shape_ttl
   * Pass the empty string when no input yet. */
  text: string | undefined
}

export interface SteeringResult {
  /** True = surface the tip card. */
  shouldSteer: boolean
  /** Concrete tokens / phrases that triggered the heuristic. Surfaced
   * in the tip's "why we think so" reveal so the author can disagree
   * with confidence. Empty when shouldSteer=false. */
  matched: string[]
}

/** Condition kinds for which the steering heuristic runs at all.
 *
 * NB: `regex` is intentionally absent. A regex pattern is not natural
 * language — the syntax (`\b`, character classes, quantifiers) reads as
 * "history" / "verified" etc. only when those words appear LITERALLY in
 * the pattern, which is a legitimate snapshot check ('tests?\\s+passed'
 * for log-line detection). Running NL keyword heuristics over regex
 * source produces too much noise. The wizard still lets authors switch
 * to evidence_ref from the kind picker. */
const PAYLOAD_KINDS: ReadonlySet<string> = new Set([
  "llm_critic",
  "shacl",
])

function someIncludes(haystack: string, needles: readonly string[]): boolean {
  for (const n of needles) {
    if (haystack.includes(n)) return true
  }
  return false
}

/** Detect whether the (conditionKind, text) pair smells like a
 * cumulative / cross-turn judgment that would be better authored as
 * `evidence_ref`. Pure function, no I/O. */
export function detectCumulativeSteering(input: SteeringInput): SteeringResult {
  const kind = input.conditionKind ?? ""
  if (!PAYLOAD_KINDS.has(kind)) {
    return { shouldSteer: false, matched: [] }
  }
  const raw = (input.text ?? "").trim()
  if (raw.length === 0) {
    return { shouldSteer: false, matched: [] }
  }
  const lowered = raw.toLowerCase()
  const matched: string[] = []

  // Unambiguous cumulative phrases.
  for (const p of EN_PHRASES) {
    if (lowered.includes(p)) matched.push(p)
  }
  // Whole-word English tokens (verified / accumulated / citations /
  // verifiers / verifier).
  for (const tok of EN_TOKENS) {
    const re = new RegExp(`\\b${escapeRegExp(tok)}\\b`)
    if (re.test(lowered)) matched.push(tok)
  }
  // Quantifier × high-frequency-noun in English. Both must be present.
  const hasEnQualifier = someIncludes(lowered, EN_QUALIFIERS)
  if (hasEnQualifier) {
    for (const noun of EN_QUALIFIED_NOUNS) {
      const re = new RegExp(`\\b${escapeRegExp(noun)}\\b`)
      if (re.test(lowered)) matched.push(noun)
    }
  }

  // Korean unambiguous cumulative phrases.
  for (const p of KO_PHRASES) {
    if (lowered.includes(p)) matched.push(p)
  }
  // Quantifier × high-frequency-noun in Korean.
  const hasKoQualifier = someIncludes(lowered, KO_QUALIFIERS)
  if (hasKoQualifier) {
    for (const noun of KO_QUALIFIED_NOUNS) {
      if (lowered.includes(noun)) matched.push(noun)
    }
    for (const part of KO_PAST_PARTICIPLES) {
      if (lowered.includes(part)) matched.push(part)
    }
  }

  if (matched.length === 0) {
    return { shouldSteer: false, matched: [] }
  }
  // Snapshot anchor present → author explicitly scoped to one turn, do
  // not steer.
  for (const s of EN_SNAPSHOT) {
    if (lowered.includes(s)) return { shouldSteer: false, matched: [] }
  }
  for (const s of KO_SNAPSHOT) {
    if (lowered.includes(s)) return { shouldSteer: false, matched: [] }
  }
  // De-dup while preserving order.
  const seen = new Set<string>()
  const dedup: string[] = []
  for (const m of matched) {
    if (seen.has(m)) continue
    seen.add(m)
    dedup.push(m)
  }
  return { shouldSteer: true, matched: dedup }
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

/** Pick the active free-text for a given condition kind out of the
 * wizard state, so the Step 3 renderer can call detect() per kind
 * without re-implementing the field-to-kind mapping. */
export function activeTextForKind(
  kind: SteerableConditionKind | string | undefined,
  state: { pattern?: string; llmCriterion?: string; shaclTtl?: string },
): string {
  if (kind === "regex") return state.pattern ?? ""
  if (kind === "llm_critic") return state.llmCriterion ?? ""
  if (kind === "shacl") return state.shaclTtl ?? ""
  return ""
}
