from __future__ import annotations

import uuid

from django.db import models


class Agent(models.Model):
    class Type(models.TextChoices):
        MARKET_RESEARCH = "market_research", "Market Research"
        JOB_DISCOVERY = "job_discovery", "Job Discovery"

    class DigestFrequency(models.TextChoices):
        DISABLED = "disabled", "Disabled"
        REAL_TIME = "real_time", "Real Time"
        HOURLY = "hourly", "Hourly"
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"

    class BackendPreference(models.TextChoices):
        LLAMACPP = "llamacpp", "llama.cpp"
        VLLM = "vllm", "vLLM"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    type = models.CharField(max_length=50, choices=Type.choices)
    enabled = models.BooleanField(default=True)
    instructions = models.TextField(blank=True, default="")
    search_query = models.CharField(max_length=1024, blank=True, default="")
    schedule_cron = models.CharField(max_length=255, blank=True, default="")
    digest_frequency = models.CharField(
        max_length=20, choices=DigestFrequency.choices, default=DigestFrequency.DISABLED
    )
    llm_backend_preference = models.CharField(
        max_length=20, choices=BackendPreference.choices, default=BackendPreference.LLAMACPP
    )
    sources = models.JSONField(default=list, blank=True)
    max_results = models.IntegerField(default=25)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_run_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Agent"
        verbose_name_plural = "Agents"

    def __str__(self) -> str:
        return f"{self.name} ({self.get_type_display()})"


class AgentRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        TIMEOUT = "timeout", "Timeout"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="runs")
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    duration_ms = models.IntegerField(null=True, blank=True)
    tokens_used = models.IntegerField(default=0)
    prompt_tokens = models.IntegerField(default=0)
    completion_tokens = models.IntegerField(default=0)
    discovered_count = models.IntegerField(default=0)
    sent_count = models.IntegerField(default=0)
    summary = models.TextField(blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    raw_logs = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-started_at"]
        verbose_name = "Agent Run"
        verbose_name_plural = "Agent Runs"

    def __str__(self) -> str:
        return f"{self.agent.name} @ {self.started_at.isoformat()} [{self.status}]"


class AgentResult(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="results")
    run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="results")
    title = models.CharField(max_length=1024, blank=True, default="")
    url = models.URLField(max_length=2048, blank=True, default="")
    source = models.CharField(max_length=255, blank=True, default="")
    content = models.TextField(blank=True, default="")
    summary = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    semantic_hash = models.CharField(max_length=64, blank=True, default="")
    similarity_score = models.FloatField(null=True, blank=True)
    match_score = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Agent Result"
        verbose_name_plural = "Agent Results"
        indexes = [
            models.Index(fields=["agent", "-created_at"]),
            models.Index(fields=["semantic_hash"]),
        ]

    def __str__(self) -> str:
        return f"{self.title[:60]} ({self.source})"


class TelegramConfig(models.Model):
    enabled = models.BooleanField(default=False)
    bot_token = models.CharField(max_length=512, blank=True, default="")
    chat_id = models.CharField(max_length=128, blank=True, default="")
    digest_enabled = models.BooleanField(default=True)
    digest_schedule = models.CharField(max_length=20, default="daily")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Telegram Configuration"
        verbose_name_plural = "Telegram Configurations"

    def __str__(self) -> str:
        return f"Telegram {'enabled' if self.enabled else 'disabled'}"
