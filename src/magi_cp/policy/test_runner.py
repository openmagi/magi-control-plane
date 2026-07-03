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
    shared modules every other surface reuses:
      - regex projection → `payload_projection` (shared with
        /verify_inline + dry_run).
      - CC hook stdout JSON → `cc_shapes` (shared with gate.py's
        runtime emitter).
      - matcher coverage → `matrix.matcher_covers`.
    Drift fires loudly via the contract tests in
    `tests/test_policy_payload_projection.py` +
    `tests/test_policy_cc_shapes.py`.
  - Pure function. No subprocess, no fetch, no LLM round-trip.

Declarative-archetype honesty (P2 cleanup):
  PermissionPolicy / SubagentPolicy / McpGatingPolicy compile to
  managed-settings the CC engine resolves internally; we cannot
  authoritatively predict the verdict offline (the grammar lives in
  CC, and Agent invocations / MCP-server gating do not always surface
  via the hook payload at all). We mirror `dry_run.py`'s
  "archetype-not-dry-runnable" posture: the simulator returns
  INDETERMINATE with a per-archetype explanation rather than a
  fabricated verdict pill. Operators read the explanation and know
  "CC owns this decision; the rule is in your settings.json".

Multi-requires honesty:
  When `len(policy.requires) > 1`, the simulator pins the headline
  verdict to INDETERMINATE (mirroring `dry_run.py`'s
  `multi-requires-not-replayable` skip). The runtime fires
  `gate_binary` once per (subject, payload_hash) and combines N
  verdicts inside the shell script; the simulator cannot reconstruct
  that join honestly. We DO keep the per-requires breakdown in
  `requires_results` so the operator still sees which entry would
  have failed individually.

Trigger-fail-closed (P2 #6 fix):
  When the request has no `event` body field AND the payload also
  lacks `hook_event_name`, the trigger-frame check would otherwise
  be bypassed and the per-archetype evaluator would run
  unconditionally. We now return SKIPPED with `no-event-supplied` so
  the operator sees the gap.

Output schema (the cloud route wraps this verbatim):
    {
      verdict: "pass" | "fail" | "deny" | "review" | "skipped" |
               "indeterminate",
      action:  "block" | "ask" | "audit" | "allow" | "rewrite" |
               "inject_context" | "run_command" | "skipped" |
               "indeterminate",
      evidence_match_reasons: [str, ...],   # one human-readable line per
                                            # requires entry.
      hook_specific_output: { ...as the runtime would emit at the gate },
      would_run: { command: str, runtime: str } | None,
      new_tool_input: dict | None,
      inject_context: str | None,
      skipped_reason: str | None,           # populated when the
                                            # frame/payload combination
                                            # does not produce an
                                            # honest verdict.
      requires_results: [{kind, status, reason}, ...]
    }

Skipped reasons:
  - "trigger-mismatch"                  payload's hook_event_name or
                                        matcher does not fall under
                                        the policy's trigger frame.
  - "no-event-supplied"                 neither the request nor the
                                        payload carry a hook event
                                        name; honest evaluation needs
                                        one.
  - "payload-missing-tool-name"         the policy targets a tool-
                                        context event but the payload
                                        omitted tool_name.
  - "multi-requires-not-replayable"     policy.requires has >1 entry;
                                        the runtime AND-combines via
                                        gate_binary which we can't
                                        reconstruct.
  - "declarative-archetype-cc-owned"    PermissionPolicy /
                                        SubagentPolicy / McpGatingPolicy:
                                        CC's permission engine owns
                                        the decision; we cannot
                                        honestly replay it offline.
  - "archetype-no-test"                 archetype has no offline
                                        prediction we can simulate
                                        (rare; forward compat).

This module is pure logic (no FastAPI imports, no DB) so the unit
tests can drive it with literal dicts. The cloud route layer is
responsible for resolving policy_id -> policy instance and shaping
the HTTP envelope.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .cc_shapes import (
    RETRY_FEEDBACK_EVENTS,
    emit_allow_payload,
    emit_ask_payload,
    emit_deny_payload,
)
from .ir import (
    AnyPolicy, ContextInjectionPolicy, EvidencePolicy, EvidenceReq,
    InputRewritePolicy, McpGatingPolicy, PermissionPolicy,
    RunCommandPolicy, SubagentPolicy,
)
from .matrix import matcher_covers
from .payload_projection import (
    FIELD_MISSING,
    project_payload_for_regex,
    resolve_field_for_regex,
)
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


# Trigger-frame events that DO carry a `tool_name` in the CC payload.
# When the policy targets one of these events, the simulator must see
# tool_name on the payload to honestly evaluate the matcher; missing
# tool_name on this event family returns SKIPPED instead of silently
# admitting wildcard matchers as a hit (the runtime CC always supplies
# tool_name on these events).
_TOOL_CONTEXT_EVENTS = frozenset({
    "PreToolUse", "PostToolUse",
    "PostToolUseFailure", "PostToolBatch",
    "PermissionRequest", "PermissionDenied",
})


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
) -> tuple[bool, str]:
    """Does the policy's (event, matcher) frame cover the payload?

    Returns (covered, skip_reason). `skip_reason` is informational —
    callers surface it as the verdict's `skipped_reason` when
    `covered` is False.

    - event: must match `payload.hook_event_name` exactly. Empty
      hook_event_name on the payload is treated as a free pass on
      the event check (the operator may have authored a minimal
      payload; the template selection already pinned the event).
    - matcher: only meaningful on tool-context events. We compare via
      `matcher_covers` which is the runtime's source of truth.
      Missing tool_name on a tool-context event → skipped with
      `payload-missing-tool-name` (P2 #9 honesty fix; CC always
      populates tool_name on this event family).
    """
    pay_event = _payload_event(payload)
    if pay_event and pay_event != policy_event:
        return (False, "trigger-mismatch")
    if policy_event not in _TOOL_CONTEXT_EVENTS:
        # Non-tool events: matcher is informational only.
        return (True, "")
    tool_name = _payload_tool_name(payload)
    if not tool_name:
        # The operator omitted tool_name on a tool-context event. CC
        # ALWAYS populates tool_name on these events at runtime, so we
        # cannot honestly evaluate the matcher offline. Surface the
        # gap rather than silently admit wildcard as a hit.
        return (False, "payload-missing-tool-name")
    if matcher_covers(policy_matcher, tool_name):
        return (True, "")
    return (False, "trigger-mismatch")


