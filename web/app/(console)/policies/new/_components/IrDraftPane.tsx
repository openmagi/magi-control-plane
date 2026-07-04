"use client"

/**
 * D55b: live IR draft preview pane (right column of Conversational compose).
 *
 * Shows:
 *   - A plain-language summary at the top ("When: ... | Condition: ...
 *     | Action: ..."). Placeholders for any field the draft hasn't
 *     filled yet. Renders inside aria-live="polite" so SR users hear
 *     each merge.
 *   - A "Dry-run on last 24h" button (gated on draft validity).
 *     Delegates to the shared DryRunPanel (D53b). We render IT here
 *     so the brief's "Reuse the existing DryRunPanel.tsx without
 *     modification" constraint holds.
 *   - A "Save this rule" CTA gated on `ready_to_save=true`. Posts to
 *     the existing saveCompiled server action exposed by the parent
 *     page; we render a real <form action={saveAction}> with the
 *     current draft serialized into the hidden `ir_json` field.
 *
 * Brief: this file MUST use sub-path imports ("@/components/ui/<X>").
 * The "@/components/ui" barrel pulls a server-only chain into the
 * client bundle and breaks `next build`.
 *
 * NEVER expose internal terms (regex / shacl / llm_critic / matcher /
 * lifecycle / kind / on_missing) to end users. The plain-language
 * summary uses friendly translations only. No raw IR / JSON view
 * lives on the chat surface (D55b code review P0); power users have
 * the Raw/Advanced mode for that.
 */

import { Button } from "@/components/ui/Button"
import { getDisplayLabel } from "@/lib/payload-schemas"
import { DryRunPanel } from "../../_components/DryRunPanel"
import { PackMultiSelect } from "./PackMultiSelect"

// i18n helper signature matches the rest of the policies/* tree.
type T = (
  k: import("@/lib/i18n/dict").TKey,
  v?: Record<string, string | number>,
) => string

// D63 review (P1): widened to include `run_command` so a wizard-handed-off
// or conversational-compose-emitted run_command IR renders its command
// body / script id / runtime / args / timeout in the right-column draft
// pane instead of falling through to "(not chosen yet)".
type ActionArchetype =
  | "block" | "ask" | "audit" | "strip"
  | "run_command"

export interface IrDraftPaneProps {
  t: T
  locale: "ko" | "en"
  /** The current draft, server-side. May be partially populated. */
  draft: Record<string, unknown> | null
  /** When true the Save CTA is enabled. */
  readyToSave: boolean
  /** Server action posted to by the Save CTA. The parent (page.tsx)
   *  threads through `saveCompiled` from its server-action wiring. */
  saveAction: (fd: FormData) => Promise<void>
  /** Q102: canonical missing-field set from the conversational compiler
   *  (mirrors `_missing_fields_for_draft` server-side). Drives the
   *  status pill (DRAFTING/READY), the per-row "named missing" copy,
   *  and the quiet "이 항목이 비어 있어요: ..." line under the card.
   *  Optional so existing callers (handoff seed mounts that have not
   *  yet round-tripped) still render the legacy placeholder copy. */
  missingFields?: readonly string[]
  /** P4: freeform text (the operator's first message) fed to the pack
   *  picker's extractor so a named work context ("리서치", "coding
   *  safety") pre-selects the matching pack. Optional; when absent the
   *  picker still renders with no suggestion. */
  suggestedPackText?: string | null
  /** P4 legacy-guard: only render the pack-membership picker when the
   *  pack-centric runtime is on. With the flag off the gate fires
   *  enabled policies regardless of pack membership, so the picker's
   *  "an unpacked policy fires in no session" hint would mislead. */
  packCentric?: boolean
  /** Policy-integrity review verdict for the ready draft (advisory).
   *  Rendered above the Save CTA so the operator sees "does this do what
   *  I asked?" before committing. Null while not-ready or unfetched.
   *  F1/F2: issues carry stable `code`s the pane localizes; `checked` lists
   *  which layers ran so a no-op review is not shown as a green pass. */
  review?: {
    ok: boolean
    summaryCode: string
    checked: string[]
    issues: {
      severity: string; code: string; message: string
      params: Record<string, unknown>; source: string
    }[]
  } | null
  /** True while the review request is in flight. */
  reviewPending?: boolean
  /** F2: true when the review request failed - renders a neutral
   *  "couldn't check (you can still save)" row, not a hidden surface. */
  reviewError?: boolean
  /** Optional test id for the root container. */
  testId?: string
}

/* ── Q102: missing-field name lookup ────────────────────────────────── */

/** Canonical missing-field names the server emits. Mirror of
 *  `FieldName` in src/magi_cp/policy/nl_compiler_interactive.py. */
type MissingField =
  | "lifecycle" | "matcher" | "requires" | "requires_body"
  | "on_missing" | "id"

const MISSING_FIELD_KEYS: ReadonlyArray<MissingField> = [
  "lifecycle", "matcher", "requires", "requires_body", "on_missing", "id",
]

