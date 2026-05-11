"""Prometheus metrics for the inference control plane (not llama.cpp internal timings)."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

CHAT_COMPLETIONS_STREAMING = Counter(
    "inference_chat_completions_streaming_total",
    "Streaming chat completion requests handled",
)

CHAT_COMPLETIONS_NONSTREAMING = Counter(
    "inference_chat_completions_nonstreaming_total",
    "Non-streaming chat completion requests handled",
)

CHAT_COMPLETION_ERRORS = Counter(
    "inference_chat_completions_errors_total",
    "Chat completion handler errors",
    labelnames=("kind",),
)

UPSTREAM_LATENCY_SECONDS = Histogram(
    "inference_upstream_wall_seconds",
    "Wall time spent waiting on llama.cpp for a chat completion",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600),
)

STREAMING_IN_FLIGHT = Gauge(
    "inference_streaming_requests_in_flight",
    "Currently active streaming upstream sessions",
)

RATE_LIMIT_EXCEEDED = Counter(
    "inference_rate_limit_exceeded_total",
    "API requests rejected due to per-key RPM limits",
)

QUOTA_EXCEEDED = Counter(
    "inference_quota_exceeded_total",
    "API requests rejected due to per-user daily quotas",
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

STREAM_TOKENS = Counter(
    "inference_tokens_total",
    "Estimated or reported tokens attributed to completions",
    labelnames=("kind",),
)

CHAT_REQUESTS = Counter(
    "inference_chat_requests_total",
    "Chat completion requests entering the handler",
    labelnames=("mode",),
)
