from __future__ import annotations

import logging

import httpx

from apps.agents.metrics import agent_source_fetch_latency_seconds
from apps.agents.services.sources.base import BaseSource, SourceItem, SourceResult

logger = logging.getLogger(__name__)


class RedditSource(BaseSource):
    name = "reddit"

    def __init__(self) -> None:
        self._client = httpx.Client(timeout=15.0, headers={"User-Agent": "ai-platform-agent/1.0"})

    async def fetch(self, query: str, max_results: int = 25) -> SourceResult:
        items: list[SourceItem] = []
        error: str | None = None
        try:
            resp = self._client.get(
                "https://www.reddit.com/search.json",
                params={"q": query, "limit": min(max_results, 50), "sort": "relevance"},
            )
            resp.raise_for_status()
            data = resp.json()
            for child in data.get("data", {}).get("children", [])[:max_results]:
                d = child.get("data", {})
                items.append(
                    SourceItem(
                        title=d.get("title", ""),
                        url=f"https://www.reddit.com{d.get('permalink', '')}",
                        source=self.name,
                        content=d.get("selftext", "") or "",
                        metadata={
                            "subreddit": d.get("subreddit", ""),
                            "score": d.get("score", 0),
                            "author": d.get("author", ""),
                            "num_comments": d.get("num_comments", 0),
                            "created_utc": d.get("created_utc", 0),
                        },
                    )
                )
        except httpx.RequestError as exc:
            error = str(exc)
            logger.warning("Reddit search failed: %s", exc)
        except Exception as exc:
            error = str(exc)
            logger.warning("Reddit parse error: %s", exc)

        agent_source_fetch_latency_seconds.labels(source=self.name, status="ok" if not error else "error").observe(0)
        return SourceResult(items=items, source_name=self.name, error=error)
