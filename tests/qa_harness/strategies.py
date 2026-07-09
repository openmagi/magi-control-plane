"""Hypothesis strategies for magi-cp conversational authoring QA harness.

All enumerables are imported from the production modules as sources of
truth - no lists are hardcoded here. This ensures strategies stay in
sync with any future additions to events, tools, or verifier descriptors.

Circular-import discipline: `magi_cp.policy.handoff_context` triggers a
circular import if imported before the cloud package initializes. We
resolve this by importing cloud first (which breaks the cycle) at the
top of the test file, and exposing a lazy accessor here for the
_RUN_COMMAND_LIFECYCLE_TO_EVENT map.

Section 4.1 of docs/plans/2026-07-09-magi-cp-authoring-qa-harness-design.md.
"""
from __future__ import annotations

import json
import re
from typing import Any

from hypothesis import strategies as st

# Import production sources of truth - never hardcode these lists.
# Note: matrix.py and verifier.descriptors are safe to import directly.
from magi_cp.policy.matrix import (
    LEGAL_COMBINATIONS,
    MatcherClass,
    _BUILTIN_TOOLS,
)
from magi_cp.verifier.descriptors import _DESCRIPTORS


def _get_run_command_lifecycle_map() -> dict[str, str]:
    """Lazy accessor for _RUN_COMMAND_LIFECYCLE_TO_EVENT.

    handoff_context.py triggers a circular import chain when imported
    before the cloud package. Since the test file imports cloud first,
    this function is safe to call after module initialization.
    """
    from magi_cp.policy.handoff_context import _RUN_COMMAND_LIFECYCLE_TO_EVENT  # noqa: PLC0415
    return _RUN_COMMAND_LIFECYCLE_TO_EVENT


# ---------------------------------------------------------------------------
# Internal helpers (computed lazily or at strategy-call time)
# ---------------------------------------------------------------------------

# Actions that are conversationally authored (block/ask/audit).
# LEGAL_COMBINATIONS also contains run_command/inject_context; we exclude
# those for the evidence archetype strategies.
_EVIDENCE_ACTIONS: frozenset[str] = frozenset({"block", "ask", "audit"})

# All legal (event, matcher_class, action) triples for evidence archetype.
_EVIDENCE_TRIPLES: list[tuple[str, MatcherClass, str]] = [
    (ev, mc, act)
    for ev, mc, act in LEGAL_COMBINATIONS
    if act in _EVIDENCE_ACTIONS
]

# Sorted list of builtin tools for reproducible strategies.
_BUILTIN_TOOLS_LIST: list[str] = sorted(_BUILTIN_TOOLS)

# Registered verifier step names.
_VERIFIER_STEPS: list[str] = sorted(_DESCRIPTORS.keys())

# The 3 q_lifecycle bucket values the answer-channel pill uses.
_LIFECYCLE_BUCKETS: list[str] = ["before_tool_use", "after_tool_use", "pre_final"]

# Valid on_missing values (from the IR's on_missing coercer).
_ON_MISSING_VALUES: list[str] = ["block", "ask", "audit"]


# ---------------------------------------------------------------------------
# Small building-block strategies
# ---------------------------------------------------------------------------

def _st_mcp_tool() -> st.SearchStrategy[str]:
    """Generate a valid mcp__<server>__<tool> matcher string.

    The MCP tool regex is `^mcp__[A-Za-z0-9_]+__[A-Za-z0-9_]+$` (ASCII only).
    """
    # ASCII alphanumeric + underscore to match the production MCP tool regex.
    ascii_alnum_under = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
    token = st.text(
        alphabet=ascii_alnum_under,
        min_size=1, max_size=12,
    )
    return st.builds(lambda a, b: f"mcp__{a}__{b}", token, token)


def _st_tool_alt() -> st.SearchStrategy[str]:
    """Generate a valid tool_alt matcher (2-3 builtins joined by |)."""
    return st.lists(
        st.sampled_from(_BUILTIN_TOOLS_LIST),
        min_size=2, max_size=3, unique=True,
    ).map("|".join)