# ── EvidencePolicy evaluator ────────────────────────────────────────


def _resolve_field_path(payload: dict, path: str) -> str | object:
    """Walk a dotted path on a payload, returning the formatted leaf.

    Delegates to the shared `resolve_field_for_regex` so the simulator,
    /verify_inline, and dry_run all resolve and format the same way.
    Returns either a string (resolved + formatted leaf) or the
    `FIELD_MISSING` sentinel (caller distinguishes "field absent" from
    "field present, empty").
    """
    return resolve_field_for_regex(payload, path)


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
        # field_path scoping: resolve via the shared helper so the
        # simulator, /verify_inline, and dry_run see byte-equal
        # projections.
        if req.field_path:
            resolved = _resolve_field_path(payload, req.field_path)
            if resolved is FIELD_MISSING:
                # Mirror /verify_inline runtime: field absent → deny
                # the requires entry with a clear reason. The
                # EvidencePolicy combine semantics interpret
                # status='fail' as "policy fires" so the operator
                # sees the same outcome the runtime gate would emit.
                return (
                    "fail",
                    f"regex did not match: field {req.field_path!r} "
                    f"absent from payload",
                )
            assert isinstance(resolved, str)
            text = resolved
        else:
            text = project_payload_for_regex(payload)
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


# ── EvidencePolicy decision combine ─────────────────────────────────