/** Resolve a missing-field name (lifecycle / matcher / ...) to its
 *  user-facing label via i18n. Internal vocabulary stays internal:
 *  the dict keys map to plain-language strings ("시점" / "trigger
 *  timing") so the operator never sees raw IR field names. */
function missingFieldLabel(field: MissingField, t: T): string {
  // Map each known field name to its i18n key. Inline so a typo in
  // any branch is caught by TKey at build time.
  switch (field) {
    case "lifecycle":
      return t("newPolicy.conv.liveDraft.missing.lifecycle")
    case "matcher":
      return t("newPolicy.conv.liveDraft.missing.matcher")
    case "requires":
      return t("newPolicy.conv.liveDraft.missing.requires")
    case "requires_body":
      return t("newPolicy.conv.liveDraft.missing.requires_body")
    case "on_missing":
      return t("newPolicy.conv.liveDraft.missing.on_missing")
    case "id":
      return t("newPolicy.conv.liveDraft.missing.id")
  }
}

/** True when the given name is one we know how to label. Anything
 *  else (a future field added server-side before the dashboard
 *  catches up) is silently dropped from the bottom list so we never
 *  surface a raw IR field name. */
function isKnownMissingField(s: string): s is MissingField {
  return (MISSING_FIELD_KEYS as ReadonlyArray<string>).includes(s)
}

/** Build the "{name} 항목이 비어 있어요" placeholder for a single
 *  missing field. Used by the WHEN / CONDITION rows so the placeholder
 *  NAMES the missing field instead of an empty-state stub. */
function namedMissingPlaceholder(field: MissingField, t: T): string {
  return t("newPolicy.conv.liveDraft.placeholderMissing", {
    name: missingFieldLabel(field, t),
  })
}

/* ── plain-language summary helpers ─────────────────────────────────── */

// D56d (P2 #14): widened to the full 8-event surface the wizard now
// covers. The conversational compiler can emit any of these for an
// IR draft; lifecycleFromDraft mirrors LIFECYCLE_TO_EVENT in
// policies/new/page.tsx so the right-column draft pane renders the
// When summary for every lifecycle the cloud accepts.
type LifecycleKey =
  | "before_tool_use" | "after_tool_use" | "pre_final"
  | "subagent_stop"   | "user_prompt"    | "pre_compact"
  | "session_start"   | "session_end"

function lifecycleFromDraft(d: Record<string, unknown> | null): LifecycleKey | null {
  if (!d || typeof d !== "object") return null
  const trig = d.trigger
  if (!trig || typeof trig !== "object") return null
  const ev = (trig as Record<string, unknown>).event
  if (typeof ev !== "string") return null
  // Mirror policies/new/page.tsx LIFECYCLE_TO_EVENT (inverse).
  if (ev === "PreToolUse") return "before_tool_use"
  if (ev === "PostToolUse") return "after_tool_use"
  if (ev === "Stop") return "pre_final"
  if (ev === "SubagentStop") return "subagent_stop"
  if (ev === "UserPromptSubmit") return "user_prompt"
  if (ev === "PreCompact") return "pre_compact"
  if (ev === "SessionStart") return "session_start"
  if (ev === "SessionEnd") return "session_end"
  return null
}

function matcherFromDraft(d: Record<string, unknown> | null): string | null {
  if (!d || typeof d !== "object") return null
  const trig = d.trigger
  if (!trig || typeof trig !== "object") return null
  const m = (trig as Record<string, unknown>).matcher
  return typeof m === "string" && m.trim() ? m.trim() : null
}

function actionFromDraft(d: Record<string, unknown> | null): ActionArchetype | null {
  if (!d || typeof d !== "object") return null
  // D63 review (P1): the run_command IR uses `type: "run_command"`
  // (sibling-archetype dispatcher convention) rather than `action`. If
  // either field signals run_command, surface it as the action label
  // for the draft pane.
  const t = (d as Record<string, unknown>).type
  if (t === "run_command") return "run_command"
  const a = d.action
  if (
    a === "block" || a === "ask" || a === "audit" || a === "strip"
    || a === "run_command"
  ) {
    return a as ActionArchetype
  }
  return null
}

/** Read a run_command spec field from an IR draft.
 *
 * The persisted shape (`RunCommandDraftPersist` in page.tsx) carries
 * `runtime` / `command` / `script_path` / `args` / `timeout_ms` /
 * `fail_closed` directly on the top-level IR object. NL-compiled IR
 * uses the same shape (we widen the NL compiler prompt below). We
 * read defensively because the draft may be mid-merge.
 */
function readRunCommandField(
  d: Record<string, unknown> | null,
  key: "runtime" | "command" | "script_path",
): string {
  if (!d || typeof d !== "object") return ""
  const v = (d as Record<string, unknown>)[key]
  return typeof v === "string" ? v : ""
}

