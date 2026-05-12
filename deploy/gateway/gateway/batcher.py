"""Request batching barrier for coordinated upstream dispatch.

Strategy
--------
Multiple concurrent /v1/chat/completions requests arriving within a small
time window are held briefly so they can be dispatched *simultaneously* to
llama.cpp.  llama.cpp's server-side slot mechanism then batches the prompt-
processing (prefill) phase across requests, which is where the throughput
gain comes from.  Each request still gets its own independent SSE stream.

This is NOT traditional request-fusion batching (merging HTTP bodies).  We
coordinate *dispatch timing* so the upstream server can optimise its own
internal batching.

Tradeoffs
---------
* TTFT increases by up to ``batch_window_ms`` (the batching delay).
* Throughput improves under concurrency (better prompt-processing batching).
* Under light load (single request per window) there is no batching benefit
  and only a latency penalty — the window timeout fires with a batch of 1.
  This is acceptable because the window is intentionally small (default 50ms).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from prometheus_client import Gauge

logger = logging.getLogger("gateway")

MetricsHook = Callable[[int, list[float]], None]


class BatchBarrier:
    """Coordination barrier that groups concurrent requests into batches.

    Each caller calls ``wait()`` and is released when either:

    * the accumulated request count reaches ``max_batch_size``, **or**
    * the batching window has elapsed since the *first* request in the batch.

    All waiters are released at the same moment so that their upstream HTTP
    calls arrive concurrently at llama.cpp.
    """

    def __init__(
        self,
        window_ms: float,
        max_batch_size: int,
        *,
        on_flush: MetricsHook | None = None,
        queue_depth_gauge: Gauge | None = None,
    ) -> None:
        self._window_s = window_ms / 1000.0
        self._max_batch_size = max_batch_size
        self._pending: list[tuple[asyncio.Event, float]] = []
        self._lock = asyncio.Lock()
        self._timer_gen = 0
        self._on_flush = on_flush
        self._qd_gauge = queue_depth_gauge

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    async def wait(self) -> float:
        """Block until the next batch dispatch.

        Returns the time (seconds) spent waiting inside the barrier.
        """
        event = asyncio.Event()
        t0 = time.monotonic()

        async with self._lock:
            self._pending.append((event, t0))
            if self._qd_gauge is not None:
                self._qd_gauge.set(len(self._pending))
            batch_size = len(self._pending)
            if batch_size == 1:
                self._timer_gen += 1
                my_gen = self._timer_gen
                asyncio.create_task(self._timer_flush(my_gen))

        if batch_size >= self._max_batch_size:
            asyncio.create_task(self._flush())

        await event.wait()
        return time.monotonic() - t0

    async def _timer_flush(self, gen: int) -> None:
        await asyncio.sleep(self._window_s)
        async with self._lock:
            if gen != self._timer_gen:
                return
        await self._flush()

    async def _flush(self) -> None:
        batch: list[tuple[asyncio.Event, float]] | None = None
        async with self._lock:
            if not self._pending:
                return
            batch = list(self._pending)
            self._pending.clear()
            if self._qd_gauge is not None:
                self._qd_gauge.set(0)

        if batch:
            wait_times = [time.monotonic() - t for _, t in batch]
            if self._on_flush:
                self._on_flush(len(batch), wait_times)
            for evt, _ in batch:
                evt.set()
