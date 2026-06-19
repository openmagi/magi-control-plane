"""Async wrapper for sync LlmProvider implementations.

Both real providers (Anthropic/OpenAI) use httpx.Client which is *sync* —
calling it directly from a FastAPI `async def` route blocks the event loop
for the duration of the LLM call (5–60s typical). That cripples the whole
process under any concurrent load.

`acomplete(provider, messages)` runs `provider.complete(...)` on a thread
pool via `asyncio.to_thread` so the event loop stays responsive. Cheap
fix that lets us keep the simple sync provider impls.
"""
from __future__ import annotations

import asyncio

from .provider import LlmMessage, LlmProvider


async def acomplete(provider: LlmProvider, messages: list[LlmMessage]) -> str:
    """Run a sync LlmProvider.complete() on the default thread pool."""
    return await asyncio.to_thread(provider.complete, messages)


__all__ = ["acomplete"]
