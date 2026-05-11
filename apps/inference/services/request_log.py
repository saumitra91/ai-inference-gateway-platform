from __future__ import annotations

import logging
from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser

from apps.api_keys.models import APIKey
from apps.inference.models import InferenceRequestLog
from apps.observability.redaction import redact_freeform_text

logger = logging.getLogger(__name__)


@sync_to_async(thread_sensitive=True)
def persist_inference_request_log(
    *,
    request_id: str,
    user: AbstractBaseUser | None,
    api_key: APIKey | None,
    model_name: str,
    stream: bool,
    status_code: int,
    latency_ms: int,
    stream_duration_ms: int | None,
    ttft_ms: int | None,
    prompt_char_length: int,
    prompt_token_estimate: int,
    completion_tokens: int,
    preview: str,
    full_prompt: str,
    error_kind: str,
) -> None:
    store_full = bool(getattr(settings, "DEBUG_LOG_FULL_PROMPTS", False))
    InferenceRequestLog.objects.create(
        request_id=request_id,
        user=user,
        api_key=api_key,
        model_name=model_name,
        stream=stream,
        status_code=status_code,
        latency_ms=latency_ms,
        stream_duration_ms=stream_duration_ms,
        ttft_ms=ttft_ms,
        prompt_char_length=prompt_char_length,
        prompt_token_estimate=prompt_token_estimate,
        completion_tokens=completion_tokens,
        preview=preview[:100],
        full_prompt=full_prompt if store_full else "",
        error_kind=error_kind[:64],
    )


def serialize_messages_for_preview(req_messages: list[Any]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for m in req_messages:
        out.append({"role": m.role, "content": m.content})
    return out


def maybe_debug_full_prompt(req_messages: list[Any]) -> str:
    if not getattr(settings, "DEBUG_LOG_FULL_PROMPTS", False):
        return ""
    parts: list[str] = []
    for m in req_messages:
        parts.append(f"{m.role}: {m.content or ''}")
    return redact_freeform_text("\n".join(parts))[:200_000]
