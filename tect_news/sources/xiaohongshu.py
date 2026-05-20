from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from tect_news.models import Article
from tect_news.sources.base import Source

UTC = timezone.utc


class XiaohongshuSeedSource(Source):
    """
    MVP 占位：小红书无稳定公开 API。
    从本地 JSON 导入你手工/后续爬虫写入的条目；auth 字段留给 .env（XHS_*），接入时再接。
    """

    name = "xiaohongshu_seed"

    def __init__(self, json_path: Path | str) -> None:
        self.json_path = Path(json_path)

    def fetch(self, since_utc: datetime, until_utc: datetime) -> list[Article]:
        if not self.json_path.is_file():
            return []
        raw = json.loads(self.json_path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return []
        out: list[Article] = []
        for row in raw:
            if not isinstance(row, dict):
                continue
            url = str(row.get("url") or "").strip()
            title = str(row.get("title") or "").strip()
            if not url or not title:
                continue
            pub_raw = row.get("published_at")
            if pub_raw:
                try:
                    published = datetime.fromisoformat(str(pub_raw).replace("Z", "+00:00"))
                    if published.tzinfo is None:
                        published = published.replace(tzinfo=UTC)
                    published = published.astimezone(UTC)
                except ValueError:
                    published = None
            else:
                published = None
            if published is None or not (since_utc <= published < until_utc):
                continue
            summary = row.get("summary")
            out.append(
                Article(
                    title=title,
                    url=url,
                    source=self.name,
                    summary=str(summary)[:500] if summary else None,
                    published_at=published,
                )
            )
        return out