def _st_matcher_for_class(mc: MatcherClass) -> st.SearchStrategy[str]:
    if mc == MatcherClass.tool:
        return st.sampled_from(_BUILTIN_TOOLS_LIST)
    if mc == MatcherClass.mcp_tool:
        return _st_mcp_tool()
    if mc == MatcherClass.wildcard:
        return st.just("*")
    if mc == MatcherClass.tool_alt:
        return _st_tool_alt()
    return st.just("Bash")


# ---------------------------------------------------------------------------
# Public strategy: st_conv_triple
# ---------------------------------------------------------------------------

def st_conv_triple() -> st.SearchStrategy[tuple[str, str, str]]:
    """Sample (event, matcher, action) from LEGAL_COMBINATIONS filtered to
    evidence-archetype actions (block/ask/audit).

    The returned matcher is a concrete string, not a MatcherClass.
    """
    return (
        st.sampled_from(_EVIDENCE_TRIPLES)
        .flatmap(
            lambda triple: _st_matcher_for_class(triple[1]).map(
                lambda m: (triple[0], m, triple[2])
            )
        )
    )


# ---------------------------------------------------------------------------
# Public strategy: st_requires
# ---------------------------------------------------------------------------

def st_requires() -> st.SearchStrategy[dict[str, Any]]:
    """Sample a single requires entry dict.

    Covers all four kinds: regex, llm_critic, shacl, step.
    Bodies come from small fixed pools plus hypothesis from_regex snippets.
    """
    valid_regexes = [
        r"\brm\b",
        r"password",
        r"^ERROR",
        r"secret_?key",
        r"[0-9]{4,}",
    ]

    regex_req = st.builds(
        lambda p: {"kind": "regex", "pattern": p},
        st.one_of(
            st.sampled_from(valid_regexes),
            st.from_regex(re.compile(r"[a-z]{1,10}"), fullmatch=False).map(
                lambda s: re.escape(s)
            ),
        ),
    )

    criterion_text = st.one_of(
        st.just("The response must not contain harmful content."),
        st.just("출처가 명확해야 합니다."),
        st.text(alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd", "Zs")),
                min_size=5, max_size=80),
    )
    llm_req = st.builds(
        lambda c: {"kind": "llm_critic", "criterion": c},
        criterion_text,
    )

    shacl_req = st.builds(
        lambda ttl: {"kind": "shacl", "shape_ttl": ttl},
        st.just("@prefix sh: <http://www.w3.org/ns/shacl#> ."),
    )

    step_req: st.SearchStrategy[dict[str, Any]]
    if _VERIFIER_STEPS:
        step_req = st.builds(
            lambda s: {"kind": "step", "step": s, "verdict": "pass"},
            st.sampled_from(_VERIFIER_STEPS),
        )
    else:
        step_req = st.builds(
            lambda: {"kind": "step", "step": "citation_verify", "verdict": "pass"}
        )

    return st.one_of(regex_req, llm_req, shacl_req, step_req)


# ---------------------------------------------------------------------------
# Public strategy: st_evidence_draft
# ---------------------------------------------------------------------------

def st_evidence_draft() -> st.SearchStrategy[dict[str, Any]]:
    """Sample a fully-specified evidence policy draft.

    All three triple fields (event/matcher/action) are explicit per
    Section 0.3 of the design doc - no IR defaulting.
    """
    return st.builds(
        lambda triple, requires, pid, desc: {
            "id": pid or "qa-test-policy",
            "description": desc,
            "trigger": {
                "host": "claude-code",
                "event": triple[0],
                "matcher": triple[1],
            },
            "requires": [requires],
            "action": triple[2],
        },
        st_conv_triple(),
        st_requires(),
        st.one_of(
            st.just(None),
            st.from_regex(re.compile(r"[a-z][a-z0-9-]{2,30}"), fullmatch=True),
        ),
        st.text(
            alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd", "Zs")),
            min_size=0, max_size=60,
        ),
    )


# ---------------------------------------------------------------------------
# Public strategy: st_partial_draft
# ---------------------------------------------------------------------------

