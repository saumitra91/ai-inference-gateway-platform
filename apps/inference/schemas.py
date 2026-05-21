"""Pydantic models for OpenAI-compatible chat payloads (validated at the edge, forwarded verbatim-ish upstream)."""

from __future__ import annotations

from typing import Literal

from django.conf import settings
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    name: str | None = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str = Field(default="default", min_length=1, max_length=256)
    messages: list[ChatMessage] = Field(min_length=1)
    stream: bool = False
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    max_tokens: int | None = Field(default=None, ge=1)
    stop: str | list[str] | None = None
    presence_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    frequency_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    user: str | None = Field(default=None, max_length=256)
    backend: str | None = Field(default=None, max_length=32)

    @field_validator("messages")
    @classmethod
    def _validate_messages(cls, messages: list[ChatMessage]) -> list[ChatMessage]:
        if not messages:
            raise ValueError("messages must not be empty")
        valid = False
        for m in messages:
            if m.content and m.content.strip():
                valid = True
                break
        if not valid:
            raise ValueError("messages must contain at least one non-empty message")
        return messages

    def apply_defaults_and_clamp(self) -> None:
        """Apply server-side defaults and clamp values to configured limits.

        Mutates the instance in-place so the original Pydantic validation
        is preserved for logging/debugging while the clamped values are
        what gets sent upstream.
        """
        hard_max = getattr(settings, "INFERENCE_HARD_MAX_TOKENS", 512)
        default_max = getattr(settings, "INFERENCE_DEFAULT_MAX_TOKENS", 128)
        default_temp = float(getattr(settings, "INFERENCE_DEFAULT_TEMPERATURE", 0.7))
        default_top_p = float(getattr(settings, "INFERENCE_DEFAULT_TOP_P", 0.9))

        if self.max_tokens is None:
            self.max_tokens = default_max
        else:
            self.max_tokens = min(self.max_tokens, hard_max)

        if self.temperature is None:
            self.temperature = default_temp
        else:
            self.temperature = max(0.0, min(self.temperature, 2.0))

        if self.top_p is None:
            self.top_p = default_top_p
        else:
            self.top_p = max(0.0, min(self.top_p, 1.0))

    def to_upstream_payload(self) -> dict[str, object]:
        """Serialize to JSON-compatible dict, stripping internal routing fields."""
        payload = self.model_dump(exclude_none=True, mode="json")
        payload.pop("backend", None)
        return payload
