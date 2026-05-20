from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable

import feedparser
import httpx

from tect_news.models import Article
from tect_news.sources.base import Source

UTC = timezone.utc


def _parse_published(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        struct = getattr(entry, key, None)
        if struct:
            return datetime(*struct[:6], tzinfo=UTC)
    for key in ("published", "updated"):
        raw = getattr(entry, key, None)
        if raw:
            try:
                dt = parsedate_to_datetime(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return dt.astimezone(UTC)
            except (TypeError, ValueError):
                continue
    return None


class RssSource(Source):
    def __init__(
        self,
        feed_urls: Iterable[str],
        source_label: str = "rss",
        *,
        timeout_sec: float = 30.0,
    ) -> None:
        self.feed_urls = list(feed_urls)
        self.name = source_label
        self._timeout_sec = timeout_sec

    def fetch(self, since_utc: datetime, until_utc: datetime) -> list[Article]:
        out: list[Article] = []
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; TectNewsAgent/0.1; +https://github.com/) "
                "AppleWebKit/537.36 (KHTML, like Gecko)"
            )
        }
        with httpx.Client(
            timeout=self._timeout_sec, follow_redirects=True, headers=headers
        ) as client:
            for url in self.feed_urls:
                resp = client.get(url)
                resp.raise_for_status()
                parsed = feedparser.parse(resp.text)
                feed_title = (parsed.feed.get("title") or url)[:80]
                for entry in parsed.entries:
                    link = entry.get("link") or ""
                    if not link:
                        continue
                    title = (entry.get("title") or "").strip() or link
                    published = _parse_published(entry)
                    if published is None or not (since_utc <= published < until_utc):
                        continue
                    summary = entry.get("summary") or entry.get("description")
                    if summary and len(summary) > 500:
                        summary = summary[:500] + "…"
                    out.append(
                        Article(
                            title=title,
                            url=link,
                            source=f"{self.name}:{feed_title}",
                            summary=summary,
                            published_at=published,
                        )
                    )
        return out
