from apps.agents.services.sources.hackernews import HackerNewsSource
from apps.agents.services.sources.reddit import RedditSource
from apps.agents.services.sources.github_trending import GitHubTrendingSource
from apps.agents.services.sources.arxiv import ArxivSource
from apps.agents.services.sources.remoteok import RemoteOKSource
from apps.agents.services.sources.greenhouse import GreenhouseSource
from apps.agents.services.sources.lever import LeverSource
from apps.agents.services.sources.yc_jobs import YCJobsSource
from apps.agents.services.sources.rss import RSSSource

__all__ = [
    "HackerNewsSource",
    "RedditSource",
    "GitHubTrendingSource",
    "ArxivSource",
    "RemoteOKSource",
    "GreenhouseSource",
    "LeverSource",
    "YCJobsSource",
    "RSSSource",
]

SOURCE_MAP: dict[str, type] = {
    "hackernews": HackerNewsSource,
    "reddit": RedditSource,
    "github_trending": GitHubTrendingSource,
    "arxiv": ArxivSource,
    "remoteok": RemoteOKSource,
    "greenhouse": GreenhouseSource,
    "lever": LeverSource,
    "yc_jobs": YCJobsSource,
    "rss": RSSSource,
}


def get_source(name: str):
    cls = SOURCE_MAP.get(name)
    if cls is None:
        raise ValueError(f"Unknown source: {name}")
    return cls()
