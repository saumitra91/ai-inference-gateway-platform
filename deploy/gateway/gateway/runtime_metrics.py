from __future__ import annotations

import logging
import os
import time

from prometheus_client import Gauge

log = logging.getLogger("gateway")

_START_WALL = time.time()

PROCESS_UPTIME_SECONDS = Gauge(
    "gateway_process_uptime_seconds",
    "Approximate gateway process uptime (wall clock)",
)

PROCESS_RESIDENT_MEMORY_BYTES = Gauge(
    "gateway_process_resident_memory_bytes",
    "Resident set size for the gateway process (best-effort via psutil)",
)

PROCESS_CPU_PERCENT = Gauge(
    "gateway_process_cpu_percent",
    "CPU percent for the gateway process (best-effort; psutil non-blocking sample)",
)


def collect_runtime_metrics() -> None:
    PROCESS_UPTIME_SECONDS.set(max(0.0, time.time() - _START_WALL))
    try:
        import psutil

        proc = psutil.Process(os.getpid())
        PROCESS_RESIDENT_MEMORY_BYTES.set(float(proc.memory_info().rss))
        PROCESS_CPU_PERCENT.set(float(proc.cpu_percent(interval=None)))
    except Exception:
        log.debug("runtime_metrics_psutil_unavailable", exc_info=True)
        PROCESS_RESIDENT_MEMORY_BYTES.set(0.0)
        PROCESS_CPU_PERCENT.set(0.0)
