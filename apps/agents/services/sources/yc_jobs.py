from __future__ import annotations

import logging

import httpx

from apps.agents.metrics import agent_source_fetch_latency_seconds
from apps.agents.services.sources.base import BaseSource, SourceItem, SourceResult

logger = logging.getLogger(__name__)


class YCJobsSource(BaseSource):
    name = "yc_jobs"

    def __init__(self) -> None:
        self._client = httpx.Client(timeout=15.0, follow_redirects=True)

    async def fetch(self, query: str, max_results: int = 25) -> SourceResult:
        items: list[SourceItem] = []
        error: str | None = None
        try:
            params = {"limit": min(max_results, 50)}
            if query:
                params["q"] = query
            resp = self._client.get(
                "https://www.workatastartup.com/jobs",
                params=params,
                headers={"Accept": "application/json"},
            )
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    jobs = data if isinstance(data, list) else data.get("jobs", [])
                    for job in jobs[:max_results]:
                        items.append(
                            SourceItem(
                                title=job.get("title", ""),
                                url=f"https://www.workatastartup.com/jobs/{job.get('id', '')}",
                                source=self.name,
                                content=job.get("description", "") or "",
                                metadata={
                                    "company": job.get("company", {}).get("name", "")
                                    if isinstance(job.get("company"), dict)
                                    else "",
                                    "location": job.get("location", ""),
                                    "role_type": job.get("role_type", ""),
                                    "salary_range": job.get("salary_range", ""),
                                    "equity": job.get("equity", ""),
                                },
                            )
                        )
                    return SourceResult(items=items, source_name=self.name, error=None)
                except (ValueError, TypeError):
                    pass

            resp2 = self._client.get(
                "https://www.workatastartup.com/companies",
                params={"limit": min(max_results, 50)},
            )
            resp2.raise_for_status()
            try:
                data2 = resp2.json()
                companies = data2 if isinstance(data2, list) else data2.get("companies", [])
                for company in companies[:max_results]:
                    name = company.get("name", "")
                    items.append(
                        SourceItem(
                            title=f"{name} - {company.get('one_liner', '')}",
                            url=f"https://www.workatastartup.com/companies/{company.get('id', '')}",
                            source=self.name,
                            content=company.get("description", "") or "",
                            metadata={
                                "company": name,
                                "location": company.get("location", ""),
                                "team_size": company.get("team_size", ""),
                                "tags": company.get("tags", []),
                            },
                        )
                    )
            except (ValueError, TypeError):
                pass
        except httpx.RequestError as exc:
            error = str(exc)
            logger.warning("YC Jobs fetch failed: %s", exc)
        except Exception as exc:
            error = str(exc)
            logger.warning("YC Jobs parse error: %s", exc)

        agent_source_fetch_latency_seconds.labels(source=self.name, status="ok" if not error else "error").observe(0)
        return SourceResult(items=items, source_name=self.name, error=error)
