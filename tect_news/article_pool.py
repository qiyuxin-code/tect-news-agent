"""采集池：按来源截断、排序。"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from tect_news.models import Article

UTC = timezone.utc


def _published_key(a: Article) -> datetime:
    return a.published_at or datetime.min.replace(tzinfo=UTC)


def cap_articles_per_source(articles: list[Article], max_per: int) -> list[Article]:
    """每个 ``Article.source`` 保留发布时间最新的 ``max_per`` 条。"""
    if max_per <= 0:
        return []
    by_source: dict[str, list[Article]] = defaultdict(list)
    for a in articles:
        by_source[a.source].append(a)
    out: list[Article] = []
    for source in sorted(by_source):
        batch = sorted(by_source[source], key=_published_key, reverse=True)
        out.extend(batch[:max_per])
    return out


def select_for_body_fetch(
    articles: list[Article],
    *,
    max_per_source: int | None = None,
    max_global: int | None = None,
) -> list[Article]:
    """决定哪些条目需要拉正文（就地返回列表，不修改输入顺序外的对象）。"""
    if max_per_source is not None and max_per_source > 0:
        return cap_articles_per_source(articles, max_per_source)
    if max_global is not None and max_global > 0:
        ordered = sorted(articles, key=_published_key, reverse=True)
        return ordered[:max_global]
    return list(articles)
