from __future__ import annotations

import logging

import httpx

from apps.agents.metrics import agent_source_fetch_latency_seconds
from apps.agents.services.sources.base import BaseSource, SourceItem, SourceResult

logger = logging.getLogger(__name__)


class RSSSource(BaseSource):
    name = "rss"

    def __init__(self) -> None:
        self._client = httpx.Client(timeout=15.0, follow_redirects=True)

    async def fetch(self, query: str, max_results: int = 25) -> SourceResult:
        items: list[SourceItem] = []
        error: str | None = None
        feed_urls = [u.strip() for u in query.split(",") if u.strip()] if query else []
        if not feed_urls:
            return SourceResult(items=[], source_name=self.name, error="No RSS URLs provided")

        for feed_url in feed_urls[:5]:
            try:
                resp = self._client.get(feed_url, headers={"Accept": "application/rss+xml, application/atom+xml, text/xml"})
                resp.raise_for_status()

                import xml.etree.ElementTree as ET

                root = ET.fromstring(resp.text)
                ns = {
                    "atom": "http://www.w3.org/2005/Atom",
                    "content": "http://purl.org/rss/1.0/modules/content/",
                    "dc": "http://purl.org/dc/elements/1.1/",
                }

                entries = []
                entries.extend(root.findall(".//atom:entry", ns))
                entries.extend(root.findall(".//item", ns))

                for entry in entries[:max_results]:
                    title = ""
                    url = ""
                    content = ""

                    title_el = entry.find("atom:title", ns) or entry.find("title")
                    if title_el is not None:
                        title = title_el.text or ""

                    link_el = entry.find("atom:link", ns) or entry.find("link")
                    if link_el is not None:
                        url = link_el.get("href", "") or link_el.text or ""

                    content_el = (
                        entry.find("atom:content", ns)
                        or entry.find("content:encoded", ns)
                        or entry.find("description")
                    )
                    if content_el is not None:
                        content = content_el.text or ""

                    import re

                    description_el = entry.find("description")
                    if not content and description_el is not None:
                        content = description_el.text or ""
                    content = re.sub(r"<[^>]+>", "", content)[:2000]

                    if title:
                        items.append(
                            SourceItem(
                                title=title,
                                url=url or feed_url,
                                source=f"{self.name}:{feed_url}",
                                content=content,
                                metadata={"feed_url": feed_url},
                            )
                        )
            except Exception as exc:
                logger.warning("RSS feed %s error: %s", feed_url, exc)
                continue

        agent_source_fetch_latency_seconds.labels(source=self.name, status="ok" if not error else "error").observe(0)
        return SourceResult(items=items, source_name=self.name, error=error)