def _build_first_failing_reason(
    requires_results: list[dict], policy_id: str,
) -> str:
    """Compose a `permissionDecisionReason`-style string from the
    first failing requires entry. Mirrors the runtime gate's deny
    reason format which echoes the verifier's literal reason (e.g.
    "MAGI: pattern matched: rm -rf"), not a policy-id boilerplate.
    """
    for rr in requires_results:
        if rr.get("status") == "fail":
            reason = rr.get("reason") or "requires failed"
            return f"{reason} (policy {policy_id!r})"
    return f"policy {policy_id!r} requires not satisfied"


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

    # Multi-requires honesty (mirrors dry_run.py:228-237). The runtime
    # fires gate_binary once per (subject, payload_hash) and combines
    # N verdicts inside the shell script; the simulator cannot
    # reconstruct that join honestly. We still keep the per-requires
    # breakdown in `requires_results` so the operator sees which entry
    # would have failed individually, but pin the headline verdict to
    # indeterminate.
    if len(policy.requires) > 1:
        return PolicyTestResult(
            verdict="indeterminate",
            action="indeterminate",
            evidence_match_reasons=reasons,
            hook_specific_output={},
            requires_results=requires_results,
            skipped_reason="multi-requires-not-replayable",
        )

    event = policy.trigger.event
    if any_fail:
        first_reason = _build_first_failing_reason(
            requires_results, policy.id,
        )
        if policy.action == "block":
            return PolicyTestResult(
                verdict="deny",
                action="block",
                evidence_match_reasons=reasons,
                hook_specific_output=emit_deny_payload(
                    first_reason, hook_event_name=event,
                ),
                requires_results=requires_results,
            )
        if policy.action == "ask":
            return PolicyTestResult(
                verdict="review",
                action="ask",
                evidence_match_reasons=reasons,
                hook_specific_output=emit_ask_payload(
                    first_reason, hook_event_name=event,
                ),
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
    # Pre-side allow shape only on the permission-lane channel; the
    # PostToolUse* channel doesn't carry an explicit allow at runtime
    # (silent gate-exit means CC continues normal flow), so we emit
    # nothing and let the action='allow' pill carry the meaning.
    if event in RETRY_FEEDBACK_EVENTS:
        hso: dict = {}
    else:
        hso = emit_allow_payload(hook_event_name=event)
    return PolicyTestResult(
        verdict="pass",
        action="allow",
        evidence_match_reasons=reasons,
        hook_specific_output=hso,
        requires_results=requires_results,
    )


# ── declarative archetype evaluators ────────────────────────────────


def _declarative_indeterminate(
    *,
    policy_id: str,
    archetype: str,
    explanation: str,
    requires_results: list[dict] | None = None,
) -> PolicyTestResult:
    """Shared honesty-posture result for declarative archetypes.

    PermissionPolicy / SubagentPolicy / McpGatingPolicy compile to
    managed-settings the CC engine resolves internally. The simulator
    cannot authoritatively predict the verdict offline because:

      - PermissionPolicy: CC owns the permission grammar
        (`Bash(rm -rf /*)` etc.); we would have to re-implement the
        engine to predict the decision.
      - SubagentPolicy: Agent invocations may not surface as
        `tool_name='Agent'` at all (CC routes some agent kinds
        through different hook events).
      - McpGatingPolicy: MCP server gating happens BEFORE the
        tool_name string lands in the hook payload, so the payload
        we see at hook time is not the right artifact to inspect.

    Returning INDETERMINATE with a per-archetype explanation matches
    the honesty posture of dry_run.py's `archetype-not-dry-runnable`
    skip; the dashboard renders "CC owns this decision" instead of a
    fabricated verdict pill.
    """
    return PolicyTestResult(
        verdict="indeterminate",
        action="indeterminate",
        evidence_match_reasons=[
            f"{archetype} '{policy_id}': {explanation}",
            "estimated: CC's permission engine owns this decision — "
            "the simulator cannot authoritatively replay declarative "
            "archetypes offline. Run the policy live to observe CC's "
            "actual verdict.",
        ],
        hook_specific_output={},
        requires_results=requires_results or [],
        skipped_reason="declarative-archetype-cc-owned",
    )


def _permission_policy_test(
    policy: PermissionPolicy, payload: dict,
) -> PolicyTestResult:
    """PermissionPolicy compiles to managed-settings
    `permissions.{allow,deny,ask}`. CC's permission engine matches the
    pattern against tool calls using its internal grammar BEFORE the
    gate fires; we cannot re-implement that grammar offline without
    drift risk. Return INDETERMINATE with an explanation.
    """
    return _declarative_indeterminate(
        policy_id=policy.id,
        archetype="PermissionPolicy",
        explanation=(
            f"would compile to permissions.{policy.permission} = "
            f"[{policy.pattern!r}] in managed-settings; CC matches "
            "the pattern against incoming tool calls via its internal "
            "permission grammar"
        ),
    )


def _subagent_policy_test(
    policy: SubagentPolicy, payload: dict,
) -> PolicyTestResult:
    """SubagentPolicy compiles to
    `permissions.deny: ["Agent(<name>)"]`. CC owns Agent dispatch and
    may surface subagent invocations through hook events other than
    PreToolUse / tool_name='Agent'. Return INDETERMINATE.
    """
    return _declarative_indeterminate(
        policy_id=policy.id,
        archetype="SubagentPolicy",
        explanation=(
            f"would deny subagent '{policy.subagent_type}' fleet-wide "
            "via managed-settings; CC's Agent dispatch may surface "
            "subagent calls through hook events the simulator cannot "
            "model offline"
        ),
    )


def _mcp_gating_policy_test(
    policy: McpGatingPolicy, payload: dict,
) -> PolicyTestResult:
    """McpGatingPolicy compiles to top-level allowedMcpServers /
    deniedMcpServers arrays. CC enforces these BEFORE the MCP tool
    name reaches the hook payload, so the hook payload is not the
    right artifact to inspect.
    """
    return _declarative_indeterminate(
        policy_id=policy.id,
        archetype="McpGatingPolicy",
        explanation=(
            f"would {policy.action} MCP server '{policy.server}' via "
            "top-level allowedMcpServers/deniedMcpServers; CC enforces "
            "these BEFORE the tool_name reaches the hook payload"
        ),
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
    CC hook event name the operator picked; when empty we read it
    from `payload.hook_event_name`. The two paths converge here:

      - if the caller passes `event` AND the payload omits
        hook_event_name, we mirror it onto the payload so per-
        archetype evaluators see a consistent shape.
      - if the payload carries hook_event_name we PREFER it over the
        caller-supplied event (P2 #5 fix: dashboard sends the
        template's default event, the operator may have edited the
        JSON to a different event — the JSON wins).
      - if BOTH are empty AND the policy has a trigger frame we
        return SKIPPED with `no-event-supplied` (P2 #6 fix: the
        runtime would NEVER reach this policy without an event, so
        emitting a fabricated verdict would lie).

    Returns a `PolicyTestResult` the cloud route serialises into JSON.
    """
    # P2 #5: payload's hook_event_name wins over the caller's `event`
    # so an operator who hand-edits the JSON sees their edit honoured.
    pay_event = _payload_event(payload)
    if pay_event:
        eff_event = pay_event
    else:
        eff_event = event
        if eff_event:
            payload = dict(payload)
            payload["hook_event_name"] = eff_event

    # Resolve the policy's trigger frame. Declarative archetypes that
    # don't carry a `trigger` (ContextInjectionPolicy, McpGatingPolicy,
    # SubagentPolicy) skip the frame check — they fire on their own
    # archetype-specific predicate.
    trig = getattr(policy, "trigger", None)
    if trig is not None:
        # P2 #6 fix: fail closed when no event was supplied at all.
        # The runtime gate would NEVER reach this policy without an
        # event; emitting a fabricated verdict against an event-less
        # payload would lie.
        if not eff_event:
            return PolicyTestResult(
                verdict="skipped",
                action="skipped",
                evidence_match_reasons=[
                    f"policy fires on event={trig.event!r}; payload "
                    "has no hook_event_name and the request supplied "
                    "no event override — supply hook_event_name on "
                    "the payload to simulate",
                ],
                hook_specific_output={},
                skipped_reason="no-event-supplied",
            )
        covered, skip_reason = _trigger_covers(
            trig.event, trig.matcher, payload,
        )
        if not covered:
            return PolicyTestResult(
                verdict="skipped",
                action="skipped",
                evidence_match_reasons=[
                    f"trigger frame does not cover this payload: policy fires "
                    f"on event={trig.event!r} matcher={trig.matcher!r}; "
                    f"payload event={eff_event!r} "
                    f"tool_name={_payload_tool_name(payload)!r}"
                    + (
                        " (CC always populates tool_name on this "
                        "event family; populate it to simulate the "
                        "matcher)"
                        if skip_reason == "payload-missing-tool-name"
                        else ""
                    ),
                ],
                hook_specific_output={},
                skipped_reason=skip_reason,
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
