from prometheus_client import Counter, Gauge, Histogram

agent_runs_total = Counter(
    "agent_runs_total",
    "Total agent runs",
    labelnames=["agent_type", "agent_name", "status"],
)

agent_failures_total = Counter(
    "agent_failures_total",
    "Total agent run failures",
    labelnames=["agent_type", "agent_name", "error_type"],
)

agent_run_duration_seconds = Histogram(
    "agent_run_duration_seconds",
    "Duration of agent runs",
    labelnames=["agent_type", "agent_name"],
    buckets=[1, 5, 10, 30, 60, 120, 300, 600, 1800],
)

agent_results_discovered_total = Counter(
    "agent_results_discovered_total",
    "Total results discovered by agents",
    labelnames=["agent_type", "agent_name", "source"],
)

agent_results_sent_total = Counter(
    "agent_results_sent_total",
    "Total results sent via digest",
    labelnames=["agent_type", "agent_name"],
)

telegram_notifications_sent_total = Counter(
    "telegram_notifications_sent_total",
    "Total Telegram notifications sent",
    labelnames=["status"],
)

telegram_notification_failures_total = Counter(
    "telegram_notification_failures_total",
    "Total Telegram notification failures",
    labelnames=["error_type"],
)

agent_duplicate_results_filtered_total = Counter(
    "agent_duplicate_results_filtered_total",
    "Total duplicate results filtered out",
    labelnames=["agent_type", "agent_name"],
)

agent_llm_requests_total = Counter(
    "agent_llm_requests_total",
    "Total LLM requests made by agents",
    labelnames=["agent_type", "backend"],
)

agent_embedding_requests_total = Counter(
    "agent_embedding_requests_total",
    "Total embedding requests made by agents",
    labelnames=["agent_type"],
)

agent_source_fetch_latency_seconds = Histogram(
    "agent_source_fetch_latency_seconds",
    "Latency of source fetches",
    labelnames=["source", "status"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)

agent_active_runs = Gauge(
    "agent_active_runs",
    "Number of currently active agent runs",
    labelnames=["agent_type"],
)

agent_scheduler_queue_depth = Gauge(
    "agent_scheduler_queue_depth",
    "Number of pending scheduled agent runs",
)
