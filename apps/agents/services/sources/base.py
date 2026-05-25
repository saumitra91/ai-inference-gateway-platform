from __future__ import annotations

import abc
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SourceItem:
    title: str
    url: str
    source: str
    content: str = ""
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceResult:
    items: list[SourceItem]
    source_name: str
    fetch_time_ms: float = 0.0
    error: str | None = None


class BaseSource(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    async def fetch(self, query: str, max_results: int = 25) -> SourceResult:
        ...

    async def health(self) -> bool:
        try:
            result = await self.fetch("health", max_results=1)
            return result.error is None
        except Exception:
            return False

    def _timed_fetch(self, query: str, max_results: int) -> SourceResult:
        start = time.monotonic()
        try:
            import asyncio

            result = asyncio.run(self.fetch(query, max_results))
            result.fetch_time_ms = (time.monotonic() - start) * 1000
            return result
        except Exception as exc:
            logger.warning("Source %s fetch error: %s", self.name, exc)
            return SourceResult(
                items=[],
                source_name=self.name,
                fetch_time_ms=(time.monotonic() - start) * 1000,
                error=str(exc),
            )
