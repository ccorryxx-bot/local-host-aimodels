"""Thin async client for llama-server's OpenAI-compatible streaming endpoint.

Kept separate from bot.py so the Telegram plumbing and the model plumbing can
change independently -- e.g. swapping llama-server for something else later
only touches this file.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx


class LlamaError(Exception):
    """Raised when llama-server returns a non-2xx response or a malformed stream."""


async def stream_chat(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    api_key: str,
    messages: list[dict[str, str]],
    max_tokens: int = 800,
    temperature: float = 0.7,
) -> AsyncIterator[str]:
    """Yield text deltas from a streaming chat completion.

    Raises LlamaError on a non-2xx response or a response stream that never
    produces a single valid delta (e.g. server crashed mid-stream).
    """
    payload = {
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    got_any_delta = False
    async with client.stream(
        "POST", f"{base_url}/v1/chat/completions", json=payload, headers=headers
    ) as resp:
        if resp.status_code != 200:
            body = (await resp.aread()).decode(errors="replace")[:500]
            raise LlamaError(f"llama-server {resp.status_code}: {body}")

        async for line in resp.aiter_lines():
            line = line.strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
                delta = chunk["choices"][0]["delta"].get("content")
            except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                # Skip anything we don't recognise (e.g. empty keep-alive lines)
                # rather than killing the whole reply over one bad chunk.
                continue
            if delta:
                got_any_delta = True
                yield delta

    if not got_any_delta:
        raise LlamaError("empty stream: no content received from llama-server")


async def health(client: httpx.AsyncClient, *, base_url: str) -> bool:
    """True if llama-server responds OK on /health."""
    try:
        resp = await client.get(f"{base_url}/health", timeout=5)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False
