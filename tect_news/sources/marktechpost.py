"""MarkTechPost：AI/ML 新闻 RSS。"""
from __future__ import annotations

from datetime import datetime

from tect_news.models import Article
from tect_news.sources.base import Source
from tect_news.sources.rss import RssSource

_MARKTECHPOST_FEED = "https://www.marktechpost.com/feed/"


class MarktechpostSource(Source):
    name = "marktechpost"

    def __init__(
        self,
        rss_url: str | None = None,
        *,
        timeout_sec: float = 30.0,
    ) -> None:
        self._rss = RssSource(
            (rss_url or _MARKTECHPOST_FEED,),
            source_label=self.name,
            timeout_sec=timeout_sec,
        )

    def fetch(self, since_utc: datetime, until_utc: datetime) -> list[Article]:
        return self._rss.fetch(since_utc, until_utc)
