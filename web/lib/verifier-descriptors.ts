/**
 * D52b: per-verifier expander descriptors. Mirrors
 * src/magi_cp/verifier/descriptors.py.
 *
 * Each entry describes:
 *   - triggers: which CC hook events + matcher_class pairs fire this
 *     verifier (per the policy/payload_schemas.py vocabulary)
 *   - input_payload_paths: which payload paths the verifier reads
 *   - verdict_set: the closed set of verdicts this verifier may return
 *   - output_evidence: shape of the record written to the audit ledger
 *
 * The mirror exists so the Rules → Verifiers tab can render the expander
 * server-side without an awaited fetch. The cloud's /verifier-descriptors
 * endpoint stays the source of truth. A dashboard build that wants to
 * stay in lockstep can call cloud.listVerifierDescriptors() and shadow
 * this static copy.
 */

export type VerdictStatus = "pass" | "review" | "deny"

export type MatcherClass = "tool" | "no_tool" | "final"

export type TriggerSpec = {
  event: string
  matcher_class: MatcherClass
  note: string
}

export type EvidenceFieldType = "str" | "int" | "bool" | "list" | "dict"

export type EvidenceField = {
  path: string
  type: EvidenceFieldType
  description: string
}

export type VerifierDescriptor = {
  step: string
  triggers: TriggerSpec[]
  input_payload_paths: string[]
  verdict_set: VerdictStatus[]
  output_evidence: EvidenceField[]
}

const COMMON_OUTPUT_FIELDS: EvidenceField[] = [
  {
    path: "step",
    type: "str",
    description:
      "Verifier step name. Same value the policy IR binds via requires[].step.",
  },
  {
    path: "subject",
    type: "str",
    description:
      "Canonical subject the verdict is bound to (filing id, session id, etc).",
  },
  {
    path: "payload_hash",
    type: "str",
    description:
      "SHA-256 of the input payload the verifier ran against. Stable across replays.",
  },
  {
    path: "verdict",
    type: "str",
    description:
      "One of pass / review / deny. Drives the runtime gate decision.",
  },
  {
    path: "reasons",
    type: "list",
    description:
      "Human-readable reasons collected during the run. Empty on clean pass.",
  },
]

const REGISTRY: Record<string, VerifierDescriptor> = {
  citation_verify: {
    step: "citation_verify",
    triggers: [
      {
        event: "Stop",
        matcher_class: "final",
        note: "Pre-final answer check. Runs once before the agent's final reply.",
      },
      {
        event: "PostToolUse",
        matcher_class: "tool",
        note: "After a research / fetch tool has gathered sources to cite.",
      },
    ],
    input_payload_paths: ["citations[].quote", "citations[].ref", "corpus_override"],
    verdict_set: ["pass", "review", "deny"],
    output_evidence: [
      ...COMMON_OUTPUT_FIELDS,
      {
        path: "citations[].ref",
        type: "str",
        description: "The cited reference id from the input.",
      },
      {
        path: "citations[].status",
        type: "str",
        description: "Per-citation verdict from the NLI pipeline.",
      },
    ],
  },
  privilege_scan: {
    step: "privilege_scan",
    triggers: [
      {
        event: "PreToolUse",
        matcher_class: "tool",
        note: "Scan tool input before it leaves the gate (Bash command / file write content).",
      },
      {
        event: "Stop",
        matcher_class: "final",
        note: "Pre-final answer scrub for privilege markers + Korean RRN.",
      },
    ],
    input_payload_paths: ["text"],
    verdict_set: ["pass", "review", "deny"],
    output_evidence: COMMON_OUTPUT_FIELDS,
  },
  source_allowlist: {
    step: "source_allowlist",
    triggers: [
      {
        event: "PreToolUse",
        matcher_class: "tool",
        note: "WebFetch source allowlist check before the request fires.",
      },
      {
        event: "PostToolUse",
        matcher_class: "tool",
        note: "Post-fetch validation of the URLs the tool actually pulled.",
      },
    ],
    input_payload_paths: ["sources", "allowlist"],
    verdict_set: ["pass", "deny"],
    output_evidence: COMMON_OUTPUT_FIELDS,
  },
  structured_output: {
    step: "structured_output",
    triggers: [
      {
        event: "Stop",
        matcher_class: "final",
        note: "Validate the agent's final reply against a JSON-Schema subset.",
      },
      {
        event: "PostToolUse",
        matcher_class: "tool",
        note: "Validate a tool's structured output (filing payload, API response).",
      },
    ],
    input_payload_paths: ["json", "data", "schema"],
    verdict_set: ["pass", "deny"],
    output_evidence: COMMON_OUTPUT_FIELDS,
  },
  prompt_injection_screen: {
    step: "prompt_injection_screen",
    triggers: [
      {
        event: "UserPromptSubmit",
        matcher_class: "no_tool",
        note: "Screen the incoming user message for jailbreak / override patterns.",
      },
      {
        event: "PostToolUse",
        matcher_class: "tool",
        note: "Screen retrieved source text for injection attempts before it joins context.",
      },
    ],
    input_payload_paths: ["text"],
    verdict_set: ["pass", "deny"],
    output_evidence: COMMON_OUTPUT_FIELDS,
  },
}

/** Look up the descriptor for one verifier step. Returns null when no
 * descriptor is registered. The dashboard renders a neutral "no
 * descriptor" notice in that case rather than throwing. */
export function getVerifierDescriptor(step: string): VerifierDescriptor | null {
  return REGISTRY[step] ?? null
}

/** Flat list dump for client-cache scenarios (parity with
 * cloud.listVerifierDescriptors()). */
export function allVerifierDescriptors(): VerifierDescriptor[] {
  return Object.keys(REGISTRY)
    .sort()
    .map((step) => REGISTRY[step])
}
