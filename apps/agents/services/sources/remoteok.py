from __future__ import annotations

import logging

import httpx

from apps.agents.metrics import agent_source_fetch_latency_seconds
from apps.agents.services.sources.base import BaseSource, SourceItem, SourceResult

logger = logging.getLogger(__name__)


class RemoteOKSource(BaseSource):
    name = "remoteok"

    def __init__(self) -> None:
        self._client = httpx.Client(timeout=15.0)

    async def fetch(self, query: str, max_results: int = 25) -> SourceResult:
        items: list[SourceItem] = []
        error: str | None = None
        try:
            params = {}
            if query and len(query.strip()) < 40:
                params["tags"] = query
            resp = self._client.get(
                "https://remoteok.com/api",
                params=params,
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                data = data[1:] if len(data) > 1 and "slug" not in data[0] else data
            for job in data[:max_results]:
                if not isinstance(job, dict):
                    continue
                job_url = job.get("url", "")
                items.append(
                    SourceItem(
                        title=job.get("position", ""),
                        url=job_url or f"https://remoteok.com/remote-jobs/{job.get('slug', '')}",
                        source=self.name,
                        content=job.get("description", "") or "",
                        metadata={
                            "company": job.get("company", ""),
                            "location": job.get("location", ""),
                            "tags": job.get("tags", []),
                            "salary_min": job.get("salary_min"),
                            "salary_max": job.get("salary_max"),
                            "currency": job.get("currency", ""),
                            "date": job.get("date", ""),
                        },
                    )
                )
        except httpx.RequestError as exc:
            error = str(exc)
            logger.warning("RemoteOK fetch failed: %s", exc)
        except Exception as exc:
            error = str(exc)
            logger.warning("RemoteOK parse error: %s", exc)

        agent_source_fetch_latency_seconds.labels(source=self.name, status="ok" if not error else "error").observe(0)
        return SourceResult(items=items, source_name=self.name, error=error)
