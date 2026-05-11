from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class APIKey(models.Model):
    """Programmatic credential. Raw secret is never persisted — only an HMAC digest."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_keys")

    # Public handle embedded in the key after `sk_local_` for indexed lookup.
    public_id = models.CharField(max_length=16, unique=True, db_index=True)

    # HMAC-SHA256 hex digest of the full raw key, keyed with server pepper.
    secret_hash = models.CharField(max_length=64)

    label = models.CharField(max_length=120, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    last_used_at = models.DateTimeField(null=True, blank=True)

    rate_limit_rpm = models.PositiveIntegerField(default=120)

    requests_count = models.BigIntegerField(default=0)
    prompt_tokens_total = models.BigIntegerField(default=0)
    completion_tokens_total = models.BigIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=["user", "revoked_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.label or 'key'} ({self.public_id})"


class APIKeyAuditLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    action = models.CharField(max_length=64, db_index=True)
    api_key = models.ForeignKey(APIKey, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    api_key_public_id = models.CharField(max_length=16, blank=True, default="")

    message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["created_at"])]
