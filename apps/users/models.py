from __future__ import annotations

from django.conf import settings
from django.db import models


class UserProfile(models.Model):
    """Per-user quotas and optional default API key for browser UI inference."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")

    daily_request_limit = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="If set, caps successful inference requests per UTC day (session UI path).",
    )
    daily_token_limit = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="If set, caps completion-token estimates per UTC day (session UI path).",
    )

    default_api_key = models.ForeignKey(
        "api_keys.APIKey",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="default_for_profiles",
    )

    def __str__(self) -> str:
        return f"Profile<{self.user_id}>"
