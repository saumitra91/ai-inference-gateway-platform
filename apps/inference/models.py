from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class InferenceRequestLog(models.Model):
    """Redacted inference audit trail — not a prompt warehouse."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    request_id = models.CharField(max_length=64, db_index=True)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inference_logs",
    )
    api_key = models.ForeignKey(
        "api_keys.APIKey",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inference_logs",
    )

    model_name = models.CharField(max_length=256)
    stream = models.BooleanField(default=False)

    status_code = models.PositiveIntegerField()
    latency_ms = models.PositiveIntegerField()
    stream_duration_ms = models.PositiveIntegerField(null=True, blank=True)
    ttft_ms = models.PositiveIntegerField(null=True, blank=True)

    prompt_char_length = models.PositiveIntegerField(default=0)
    prompt_token_estimate = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)

    preview = models.CharField(max_length=100, blank=True, default="")

    # Local debugging only — gated by DEBUG_LOG_FULL_PROMPTS.
    full_prompt = models.TextField(blank=True, default="")

    error_kind = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["api_key", "created_at"]),
        ]
