"""Cassette record/replay layer for the magi-cp authoring QA harness (PR-D).

Every compile-interactive LLM call goes through CassetteProvider.  In replay
mode (the CI default) the provider returns the previously-stored response
keyed by a sha256 digest of the normalised message list.  In record mode
(MAGI_CP_QA_RECORD=1) it delegates to a real provider (ClaudeCliProvider by
default) and writes the result to the cassette file.

Cassette file layout: tests/qa_corpus/cassettes/<scenario_id>.json
Schema:
  {
    "schema_version": 1,
    "recorded_at": "<ISO-8601 or null for authored>",
    "model": "<model string or null for authored>",
    "generated_by": "authored" | "recorded",
    "compiler": [
      {
        "key": "<sha256 hex>",
        "messages_digest_preview": "<first 120 chars of last user content>",
        "response": "<raw provider response string>"
      },
      ...
    ],
    "user_sim": []
  }

Key derivation: sha256(canonical_json(normalised_messages)) where every
UNTRUSTED-<16hex> token is replaced with UNTRUSTED-N (belt-and-braces even
when the conftest monkeypatch already pins the nonce to a deterministic
counter).

Design reference:
  clawy docs/plans/2026-07-09-magi-cp-authoring-qa-harness-design.md
  Section 6.4 (cassette format + key normalisation + record/replay).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from magi_cp.llm.provider import LlmMessage, LlmProviderError


# ── key normalisation ─────────────────────────────────────────────────────
# Replace UNTRUSTED-<hex16> so a fresh nonce does not invalidate a cassette.
_NONCE_RE = re.compile(r"UNTRUSTED-[0-9a-fA-F]{16}", re.IGNORECASE)


def _normalise_messages(messages: list[LlmMessage]) -> list[dict[str, str]]:
    """Return a copy of messages with every UNTRUSTED-<nonce> replaced by
    UNTRUSTED-N.  The canonical JSON of this normalised list is the cassette
    key input."""
    result: list[dict[str, str]] = []
    for m in messages:
        normalised_content = _NONCE_RE.sub("UNTRUSTED-N", m["content"])
        result.append({"role": m["role"], "content": normalised_content})
    return result


def _make_key(messages: list[LlmMessage]) -> str:
    """sha256 hex digest of the canonical JSON of the normalised messages."""
    normalised = _normalise_messages(messages)
    canonical = json.dumps(normalised, ensure_ascii=False, sort_keys=False,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _preview(messages: list[LlmMessage]) -> str:
    """First 120 chars of the last user-turn content, for debug."""
    for m in reversed(messages):
        if m["role"] == "user":
            return m["content"][:120]
    return ""


# ── cassette file helpers ─────────────────────────────────────────────────

_CASSETTES_DIR = Path(__file__).parent.parent / "qa_corpus" / "cassettes"


def _cassette_path(scenario_id: str) -> Path:
    return _CASSETTES_DIR / f"{scenario_id}.json"


def _load_cassette(scenario_id: str) -> dict[str, Any]:
    p = _cassette_path(scenario_id)
    if not p.exists():
        return {"schema_version": 1, "compiler": [], "user_sim": []}
    with p.open(encoding="utf-8") as fh:
        return json.load(fh)


def _write_cassette(scenario_id: str, cassette: dict[str, Any]) -> None:
    _CASSETTES_DIR.mkdir(parents=True, exist_ok=True)
    p = _cassette_path(scenario_id)
    with p.open("w", encoding="utf-8") as fh:
        json.dump(cassette, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _index_compiler(cassette: dict[str, Any]) -> dict[str, str]:
    """Return a dict mapping key -> response for the compiler lane."""
    return {
        entry["key"]: entry["response"]
        for entry in cassette.get("compiler", [])
        if "key" in entry and "response" in entry
    }


# ── CassetteProvider ──────────────────────────────────────────────────────

class CassetteProvider:
    """LlmProvider that records or replays from a per-scenario cassette file.

    Construct with scenario_id.  In replay mode (default):
      - look up the response by sha256 key
      - on miss: raise an actionable LlmProviderError with re-record instructions

    In record mode (MAGI_CP_QA_RECORD=1):
      - delegate to the underlying real provider
      - write the (key, response) pair to the cassette file
      - return the response

    The cassette lane is ``compiler``; the ``user_sim`` lane is reserved for
    future user-side LLM answerer recording (Section 6.2) but is stored as an
    empty list here.
    """

    def __init__(
        self,
        scenario_id: str,
        *,
        record_mode: bool | None = None,
        underlying_provider: Any = None,
    ) -> None:
        self.scenario_id = scenario_id
        self._record_mode = record_mode if record_mode is not None else (
            os.environ.get("MAGI_CP_QA_RECORD", "").strip() == "1"
        )
        self._underlying = underlying_provider
        # Lazy-load the cassette index on first call.
        self._index: dict[str, str] | None = None
        self._cassette: dict[str, Any] | None = None

    def _ensure_loaded(self) -> None:
        if self._index is None:
            raw = _load_cassette(self.scenario_id)
            self._cassette = raw
            self._index = _index_compiler(raw)

    def _get_underlying(self) -> Any:
        """Return or build the real provider for record mode."""
        if self._underlying is not None:
            return self._underlying
        # Default: ClaudeCliProvider.
        try:
            from magi_cp.llm.claude_cli_provider import ClaudeCliProvider
            self._underlying = ClaudeCliProvider()
        except Exception as e:
            raise LlmProviderError(
                f"CassetteProvider: cannot build ClaudeCliProvider for record: {e}"
            ) from e
        return self._underlying

    def complete(self, messages: list[LlmMessage]) -> str:
        self._ensure_loaded()
        key = _make_key(messages)
        assert self._index is not None

        if self._record_mode:
            # Always call through and (over)write the cassette entry.
            real = self._get_underlying()
            response = real.complete(messages)
            self._record(key, messages, response)
            return response

        # Replay mode.
        if key in self._index:
            return self._index[key]

        # Miss - raise actionable error.
        raise LlmProviderError(
            f"cassette stale/missing for {self.scenario_id!r}: "
            f"no entry for key {key[:12]}... "
            f"Re-record with: "
            f"MAGI_CP_QA_RECORD=1 PYTHONPATH=src python3 -m pytest "
            f"tests/test_qa_corpus_replay.py -k {self.scenario_id}"
        )

    def _record(self, key: str, messages: list[LlmMessage], response: str) -> None:
        """Write a new (key, response) pair to the cassette file."""
        import datetime
        assert self._cassette is not None
        # Rebuild the index from the file (may have been updated by another call).
        raw = _load_cassette(self.scenario_id)
        index = _index_compiler(raw)
        if key not in index:
            # Detect model used (best effort).
            model: str | None = None
            try:
                underlying = self._underlying
                if underlying is not None:
                    model = getattr(underlying, "model", None)
            except Exception:
                pass
            entry: dict[str, Any] = {
                "key": key,
                "messages_digest_preview": _preview(messages),
                "response": response,
            }
            raw.setdefault("compiler", []).append(entry)
            if raw.get("generated_by") is None:
                raw["generated_by"] = "recorded"
            if raw.get("recorded_at") is None:
                raw["recorded_at"] = datetime.datetime.utcnow().isoformat() + "Z"
            if raw.get("model") is None and model:
                raw["model"] = model
            raw.setdefault("schema_version", 1)
            raw.setdefault("user_sim", [])
            _write_cassette(self.scenario_id, raw)
        # Refresh our in-memory index.
        self._index = _index_compiler(raw)
        self._cassette = raw