function readRunCommandArgs(d: Record<string, unknown> | null): string[] {
  if (!d || typeof d !== "object") return []
  const v = (d as Record<string, unknown>).args
  if (!Array.isArray(v)) return []
  return v.filter((x): x is string => typeof x === "string")
}

function readRunCommandTimeoutMs(d: Record<string, unknown> | null): number | null {
  if (!d || typeof d !== "object") return null
  const v = (d as Record<string, unknown>).timeout_ms
  return typeof v === "number" && Number.isFinite(v) ? v : null
}

function readRunCommandFailClosed(d: Record<string, unknown> | null): boolean {
  if (!d || typeof d !== "object") return false
  const v = (d as Record<string, unknown>).fail_closed
  return v === true
}

/**
 * D64: extract a path-like reference from a requires item if present,
 * then resolve it to the friendly display label. Falls back to the raw
 * path when the runtime registry doesn't know it.
 *
 * Some condition kinds (shacl shapes the conversational compiler emits,
 * or a custom evidence shape that points at a known CC stdin field) put
 * the field path on a `path` key inside the requires entry. Surfacing
 * the friendly label there keeps the IR draft pane plain-language even
 * for shapes the operator is iterating on through the conversational
 * compose loop.
 */
function maybeFriendlyPath(
  item: Record<string, unknown>,
  ko: boolean,
): string | null {
  const raw = item.path
  if (typeof raw !== "string" || !raw.trim()) return null
  // The conversational compiler may stash the path as the namespaced
  // SHACL predicate (`magi:tool_input.command`) it just emitted to TTL.
  // The display-label lookup keys on the BARE path, so strip the
  // leading `magi:` here. Display-only; the underlying IR keeps the
  // exact `path` it was given.
  const trimmed = raw.trim()
  const bare = trimmed.startsWith("magi:") ? trimmed.slice("magi:".length) : trimmed
  return getDisplayLabel(bare, ko ? "ko" : "en")
}

function conditionLabel(
  d: Record<string, unknown> | null,
  ko: boolean,
  t: T,
  missingFields: ReadonlySet<MissingField>,
): string {
  // Q102: when the server reports the CONDITION row is what's missing,
  // NAME the missing field instead of the legacy "waiting for ..."
  // placeholder. requires_body is the more specific signal so it takes
  // priority over requires (an item exists but its body is empty).
  if (missingFields.has("requires_body")) {
    return namedMissingPlaceholder("requires_body", t)
  }
  if (missingFields.has("requires")) {
    return namedMissingPlaceholder("requires", t)
  }
  if (!d || typeof d !== "object") {
    return ko ? "(아직 정해지지 않음)" : "(not chosen yet)"
  }
  const reqs = d.requires
  if (!Array.isArray(reqs) || reqs.length === 0) {
    return ko ? "(아직 정해지지 않음)" : "(not chosen yet)"
  }
  const first = reqs[0]
  if (!first || typeof first !== "object") {
    return ko ? "(아직 정해지지 않음)" : "(not chosen yet)"
  }
  const item = first as Record<string, unknown>
  const kind = typeof item.kind === "string"
    ? item.kind
    : "step" in item ? "step" : null
  // Plain-language translation. NEVER expose `regex` / `shacl` /
  // `llm_critic` / `step` (a verifier label is internal vocab too) to
  // the user.
  switch (kind) {
    case "regex": {
      const pat = typeof item.pattern === "string" ? item.pattern : ""
      if (!pat) return namedMissingPlaceholder("requires_body", t)
      return ko ? `응답에서 패턴 발견` : `Pattern in the response`
    }
    case "llm_critic": {
      const c = typeof item.criterion === "string" ? item.criterion : ""
      if (!c) return namedMissingPlaceholder("requires_body", t)
      return ko ? "AI 판단" : "AI judge"
    }
    case "shacl": {
      const ttl = typeof item.shape_ttl === "string" ? item.shape_ttl : ""
      if (!ttl) return namedMissingPlaceholder("requires_body", t)
      // D64: when the shacl entry carries an explicit `path` (the
      // conversational compiler can stash the target path alongside the
      // shape ttl during incremental compose), surface the friendly
      // display label so the operator sees "Bash command" instead of
      // the raw `magi:tool_input.command` predicate.
      const friendlyPath = maybeFriendlyPath(item, ko)
      if (friendlyPath) {
        return ko
          ? `구조 규칙 (${friendlyPath})`
          : `Structured rule (${friendlyPath})`
      }
      return ko ? "구조 규칙" : "Structured rule"
    }
    case "step": {
      const step = typeof item.step === "string" ? item.step : ""
      if (!step) return namedMissingPlaceholder("requires_body", t)
      return ko ? `필수 확인: ${step}` : `Required check: ${step}`
    }
    default:
      return ko ? "(아직 정해지지 않음)" : "(not chosen yet)"
  }
}

/** Pretty-print a raw matcher value for the user. Hides regex /
 *  MCP-slug shape so the When summary stays plain language even when
 *  the underlying matcher is a regex alternation or an mcp__ slug. */
