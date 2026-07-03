"""Per-verifier expander descriptors (read-only) (D52b)."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException


def attach(app: FastAPI) -> None:
    """D52b: per-verifier expander descriptors.

    Read-only registry describing each built-in verifier's triggers,
    input payload paths, possible verdicts, and the evidence record it
    emits to the audit ledger. The dashboard ships a byte-stable mirror
    at web/lib/verifier-descriptors.ts; this endpoint exists so third
    party UIs and automated linters can pull the cloud's authoritative
    copy without scraping the Python source.

    Public on purpose. The descriptors describe verifier semantics, not
    tenant data; gating them would force the dashboard's anonymous
    public install flow to wire an API key just to render the Rules tab.
    Rate limit still applies via the global TokenBucketLimiter.
    """
    from ...verifier.descriptors import (
        all_descriptors, field_checks_flat, get_descriptor,
    )

    def _augment_with_flat(d: dict) -> dict:
        """D57e follow-up (P1 wire-format back-compat): emit a
        `field_checks_flat` sibling key alongside the grouped
        `field_checks` dict so third-party consumers that pre-date the
        D57e shape (and iterate `field_checks` as a flat list) keep
        working without code changes during their migration window.

        The grouped shape stays in `field_checks` (new contract). New
        consumers ignore `field_checks_flat`; legacy consumers ignore
        the grouped dict and read the flat list. Both are a single
        Python source of truth via `field_checks_flat()`.
        """
        out = dict(d)
        out["field_checks_flat"] = field_checks_flat(d)
        return out

    def _flat_only(d: dict) -> dict:
        """D57e follow-up (P1): when `?shape=flat` is set, serve the
        pre-D57e shape: `field_checks` is the flat list and the
        grouped `field_checks_flat` sibling is omitted. One-shot
        escape hatch for consumers that cannot yet adopt either the
        sibling key or the grouped shape; documented as deprecated.
        """
        out = dict(d)
        out["field_checks"] = field_checks_flat(d)
        out.pop("field_checks_flat", None)
        return out

    @app.get("/verifier-descriptors")
    def list_verifier_descriptors(shape: str | None = None) -> dict:
        # `shape=flat` collapses `field_checks` back to the pre-D57e
        # flat list for legacy consumers still on the old contract.
        # Default emits the D57e grouped shape AND a `field_checks_flat`
        # sibling so consumers can migrate without breaking.
        if shape == "flat":
            return {"descriptors": [_flat_only(d) for d in all_descriptors()]}
        if shape not in (None, "grouped"):
            raise HTTPException(
                400,
                f"unknown shape {shape!r}; allowed: 'grouped' (default), 'flat'",
            )
        return {"descriptors": [_augment_with_flat(d) for d in all_descriptors()]}

    @app.get("/verifier-descriptors/{step}")
    def get_verifier_descriptor(step: str, shape: str | None = None) -> dict:
        d = get_descriptor(step)
        if d is None:
            raise HTTPException(
                404,
                f"no descriptor for verifier step {step!r}",
            )
        if shape == "flat":
            return _flat_only(d)
        if shape not in (None, "grouped"):
            raise HTTPException(
                400,
                f"unknown shape {shape!r}; allowed: 'grouped' (default), 'flat'",
            )
        return _augment_with_flat(d)
