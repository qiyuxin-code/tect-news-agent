"""InfoQ：中文 / 英文 RSS。

- 中文：https://www.infoq.cn/feed（默认）
- 英文：https://www.infoq.com/rss/rss.action（``INFOQ_INCLUDE_EN=1`` 或 ``INFOQ_FEED_URLS``）
"""
from __future__ import annotations

from datetime import datetime

from tect_news.models import Article
from tect_news.sources.base import Source
from tect_news.sources.rss import RssSource

_CN_FEED = "https://www.infoq.cn/feed"
_EN_FEED = "https://www.infoq.com/rss/rss.action"


class InfoQSource(Source):
    name = "infoq"

    def __init__(
        self,
        feed_urls: tuple[str, ...] | None = None,
        *,
        timeout_sec: float = 30.0,
    ) -> None:
        urls = tuple(feed_urls) if feed_urls else (_CN_FEED,)
        self._rss = RssSource(urls, source_label=self.name, timeout_sec=timeout_sec)

    def fetch(self, since_utc: datetime, until_utc: datetime) -> list[Article]:
        return self._rss.fetch(since_utc, until_utc)


def default_infoq_feed_urls(*, include_en: bool) -> list[str]:
    urls = [_CN_FEED]
    if include_en:
        urls.append(_EN_FEED)
    return urls
