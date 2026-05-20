"""InfoQ 中文站：RSS（infoq.cn/feed）。

英文站主页 feed 常为 WAF 拦截 HEAD/UA，暂不默认混入；可按需在 RSS_FEED_URLS 里自建订阅。"""
from __future__ import annotations

from datetime import datetime

from tect_news.models import Article
from tect_news.sources.base import Source
from tect_news.sources.rss import RssSource

_CN_FEED = "https://www.infoq.cn/feed"


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
