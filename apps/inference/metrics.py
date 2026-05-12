"""Prometheus metrics for the inference control plane (not llama.cpp internal timings)."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ── Request lifecycle ──────────────────────────────────────────

CHAT_REQUESTS = Counter(
    "inference_chat_requests_total",
    "Chat completion requests entering the handler",
    labelnames=("mode",),
)

CHAT_COMPLETIONS_STREAMING = Counter(
    "inference_chat_completions_streaming_total",
    "Streaming chat completion requests handled",
)

CHAT_COMPLETIONS_NONSTREAMING = Counter(
    "inference_chat_completions_nonstreaming_total",
    "Non-streaming chat completion requests handled",
)

ACTIVE_INFERENCE_REQUESTS = Gauge(
    "inference_active_requests",
    "Currently active inference requests (streaming + non-streaming)",
    labelnames=("mode",),
)

# ── Validation & rejection ────────────────────────────────────

VALIDATION_ERRORS = Counter(
    "inference_validation_errors_total",
    "Request validation failures",
    labelnames=("kind",),
)

REJECTED_REQUESTS = Counter(
    "inference_rejected_requests_total",
    "Requests rejected by policy (prompt too long, etc.)",
    labelnames=("reason",),
)

# ── Generation controls ───────────────────────────────────────

MAX_TOKENS_REQUESTED = Histogram(
    "inference_max_tokens_requested",
    "Distribution of max_tokens values requested by clients",
    buckets=(1, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192),
)

CLAMPED_REQUESTS = Counter(
    "inference_clamped_requests_total",
    "Requests where a parameter was clamped to a server limit",
    labelnames=("field",),
)

# ── Errors & timeouts ─────────────────────────────────────────

CHAT_COMPLETION_ERRORS = Counter(
    "inference_chat_completions_errors_total",
    "Chat completion handler errors",
    labelnames=("kind",),
)

UPSTREAM_TIMEOUTS = Counter(
    "inference_upstream_timeouts_total",
    "Upstream llama.cpp requests that exceeded the configured timeout",
)

# ── Upstream performance ──────────────────────────────────────

UPSTREAM_LATENCY_SECONDS = Histogram(
    "inference_upstream_wall_seconds",
    "Wall time spent waiting on llama.cpp for a chat completion",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600),
)

TTFT_SECONDS = Histogram(
    "inference_time_to_first_token_seconds",
    "Time from handler start until first upstream byte (streaming) or full response (non-stream)",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)

STREAMING_DURATION_SECONDS = Histogram(
    "inference_streaming_duration_seconds",
    "Wall clock duration of streaming sessions",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600),
)

STREAMING_IN_FLIGHT = Gauge(
    "inference_streaming_requests_in_flight",
    "Currently active streaming upstream sessions",
)

# ── Token accounting ──────────────────────────────────────────

STREAM_TOKENS = Counter(
    "inference_tokens_total",
    "Estimated or reported tokens attributed to completions",
    labelnames=("kind",),
)

# ── Rate limits & quotas ──────────────────────────────────────

RATE_LIMIT_EXCEEDED = Counter(
    "inference_rate_limit_exceeded_total",
    "API requests rejected due to per-key RPM limits",
)

QUOTA_EXCEEDED = Counter(
    "inference_quota_exceeded_total",
    "API requests rejected due to per-user daily quotas",
)
