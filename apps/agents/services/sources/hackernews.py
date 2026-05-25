from __future__ import annotations

import logging

import httpx

from apps.agents.metrics import agent_source_fetch_latency_seconds
from apps.agents.services.sources.base import BaseSource, SourceItem, SourceResult

logger = logging.getLogger(__name__)


class HackerNewsSource(BaseSource):
    name = "hackernews"

    def __init__(self) -> None:
        self._client = httpx.Client(timeout=15.0)

    async def fetch(self, query: str, max_results: int = 25) -> SourceResult:
        items: list[SourceItem] = []
        error: str | None = None
        try:
            resp = self._client.get(
                "https://hn.algolia.com/api/v1/search",
                params={"query": query, "tags": "story", "hitsPerPage": min(max_results, 50)},
            )
            resp.raise_for_status()
            data = resp.json()
            for hit in data.get("hits", [])[:max_results]:
                url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
                items.append(
                    SourceItem(
                        title=hit.get("title", ""),
                        url=url,
                        source=self.name,
                        content=hit.get("story_text", "") or "",
                        metadata={
                            "points": hit.get("points", 0),
                            "author": hit.get("author", ""),
                            "object_id": hit.get("objectID", ""),
                            "created_at": hit.get("created_at", ""),
                        },
                    )
                )
        except httpx.RequestError as exc:
            error = str(exc)
            logger.warning("HN search failed: %s", exc)
        except Exception as exc:
            error = str(exc)
            logger.warning("HN parse error: %s", exc)

        agent_source_fetch_latency_seconds.labels(source=self.name, status="ok" if not error else "error").observe(0)
        return SourceResult(items=items, source_name=self.name, error=error)