def st_partial_draft() -> st.SearchStrategy[dict[str, Any] | None]:
    """Sample a random field-subset of a full draft (models mid-conversation).

    Returns None (no draft yet) with ~20% probability, or a dict with an
    arbitrary subset of fields from a full evidence draft.
    """
    full = st_evidence_draft()

    def _pick_subset(d: dict[str, Any]) -> dict[str, Any]:
        """Randomly drop some top-level fields to simulate partial state."""
        keys = list(d.keys())
        # Always keep at least 0 keys (empty dict is a valid partial).
        return {k: d[k] for k in keys if True}  # start fully; see filter below

    # Use draw strategy to pick a subset of fields
    all_keys = ["id", "description", "trigger", "requires", "action"]

    @st.composite
    def _partial(draw: Any) -> dict[str, Any] | None:
        if draw(st.booleans()) and draw(st.booleans()):  # ~25% chance of None
            return None
        full_draft = draw(full)
        # Pick a random subset of top-level keys
        n = draw(st.integers(min_value=0, max_value=len(all_keys)))
        chosen = draw(st.lists(st.sampled_from(all_keys), min_size=n, max_size=n, unique=True))
        if not chosen:
            return {}
        out: dict[str, Any] = {}
        for k in chosen:
            if k in full_draft:
                out[k] = full_draft[k]
        # Also sometimes include trigger with only some sub-fields.
        if "trigger" in out and draw(st.booleans()):
            trig = out["trigger"]
            partial_trig: dict[str, Any] = {"host": "claude-code"}
            if draw(st.booleans()) and "event" in trig:
                partial_trig["event"] = trig["event"]
            if draw(st.booleans()) and "matcher" in trig:
                partial_trig["matcher"] = trig["matcher"]
            out["trigger"] = partial_trig
        return out

    return _partial()


# ---------------------------------------------------------------------------
# Public strategy: st_run_command_draft
# ---------------------------------------------------------------------------

def st_run_command_draft() -> st.SearchStrategy[dict[str, Any]]:
    """Sample a partial run_command archetype draft.

    event drawn from _RUN_COMMAND_LIFECYCLE_TO_EVENT values (the canonical
    set); runtime from ("bash","python3","node").
    """

    @st.composite
    def _build(draw: Any) -> dict[str, Any]:
        # Lazy access to avoid circular import at module load time.
        events = list(_get_run_command_lifecycle_map().values())
        event = draw(st.sampled_from(events))
        runtime = draw(st.sampled_from(["bash", "python3", "node"]))
        use_script = draw(st.booleans())
        out: dict[str, Any] = {
            "type": "run_command",
            "trigger": {"host": "claude-code", "event": event},
            "runtime": runtime,
        }
        if use_script:
            # 64-hex script_path
            hex_chars = "0123456789abcdef"
            path = draw(st.text(alphabet=hex_chars, min_size=64, max_size=64))
            out["script_path"] = path
        else:
            out["command"] = draw(st.text(
                alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd", "Zs", "Po")),
                min_size=1, max_size=80,
            ))
        return out

    return _build()


# ---------------------------------------------------------------------------
# Public strategy: st_adversarial_llm_response
# ---------------------------------------------------------------------------

