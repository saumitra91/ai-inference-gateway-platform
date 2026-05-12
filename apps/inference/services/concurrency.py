"""Per-process concurrency limiter for inference requests.

Uses an asyncio.Semaphore to cap the number of simultaneous upstream calls.
When all slots are occupied new requests are queued (up to a configurable depth)
or rejected with a 503.

Single-worker correctness: the semaphore is per-process, which is correct for
single-worker ASGI deployments. Multi-worker deployments should either pin the
worker count to 1 or replace this with a distributed semaphore (Redis-based).
"""

from __future__ import annotations

import asyncio
import logging
import time

from django.conf import settings

from apps.inference.metrics import QUEUE_DEPTH, QUEUE_WAIT_SECONDS, REJECTED_OVERLOAD

logger = logging.getLogger(__name__)

_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        max_conc = getattr(settings, "INFERENCE_MAX_CONCURRENCY", 4)
        _semaphore = asyncio.Semaphore(max_conc)
    return _semaphore


async def acquire(request_id: str) -> float | None:
    """Try to acquire a concurrency slot.

    Returns the wait time in seconds if a slot was acquired, or None if the
    request should be rejected with a 503 (queue is full).
    """
    sem = _get_semaphore()
    queue_size = getattr(settings, "INFERENCE_QUEUE_SIZE", 10)

    if sem.locked():
        qd = QUEUE_DEPTH._value.get() + 1 if hasattr(QUEUE_DEPTH, "_value") else 1
        QUEUE_DEPTH.inc()
        if qd >= queue_size:
            QUEUE_DEPTH.dec()
            REJECTED_OVERLOAD.inc()
            logger.warning("overload_rejected", extra={
                "request_id": request_id,
                "queue_depth": int(qd),
                "max_queue": queue_size,
            })
            return None
        queue_timeout = getattr(settings, "INFERENCE_QUEUE_TIMEOUT_S", 30.0)
        try:
            wait_start = time.perf_counter()
            await asyncio.wait_for(sem.acquire(), timeout=queue_timeout)
            wait_time = time.perf_counter() - wait_start
        except TimeoutError:
            QUEUE_DEPTH.dec()
            REJECTED_OVERLOAD.inc()
            logger.warning("queue_timeout", extra={
                "request_id": request_id,
                "queue_timeout_s": queue_timeout,
            })
            return None
        QUEUE_DEPTH.dec()
        QUEUE_WAIT_SECONDS.observe(wait_time)
        logger.info("queue_acquired", extra={
            "request_id": request_id,
            "wait_time_ms": int(wait_time * 1000),
        })
        return wait_time
    else:
        await sem.acquire()
        return 0.0


def release() -> None:
    """Release a concurrency slot. Must be called in a finally block."""
    sem = _get_semaphore()
    sem.release()
