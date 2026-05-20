"""Hacker News：官方首页 RSS。

发布时间须在 [since_utc, until_utc)（左闭右开）。条目若无可用发布时间则从结果中跳过。"""
from __future__ import annotations

from datetime import datetime

from tect_news.models import Article
from tect_news.sources.base import Source
from tect_news.sources.rss import RssSource

_HN_FEED = "https://news.ycombinator.com/rss"


class HackerNewsSource(Source):
    name = "hackernews"

    def __init__(
        self,
        rss_url: str | None = None,
        *,
        timeout_sec: float = 30.0,
    ) -> None:
        u = rss_url or _HN_FEED
        self._rss = RssSource((u,), source_label=self.name, timeout_sec=timeout_sec)

    def fetch(self, since_utc: datetime, until_utc: datetime) -> list[Article]:
        return self._rss.fetch(since_utc, until_utc)
