from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlparse

import httpx

from tect_news.config import Settings
from tect_news.models import Article

UTC = timezone.utc


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; TectNewsAgent/0.1; +https://github.com/) "
        "AppleWebKit/537.36 (KHTML, like Gecko)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _scheme_ok(url: str) -> bool:
    try:
        p = urlparse(url.strip())
    except ValueError:
        return False
    return p.scheme in ("http", "https")


def _fetch_body_text(
    url: str,
    *,
    timeout_sec: float,
    max_response_bytes: int,
    max_body_chars: int,
) -> tuple[str, str | None]:
    """Returns (status, text). status one of ok|http_error|empty|not_html|bad_url|error."""
    if not _scheme_ok(url):
        return "bad_url", None
    try:
        with httpx.Client(timeout=timeout_sec, follow_redirects=True, headers=DEFAULT_HEADERS) as client:
            with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    return "http_error", None
                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_bytes():
                    if not chunk:
                        continue
                    take = chunk[: max(0, max_response_bytes - total)]
                    chunks.append(take)
                    total += len(take)
                    if total >= max_response_bytes:
                        break
                raw = b"".join(chunks)
        html = raw.decode("utf-8", errors="replace")
        import trafilatura  # 延迟导入：避免 --dry-collect 等路径加载 justext/lxml 栈

        extracted = trafilatura.extract(html, url=url)
        if not extracted or not extracted.strip():
            return "empty", None
        text = extracted.strip()
        if len(text) > max_body_chars:
            text = text[:max_body_chars].rstrip() + "\n…"
        return "ok", text
    except Exception:
        return "error", None


def enrich_article_bodies(
    articles: list[Article],
    settings: Settings,
    *,
    log: Callable[[str], None] | None = None,
) -> None:
    """Mutates each article's ``extra`` with ``body_text`` / ``body_fetch_status`` when enabled."""
    if not settings.digest_fetch_body:
        return

    def _log(msg: str) -> None:
        if log:
            log(msg)

    max_n = max(0, settings.digest_fetch_max_articles)
    if max_n == 0:
        return

    ordered = sorted(
        articles,
        key=lambda a: a.published_at or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    to_fetch = ordered[:max_n]
    skip_ids = {id(a) for a in articles} - {id(a) for a in to_fetch}
    for a in articles:
        if id(a) in skip_ids:
            a.extra.setdefault("body_fetch_status", "skipped_cap")

    timeout = settings.digest_fetch_timeout_sec
    max_bytes = settings.digest_fetch_max_response_bytes
    max_chars = settings.digest_body_max_chars

    workers = min(8, max(1, len(to_fetch)))

    def job(a: Article) -> Article:
        status, text = _fetch_body_text(
            a.url,
            timeout_sec=timeout,
            max_response_bytes=max_bytes,
            max_body_chars=max_chars,
        )
        a.extra["body_fetch_status"] = status
        if text:
            a.extra["body_text"] = text
        return a

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(job, a) for a in to_fetch]
        for fut in as_completed(futures):
            fut.result()

    ok = sum(1 for a in to_fetch if a.extra.get("body_fetch_status") == "ok")
    _log(f"tect_news: 正文抓取完成 {ok}/{len(to_fetch)}（上限 {max_n} 条）。")
