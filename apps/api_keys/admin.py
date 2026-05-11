from __future__ import annotations

from django.contrib import admin

from apps.api_keys.models import APIKey, APIKeyAuditLog


@admin.register(APIKey)
class APIKeyAdmin(admin.ModelAdmin):
    list_display = ("public_id", "user", "label", "created_at", "expires_at", "revoked_at", "last_used_at", "rate_limit_rpm")
    list_filter = ("revoked_at",)
    search_fields = ("public_id", "label", "user__username", "user__email")
    readonly_fields = (
        "id",
        "public_id",
        "secret_hash",
        "created_at",
        "last_used_at",
        "requests_count",
        "prompt_tokens_total",
        "completion_tokens_total",
    )


@admin.register(APIKeyAuditLog)
class APIKeyAuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "actor", "api_key_public_id")
    search_fields = ("action", "api_key_public_id", "message")
    readonly_fields = ("id", "created_at", "actor", "action", "api_key", "api_key_public_id", "message")