function prettyMatcher(raw: string, ko: boolean): string {
  // Trim wrapping parentheses or whitespace.
  let m = raw.trim()
  if (m.startsWith("(") && m.endsWith(")")) {
    m = m.slice(1, -1).trim()
  }
  // Alternation: `Bash|Edit|Write` -> "Bash or Edit or Write".
  if (m.includes("|")) {
    const parts = m.split("|").map((p) => prettyOneTool(p.trim(), ko)).filter(Boolean)
    if (parts.length === 0) return ko ? "특정 도구들" : "specific tools"
    const sep = ko ? " 또는 " : " or "
    return parts.join(sep)
  }
  return prettyOneTool(m, ko)
}

/** Friendly form of a single matcher token. */
function prettyOneTool(m: string, ko: boolean): string {
  if (!m) return ko ? "특정 도구" : "a specific tool"
  // MCP-shaped slug: `mcp__server__tool` -> `server.tool`.
  if (m.startsWith("mcp__")) {
    const tail = m.slice("mcp__".length)
    const parts = tail.split("__").filter(Boolean)
    if (parts.length >= 2) return parts.join(".")
    if (parts.length === 1) return parts[0]
    return ko ? "특정 도구" : "a specific tool"
  }
  return m
}

function whenLabel(
  d: Record<string, unknown> | null,
  ko: boolean,
  t: T,
  missingFields: ReadonlySet<MissingField>,
): string {
  const life = lifecycleFromDraft(d)
  // Q102: when the server reports the lifecycle (trigger timing) is
  // missing, NAME the missing field instead of the legacy "(not chosen
  // yet)" placeholder.
  if (!life) {
    if (missingFields.has("lifecycle")) {
      return namedMissingPlaceholder("lifecycle", t)
    }
    return ko ? "(아직 정해지지 않음)" : "(not chosen yet)"
  }
  const m = matcherFromDraft(d)
  // D56d (P2 #14): widened lifecycle map mirrors page.tsx
  // LIFECYCLE_LABEL_KO / _EN. CC Stop fires after the agent finishes
  // responding (not "just before the final answer").
  const lifeLabel = ko
    ? ({
        before_tool_use: "도구 실행 전",
        after_tool_use: "도구 실행 후",
        pre_final: "에이전트 응답 직후",
        subagent_stop: "서브에이전트 종료 시점",
        user_prompt: "유저 프롬프트 직전",
        pre_compact: "컨텍스트 컴팩션 직전",
        session_start: "세션 시작 시점",
        session_end: "세션 종료 시점",
      } as const)[life]
    : ({
        before_tool_use: "Before a tool runs",
        after_tool_use: "After a tool runs",
        pre_final: "After the agent finishes responding",
        subagent_stop: "When a subagent stops",
        user_prompt: "Before a user prompt reaches the LLM",
        pre_compact: "Before context compaction",
        session_start: "When the session opens",
        session_end: "When the session closes",
      } as const)[life]
  if (m && m !== "*") {
    const friendly = prettyMatcher(m, ko)
    return ko
      ? `${lifeLabel} (${friendly})`
      : `${lifeLabel} (${friendly})`
  }
  // Q102: lifecycle is set but the server reports the matcher (target
  // tool) is still missing. Append a NAMED placeholder so the operator
  // sees exactly what's blocking save next to the resolved lifecycle.
  if (missingFields.has("matcher")) {
    return `${lifeLabel} (${namedMissingPlaceholder("matcher", t)})`
  }
  return lifeLabel
}

function actionLabel(
  d: Record<string, unknown> | null,
  ko: boolean,
  t: T,
  missingFields: ReadonlySet<MissingField>,
): string {
  const a = actionFromDraft(d)
  if (!a) {
    // Q102: the action archetype itself isn't on the canonical
    // missing-field list, but `on_missing` (the fallback verdict the
    // wizard maps onto the action archetype server-side) is. When the
    // compiler reports on_missing is missing AND we don't have a
    // resolved action archetype to render, NAME the missing field
    // instead of the legacy "(not chosen yet)" placeholder.
    if (missingFields.has("on_missing")) {
      return namedMissingPlaceholder("on_missing", t)
    }
    return ko ? "(아직 정해지지 않음)" : "(not chosen yet)"
  }
  return ko
    ? ({
        block: "차단",
        ask: "사용자 승인 요청",
        audit: "기록만",
        strip: "출력에서 제거",
        run_command: "쉘 명령 실행",
      } as const)[a]
    : ({
        block: "Block the action",
        ask: "Ask a human",
        audit: "Just record",
        strip: "Strip from output",
        run_command: "Run a shell command",
      } as const)[a]
}

/* ── compound (evidence_gate) summary ───────────────────────────────── */

/** True when the draft is a compound evidence_gate (audit + precondition
 *  authored as one policy). Its shape is `gate`/`audit` nested dicts, not
 *  the single-policy `trigger`/`requires`/`action`, so the summary rows
 *  need their own readers. */
