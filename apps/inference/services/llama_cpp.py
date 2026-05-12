"""HTTP integration with llama.cpp's OpenAI-compatible server (`llama-server`).

Explicit timeouts are enforced at the httpx level. Streaming paths use the
configured read timeout; if llama.cpp stops sending bytes for longer than
the timeout period the connection is torn down and a 504 is returned.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
from django.conf import settings

from apps.inference.exceptions import UpstreamHTTPError, UpstreamTimeoutError, UpstreamUnavailableError
from apps.inference.http_client import get_async_client
from apps.inference.schemas import ChatCompletionRequest

logger = logging.getLogger(__name__)


class LlamaCppBackend:
    """Thin streaming proxy to llama.cpp. No model weights are loaded in this process."""

    def __init__(self, base_url: str | None = None) -> None:
        configured = base_url or getattr(settings, "LLAMA_CPP_BASE_URL", None)
        if not configured:
            raise RuntimeError("LLAMA_CPP_BASE_URL is not configured")
        self._base_url = str(configured).rstrip("/")

    def _url(self) -> str:
        return f"{self._base_url}/v1/chat/completions"

    async def stream_chat_completion(
        self,
        request: ChatCompletionRequest,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> AsyncIterator[bytes]:
        payload: dict[str, Any] = request.to_upstream_payload()
        payload["stream"] = True

        client = get_async_client()
        try:
            upstream = client.stream(
                "POST",
                self._url(),
                json=payload,
                headers=self._forward_headers(stream=True, extra_headers=extra_headers),
            )
        except httpx.RequestError as exc:
            logger.warning("upstream_unavailable", extra={"error": str(exc)})
            raise UpstreamUnavailableError(str(exc)) from exc

        try:
            async with upstream as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    raise UpstreamHTTPError(resp.status_code, body)

                async for chunk in resp.aiter_bytes():
                    if chunk:
                        yield chunk
        except httpx.ReadTimeout as exc:
            logger.warning("upstream_timeout", extra={"error": str(exc), "stream": True})
            raise UpstreamTimeoutError() from exc

    async def chat_completion(
        self,
        request: ChatCompletionRequest,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> bytes:
        payload: dict[str, Any] = request.to_upstream_payload()
        payload["stream"] = False

        client = get_async_client()
        resp: httpx.Response | None = None
        for attempt in range(2):
            try:
                resp = await client.post(
                    self._url(),
                    json=payload,
                    headers=self._forward_headers(stream=False, extra_headers=extra_headers),
                )
                break
            except httpx.ReadTimeout as exc:
                logger.warning("upstream_timeout", extra={"error": str(exc), "attempt": attempt})
                raise UpstreamTimeoutError() from exc
            except httpx.RequestError as exc:
                logger.warning("upstream_request_error", extra={"error": str(exc), "attempt": attempt})
                if attempt == 0:
                    await asyncio.sleep(0.05)
                    continue
                raise UpstreamUnavailableError(str(exc)) from exc

        assert resp is not None
        if resp.status_code >= 400:
            raise UpstreamHTTPError(resp.status_code, resp.content)
        return resp.content

    async def list_models(self, *, extra_headers: dict[str, str] | None = None) -> bytes:
        client = get_async_client()
        url = f"{self._base_url}/v1/models"
        headers = {"Accept": "application/json"}
        if extra_headers:
            headers.update({k: v for k, v in extra_headers.items() if v})
        try:
            resp = await client.get(url, headers=headers)
        except httpx.RequestError as exc:
            logger.warning("upstream_unavailable", extra={"error": str(exc)})
            raise UpstreamUnavailableError(str(exc)) from exc
        if resp.status_code >= 400:
            raise UpstreamHTTPError(resp.status_code, resp.content)
        return resp.content

    def _forward_headers(self, *, stream: bool, extra_headers: dict[str, str] | None) -> dict[str, str]:
        headers: dict[str, str] = (
            {"Accept": "text/event-stream"} if stream else {"Accept": "application/json"}
        )
        if extra_headers:
            headers.update({k: v for k, v in extra_headers.items() if v})
        return headers
