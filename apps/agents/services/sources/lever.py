from __future__ import annotations

import logging

import httpx

from apps.agents.metrics import agent_source_fetch_latency_seconds
from apps.agents.services.sources.base import BaseSource, SourceItem, SourceResult

logger = logging.getLogger(__name__)


class LeverSource(BaseSource):
    name = "lever"

    def __init__(self) -> None:
        self._client = httpx.Client(timeout=15.0)

    async def fetch(self, query: str, max_results: int = 25) -> SourceResult:
        items: list[SourceItem] = []
        error: str | None = None
        try:
            board_name = query.strip() if query else "nvidia"
            resp = self._client.get(
                f"https://api.lever.co/v0/postings/{board_name}",
                params={"mode": "json"},
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                data = data.get("data", [])
            for job in data[:max_results]:
                if not isinstance(job, dict):
                    continue
                items.append(
                    SourceItem(
                        title=job.get("text", job.get("title", "")),
                        url=job.get("hostedUrl", job.get("url", "")),
                        source=self.name,
                        content=job.get("descriptionText", job.get("description", "")) or "",
                        metadata={
                            "company": board_name,
                            "location": job.get("categories", {}).get("location", "")
                            if isinstance(job.get("categories"), dict)
                            else "",
                            "commitment": job.get("categories", {}).get("commitment", "")
                            if isinstance(job.get("categories"), dict)
                            else "",
                            "team": job.get("categories", {}).get("team", "")
                            if isinstance(job.get("categories"), dict)
                            else "",
                            "created_at": job.get("createdAt", ""),
                        },
                    )
                )
        except httpx.RequestError as exc:
            error = str(exc)
            logger.warning("Lever fetch failed: %s", exc)
        except Exception as exc:
            error = str(exc)
            logger.warning("Lever parse error: %s", exc)

        agent_source_fetch_latency_seconds.labels(source=self.name, status="ok" if not error else "error").observe(0)
        return SourceResult(items=items, source_name=self.name, error=error)
