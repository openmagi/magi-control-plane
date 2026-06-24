"""Policy IR + deterministic compiler (LLM-free) + v1 resolved-set."""
from .ir import (
    AnyPolicy, ContextInjectionPolicy, EvidencePolicy, EvidenceReq,
    McpGatingPolicy, PermissionPolicy, Policy, SubagentPolicy, Trigger,
    load_policy, policy_from_dict, policy_to_dict,
)
from .compiler import compile_to_managed_settings, compile_files
from .matrix import (
    LEGAL_COMBINATIONS, MatcherClass,
    matcher_class_of, validate_combination, supported_events,
)
from .precedence import (
    LooseningError, PolicySource, SOURCE_PRECEDENCE, is_loosening,
    more_authoritative, resolve_by_id, source_rank, tighten_against,
)
from .resolved import (
    PolicyOverride, ResolvedPolicy, ResolvedPolicySet, resolve_with_tightening,
)

__all__ = [
    "Policy", "EvidencePolicy", "Trigger", "EvidenceReq", "load_policy",
    "PermissionPolicy", "SubagentPolicy", "McpGatingPolicy",
    "ContextInjectionPolicy", "AnyPolicy",
    "policy_from_dict", "policy_to_dict",
    "compile_to_managed_settings", "compile_files",
    "LEGAL_COMBINATIONS", "MatcherClass",
    "matcher_class_of", "validate_combination", "supported_events",
    "PolicySource", "SOURCE_PRECEDENCE", "source_rank", "more_authoritative",
    "resolve_by_id", "tighten_against", "is_loosening", "LooseningError",
    "PolicyOverride", "ResolvedPolicy", "ResolvedPolicySet",
    "resolve_with_tightening",
]
