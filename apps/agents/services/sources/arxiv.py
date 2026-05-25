from __future__ import annotations

import logging

import httpx

from apps.agents.metrics import agent_source_fetch_latency_seconds
from apps.agents.services.sources.base import BaseSource, SourceItem, SourceResult

logger = logging.getLogger(__name__)


class ArxivSource(BaseSource):
    name = "arxiv"

    def __init__(self) -> None:
        self._client = httpx.Client(timeout=30.0)

    async def fetch(self, query: str, max_results: int = 25) -> SourceResult:
        items: list[SourceItem] = []
        error: str | None = None
        try:
            resp = self._client.get(
                "https://export.arxiv.org/api/query",
                params={
                    "search_query": f"all:{query}",
                    "start": 0,
                    "max_results": min(max_results, 50),
                    "sortBy": "relevance",
                    "sortOrder": "descending",
                },
                headers={"Accept": "application/atom+xml"},
            )
            resp.raise_for_status()

            import xml.etree.ElementTree as ET

            root = ET.fromstring(resp.text)
            ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
            for entry in root.findall("atom:entry", ns)[:max_results]:
                title = entry.findtext("atom:title", "", ns).replace("\n", " ").strip()
                arxiv_id = entry.findtext("atom:id", "", ns).strip()
                url = f"https://arxiv.org/abs/{arxiv_id.split('/')[-1]}"
                summary = entry.findtext("atom:summary", "", ns).replace("\n", " ").strip()
                published = entry.findtext("atom:published", "", ns)
                authors = [a.findtext("atom:name", "", ns) for a in entry.findall("atom:author", ns)]

                items.append(
                    SourceItem(
                        title=title,
                        url=url,
                        source=self.name,
                        content=summary[:2000],
                        metadata={
                            "arxiv_id": arxiv_id,
                            "published": published,
                            "authors": authors,
                            "summary": summary[:500],
                        },
                    )
                )
        except httpx.RequestError as exc:
            error = str(exc)
            logger.warning("arXiv search failed: %s", exc)
        except Exception as exc:
            error = str(exc)
            logger.warning("arXiv parse error: %s", exc)

        agent_source_fetch_latency_seconds.labels(source=self.name, status="ok" if not error else "error").observe(0)
        return SourceResult(items=items, source_name=self.name, error=error)
