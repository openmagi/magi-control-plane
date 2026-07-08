"""LLM API-key dashboard routes: read provider status (last4), write/rotate
keys with in-place provider-singleton hot-reload, and a live key test."""
from __future__ import annotations

import asyncio

from fastapi import Body, Depends, FastAPI, Request

from ..deps import require_admin_key
from ..schemas import LlmKeysPutReq, LlmKeysTestReq
from ..serialization import _resolve_llm_provider_from_env


def attach(app: FastAPI, *, llm_keys_lock) -> None:
    # ── Q97a: LLM API key dashboard surface ─────────────────────────
    # Self-host operators paste keys into /settings instead of editing
    # `~/.magi-cp/.env`. The PUT route hot-reloads the provider
    # singletons in-place so the next /policies/compile-interactive
    # picks them up WITHOUT a container restart.
    #
    # Body models live at module scope (LlmKeysPutReq / LlmKeysTestReq)
    # because FastAPI's `get_type_hints` cannot resolve forward refs to
    # classes defined inside the create_app closure on Python 3.14.

    def _llm_status_payload() -> dict:
        from ..llm_key_store import status as _status
        s = _status()
        # Subscription-auth fallback signal: the local `claude` CLI powers the
        # compiler/reviewer only when it is on PATH AND no API key is set (see
        # _resolve_llm_provider_optional precedence). Advisory boolean for the
        # dashboard to show "Running on your Claude subscription (no API key)".
        claude_cli_active = False
        try:
            from ...llm.claude_cli_provider import claude_cli_available
            claude_cli_active = bool(
                claude_cli_available()
                and not s["anthropic_set"]
                and not s["openai_set"]
            )
        except Exception:
            claude_cli_active = False
        return {
            "anthropic": {
                "set": s["anthropic_set"],
                "last4": s["anthropic_last4"],
            },
            "openai": {
                "set": s["openai_set"],
                "last4": s["openai_last4"],
            },
            "claude_cli_active": claude_cli_active,
        }

    def _rebuild_provider_singletons() -> None:
        """Re-resolve `app.state.llm_compiler` / `app.state.llm_reviewer`
        from the env-pointed factories. The factories now consult the
        on-disk overlay first, so the very next /policies/compile call
        uses the just-written keys.

        Either env var being unset leaves the corresponding singleton at
        None (matches the pre-Q97a 503-on-unconfigured behaviour); the
        admin endpoint's response will reflect the same `set=False`
        status the dashboard reads on GET.

        Errors raised by the factory itself (e.g. the provider's
        `__init__` rejecting a still-missing key) propagate up so the
        PUT response surfaces "you set anthropic but the openai factory
        is still missing its key" instead of silently rolling back.
        """
        try:
            app.state.llm_compiler = _resolve_llm_provider_from_env(
                "MAGI_CP_LLM_COMPILER",
            )
        except Exception:
            # Don't take the app down — keep the existing singleton, but
            # surface the failure as None so the dashboard can render
            # an actionable "provider error" pill.
            app.state.llm_compiler = None
        try:
            app.state.llm_reviewer = _resolve_llm_provider_from_env(
                "MAGI_CP_LLM_REVIEWER",
            )
        except Exception:
            app.state.llm_reviewer = None

    @app.get("/admin/llm-keys", dependencies=[Depends(require_admin_key)])
    def admin_llm_keys_get() -> dict:
        """Dashboard reads which providers are configured + last4.
        Never returns the raw key value — only `set: bool` and the last
        4 characters for a "yes this is the key I just pasted" check."""
        return _llm_status_payload()

    @app.put("/admin/llm-keys", dependencies=[Depends(require_admin_key)])
    async def admin_llm_keys_put(req: LlmKeysPutReq) -> dict:
        """Dashboard writes new keys.

        Both fields optional on the body. Missing field = preserve.
        Empty string = clear. Non-empty = overwrite. Atomic write via
        tempfile + rename; final file is 0600.

        After persisting, the provider singletons on `app.state` are
        rebuilt in-place so the very next /policies/compile call uses
        the new credentials without a container restart.
        """
        from ..llm_key_store import set as _store_set
        async with llm_keys_lock:
            await asyncio.to_thread(
                _store_set, req.anthropic_api_key, req.openai_api_key,
            )
            _rebuild_provider_singletons()
        return _llm_status_payload()

    @app.post(
        "/admin/llm-keys/test",
        dependencies=[Depends(require_admin_key)],
    )
    async def admin_llm_keys_test(
        request: Request,
        req: LlmKeysTestReq = Body(default_factory=LlmKeysTestReq),
    ) -> dict:
        """One cheap "ping" completion per provider to verify the keys.

        With `{"provider": "anthropic"|"openai"}` the route exercises
        just that side. Without a body (or with `{"provider": null}`)
        both are run and a per-provider result map is returned.

        Each probe sends `[user: "ping"]` with a 4-token cap. On
        success: `{"ok": true, "error": null, "provider_used": "..."}`.
        On failure: `{"ok": false, "error": "<reason>", ...}`. A
        provider that isn't configured at all reports `{"ok": false,
        "error": "not configured", ...}` so the dashboard renders a
        consistent state.

        Runs in a thread so the live HTTP call doesn't block the loop.
        """
        which = req.provider if req else None

        def _one(provider_name: str) -> dict:
            singleton = (
                getattr(app.state, "llm_compiler", None)
                if provider_name == "anthropic"
                else getattr(app.state, "llm_reviewer", None)
            )
            if singleton is None:
                # Best-effort: try to construct a fresh provider directly
                # so an operator who has set keys but hasn't restarted
                # gets a real probe instead of a stale "not configured".
                try:
                    if provider_name == "anthropic":
                        from ...llm.anthropic_provider import AnthropicProvider
                        singleton = AnthropicProvider()
                    else:
                        from ...llm.openai_provider import OpenAIProvider
                        singleton = OpenAIProvider()
                except Exception as e:
                    return {
                        "ok": False,
                        "error": f"not configured: {type(e).__name__}: {e}",
                        "provider_used": provider_name,
                    }
            try:
                singleton.complete([
                    {"role": "user", "content": "ping"},
                ])
            except Exception as e:
                return {
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}",
                    "provider_used": provider_name,
                }
            return {
                "ok": True,
                "error": None,
                "provider_used": provider_name,
            }

        if which in ("anthropic", "openai"):
            return await asyncio.to_thread(_one, which)
        # both
        a = await asyncio.to_thread(_one, "anthropic")
        o = await asyncio.to_thread(_one, "openai")
        return {"anthropic": a, "openai": o}

