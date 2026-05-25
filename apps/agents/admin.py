from django.contrib import admin

from .models import Agent, AgentResult, AgentRun, TelegramConfig


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = ["name", "type", "enabled", "digest_frequency", "last_run_at", "created_at"]
    list_filter = ["type", "enabled", "digest_frequency"]
    search_fields = ["name", "slug", "search_query"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["id", "created_at", "updated_at", "last_run_at"]


@admin.register(AgentRun)
class AgentRunAdmin(admin.ModelAdmin):
    list_display = ["agent", "status", "started_at", "duration_ms", "discovered_count", "sent_count"]
    list_filter = ["status", "started_at"]
    search_fields = ["agent__name"]
    readonly_fields = ["id", "started_at", "completed_at"]


@admin.register(AgentResult)
class AgentResultAdmin(admin.ModelAdmin):
    list_display = ["title", "agent", "source", "match_score", "created_at"]
    list_filter = ["source", "created_at"]
    search_fields = ["title", "url", "content"]
    readonly_fields = ["id", "created_at"]


@admin.register(TelegramConfig)
class TelegramConfigAdmin(admin.ModelAdmin):
    list_display = ["enabled", "chat_id", "digest_enabled", "digest_schedule", "created_at"]
