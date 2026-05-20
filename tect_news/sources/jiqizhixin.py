"""机器之心（Synced）— 占位。

本站曾提供 /rss，目前无稳定公开的 XML 订阅；合规接入需在取得可用 RSS 或其它官方 API 后再实现 ``fetch``。"""
from __future__ import annotations

from datetime import datetime

from tect_news.models import Article
from tect_news.sources.base import Source


class JiqizhixinSource(Source):
    name = "jiqizhixin"

    def fetch(self, since_utc: datetime, until_utc: datetime) -> list[Article]:
        return []
