from __future__ import annotations

import asyncio
import logging
import time

from gateway.config import Settings
from gateway.metrics import QUEUE_DEPTH, QUEUE_WAIT_SECONDS, REJECTED_OVERLOAD

log = logging.getLogger("gateway")

_semaphore: asyncio.Semaphore | None = None
_queue_count: int = 0


def _get_semaphore(settings: Settings) -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(settings.inference_max_concurrency)
    return _semaphore


async def acquire_slot(*, request_id: str, settings: Settings) -> float | None:
    global _queue_count
    sem = _get_semaphore(settings)

    if sem.locked():
        _queue_count += 1
        QUEUE_DEPTH.set(_queue_count)
        if _queue_count > settings.inference_queue_size:
            _queue_count -= 1
            QUEUE_DEPTH.set(_queue_count)
            REJECTED_OVERLOAD.inc()
            log.warning("level=warn event=overload_rejected request_id=%s queue_depth=%d max_queue=%d",
                        request_id, _queue_count, settings.inference_queue_size)
            return None
        try:
            wait_start = time.perf_counter()
            await asyncio.wait_for(sem.acquire(), timeout=settings.inference_queue_timeout_s)
            wait_time = time.perf_counter() - wait_start
        except TimeoutError:
            _queue_count -= 1
            QUEUE_DEPTH.set(_queue_count)
            REJECTED_OVERLOAD.inc()
            log.warning("level=warn event=queue_timeout request_id=%s timeout_s=%s",
                        request_id, settings.inference_queue_timeout_s)
            return None
        _queue_count -= 1
        QUEUE_DEPTH.set(_queue_count)
        QUEUE_WAIT_SECONDS.observe(wait_time)
        log.debug("level=debug event=queue_acquired request_id=%s wait_time_ms=%d",
                  request_id, int(wait_time * 1000))
        return wait_time
    else:
        await sem.acquire()
        QUEUE_DEPTH.set(0)
        return 0.0


def release_slot(settings: Settings) -> None:
    sem = _get_semaphore(settings)
    sem.release()
