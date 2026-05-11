"""Inference backend protocol: Django stays a control plane; backends speak HTTP to runtimes."""

from __future__ import annotations

from typing import AsyncIterator, Protocol

from apps.inference.schemas import ChatCompletionRequest


class InferenceBackend(Protocol):
    async def stream_chat_completion(self, request: ChatCompletionRequest) -> AsyncIterator[bytes]:
        """Yield raw upstream bytes (typically SSE chunks)."""

    async def chat_completion(self, request: ChatCompletionRequest) -> bytes:
        """Return full upstream JSON body for non-streaming completions."""