function isEvidenceGate(d: Record<string, unknown> | null): boolean {
  return !!d && typeof d === "object" && d.type === "evidence_gate"
}

function evidenceGateTool(d: Record<string, unknown> | null): string {
  if (!d) return ""
  const gate = d.gate
  if (!gate || typeof gate !== "object") return ""
  const m = (gate as Record<string, unknown>).matcher
  return typeof m === "string" ? m.trim() : ""
}

function evidenceGateAction(d: Record<string, unknown> | null): "block" | "ask" {
  if (!d) return "block"
  const gate = d.gate
  const a = gate && typeof gate === "object"
    ? (gate as Record<string, unknown>).action : undefined
  return a === "ask" ? "ask" : "block"
}

function evidenceGateScope(d: Record<string, unknown> | null): string {
  if (!d) return ""
  const s = d.project_scope
  return typeof s === "string" ? s.trim() : ""
}

/* ── component ─────────────────────────────────────────────────────── */

/* ── F1: localize review issue codes + summary ──────────────────────── */
type ReviewIssue = {
  severity: string; code: string; message: string
  params: Record<string, unknown>; source: string
}

/** Localize a deterministic issue by its stable `code`; fall back to the
 *  server's English `message` for the semantic layer (source=semantic, whose
 *  prose the LLM already wrote in the operator's locale) or an unknown code. */
function localizeReviewIssue(iss: ReviewIssue, ko: boolean): string {
  if (iss.source === "semantic") return iss.message
  const a = (iss.params?.action as string) ?? ""
  switch (iss.code) {
    case "no_gate_matcher":
      return ko
        ? "어떤 작업을 막을지 지정하지 않아, 아무것도 차단하지 않습니다."
        : "The policy does not say which action to gate, so it would never block anything."
    case "non_enforcing_action":
      return ko
        ? `동작이 '${a}'라 기록만 하고 막지는 않습니다. 차단하려면 block 또는 ask 를 쓰세요.`
        : `The gate action is '${a}', which records but does not stop the action. Use block or ask to enforce.`
    case "orphan_gate":
      return ko
        ? "이 정책은 기존 출처-검증 producer 를 재사용하는데, 켜져 있는 producer 가 없어 매번 차단됩니다. producer 정책을 켜거나 이 정책이 직접 기록하게 하세요."
        : "This policy reuses an existing credible-source producer, but none is enabled. It would block every time. Enable the producer policy or let this one record its own evidence."
    case "kind_mismatch":
      return ko
        ? "기록하는 evidence 종류와 gate 가 요구하는 종류가 달라, gate 가 기록을 보지 못합니다."
        : "The recorder and the gate use different evidence types, so the gate would never see the recorded evidence."
    case "expand_failed":
      return ko ? "정책을 확장할 수 없습니다." : "The policy could not be expanded."
    case "no_rules":
      return ko ? "정책이 규칙으로 확장되지 않습니다." : "The policy expands to no rules."
    case "invalid_member":
      return ko
        ? `규칙 '${(iss.params?.id as string) ?? ""}'이(가) 유효하지 않습니다.`
        : `Rule '${(iss.params?.id as string) ?? ""}' is invalid.`
    case "single_no_matcher":
      return ko
        ? "도구 시점을 대상으로 하지만 도구를 지정하지 않아, 특정 작업에 매칭되지 않습니다."
        : "This rule targets a tool event but names no tool, so it will not match a specific action."
    case "action_intent_mismatch":
      return ko
        ? "설명은 차단/중지를 요구하는데 이 규칙은 기록(audit)만 합니다. 차단하려면 block 또는 ask 를 쓰세요."
        : "Your description asks to block or stop something, but this rule only records (audit). Use block or ask to enforce."
    default:
      return iss.message
  }
}

function localizeReviewSummary(code: string, ok: boolean, ko: boolean): string {
  if (code === "clean") {
    return ko ? "의도대로 작동합니다. 발견된 문제 없음." : "Implements your intent, no issues found."
  }
  if (code === "notes") {
    return ko ? "괜찮아 보입니다. 저장 전 아래 메모를 확인하세요." : "Looks sound; see the notes below before saving."
  }
  if (code === "gap") {
    return ko
      ? "의도대로 작동하지 않게 만드는 문제가 있습니다."
      : "This policy has a gap that would stop it from working as intended."
  }
  return ok ? "" : ""
}

