from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import httpx

from tect_news.models import Article
from tect_news.sources.base import Source

UTC = timezone.utc


class GitHubRepoSource(Source):
    """Notable repositories pushed within the window (search API)."""

    name = "github_repo"

    def __init__(
        self,
        api_base: str,
        token: str | None,
        digest_tz: ZoneInfo,
        min_stars: int = 300,
        per_page: int = 20,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.token = token
        self.digest_tz = digest_tz
        self.min_stars = min_stars
        self._per_page = min(100, max(1, per_page))

    def fetch(self, since_utc: datetime, until_utc: datetime) -> list[Article]:
        since = since_utc.astimezone(self.digest_tz).date().isoformat()
        # pushed 窗口近似「本周有活动」；可在后续换成更精细的过滤
        q = f"pushed:>{since} stars:>={self.min_stars}"
        url = f"{self.api_base}/search/repositories?q={quote_plus(q)}&sort=updated&order=desc&per_page={self._per_page}"
        headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
        items = data.get("items") or []
        out: list[Article] = []
        for repo in items:
            pushed_at = repo.get("pushed_at")
            if not pushed_at:
                continue
            try:
                pushed = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
            except ValueError:
                continue
            if not (since_utc <= pushed.astimezone(UTC) < until_utc):
                continue
            name = repo.get("full_name") or ""
            desc = (repo.get("description") or "").strip()
            html_url = repo.get("html_url") or ""
            stars = repo.get("stargazers_count")
            out.append(
                Article(
                    title=name or html_url,
                    url=html_url,
                    source=self.name,
                    summary=desc or None,
                    published_at=pushed.astimezone(UTC),
                    extra={"stars": stars},
                )
            )
        return out
