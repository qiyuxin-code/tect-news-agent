"""机器之心：文章库 JSON API（非官方 RSS）。

站点为 React SPA，列表数据来自 ``/api/article_library/articles.json``。
需浏览器式 User-Agent；发布时间按 ``DIGEST_TZ`` 解析。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc

import httpx

from tect_news.models import Article
from tect_news.sources.base import Source

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_DEFAULT_API_BASE = "https://www.jiqizhixin.com"


def _parse_published_at(raw: str, tz: ZoneInfo) -> datetime | None:
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            naive = datetime.strptime(s, fmt)
            return naive.replace(tzinfo=tz)
        except ValueError:
            continue
    return None


def _article_url(api_base: str, row: dict[str, Any]) -> str:
    slug = str(row.get("slug") or "").strip()
    if slug:
        return f"{api_base.rstrip('/')}/articles/{slug}"
    aid = str(row.get("id") or "").strip()
    if aid:
        return f"{api_base.rstrip('/')}/articles/{aid}"
    return ""


class JiqizhixinSource(Source):
    name = "jiqizhixin"

    def __init__(
        self,
        api_base: str = _DEFAULT_API_BASE,
        digest_tz: ZoneInfo | None = None,
        *,
        timeout_sec: float = 30.0,
        max_pages: int = 8,
        per_page: int = 20,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.digest_tz = digest_tz or ZoneInfo("Asia/Shanghai")
        self._timeout_sec = timeout_sec
        self._max_pages = max(1, max_pages)
        self._per_page = min(50, max(1, per_page))

    def fetch(self, since_utc: datetime, until_utc: datetime) -> list[Article]:
        headers = {
            "User-Agent": _BROWSER_UA,
            "Accept": "application/json",
            "Referer": f"{self.api_base}/articles",
        }
        list_url = f"{self.api_base}/api/article_library/articles.json"
        out: list[Article] = []
        seen_ids: set[str] = set()
        prev_first_id: str | None = None

        with httpx.Client(
            timeout=self._timeout_sec, follow_redirects=True, headers=headers
        ) as client:
            for page in range(1, self._max_pages + 1):
                resp = client.get(
                    list_url,
                    params={
                        "sort": "time",
                        "page": page,
                        "per": self._per_page,
                    },
                )
                resp.raise_for_status()
                data: dict[str, Any] = resp.json()
                rows = data.get("articles")
                if not isinstance(rows, list) or not rows:
                    break

                first_id = str(rows[0].get("id") or "")
                if first_id and first_id == prev_first_id:
                    break
                prev_first_id = first_id or prev_first_id

                page_all_before_window = True
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    aid = str(row.get("id") or "").strip()
                    if aid and aid in seen_ids:
                        continue
                    if aid:
                        seen_ids.add(aid)

                    published = _parse_published_at(
                        str(row.get("publishedAt") or ""), self.digest_tz
                    )
                    if published is None:
                        continue
                    pub_utc = published.astimezone(UTC)
                    if pub_utc >= until_utc.astimezone(UTC):
                        continue
                    if pub_utc < since_utc.astimezone(UTC):
                        continue
                    page_all_before_window = False

                    url = _article_url(self.api_base, row)
                    if not url:
                        continue
                    title = str(row.get("title") or "").strip() or url
                    summary = row.get("content") or row.get("description")
                    out.append(
                        Article(
                            title=title,
                            url=url,
                            source=self.name,
                            summary=str(summary)[:500] if summary else None,
                            published_at=pub_utc,
                            extra={"author": row.get("author")},
                        )
                    )

                if page_all_before_window:
                    break
                if not data.get("hasNextPage"):
                    break

        return out
