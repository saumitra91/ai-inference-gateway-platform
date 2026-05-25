from __future__ import annotations

import logging
import re

import httpx

from apps.agents.metrics import agent_source_fetch_latency_seconds
from apps.agents.services.sources.base import BaseSource, SourceItem, SourceResult

logger = logging.getLogger(__name__)


class GitHubTrendingSource(BaseSource):
    name = "github_trending"

    def __init__(self) -> None:
        self._client = httpx.Client(timeout=15.0, follow_redirects=True)

    async def fetch(self, query: str, max_results: int = 25) -> SourceResult:
        items: list[SourceItem] = []
        error: str | None = None
        try:
            language = query.strip() if query else ""
            url = "https://github.com/trending"
            if language:
                url += f"/{language}"
            resp = self._client.get(url, headers={"Accept": "text/html"})
            resp.raise_for_status()

            html = resp.text
            repos = re.findall(
                r'href="/repositories\?q=([^"]+)"[^>]*>([^<]+)</a>.*?<p[^>]*class="[^"]*col-9[^"]*"[^>]*>([^<]*)</p>',
                html,
                re.DOTALL,
            )
            if not repos:
                repos = re.findall(
                    r'<h2[^>]*class="[^"]*h3[^"]*"[^>]*>.*?<a[^>]*href="/([^"]+)"[^>]*>([^<]+)</a>',
                    html,
                    re.DOTALL,
                )
                descriptions = re.findall(
                    r'<p[^>]*class="[^"]*col-9[^"]*"[^>]*>(.*?)</p>', html, re.DOTALL
                )
                stars = re.findall(r'<svg.*?octicon-star.*?</svg>\s*([\d,]+)', html, re.DOTALL)
                for i, (repo_path, name) in enumerate(repos[:max_results]):
                    desc = descriptions[i].strip() if i < len(descriptions) else ""
                    star = stars[i].strip() if i < len(stars) else "0"
                    desc = re.sub(r"<[^>]+>", "", desc).strip()
                    items.append(
                        SourceItem(
                            title=f"github.com/{repo_path}",
                            url=f"https://github.com/{repo_path}",
                            source=self.name,
                            content=desc,
                            metadata={"stars": star.replace(",", ""), "repo": repo_path, "language": language},
                        )
                    )
            else:
                for i, (repo_path, name, desc) in enumerate(repos[:max_results]):
                    items.append(
                        SourceItem(
                            title=name.strip(),
                            url=f"https://github.com/{repo_path}",
                            source=self.name,
                            content=desc.strip(),
                            metadata={"language": language},
                        )
                    )

            if not items:
                titles = re.findall(
                    r'<a[^>]*href="/trending[^"]*"[^>]*>([^<]+)</a>', html, re.DOTALL
                )
                for t in titles[:max_results]:
                    items.append(
                        SourceItem(
                            title=t.strip(),
                            url=f"https://github.com/trending?q={t.strip()}",
                            source=self.name,
                            content="",
                        )
                    )
        except httpx.RequestError as exc:
            error = str(exc)
            logger.warning("GitHub trending fetch failed: %s", exc)
        except Exception as exc:
            error = str(exc)
            logger.warning("GitHub trending parse error: %s", exc)

        agent_source_fetch_latency_seconds.labels(source=self.name, status="ok" if not error else "error").observe(0)
        return SourceResult(items=items, source_name=self.name, error=error)
