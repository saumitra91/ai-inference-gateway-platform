from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ── Backend-specific metrics (for multi-backend comparison) ─────

BACKEND_CHAT_REQUESTS = Counter(
    "inference_backend_chat_requests_total",
    "Chat completion requests by backend",
    labelnames=("backend", "mode"),
)

BACKEND_CHAT_COMPLETIONS_STREAMING = Counter(
    "inference_backend_chat_completions_streaming_total",
    "Streaming chat completions by backend",
    labelnames=("backend",),
)

BACKEND_CHAT_COMPLETIONS_NONSTREAMING = Counter(
    "inference_backend_chat_completions_nonstreaming_total",
    "Non-streaming chat completions by backend",
    labelnames=("backend",),
)

BACKEND_ACTIVE_REQUESTS = Gauge(
    "inference_backend_active_requests",
    "Currently active inference requests by backend",
    labelnames=("backend", "mode"),
)

BACKEND_STREAMING_IN_FLIGHT = Gauge(
    "inference_backend_streaming_in_flight",
    "Active streaming sessions by backend",
    labelnames=("backend",),
)

BACKEND_UPSTREAM_LATENCY_SECONDS = Histogram(
    "inference_backend_upstream_seconds",
    "Upstream wall time by backend",
    labelnames=("backend",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600),
)

BACKEND_TTFT_SECONDS = Histogram(
    "inference_backend_ttft_seconds",
    "TTFT by backend",
    labelnames=("backend",),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)

BACKEND_STREAMING_DURATION_SECONDS = Histogram(
    "inference_backend_streaming_duration_seconds",
    "Streaming session duration by backend",
    labelnames=("backend",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600),
)

BACKEND_STREAM_TOKENS = Counter(
    "inference_backend_tokens_total",
    "Completion tokens by backend",
    labelnames=("backend", "kind"),
)

BACKEND_CHAT_COMPLETION_ERRORS = Counter(
    "inference_backend_chat_completions_errors_total",
    "Chat completion errors by backend",
    labelnames=("backend", "kind"),
)

BACKEND_UPSTREAM_TIMEOUTS = Counter(
    "inference_backend_upstream_timeouts_total",
    "Upstream timeouts by backend",
    labelnames=("backend",),
)

BACKEND_REJECTED_OVERLOAD = Counter(
    "inference_backend_rejected_overload_total",
    "Overload rejections by backend",
    labelnames=("backend",),
)

BACKEND_BYTES_TOTAL = Counter(
    "inference_backend_stream_bytes_total",
    "Streamed bytes by backend",
    labelnames=("backend",),
)

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

VALIDATION_ERRORS = Counter(
    "inference_validation_errors_total",
    "Request validation failures",
    labelnames=("kind",),
)

REJECTED_REQUESTS = Counter(
    "inference_rejected_requests_total",
    "Requests rejected by policy",
    labelnames=("reason",),
)

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

QUEUE_DEPTH = Gauge(
    "inference_queue_depth",
    "Number of requests waiting for a concurrency slot",
)

QUEUE_WAIT_SECONDS = Histogram(
    "inference_queue_wait_seconds",
    "Time requests spend waiting in the concurrency queue before inference",
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 2.5, 5, 10, 30),
)

REJECTED_OVERLOAD = Counter(
    "inference_rejected_overload_total",
    "Requests rejected because the concurrency queue was full",
)

CHAT_COMPLETION_ERRORS = Counter(
    "inference_chat_completions_errors_total",
    "Chat completion handler errors",
    labelnames=("kind",),
)

UPSTREAM_TIMEOUTS = Counter(
    "inference_upstream_timeouts_total",
    "Upstream llama.cpp requests that exceeded the configured timeout",
)

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

STREAM_TOKENS = Counter(
    "inference_tokens_total",
    "Estimated or reported tokens attributed to completions",
    labelnames=("kind",),
)

RATE_LIMIT_EXCEEDED = Counter(
    "inference_rate_limit_exceeded_total",
    "API requests rejected due to per-key RPM limits",
)

QUOTA_EXCEEDED = Counter(
    "inference_quota_exceeded_total",
    "API requests rejected due to per-user daily quotas",
)

# ── Batching ─────────────────────────────────────────────────────

BATCH_DISPATCH_COUNT = Counter(
    "inference_batch_dispatches_total",
    "Number of batch dispatch events (regardless of batch size)",
)

BATCH_SINGLE_COUNT = Counter(
    "inference_batch_single_dispatches_total",
    "Batches that contained only one request (no batching benefit)",
)

BATCH_SIZE = Histogram(
    "inference_batch_size",
    "Number of requests per dispatched batch",
    buckets=(1, 2, 4, 8, 16, 32),
)

BATCH_WAIT_SECONDS = Histogram(
    "inference_batch_wait_seconds",
    "Time requests spend waiting inside the batch barrier before upstream dispatch",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)

BATCH_QUEUE_DEPTH = Gauge(
    "inference_batch_queue_depth",
    "Number of requests currently waiting in the batch barrier",
)

BATCH_EFFICIENCY = Gauge(
    "inference_batch_efficiency",
    "Batching efficiency ratio: (batched_requests - batch_count) / batched_requests. "
    "0 = all single-request batches, approaches 1 as batches grow.",
)
