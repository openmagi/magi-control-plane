"""Shared pytest configuration for all magi-cp tests.

This module provides fixtures used by the QA harness replay tests.

QA harness fixture: qa_nonce_counter
  Monkeypatches magi_cp.cloud.nl_compiler._make_fence_nonce() (and its
  re-export from magi_cp.policy.nl_compiler_interactive) to return a
  deterministic counter-based nonce ("0000000000000001", "0000000000000002",
  ...)  instead of a cryptographic random hex string.

  Purpose: the cassette key is sha256(canonical-JSON(normalised-messages)).
  Normalisation already replaces UNTRUSTED-<16hex> with UNTRUSTED-N, but
  belt-and-braces pinning ensures the SYSTEM PROMPT text (which interpolates
  the nonce) is also identical between the recording run and the replay run.
  Without this the system prompt text would differ, producing a different sha256
  key, and the cassette lookup would always miss.

  Scope: function (re-set for every test so the counter resets; avoids
  cross-test pollution where an earlier test consuming N nonces shifts the
  sequence for the next test).

  Only active for tests in tests/qa_harness and tests/test_qa_corpus_replay.py.
  Other tests that import nl_compiler should not be affected because the fixture
  is explicit (autouse=False).
"""

from __future__ import annotations

import itertools
from typing import Generator

import pytest


@pytest.fixture()
def qa_nonce_counter(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Patch _make_fence_nonce() to a deterministic counter for QA replay tests.

    The counter produces 16-hex strings: '0000000000000001',
    '0000000000000002', ...  This keeps the nonce-interpolated system prompt
    identical across record and replay, making cassette key lookup stable.
    """
    counter = itertools.count(1)

    def _deterministic_nonce() -> str:
        return f"{next(counter):016x}"

    # Patch both the canonical location and the re-export in nl_compiler_interactive.
    try:
        import magi_cp.cloud.nl_compiler as _nl
        monkeypatch.setattr(_nl, "_make_fence_nonce", _deterministic_nonce)
    except (ImportError, AttributeError):
        pass

    try:
        import magi_cp.policy.nl_compiler_interactive as _ic
        monkeypatch.setattr(_ic, "_make_fence_nonce", _deterministic_nonce)
    except (ImportError, AttributeError):
        pass

    yield
