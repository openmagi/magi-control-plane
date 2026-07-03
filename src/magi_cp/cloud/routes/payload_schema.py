"""CC hook payload schema menu (read-only) at /payload-schemas (P7)."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException


def attach(app: FastAPI) -> None:
    """P7: CC hook payload schema menu.

    Read-only registry of what fields each (event, matcher_class) pair
    delivers on the gate's stdin. The wizard's regex / llm_critic /
    shacl steps render these as suggestion chips so authors stop
    guessing the payload shape — a SHACL shape that targets a
    non-existent field is "vacuously satisfied" (zero focus nodes →
    conforms), so a mis-typed path silently fails open at gate time.

    Public on purpose: this is reference data, not a tenant resource.
    The schema content is identical for every caller; no auth needed.
    Rate limit still applies via the global TokenBucketLimiter.
    """
    from ...policy.payload_schemas import (
        PAYLOAD_SCHEMAS_BY_EVENT, all_schemas, available_fields,
    )

    @app.get("/payload-schemas")
    def list_payload_schemas() -> dict:
        return {"schemas": all_schemas()}

    @app.get("/payload-schemas/{event}")
    def get_payload_schema(event: str, matcher: str | None = None) -> dict:
        if event not in PAYLOAD_SCHEMAS_BY_EVENT:
            raise HTTPException(
                404,
                f"no payload schema for event {event!r}; "
                f"known: {sorted(PAYLOAD_SCHEMAS_BY_EVENT.keys())}",
            )
        fields = available_fields(event, matcher)
        return {"event": event, "matcher": matcher, "fields": fields}