export function IrDraftPane({
  t, locale, draft, readyToSave, saveAction, missingFields,
  suggestedPackText, packCentric = false, review, reviewPending = false,
  reviewError = false, testId,
}: IrDraftPaneProps) {
  const ko = locale === "ko"
  const action = actionFromDraft(draft)
  const irJson = draft ? JSON.stringify(draft, null, 2) : ""
  const hasDraft = !!draft && Object.keys(draft).length > 0
  // Compound (evidence_gate) drafts carry gate/audit subtrees instead of
  // trigger/requires/action, so they render a dedicated summary below.
  const compound = isEvidenceGate(draft)

  // Q102: normalize the optional missing-field set into a typed Set so
  // the helpers below can do O(1) lookups without re-validating each
  // call. Unknown server-emitted field names (a future field added
  // before the dashboard catches up) are silently dropped so we never
  // surface a raw IR field name.
  const knownMissing: ReadonlyArray<MissingField> = (missingFields ?? [])
    .filter(isKnownMissingField)
  const missingSet: ReadonlySet<MissingField> = new Set(knownMissing)

  return (
    <aside
      data-testid={testId ?? "ir-draft-pane"}
      aria-label={t("newPolicy.conv.draftPane.title")}
      className="rounded-2xl border border-black/[0.08] bg-white p-4 shadow-sm flex flex-col gap-3"
    >
      <header className="flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)] m-0">
          {t("newPolicy.conv.draftPane.title")}
        </h2>
        {/* Q102: status pill is now ALWAYS rendered (amber DRAFTING by
         *  default, emerald READY once the server flips ready_to_save).
         *  The pill drives at-a-glance "is this savable?" feedback so
         *  the operator never wonders whether the panel is alive. */}
        <span
          data-testid="ir-draft-status-pill"
          data-state={readyToSave ? "ready" : "drafting"}
          className={
            readyToSave
              ? "rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-bold uppercase tracking-[0.16em] text-emerald-800"
              : "rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold uppercase tracking-[0.16em] text-amber-900"
          }
        >
          {readyToSave
            ? t("newPolicy.conv.liveDraft.statusReady")
            : t("newPolicy.conv.liveDraft.statusDrafting")}
        </span>
      </header>

      <section
        aria-live="polite"
        data-testid="ir-draft-summary"
        className="rounded-xl border border-black/[0.06] bg-gray-50/60 p-3 text-xs leading-relaxed text-[var(--color-text-secondary)]"
      >
        {!hasDraft && (
          <p data-testid="ir-draft-empty" className="m-0 italic">
            {t("newPolicy.conv.draftPane.emptyHint")}
          </p>
        )}
        {hasDraft && compound && (() => {
          const tool = evidenceGateTool(draft)
          const friendly = tool ? prettyMatcher(tool, ko) : ""
          const gAction = evidenceGateAction(draft)
          const scope = evidenceGateScope(draft)
          return (
            <dl
              className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 m-0"
              data-testid="ir-draft-compound"
            >
              <dt className="text-[10px] uppercase tracking-wider font-semibold text-[var(--color-text-tertiary)]">
                {ko ? "언제" : "When"}
              </dt>
              <dd className="m-0" data-testid="ir-draft-compound-when">
                {friendly
                  ? (ko ? `${friendly} 실행 전` : `Before ${friendly} runs`)
                  : namedMissingPlaceholder("matcher", t)}
              </dd>
              <dt className="text-[10px] uppercase tracking-wider font-semibold text-[var(--color-text-tertiary)]">
                {ko ? "조건" : "Condition"}
              </dt>
              <dd className="m-0" data-testid="ir-draft-compound-condition">
                {ko
                  ? "이번 세션에서 신뢰할 수 있는 출처가 확인됨"
                  : "A credible source was verified earlier this session"}
              </dd>
              <dt className="text-[10px] uppercase tracking-wider font-semibold text-[var(--color-text-tertiary)]">
                {ko ? "동작" : "Action"}
              </dt>
              <dd className="m-0" data-testid="ir-draft-compound-action">
                {gAction === "ask"
                  ? (ko ? "사용자 승인 요청" : "Ask a human")
                  : (ko ? "차단" : "Block the action")}
              </dd>
              {scope && (
                <>
                  <dt className="text-[10px] uppercase tracking-wider font-semibold text-[var(--color-text-tertiary)]">
                    {ko ? "적용 범위" : "Scope"}
                  </dt>
                  <dd
                    className="m-0 font-mono text-[11px]"
                    data-testid="ir-draft-compound-scope"
                  >
                    {scope}
                  </dd>
                </>
              )}
            </dl>
          )
        })()}
        {hasDraft && !compound && (
          <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 m-0">
            <dt className="text-[10px] uppercase tracking-wider font-semibold text-[var(--color-text-tertiary)]">
              {ko ? "언제" : "When"}
            </dt>
            <dd className="m-0" data-testid="ir-draft-when">
              {whenLabel(draft, ko, t, missingSet)}
            </dd>
            <dt className="text-[10px] uppercase tracking-wider font-semibold text-[var(--color-text-tertiary)]">
              {ko ? "조건" : "Condition"}
            </dt>
            <dd className="m-0" data-testid="ir-draft-condition">
              {conditionLabel(draft, ko, t, missingSet)}
            </dd>
            <dt className="text-[10px] uppercase tracking-wider font-semibold text-[var(--color-text-tertiary)]">
              {ko ? "동작" : "Action"}
            </dt>
            <dd className="m-0" data-testid="ir-draft-action">
              <span>{actionLabel(draft, ko, t, missingSet)}</span>
              {action === "run_command" && (
                <span
                  data-testid="ir-draft-action-warning"
                  className="ml-2 inline-flex items-center rounded-md bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-amber-900"
                  title={
                    ko
                      ? "이 정책은 magi-cp 프로세스 권한으로 쉘 명령을 실행합니다."
                      : "This policy runs a shell command as the magi-cp process."
                  }
                >
                  {ko ? "쉘 실행" : "runs shell"}
                </span>
              )}
            </dd>
            {/*
             * D63 review (P1): render run_command specifics so the
             * operator sees what will execute, not a generic
             * placeholder. The pane's "NEVER expose internal terms"
             * rule still holds (we use plain-language `Runs:` etc),
             * but the command body itself IS the operator-authored
             * surface — withholding it would hide the policy's
             * effect.
             */}
            {action === "run_command" && (() => {
              const runtime = readRunCommandField(draft, "runtime") || "bash"
              const command = readRunCommandField(draft, "command")
              const scriptPath = readRunCommandField(draft, "script_path")
              const args = readRunCommandArgs(draft)
              const timeoutMs = readRunCommandTimeoutMs(draft)
              const failClosed = readRunCommandFailClosed(draft)
              return (
                <>
                  <dt className="text-[10px] uppercase tracking-wider font-semibold text-[var(--color-text-tertiary)]">
                    {ko ? "런타임" : "Runtime"}
                  </dt>
                  <dd
                    className="m-0 font-mono text-[11px]"
                    data-testid="ir-draft-run-command-runtime"
                  >
                    {runtime}
                  </dd>
                  <dt className="text-[10px] uppercase tracking-wider font-semibold text-[var(--color-text-tertiary)]">
                    {ko ? "실행 내용" : "Runs"}
                  </dt>
                  <dd
                    className="m-0 font-mono text-[11px] whitespace-pre-wrap break-words"
                    data-testid="ir-draft-run-command-body"
                  >
                    {command && (
                      <code className="block">{command}</code>
                    )}
                    {!command && scriptPath && (
                      <code className="block">
                        {ko ? "스크립트 id: " : "script id: "}
                        {scriptPath}
                      </code>
                    )}
                    {!command && !scriptPath && (
                      <em>{ko ? "(아직 명령 없음)" : "(no command yet)"}</em>
                    )}
                  </dd>
                  {args.length > 0 && (
                    <>
                      <dt className="text-[10px] uppercase tracking-wider font-semibold text-[var(--color-text-tertiary)]">
                        {ko ? "인자" : "Args"}
                      </dt>
                      <dd
                        className="m-0 font-mono text-[11px]"
                        data-testid="ir-draft-run-command-args"
                      >
                        [{args.map((a) => JSON.stringify(a)).join(", ")}]
                      </dd>
                    </>
                  )}
                  {timeoutMs !== null && (
                    <>
                      <dt className="text-[10px] uppercase tracking-wider font-semibold text-[var(--color-text-tertiary)]">
                        {ko ? "타임아웃" : "Timeout"}
                      </dt>
                      <dd
                        className="m-0 font-mono text-[11px]"
                        data-testid="ir-draft-run-command-timeout"
                      >
                        {timeoutMs}ms
                      </dd>
                    </>
                  )}
                  <dt className="text-[10px] uppercase tracking-wider font-semibold text-[var(--color-text-tertiary)]">
                    {ko ? "실패 처리" : "On failure"}
                  </dt>
                  <dd
                    className="m-0 text-[11px]"
                    data-testid="ir-draft-run-command-fail-closed"
                  >
                    {failClosed
                      ? (ko ? "타임아웃/실패 시 deny" : "Deny on timeout or non-zero exit")
                      : (ko ? "타임아웃/실패 시 통과 + ledger 기록" : "Allow on timeout / failure, log to ledger")}
                  </dd>
                </>
              )
            })()}
          </dl>
        )}
      </section>

      {/* Brief: NO raw JSON / IR view on the conversational chat
       *  surface. Banned tokens (lifecycle / matcher / requires.kind /
       *  on_missing / sentinel_re / gate_binary) leak through any
       *  verbatim render. Power users see the IR shape in the Raw /
       *  Advanced mode (PolicyBuilder), not here. */}

      {/*
       * DryRunPanel's ActionArchetype union still covers the legacy 4
       * archetypes (block / ask / audit / strip). For run_command we
       * pass `audit` as a label hint (the dry-run replay shows
       * historical hits, not a forecast of what run_command would
       * decide — the script's stdout JSON is operator-supplied and
       * cannot be replayed deterministically). A future widening of
       * the panel's union can map run_command directly.
       */}
      <DryRunPanel
        locale={locale}
        ir={readyToSave && draft ? draft : null}
        disabled={!readyToSave}
        action={action === "run_command" ? "audit" : (action ?? "audit")}
      />

      {/* Policy-integrity review (advisory). Renders once the draft is
       *  ready: a green confirmation, amber/red findings, or a neutral
       *  "couldn't check" row on failure (F2). Never blocks Save. */}
      {readyToSave && (reviewPending || review || reviewError) && (
        <section
          data-testid="ir-draft-review"
          data-review-ok={review ? String(review.ok) : undefined}
          aria-live="polite"
          className={
            "rounded-xl border p-3 text-xs " + (
              reviewPending || (reviewError && !review)
                ? "border-black/[0.06] bg-gray-50/60 text-[var(--color-text-secondary)]"
                : review && review.ok
                  ? "border-emerald-200 bg-emerald-50/70 text-emerald-900"
                  : "border-amber-300 bg-amber-50/70 text-amber-900"
            )
          }
        >
          {reviewPending && (
            <p className="m-0 italic" data-testid="ir-draft-review-pending">
              {ko ? "정책이 의도대로 작동하는지 확인 중..." : "Checking that this policy does what you asked..."}
            </p>
          )}
          {!reviewPending && !review && reviewError && (
            <p className="m-0" data-testid="ir-draft-review-error">
              {ko ? "확인을 완료하지 못했어요 (저장은 가능합니다)." : "Couldn't finish the check (you can still save)."}
            </p>
          )}
          {!reviewPending && review && (
            <>
              <p className="m-0 font-semibold" data-testid="ir-draft-review-summary">
                {review.ok
                  ? (review.checked.includes("semantic")
                      ? (ko ? "의도대로 작동합니다" : "Implements your intent")
                      : (ko ? "구조 확인 완료" : "Structure checked"))
                  : (ko ? "확인이 필요합니다" : "Needs a look")}
                {": " + localizeReviewSummary(review.summaryCode, review.ok, ko)}
              </p>
              {review.issues.length > 0 && (
                <ul className="mt-1 mb-0 list-disc pl-4" data-testid="ir-draft-review-issues">
                  {review.issues.map((iss, i) => (
                    <li key={i} data-severity={iss.severity} data-code={iss.code}>
                      {localizeReviewIssue(iss, ko)}
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </section>
      )}

      {readyToSave && draft && (
        <form
          action={saveAction}
          className="mt-1 flex flex-col gap-2"
          data-testid="ir-draft-save-form"
        >
          <input type="hidden" name="ir_json" value={irJson} />
          <input type="hidden" name="source" value="org" />
          {/* P4: pack-membership picker — writes the hidden `pack_ids`
           *  field the saveCompiled server action reads. Legacy-guard:
           *  only rendered under the pack-centric runtime; with the flag
           *  off the save flows through the legacy enabled path. */}
          {packCentric && (
            <PackMultiSelect
              locale={locale}
              suggestedPackText={suggestedPackText ?? null}
              labels={{
                heading: t("packs.picker.heading"),
                hint: t("packs.picker.hint"),
                search: t("packs.picker.search"),
                alwaysOn: t("packs.alwaysOn"),
                orphan: t("packs.orphan"),
                loading: t("packs.picker.loading"),
                empty: t("packs.picker.empty"),
                suggested: t("packs.picker.suggested"),
              }}
            />
          )}
          {/* Q102: Save CTA prominence on the ready transition. size=lg
           *  + extra padding/text bumps it above the surrounding chrome,
           *  and `motion-safe:animate-pulse` adds a subtle pulse the
           *  user notices on mount; the global reduced-motion rule in
           *  app/globals.css short-circuits the animation duration to
           *  1ms so users with `prefers-reduced-motion: reduce` get the
           *  static styled CTA. The CTA variant stays "primary", which
           *  already maps to brand purple via --color-accent (#7C3AED).
           */}
          <Button
            type="submit"
            variant="primary"
            size="lg"
            data-testid="ir-draft-save"
            className="text-base px-5 shadow-md motion-safe:animate-pulse"
          >
            {t("newPolicy.conv.saveReady")}
          </Button>
        </form>
      )}

      {/* Q102: quiet "missing fields" footer. Renders only while the
       *  draft is not yet ready_to_save AND the server reported at
       *  least one known missing field. The line names every missing
       *  field in plain language so the operator immediately knows
       *  what to type next. Sub-text styling keeps it unobtrusive
       *  (color-text-tertiary, italic) so it does not compete with
       *  the summary rows above. */}
      {!readyToSave && knownMissing.length > 0 && (
        <p
          data-testid="ir-draft-missing-list"
          className="m-0 text-[11px] italic text-[var(--color-text-tertiary)]"
        >
          {t("newPolicy.conv.liveDraft.missingList", {
            names: knownMissing
              .map((f) => missingFieldLabel(f, t))
              .join(", "),
          })}
        </p>
      )}
    </aside>
  )
}

export default IrDraftPane
