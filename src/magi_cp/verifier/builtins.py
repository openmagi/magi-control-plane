"""v1.1-PB beachhead batch — 5 built-in verifiers for legal filing.

Wired to the registry via register_builtins(). Each verifier follows the
Verifier protocol (name/step/category/enforcement/description/input_schema/run).

Design notes
------------
* Deterministic-first: every verifier reaches a verdict from a regex/parser
  decision; nothing here calls an LLM.
* Reasons travel with verdicts so the HITL queue can display them.
* The citation verifier is exposed via a thin adapter so the existing
  verify_document() pipeline stays the single implementation.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from .citations import Citation, verify_document
from .protocol import Enforcement, Verdict, VerifierRegistry
from .sources import DictResolver, SourceResolver


# ── shared helpers ─────────────────────────────────────────────────
def _u(text: str | None) -> str:
    return (text or "").lower()


# ── 1) privilege_scan ─────────────────────────────────────────────
# Korean RRN: YYMMDD-XBBBBNN, X ∈ {1,2,3,4} (gender/century code).
# The month/day bounds keep "1234567890123" from triggering by accident.
_RRN_RE = re.compile(
    r"\b\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])-[1-4]\d{6}\b"
)
_PRIV_HARD_RE = re.compile(
    r"(attorney[\s\-]*client[\s\-]*privileged?"
    r"|work[\s\-]*product"
    r"|변호사[\s\-]*의뢰인[\s\-]*특권)",
    re.IGNORECASE,
)
_PRIV_SOFT_RE = re.compile(
    r"(confidential[\s\-]*draft|기밀[\s\-]*초안|\[CONFIDENTIAL\b)",
    re.IGNORECASE,
)


class PrivilegeScanVerifier:
    name = "verify_privilege_scan"
    step = "privilege_scan"
    category = "SECURITY"
    enforcement = Enforcement.enforcing
    description = (
        "Scans the caller-assembled `text` field for attorney-client "
        "privilege markers, work-product flags, and Korean RRN "
        "(주민등록번호) patterns via fixed regex. The caller routes the "
        "right CC stdin surface into `text` (tool_input.command / "
        "tool_input.new_string / tool_input.content on PreToolUse, "
        "final_message on Stop). Verdict mapping: any hard marker or "
        "RRN hit returns deny; a soft confidentiality marker with no "
        "hard hit returns review; no hit returns pass."
    )
    input_schema = {
        "type": "object",
        "required": ["text"],
        "properties": {"text": {"type": "string"}},
    }

    def run(self, payload: dict) -> Verdict:
        text = payload.get("text") or ""
        reasons: list[str] = []
        if _PRIV_HARD_RE.search(text):
            reasons.append("attorney-client privilege / work-product marker present")
        if _RRN_RE.search(text):
            reasons.append("Korean RRN (주민등록번호) pattern present")
        if reasons:
            return Verdict(status="deny", reasons=reasons)
        if _PRIV_SOFT_RE.search(text):
            return Verdict(
                status="review",
                reasons=["soft confidentiality marker — HITL"],
            )
        return Verdict(status="pass", reasons=[])


# ── 2) source_allowlist ───────────────────────────────────────────
class SourceAllowlistVerifier:
    name = "verify_source_allowlist"
    step = "source_allowlist"
    category = "RESEARCH"
    enforcement = Enforcement.enforcing
    description = (
        "Walks every URL in `sources`, parses each into a host (or rejects "
        "as malformed), and suffix-matches the host against the configured "
        "`allowlist` (an entry covers itself and its subdomains). Verdict "
        "mapping: every source host suffix-matches an allowlist entry "
        "returns pass. Any malformed URL or any host not covered by the "
        "allowlist returns deny. An empty `sources` list returns pass."
    )
    input_schema = {
        "type": "object",
        "required": ["sources", "allowlist"],
        "properties": {
            "sources": {"type": "array", "items": {"type": "string"}},
            "allowlist": {"type": "array", "items": {"type": "string"}},
        },
    }

    _ALLOWED_SCHEMES = ("http", "https")

    @staticmethod
    def _host(url: str) -> str | None:
        try:
            p = urlparse(url)
        except Exception:
            return None
        if p.scheme and p.scheme not in SourceAllowlistVerifier._ALLOWED_SCHEMES:
            return None
        host = (p.hostname or "").lower()
        return host or None

    @staticmethod
    def _normalize_allowlist_entry(raw: str) -> str | None:
        """Accept either bare hostname ('law.go.kr') or full URL
        ('https://law.go.kr/'). Extracts hostname before comparison so the
        operator-facing form is forgiving without breaking subdomain logic.
        """
        s = raw.lower().strip().strip("/")
        if not s:
            return None
        if "://" in s:
            try:
                h = urlparse(s).hostname
            except Exception:
                return None
            return h or None
        # bare form may carry path or port — keep only the host portion
        s = s.split("/", 1)[0]
        s = s.split(":", 1)[0]
        return s or None

    @staticmethod
    def _matches(host: str, allowed: str) -> bool:
        # Suffix match: host == allowed OR host endswith "." + allowed.
        # Prevents "evil-law.go.kr" from matching "law.go.kr".
        return host == allowed or host.endswith("." + allowed)

    def run(self, payload: dict) -> Verdict:
        sources = list(payload.get("sources") or [])
        raw_allow = (payload.get("allowlist") or [])
        allowlist = [h for h in (self._normalize_allowlist_entry(a) for a in raw_allow) if h]
        if not sources:
            return Verdict(status="pass", reasons=[])
        reasons: list[str] = []
        for src in sources:
            host = self._host(src)
            if not host:
                reasons.append(f"source not in allowlist (malformed/blocked URL): {src!r}")
                continue
            if not any(self._matches(host, a) for a in allowlist):
                reasons.append(f"source not in allowlist: {host}")
        if reasons:
            return Verdict(status="deny", reasons=reasons)
        return Verdict(status="pass", reasons=[])


# ── 3) structured_output ───────────────────────────────────────────
_SUPPORTED_SCHEMA_KEYWORDS = {
    "type", "required", "enum", "properties", "items", "description", "title",
}


def _schema_unsupported(schema: dict) -> list[str]:
    """Return any schema keywords this verifier doesn't understand. Filing
    schemas using `additionalProperties: false`, `oneOf`, `$ref`, etc. would
    silently pass otherwise — reject them at runtime so the operator gets a
    clear signal instead of a false-pass verdict.
    """
    if not isinstance(schema, dict):
        return ["schema must be an object"]
    bad: list[str] = []
    for k in schema:
        if k not in _SUPPORTED_SCHEMA_KEYWORDS:
            bad.append(k)
    # recurse into properties and items
    for sub in (schema.get("properties") or {}).values():
        if isinstance(sub, dict):
            bad.extend(_schema_unsupported(sub))
    items = schema.get("items")
    if isinstance(items, dict):
        bad.extend(_schema_unsupported(items))
    return bad


def _validate_json_schema(data: Any, schema: dict, path: str = "") -> list[str]:
    """Tiny subset of JSON Schema: type, required, enum, properties, items.

    The goal isn't general validation — it's catching the shape failures that
    filing payloads actually have. Unsupported keywords are rejected at the
    boundary by _schema_unsupported() before we get here.
    """
    errs: list[str] = []
    t = schema.get("type")
    if t == "object":
        if not isinstance(data, dict):
            return [f"{path or '<root>'}: expected object"]
        for req in schema.get("required", []):
            if req not in data:
                errs.append(f"{path or '<root>'}: required field missing: {req}")
        for k, sub in (schema.get("properties") or {}).items():
            if k in data:
                errs.extend(_validate_json_schema(data[k], sub, f"{path}.{k}" if path else k))
    elif t == "array":
        if not isinstance(data, list):
            return [f"{path}: expected array"]
        items = schema.get("items")
        if items:
            for i, el in enumerate(data):
                errs.extend(_validate_json_schema(el, items, f"{path}[{i}]"))
    elif t == "string":
        if not isinstance(data, str):
            errs.append(f"{path}: expected string")
    elif t == "number":
        if not isinstance(data, (int, float)) or isinstance(data, bool):
            errs.append(f"{path}: expected number")
    elif t == "integer":
        if not isinstance(data, int) or isinstance(data, bool):
            errs.append(f"{path}: expected integer")
    elif t == "boolean":
        if not isinstance(data, bool):
            errs.append(f"{path}: expected boolean")
    if "enum" in schema and data not in schema["enum"]:
        errs.append(f"{path}: value {data!r} not in enum {schema['enum']}")
    return errs


class StructuredOutputVerifier:
    name = "verify_structured_output"
    step = "structured_output"
    category = "OUTPUT"
    enforcement = Enforcement.enforcing
    description = (
        "Reads the payload from `data` (already-parsed dict) or `json` "
        "(JSON-encoded string, parsed at runtime). Validates the result "
        "against the configured small JSON-Schema subset (type / "
        "required / enum / properties / items; unknown keywords are "
        "rejected at the boundary). Verdict mapping: schema validation "
        "passes returns pass. JSON parse failure, unsupported schema "
        "keyword, missing payload, or any validation error returns deny."
    )
    input_schema = {
        "type": "object",
        "required": ["schema"],
        "properties": {
            "json": {"type": "string", "description": "JSON-encoded payload"},
            "data": {"description": "Pre-parsed payload (alternative to `json`)"},
            "schema": {"type": "object"},
        },
    }

    def run(self, payload: dict) -> Verdict:
        schema = payload.get("schema")
        if not schema:
            return Verdict(status="deny", reasons=["schema is required"])
        unsupported = _schema_unsupported(schema)
        if unsupported:
            return Verdict(
                status="deny",
                reasons=[f"unsupported schema keyword: {k}" for k in sorted(set(unsupported))],
            )
        if "data" in payload:
            data = payload["data"]
        elif "json" in payload:
            try:
                data = json.loads(payload["json"])
            except (ValueError, TypeError) as e:
                return Verdict(status="deny", reasons=[f"JSON parse error: {e}"])
        else:
            return Verdict(status="deny", reasons=["neither `json` nor `data` provided"])
        errs = _validate_json_schema(data, schema)
        if errs:
            return Verdict(status="deny", reasons=errs)
        return Verdict(status="pass", reasons=[])


# ── 4) prompt_injection_screen ────────────────────────────────────
# Hits an injection ATTEMPT — not a mention of the topic. The verbs and
# the "previous instructions" object give us reasonable specificity.
_INJ_PATTERNS = [
    re.compile(r"\bignore\s+(?:all\s+)?previous\s+instructions?", re.IGNORECASE),
    re.compile(r"\bdisregard\s+(?:all\s+)?(?:prior|previous|above)\s+(?:instructions?|messages?|prompts?)", re.IGNORECASE),
    re.compile(r"\bforget\s+(?:everything|all|the)\s+(?:above|previous|prior)", re.IGNORECASE),
    re.compile(r"\bnew\s+instructions?\s*:", re.IGNORECASE),
    re.compile(r"이전\s*지시(?:사항)?(?:은|는|을|를)?\s*(?:모두\s*)?무시"),
    re.compile(r"<\|im_start\|>\s*system", re.IGNORECASE),
    re.compile(r"<\|system\|>", re.IGNORECASE),
    re.compile(r"\byou\s+are\s+now\s+(?:uncensored|jailbroken|dan)", re.IGNORECASE),
]


class PromptInjectionScreenVerifier:
    name = "verify_prompt_injection_screen"
    step = "prompt_injection_screen"
    category = "SECURITY"
    enforcement = Enforcement.enforcing
    description = (
        "Scans the caller-assembled `text` field for override verbs "
        "(\"ignore previous instructions\"), role-tag injection (system "
        "/ assistant / user tag patterns), and known jailbreak markers "
        "via fixed regex. Hit semantics target an injection attempt, "
        "not a topical mention. Verdict mapping: any pattern hit "
        "returns deny. No hit returns pass."
    )
    input_schema = {
        "type": "object",
        "required": ["text"],
        "properties": {"text": {"type": "string"}},
    }

    def run(self, payload: dict) -> Verdict:
        text = payload.get("text") or ""
        hits = [pat.pattern for pat in _INJ_PATTERNS if pat.search(text)]
        if hits:
            return Verdict(
                status="deny",
                reasons=[f"prompt-injection pattern: {p}" for p in hits],
            )
        return Verdict(status="pass", reasons=[])


# ── 5) citation verifier — registry adapter ───────────────────────
class CitationVerifierAdapter:
    """Wraps verify_document() so it surfaces in the registry.

    The legacy MCP `verify_citations` tool keeps its current handler for
    backwards compat. This adapter additionally exposes the verifier through
    Policy IR / /presets so all 5 batch entries can be cited by name.
    """

    name = "verify_citations"
    step = "citation_verify"
    category = "FACT"
    enforcement = Enforcement.enforcing
    description = (
        "Walks each entry in `citations[]` ({quote, ref}). Resolves the "
        "ref through `corpus_override` (if supplied) or the default "
        "SourceResolver. For each resolved ref, runs verbatim + NLI "
        "match between the quote and the source body. Verdict mapping: "
        "every entry resolved and matched returns pass. Any resolution "
        "failure returns deny. Resolved but verbatim / NLI mismatch on "
        "any entry returns review. A missing or empty `corpus_override` "
        "with no default resolver returns review (defer to HITL)."
    )
    input_schema = {
        "type": "object",
        "required": ["citations"],
        "properties": {
            "citations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["quote", "ref"],
                    "properties": {
                        "quote": {"type": "string"},
                        "ref": {"type": "string"},
                    },
                },
            },
            "corpus_override": {
                "type": "object",
                "description": "case_no → source text. Bypass network in tests.",
            },
        },
    }

    def run(self, payload: dict) -> Verdict:
        raw = payload.get("citations") or []
        citations: list[Citation] = []
        malformed: list[str] = []
        for i, c in enumerate(raw):
            if not isinstance(c, dict):
                malformed.append(f"citations[{i}]: not an object")
                continue
            quote = c.get("quote")
            ref = c.get("ref")
            if not isinstance(quote, str) or not isinstance(ref, str):
                malformed.append(f"citations[{i}]: missing required 'quote' or 'ref'")
                continue
            citations.append(Citation(quote, ref))
        if malformed:
            return Verdict(status="deny", reasons=malformed)
        if not citations:
            return Verdict(status="pass", reasons=[])
        override = payload.get("corpus_override") or {}
        if not override:
            # No corpus given → can't deterministically verify. Default to review
            # so HITL gets the call rather than the verifier issuing a token.
            return Verdict(
                status="review",
                reasons=["no corpus_override provided — defer to HITL"],
            )
        resolver: SourceResolver = DictResolver(override)
        doc = verify_document(citations, resolver)
        reasons: list[str] = []
        for v in doc.verdicts:
            reasons.extend(v.reasons)
        return Verdict(status=doc.verdict, reasons=reasons)


# ── registry installer ─────────────────────────────────────────────
def register_builtins(reg: VerifierRegistry) -> None:
    """Install the 5 beachhead verifiers into `reg`.

    Idempotent across separate registries, NOT idempotent on the same one —
    a second call raises ValueError(duplicate). That's intentional: silent
    re-registration would mask a wiring bug at startup.
    """
    reg.register(CitationVerifierAdapter())
    reg.register(PrivilegeScanVerifier())
    reg.register(SourceAllowlistVerifier())
    reg.register(StructuredOutputVerifier())
    reg.register(PromptInjectionScreenVerifier())


__all__ = [
    "CitationVerifierAdapter",
    "PrivilegeScanVerifier",
    "SourceAllowlistVerifier",
    "StructuredOutputVerifier",
    "PromptInjectionScreenVerifier",
    "register_builtins",
]
