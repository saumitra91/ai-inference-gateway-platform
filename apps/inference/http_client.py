"""Shared httpx async client for upstream inference.

A single client amortizes TLS session setup and connection pooling. For tests, call `reset_client()`.
"""

from __future__ import annotations

import httpx
from django.conf import settings

_client: httpx.AsyncClient | None = None


def get_async_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
        read_s = float(getattr(settings, "INFERENCE_UPSTREAM_TIMEOUT_S", 600.0))
        timeout = httpx.Timeout(connect=5.0, read=read_s, write=min(120.0, read_s), pool=5.0)
        # HTTP/2 is disabled by default: many internal llama.cpp deployments are plain HTTP/1.1.
        _client = httpx.AsyncClient(http2=False, limits=limits, timeout=timeout)
    return _client


async def aclose_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def reset_client() -> None:
    """Sync reset for tests (cannot await aclose in teardown sometimes)."""
    global _client
    _client = None