def _st_adversarial_llm_json_body() -> st.SearchStrategy[str]:
    """Generate adversarial but valid-JSON LLM response strings.

    For direct step_compile calls where ValueError from bad JSON is not
    the behavior under test. Variants:
    - requires re-emitted with empty bodies (R1-02 killer)
    - trigger rewrites (host pivot)
    - host/gate_binary/type pivots (must be refused)
    - divergent questions proposed by LLM
    - jargon and wrong-language assistant_message
    - blanket infeasible_hint
    """
    jargon_messages = [
        "The llm_critic kind requires a criterion.",
        "Set on_missing to block or audit.",
        "The regex matcher is invalid.",
        "lifecycle must be PreToolUse.",
        "gate_binary path required.",
        "LLM judge will evaluate this.",
        "shacl validation failed.",
    ]
    ko_messages = [
        "정책을 저장할 준비가 됐습니다.",
        "어떤 도구를 차단할까요?",
    ]

    # Requires with empty bodies (the R1-02 class).
    empty_regex_req = {"kind": "regex", "pattern": ""}
    empty_llm_req = {"kind": "llm_critic", "criterion": ""}
    empty_shacl_req = {"kind": "shacl", "shape_ttl": ""}

    @st.composite
    def _build(draw: Any) -> str:
        variant = draw(st.integers(min_value=0, max_value=6))

        if variant == 0:
            # Empty-body requires - the R1-02 killer
            req = draw(st.sampled_from([empty_regex_req, empty_llm_req, empty_shacl_req]))
            return json.dumps({
                "draft_updates": {"requires": [req]},
                "assistant_message": draw(st.sampled_from(jargon_messages)),
                "questions": [],
            })
        if variant == 1:
            # Trigger rewrite - try to change host
            return json.dumps({
                "draft_updates": {
                    "trigger": {"host": "codex", "event": "PreToolUse", "matcher": "Bash"},
                },
                "assistant_message": "",
                "questions": [],
            })
        if variant == 2:
            # Pivot type/gate_binary
            return json.dumps({
                "draft_updates": {
                    "type": "run_command",
                    "gate_binary": "/tmp/evil",
                },
                "assistant_message": "",
                "questions": [],
            })
        if variant == 3:
            # Divergent questions proposed
            return json.dumps({
                "draft_updates": {},
                "assistant_message": "",
                "questions": [
                    {"id": "q_evil", "prompt": "malicious?", "kind": "single_select",
                     "targets_field": "lifecycle", "options": [{"value": "x", "label": "X"}]},
                ],
            })
        if variant == 4:
            # Jargon assistant_message
            return json.dumps({
                "draft_updates": {},
                "assistant_message": draw(st.sampled_from(jargon_messages)),
                "questions": [],
            })
        if variant == 5:
            # Wrong language (Korean) assistant_message
            return json.dumps({
                "draft_updates": {},
                "assistant_message": draw(st.sampled_from(ko_messages)),
                "questions": [],
            })
        # variant == 6: blanket infeasible_hint
        return json.dumps({
            "draft_updates": {},
            "assistant_message": "This cannot be expressed.",
            "questions": [],
            "infeasible_hint": "not_expressible",
        })

    return _build()


def st_adversarial_llm_response(*, include_non_json: bool = False) -> st.SearchStrategy[str]:
    """Generate adversarial LLM response strings.

    When include_non_json=False (default): valid JSON only - use for direct
    step_compile calls where JSON parse errors are not the behavior under test.

    When include_non_json=True: also includes non-JSON prose and fenced JSON -
    use for route-level tests (I9) where 422 is the expected outcome for
    garbage LLM output.

    Traces to: R1-02, AF-15 adversarial injection.
    """
    json_strategy = _st_adversarial_llm_json_body()
    if not include_non_json:
        return json_strategy

    non_json = st.sampled_from([
        "Sorry, I cannot help with that.",
        "```json\n{}\n```",
        "yes",
        "",
    ])
    return st.one_of(json_strategy, non_json)


# ---------------------------------------------------------------------------
# Public strategy: st_answer_walk
# ---------------------------------------------------------------------------

def st_answer_walk(wire: dict[str, Any]) -> st.SearchStrategy[dict[str, str] | None]:
    """Given a wire response from step_compile, pick an answer dict.

    For single_select questions with options: pick one option value.
    For text questions: return None (text questions answered via userText,
    not via the answers payload).
    For multi-select: join options with ",".
    Returns None if no answerable questions with options are present.
    """
    questions = wire.get("questions") or []
    pill_qs = [
        q for q in questions
        if q.get("kind") in ("single_select", "multi_select")
        and q.get("options")
        and q.get("id")
    ]
    if not pill_qs:
        return st.just(None)

    @st.composite
    def _pick(draw: Any) -> dict[str, str] | None:
        q = draw(st.sampled_from(pill_qs))
        opts = q.get("options") or []
        if not opts:
            return None
        if q.get("kind") == "multi_select" and len(opts) > 1:
            chosen = draw(
                st.lists(st.sampled_from(opts), min_size=1, max_size=len(opts), unique=True)
            )
            value = ",".join(o.get("value", "") if isinstance(o, dict) else str(o)
                            for o in chosen)
        else:
            opt = draw(st.sampled_from(opts))
            value = opt.get("value", "") if isinstance(opt, dict) else str(opt)
        return {q["id"]: value}

    return _pick()
