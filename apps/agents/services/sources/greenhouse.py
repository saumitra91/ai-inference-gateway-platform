from __future__ import annotations

import logging

import httpx

from apps.agents.metrics import agent_source_fetch_latency_seconds
from apps.agents.services.sources.base import BaseSource, SourceItem, SourceResult

logger = logging.getLogger(__name__)


class GreenhouseSource(BaseSource):
    name = "greenhouse"

    def __init__(self) -> None:
        self._client = httpx.Client(timeout=15.0)

    async def fetch(self, query: str, max_results: int = 25) -> SourceResult:
        items: list[SourceItem] = []
        error: str | None = None
        try:
            board_token = query.strip() if query and "," not in query and len(query.strip().split()) <= 3 and len(query.strip()) < 25 else "nvidia"
            resp = self._client.get(
                f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs",
                params={"content": "true", "per_page": min(max_results, 50)},
            )
            resp.raise_for_status()
            data = resp.json()
            for job in data.get("jobs", [])[:max_results]:
                items.append(
                    SourceItem(
                        title=job.get("title", ""),
                        url=job.get("absolute_url", ""),
                        source=self.name,
                        content=job.get("content", "") or "",
                        metadata={
                            "company": board_token,
                            "location": job.get("offices", [{}])[0].get("name", "")
                            if job.get("offices")
                            else "",
                            "department": job.get("departments", [{}])[0].get("name", "")
                            if job.get("departments")
                            else "",
                            "updated_at": job.get("updated_at", ""),
                        },
                    )
                )
        except httpx.RequestError as exc:
            error = str(exc)
            logger.warning("Greenhouse fetch failed: %s", exc)
        except Exception as exc:
            error = str(exc)
            logger.warning("Greenhouse parse error: %s", exc)

        agent_source_fetch_latency_seconds.labels(source=self.name, status="ok" if not error else "error").observe(0)
        return SourceResult(items=items, source_name=self.name, error=error)
