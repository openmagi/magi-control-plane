"""D77 - synthetic CC hook payload simulator.

Given a saved policy and a synthetic CC hook payload, predict the
verdict + action + hookSpecificOutput the runtime would emit at the
gate WITHOUT running CC, spawning a subprocess, or mutating any state.

Why this exists:
  - The dry-run-on-last-24h replay (D53b) needs real ledger history. A
    first-time operator has no history; they need a way to confirm a
    just-enabled policy will fire against the payload they care about
    (e.g. "if I save this PermissionPolicy deny rule, would `rm -rf /`
    actually be blocked?").
  - The runtime gate (gate.py + the compiled managed-settings.json) is
    the source of truth for what CC sees. This module REUSES the same
    matrix coherence + matcher predicates + rewriter applier so the
    answer is structurally identical to what the runtime would emit.
  - Pure function. No subprocess, no fetch, no LLM round-trip. SHACL
    + llm_critic are surfaced as INDETERMINATE rather than silently
    "would fire" / "would not" because we cannot evaluate them
    offline without the configured LLM provider.

Output schema (the cloud route wraps this verbatim):
    {
      verdict: "pass" | "fail" | "deny" | "review" | "skipped" |
               "indeterminate",
      action:  "block" | "ask" | "audit" | "allow" | "rewrite" |
               "inject_context" | "run_command" | "skipped",
      evidence_match_reasons: [str, ...],   # one human-readable line per
                                            # requires entry (kind=step
                                            # passes/fails by exact
                                            # comparison; regex by
                                            # search; llm_critic/shacl
                                            # marked indeterminate).
      hook_specific_output: { ...as the runtime would emit at the gate },
      would_run: { command: str, runtime: str } | None,
                                            # for run_command policies:
                                            # surfaces the command WITHOUT
                                            # executing it.
      new_tool_input: dict | None,          # for input_rewrite policies:
                                            # the new tool_input the
                                            # rewriter would emit.
      skipped_reason: str | None,           # populated when the
                                            # (event, matcher) frame
                                            # does not cover the
                                            # incoming payload.
    }

Skipped reasons:
  - "trigger-mismatch"     payload's hook_event_name or matcher does
                           not fall under the policy's trigger frame.
  - "archetype-no-test"    archetype has no meaningful runtime
                           prediction we can simulate offline (rare;
                           kept for forward compatibility).

This module is pure logic (no FastAPI imports, no DB) so the unit
tests can drive it with literal dicts. The cloud route layer is
responsible for resolving policy_id -> policy instance and shaping
the HTTP envelope.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .ir import (
    AnyPolicy, ContextInjectionPolicy, EvidencePolicy, EvidenceReq,
    InputRewritePolicy, McpGatingPolicy, PermissionPolicy,
    RunCommandPolicy, SubagentPolicy,
)
from .matrix import matcher_covers
from .rewriters import apply_rewriter


# Action surface the simulator emits. We deliberately use a wider set
# than the EvidencePolicy ActionLiteral because the simulator covers
# every archetype (declarative permission rules return "allow"/"deny"
# directly; rewriter returns "rewrite"; etc.) and the dashboard's pill
# row needs the broader vocabulary.
SimAction = str  # any of: block, ask, audit, allow, rewrite,
                  # inject_context, run_command, skipped, indeterminate

# Verdict surface the simulator emits. EvidencePolicy verdicts
# (pass / fail) participate in the gate decision; declarative
# archetypes always return "pass" with their archetype-specific
# action because the decision is structural not evidence-based.
SimVerdict = str  # any of: pass, fail, deny, review, skipped,
                   # indeterminate


@dataclass
class PolicyTestResult:
    verdict: SimVerdict
    action: SimAction
    evidence_match_reasons: list[str]
    hook_specific_output: dict
    # input_rewrite only: the dict the runtime would emit under
    # `hookSpecificOutput.updatedInput`. Surfaced separately so the
    # dashboard can diff it against the original.
    new_tool_input: dict | None = None
    # run_command only: the command the runtime WOULD invoke. We never
    # execute. Both `command` and `runtime` for inline; `script_path`
    # for stored scripts.
    would_run: dict | None = None
    # context_injection only: the additionalContext text the runtime
    # would emit under hookSpecificOutput.additionalContext.
    inject_context: str | None = None
    skipped_reason: str | None = None
    # Per-requires evidence-entry summaries (offline-evaluated). Same
    # shape as evidence_match_reasons but typed so the frontend can
    # color each row. Reserved for future use; the wire body packs
    # them into `evidence_match_reasons` as plain strings today.
    requires_results: list[dict] = field(default_factory=list)


# Trigger-frame events that do NOT carry a `tool_name` in the CC
# payload. Matcher must be wildcard or absent for these events; the
# simulator treats any non-"*" matcher as a coverage hit only when the
# payload omits tool_name (i.e. the operator chose to ignore the
# matcher value on a non-tool-context event).
_TOOL_CONTEXT_EVENTS = frozenset({
    "PreToolUse", "PostToolUse",
    "PostToolUseFailure", "PostToolBatch",
    "PermissionRequest", "PermissionDenied",
})


# Length cap for evidence-snapshot regex projection. Same as the
# runtime's `_payload_text` projection cap in dry_run.py — keeps any
# adversarial fixture from pinning CPU on `re.search` and matches the
# offline-replay character profile.
_REGEX_SNAPSHOT_MAX = 8000


def _payload_event(payload: dict) -> str:
    """Read CC hook_event_name from a synthetic payload.

    CC writes `hook_event_name` on every payload. Our synthetic
    templates may carry it explicitly or implicitly (when the operator
    selected a template, the event is fixed by template selection;
    we still preserve it so authored payloads round-trip).
    """
    name = payload.get("hook_event_name")
    if isinstance(name, str) and name:
        return name
    return ""


def _payload_tool_name(payload: dict) -> str:
    """Read `tool_name` from the payload.

    CC payloads for PreToolUse / PostToolUse / PostToolUseFailure /
    PostToolBatch carry `tool_name` at the top level. Other events
    don't; this returns "" for those (the matcher_covers gate is
    then bypassed and the simulator treats the frame as a coverage
    hit when the matcher is wildcard or absent).
    """
    name = payload.get("tool_name")
    if isinstance(name, str) and name:
        return name
    return ""


def _trigger_covers(
    policy_event: str, policy_matcher: str, payload: dict,
) -> bool:
    """Does the policy's (event, matcher) frame cover the payload?

    - event: must match `payload.hook_event_name` exactly. Empty
      hook_event_name on the payload is treated as a free pass on
      the event check (the operator may have authored a minimal
      payload; the template selection already pinned the event).
    - matcher: only meaningful on tool-context events. We compare via
      `matcher_covers` which is the runtime's source of truth.
    """
    pay_event = _payload_event(payload)
    if pay_event and pay_event != policy_event:
        return False
    if policy_event not in _TOOL_CONTEXT_EVENTS:
        # Non-tool events: matcher is informational only.
        return True
    tool_name = _payload_tool_name(payload)
    if not tool_name:
        # The operator omitted tool_name on a tool-context event.
        # We accept wildcard matchers as a coverage hit (matches every
        # tool); anything else needs the tool_name to compare against.
        return policy_matcher == "*"
    return matcher_covers(policy_matcher, tool_name)


# ── EvidencePolicy evaluator ────────────────────────────────────────


def _project_payload_text(payload: dict) -> str:
    """Flatten a synthetic payload into a single string for
    regex-based requires evaluation.

    Mirrors the runtime `/verify_inline` projection + dry_run.py's
    `_payload_text`: walk the canonical CC fields (`text`, `command`,
    `prompt`, `final_message`, `tool_input.*` string values), fall back
    to a JSON dump for less-common fields. Bounded so an adversarial
    fixture cannot pin CPU.
    """
    parts: list[str] = []
    for k in ("text", "command", "prompt", "final_message"):
        v = payload.get(k)
        if isinstance(v, str):
            parts.append(v)
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        for v in tool_input.values():
            if isinstance(v, str):
                parts.append(v)
    tool_response = payload.get("tool_response")
    if isinstance(tool_response, dict):
        for v in tool_response.values():
            if isinstance(v, str):
                parts.append(v)
    if parts:
        joined = "\n".join(parts)
        return joined[:_REGEX_SNAPSHOT_MAX]
    try:
        return json.dumps(payload, ensure_ascii=False)[:_REGEX_SNAPSHOT_MAX]
    except (TypeError, ValueError):
        return ""


def _resolve_field_path(payload: dict, path: str) -> str:
    """Walk a dotted path on a payload and return the string at the
    leaf. Empty / non-string / missing leaf → "".
    """
    if not path:
        return ""
    cur: Any = payload
    for seg in path.split("."):
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(seg)
    if isinstance(cur, str):
        return cur
    return ""


def _evaluate_requires(
    req: EvidenceReq, payload: dict,
) -> tuple[str, str]:
    """Tri-state evaluation of one requires[] entry.

    Returns (status, reason) where status is one of:
      - "pass"          : entry passes against the payload
      - "fail"          : entry fails (the policy gate would fire)
      - "indeterminate" : cannot evaluate offline (llm_critic / shacl
                          / regex without a snapshot)
    `reason` is a human-readable explanation for the dashboard pill.
    """
    if req.kind == "step":
        # The runtime `/verify_inline` writes {step, verdict} into the
        # ledger body. For the simulator we honour an optional override
        # on the synthetic payload: `evidence: {<step>: <verdict>}`.
        # Without that hint we cannot know the verdict offline — mark
        # indeterminate so the operator sees the gap rather than a
        # false-positive pass.
        evidence = payload.get("evidence")
        if isinstance(evidence, dict):
            actual = evidence.get(req.step)
            if isinstance(actual, str):
                if actual == req.verdict:
                    return (
                        "pass",
                        f"step '{req.step}' produced verdict "
                        f"'{actual}' (matches required '{req.verdict}')",
                    )
                return (
                    "fail",
                    f"step '{req.step}' produced verdict "
                    f"'{actual}' (expected '{req.verdict}')",
                )
        return (
            "indeterminate",
            f"step '{req.step}' verdict not known offline (provide "
            f"`evidence.{req.step}` on the test payload to simulate)",
        )
    if req.kind == "regex":
        if not req.pattern:
            return ("indeterminate", "regex pattern empty")
        try:
            compiled = re.compile(req.pattern)
        except re.error as e:
            return ("indeterminate", f"regex pattern fails to compile: {e}")
        # field_path scoping: pull the named field if present, else
        # project the whole payload.
        if req.field_path:
            text = _resolve_field_path(payload, req.field_path)
        else:
            text = _project_payload_text(payload)
        if not text:
            return (
                "indeterminate",
                "regex target text empty (no projectable string fields)",
            )
        try:
            hit = compiled.search(text)
        except re.error as e:
            return ("indeterminate", f"regex search failed: {e}")
        if hit is not None:
            return (
                "pass",
                f"regex matched at offset {hit.start()}"
                + (f" on field '{req.field_path}'" if req.field_path else ""),
            )
        return (
            "fail",
            "regex did not match"
            + (f" on field '{req.field_path}'" if req.field_path else ""),
        )
    if req.kind == "llm_critic":
        return (
            "indeterminate",
            "llm_critic cannot be evaluated offline (no LLM round-trip)",
        )
    if req.kind == "shacl":
        return (
            "indeterminate",
            "shacl cannot be evaluated offline (no pyshacl validation)",
        )
    return ("indeterminate", f"unknown evidence kind: {req.kind!r}")


def _evidence_policy_test(
    policy: EvidencePolicy, payload: dict,
) -> PolicyTestResult:
    reasons: list[str] = []
    requires_results: list[dict] = []
    any_fail = False
    any_indet = False
    for req in policy.requires:
        status, reason = _evaluate_requires(req, payload)
        reasons.append(f"[{status}] {reason}")
        requires_results.append({
            "kind": req.kind, "status": status, "reason": reason,
        })
        if status == "fail":
            any_fail = True
        elif status == "indeterminate":
            any_indet = True
    # Empty requires[] = unconditional fire (audit-emit archetype).
    if not policy.requires:
        any_fail = True
        reasons.append("[pass] no requires (unconditional signal)")
        requires_results.append({
            "kind": "unconditional", "status": "fail",
            "reason": "no requires (unconditional signal)",
        })

    # Decision combine:
    #   any fail → action fires (block/ask/audit per policy.action)
    #   else any indeterminate → simulator cannot honestly decide
    #   else → policy would allow (no action emitted)
    event = policy.trigger.event
    if any_fail:
        if policy.action == "block":
            hso = {
                "hookSpecificOutput": {
                    "hookEventName": event,
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"MAGI: policy '{policy.id}' requires not satisfied"
                    ),
                },
            }
            return PolicyTestResult(
                verdict="deny",
                action="block",
                evidence_match_reasons=reasons,
                hook_specific_output=hso,
                requires_results=requires_results,
            )
        if policy.action == "ask":
            hso = {
                "hookSpecificOutput": {
                    "hookEventName": event,
                    "permissionDecision": "ask",
                    "permissionDecisionReason": (
                        f"MAGI: policy '{policy.id}' awaits HITL approval"
                    ),
                },
            }
            return PolicyTestResult(
                verdict="review",
                action="ask",
                evidence_match_reasons=reasons,
                hook_specific_output=hso,
                requires_results=requires_results,
            )
        # audit: silent ledger entry, no permission decision.
        return PolicyTestResult(
            verdict="fail",
            action="audit",
            evidence_match_reasons=reasons,
            hook_specific_output={},
            requires_results=requires_results,
        )
    if any_indet:
        return PolicyTestResult(
            verdict="indeterminate",
            action="indeterminate",
            evidence_match_reasons=reasons,
            hook_specific_output={},
            requires_results=requires_results,
        )
    return PolicyTestResult(
        verdict="pass",
        action="allow",
        evidence_match_reasons=reasons,
        hook_specific_output={
            "hookSpecificOutput": {
                "hookEventName": event,
                "permissionDecision": "allow",
            },
        },
        requires_results=requires_results,
    )


# ── declarative archetype evaluators ────────────────────────────────


def _permission_policy_test(
    policy: PermissionPolicy, payload: dict,
) -> PolicyTestResult:
    """PermissionPolicy compiles into managed-settings; CC handles the
    decision before the gate fires. The simulator surfaces what CC
    would do based on the operator-authored pattern + permission verb.

    We do NOT pattern-match the CC permission grammar at the level of
    `Bash(rm -rf /*)` (the grammar is CC-internal). Instead we report
    the verb directly and let the dashboard render "would <verb> with
    pattern <pattern>". The dashboard's "Test this policy" panel is
    designed as an authoring aid for the operator who already knows
    what their pattern means; deep semantic prediction would require
    re-implementing the CC permission engine.
    """
    event = policy.trigger.event
    reason = (
        f"PermissionPolicy '{policy.id}': CC would {policy.permission!r} "
        f"matching tool calls (pattern {policy.pattern!r})"
    )
    if policy.permission == "deny":
        return PolicyTestResult(
            verdict="deny",
            action="block",
            evidence_match_reasons=[reason],
            hook_specific_output={
                "hookSpecificOutput": {
                    "hookEventName": event,
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"MAGI: matched permission pattern {policy.pattern!r}"
                    ),
                },
            },
        )
    if policy.permission == "ask":
        return PolicyTestResult(
            verdict="review",
            action="ask",
            evidence_match_reasons=[reason],
            hook_specific_output={
                "hookSpecificOutput": {
                    "hookEventName": event,
                    "permissionDecision": "ask",
                    "permissionDecisionReason": (
                        f"MAGI: matched permission pattern {policy.pattern!r}"
                    ),
                },
            },
        )
    # allow
    return PolicyTestResult(
        verdict="pass",
        action="allow",
        evidence_match_reasons=[reason],
        hook_specific_output={
            "hookSpecificOutput": {
                "hookEventName": event,
                "permissionDecision": "allow",
            },
        },
    )


def _subagent_policy_test(
    policy: SubagentPolicy, payload: dict,
) -> PolicyTestResult:
    """SubagentPolicy compiles to `permissions.deny: ["Agent(<name>)"]`.

    The simulator predicts deny when the payload's tool_name is Agent
    and `tool_input.subagent_type` matches; otherwise allow.
    """
    tool_name = _payload_tool_name(payload)
    sub_type = ""
    ti = payload.get("tool_input")
    if isinstance(ti, dict):
        st = ti.get("subagent_type")
        if isinstance(st, str):
            sub_type = st
    if tool_name == "Agent" and sub_type == policy.subagent_type:
        reason = (
            f"SubagentPolicy '{policy.id}': CC would deny subagent "
            f"'{policy.subagent_type}' (fleet-wide disable)"
        )
        return PolicyTestResult(
            verdict="deny",
            action="block",
            evidence_match_reasons=[reason],
            hook_specific_output={
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"MAGI: subagent '{policy.subagent_type}' is disabled"
                    ),
                },
            },
        )
    return PolicyTestResult(
        verdict="pass",
        action="allow",
        evidence_match_reasons=[
            f"SubagentPolicy '{policy.id}': only fires when "
            f"tool_name=='Agent' and "
            f"tool_input.subagent_type=='{policy.subagent_type}'",
        ],
        hook_specific_output={},
    )


def _mcp_gating_policy_test(
    policy: McpGatingPolicy, payload: dict,
) -> PolicyTestResult:
    """McpGatingPolicy compiles to top-level allowedMcpServers /
    deniedMcpServers arrays. The simulator predicts the verdict by
    inspecting the payload's tool_name prefix `mcp__<server>__<tool>`.
    """
    tool_name = _payload_tool_name(payload)
    if tool_name.startswith(f"mcp__{policy.server}__"):
        if policy.action == "deny":
            reason = (
                f"McpGatingPolicy '{policy.id}': CC would deny MCP server "
                f"'{policy.server}' (tool_name prefix matched)"
            )
            return PolicyTestResult(
                verdict="deny",
                action="block",
                evidence_match_reasons=[reason],
                hook_specific_output={
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"MAGI: MCP server '{policy.server}' is denied"
                        ),
                    },
                },
            )
        # allow
        return PolicyTestResult(
            verdict="pass",
            action="allow",
            evidence_match_reasons=[
                f"McpGatingPolicy '{policy.id}': CC would allow MCP server "
                f"'{policy.server}'",
            ],
            hook_specific_output={
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                },
            },
        )
    return PolicyTestResult(
        verdict="pass",
        action="allow",
        evidence_match_reasons=[
            f"McpGatingPolicy '{policy.id}': tool_name does not start with "
            f"'mcp__{policy.server}__'; policy does not fire",
        ],
        hook_specific_output={},
    )


def _context_injection_policy_test(
    policy: ContextInjectionPolicy, payload: dict,
) -> PolicyTestResult:
    """ContextInjectionPolicy compiles to a `command` hook that emits
    `hookSpecificOutput.additionalContext`. The simulator surfaces the
    static template the policy would inject; we do NOT render any
    runtime templating (the template field is a constant string the
    operator authored).
    """
    return PolicyTestResult(
        verdict="pass",
        action="inject_context",
        evidence_match_reasons=[
            f"ContextInjectionPolicy '{policy.id}': would inject "
            f"{len(policy.template)} chars of additionalContext on "
            f"event {policy.event!r}",
        ],
        hook_specific_output={
            "hookSpecificOutput": {
                "hookEventName": policy.event,
                "additionalContext": policy.template,
            },
        },
        inject_context=policy.template,
    )


def _input_rewrite_policy_test(
    policy: InputRewritePolicy, payload: dict,
) -> PolicyTestResult:
    """InputRewritePolicy mutates tool_input BEFORE the tool runs. The
    simulator runs the SAME rewriter the cloud applies at runtime
    (`apply_rewriter`), returns the new tool_input shape, and packs
    it under `hookSpecificOutput.updatedInput` exactly like the gate.
    """
    ti = payload.get("tool_input")
    if not isinstance(ti, dict):
        return PolicyTestResult(
            verdict="pass",
            action="allow",
            evidence_match_reasons=[
                f"InputRewritePolicy '{policy.id}': payload lacks a "
                f"`tool_input` dict; rewriter would no-op",
            ],
            hook_specific_output={},
        )
    new_ti = apply_rewriter(policy.rewriter, ti)
    if new_ti == ti:
        return PolicyTestResult(
            verdict="pass",
            action="allow",
            evidence_match_reasons=[
                f"InputRewritePolicy '{policy.id}': rewriter would no-op on "
                f"this tool_input (field absent / pattern miss / "
                f"post-rewrite identical to pre)",
            ],
            hook_specific_output={},
        )
    return PolicyTestResult(
        verdict="pass",
        action="rewrite",
        evidence_match_reasons=[
            f"InputRewritePolicy '{policy.id}': rewriter would emit a new "
            f"tool_input dict via updatedInput",
        ],
        hook_specific_output={
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "updatedInput": new_ti,
            },
        },
        new_tool_input=new_ti,
    )


def _run_command_policy_test(
    policy: RunCommandPolicy, payload: dict,
) -> PolicyTestResult:
    """RunCommandPolicy WOULD run an inline command / attached script
    and use its stdout as the hook output. The simulator NEVER spawns
    a subprocess; it reports the command + runtime + args + script_path
    so the operator sees exactly what the runtime would invoke.

    Security: this is the most sensitive archetype to simulate; we
    deliberately surface the literal command string the runtime would
    use. The cloud route layer is admin-key gated so a non-admin
    cannot read another tenant's command bodies via this surface.
    """
    parts: list[str] = [
        f"RunCommandPolicy '{policy.id}': would invoke runtime "
        f"'{policy.runtime}'",
    ]
    would_run: dict = {
        "runtime": policy.runtime,
        "command": policy.command,
        "script_path": policy.script_path,
        "args": list(policy.args),
        "timeout_ms": policy.timeout_ms,
        "fail_closed": policy.fail_closed,
    }
    if policy.command:
        parts.append(f"inline command: {policy.command!r}")
    if policy.script_path:
        parts.append(f"script id: {policy.script_path!r}")
    if policy.args:
        parts.append(f"args: {policy.args!r}")
    parts.append(
        "simulator does NOT execute the command (use the real runtime "
        "to observe stdout)"
    )
    return PolicyTestResult(
        verdict="pass",
        action="run_command",
        evidence_match_reasons=parts,
        hook_specific_output={},
        would_run=would_run,
    )


# ── public entrypoint ───────────────────────────────────────────────


def test_policy(policy: AnyPolicy, payload: dict, event: str = "") -> PolicyTestResult:
    """Pure-function policy test: predict what the runtime would emit
    against `payload` for `policy`.

    `payload` is the JSON-serialisable CC hook payload the operator
    authored (the synthetic templates produce these). `event` is the
    CC hook event name the operator picked; when empty we read it from
    `payload.hook_event_name`. The two paths converge: the cloud route
    accepts both and normalises here.

    Returns a `PolicyTestResult` the cloud route serialises into JSON.
    """
    # Normalise event: if the caller passed one, mirror it onto the
    # payload so the trigger-frame check + per-archetype evaluators
    # see a consistent shape.
    pay_event = _payload_event(payload)
    eff_event = event or pay_event
    if eff_event and not pay_event:
        payload = dict(payload)
        payload["hook_event_name"] = eff_event

    # Resolve the policy's trigger frame. Declarative archetypes that
    # don't carry a `trigger` (ContextInjectionPolicy, McpGatingPolicy,
    # SubagentPolicy) skip the frame check — they fire on their own
    # archetype-specific predicate.
    trig = getattr(policy, "trigger", None)
    if trig is not None and eff_event:
        if not _trigger_covers(trig.event, trig.matcher, payload):
            return PolicyTestResult(
                verdict="skipped",
                action="skipped",
                evidence_match_reasons=[
                    f"trigger frame does not cover this payload: policy fires "
                    f"on event={trig.event!r} matcher={trig.matcher!r}; "
                    f"payload event={eff_event!r} "
                    f"tool_name={_payload_tool_name(payload)!r}"
                ],
                hook_specific_output={},
                skipped_reason="trigger-mismatch",
            )

    if isinstance(policy, EvidencePolicy):
        return _evidence_policy_test(policy, payload)
    if isinstance(policy, PermissionPolicy):
        return _permission_policy_test(policy, payload)
    if isinstance(policy, SubagentPolicy):
        return _subagent_policy_test(policy, payload)
    if isinstance(policy, McpGatingPolicy):
        return _mcp_gating_policy_test(policy, payload)
    if isinstance(policy, ContextInjectionPolicy):
        return _context_injection_policy_test(policy, payload)
    if isinstance(policy, InputRewritePolicy):
        return _input_rewrite_policy_test(policy, payload)
    if isinstance(policy, RunCommandPolicy):
        return _run_command_policy_test(policy, payload)

    return PolicyTestResult(
        verdict="skipped",
        action="skipped",
        evidence_match_reasons=[
            f"policy archetype {type(policy).__name__} has no offline "
            f"simulator yet",
        ],
        hook_specific_output={},
        skipped_reason="archetype-no-test",
    )


def result_to_dict(r: PolicyTestResult) -> dict:
    """Serialize a PolicyTestResult into the cloud route's response
    envelope. Pure utility so the route layer stays a thin wrapper."""
    out: dict = {
        "verdict": r.verdict,
        "action": r.action,
        "evidence_match_reasons": list(r.evidence_match_reasons),
        "hook_specific_output": r.hook_specific_output,
        "requires_results": list(r.requires_results),
    }
    if r.new_tool_input is not None:
        out["new_tool_input"] = r.new_tool_input
    if r.would_run is not None:
        out["would_run"] = r.would_run
    if r.inject_context is not None:
        out["inject_context"] = r.inject_context
    if r.skipped_reason is not None:
        out["skipped_reason"] = r.skipped_reason
    return out


__all__ = [
    "PolicyTestResult",
    "SimAction",
    "SimVerdict",
    "result_to_dict",
    "test_policy",
]
