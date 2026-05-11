from __future__ import annotations

from django.contrib import admin

from apps.inference.models import InferenceRequestLog


@admin.register(InferenceRequestLog)
class InferenceRequestLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "request_id",
        "user",
        "api_key",
        "model_name",
        "status_code",
        "latency_ms",
        "stream",
        "completion_tokens",
    )
    list_filter = ("status_code", "stream")
    search_fields = ("request_id", "model_name", "preview")
    readonly_fields = ("id", "created_at")
