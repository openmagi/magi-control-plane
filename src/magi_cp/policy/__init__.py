"""Policy IR + deterministic compiler (LLM-free) + v1 resolved-set."""
from .ir import Policy, Trigger, EvidenceReq, load_policy
from .compiler import compile_to_managed_settings, compile_files
from .matrix import (
    LEGAL_COMBINATIONS, MatcherClass,
    matcher_class_of, validate_combination, supported_events,
)
from .precedence import (
    PolicySource, SOURCE_PRECEDENCE, source_rank, more_authoritative,
    resolve_by_id,
)
from .resolved import PolicyOverride, ResolvedPolicy, ResolvedPolicySet

__all__ = [
    "Policy", "Trigger", "EvidenceReq", "load_policy",
    "compile_to_managed_settings", "compile_files",
    "LEGAL_COMBINATIONS", "MatcherClass",
    "matcher_class_of", "validate_combination", "supported_events",
    "PolicySource", "SOURCE_PRECEDENCE", "source_rank", "more_authoritative",
    "resolve_by_id",
    "PolicyOverride", "ResolvedPolicy", "ResolvedPolicySet",
]
