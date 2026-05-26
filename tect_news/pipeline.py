from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from tect_news.article_pool import cap_articles_per_source
from tect_news.config import Settings, load_settings
from tect_news.agent_scoring import enrich_articles_agent_scores
from tect_news.digest import (
    filter_articles_by_cs_depth,
    filter_articles_professional,
    generate_digest_bundle,
    week_label_for,
)
from tect_news.enrich import enrich_article_bodies
from tect_news.models import Article
from tect_news.sources.github_repos import GitHubRepoSource
from tect_news.sources.hackernews import HackerNewsSource
from tect_news.sources.infoq import InfoQSource
from tect_news.sources.jiqizhixin import JiqizhixinSource
from tect_news.sources.lobsters import LobstersSource
from tect_news.sources.marktechpost import MarktechpostSource
from tect_news.sources.rss import RssSource
from tect_news.sources.xiaohongshu import XiaohongshuSeedSource
from tect_news.time_window import week_bounds_utc

UTC = timezone.utc


def dedupe_by_url(articles: list[Article]) -> list[Article]:
    seen: set[str] = set()
    out: list[Article] = []
    for a in articles:
        key = a.url.split("#", 1)[0].strip().rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out


def default_seed_path(root: Path | None = None) -> Path:
    base = root or Path(__file__).resolve().parent.parent
    return base / "data" / "xiaohongshu_seed.json"


def collection_bounds(
    settings: Settings, now_utc: datetime
) -> tuple[datetime, datetime, datetime]:
    """返回 (week_since, until, collect_since)。专业模式用更长 lookback 捞池子。"""
    week_since, until = week_bounds_utc(now_utc, settings.digest_tz)
    if settings.digest_professional_mode:
        collect_since = until - timedelta(days=settings.digest_collect_lookback_days)
    else:
        collect_since = week_since
    return week_since, until, collect_since


def _jiqizhixin_pages(settings: Settings) -> int:
    pages = settings.jiqizhixin_max_pages
    if settings.digest_professional_mode:
        pool = settings.digest_collect_max_per_source
        pages = max(pages, (pool + 19) // 20)
    return pages


def collect_articles(
    settings: Settings,
    *,
    since_utc: datetime,
    until_utc: datetime,
    seed_path: Path,
) -> list[Article]:
    # RSS：与 DIGEST_FETCH_TIMEOUT_SEC 对齐，且不低于 45s（经代理时常更慢）。
    rss_timeout_sec = max(45.0, float(settings.digest_fetch_timeout_sec))
    gh_per_page = 100 if settings.digest_professional_mode else 20

    sources = [
        RssSource(
            settings.rss_feed_urls,
            source_label="rss",
            timeout_sec=rss_timeout_sec,
        ),
        GitHubRepoSource(
            settings.github_api_base_url,
            settings.github_token,
            settings.digest_tz,
            per_page=gh_per_page,
        ),
        XiaohongshuSeedSource(seed_path),
        HackerNewsSource(timeout_sec=rss_timeout_sec),
        LobstersSource(timeout_sec=rss_timeout_sec),
        MarktechpostSource(timeout_sec=rss_timeout_sec),
        InfoQSource(
            tuple(settings.infoq_feed_urls),
            timeout_sec=rss_timeout_sec,
        ),
        JiqizhixinSource(
            settings.jiqizhixin_api_base_url,
            settings.digest_tz,
            timeout_sec=rss_timeout_sec,
            max_pages=_jiqizhixin_pages(settings),
        ),
    ]
    collected: list[Article] = []
    for src in sources:
        try:
            collected.extend(src.fetch(since_utc, until_utc))
        except Exception as exc:
            print(
                f"tect_news: 采集跳过 source={src.name!r} ({type(exc).__name__}: {exc})",
                file=sys.stderr,
            )
    return dedupe_by_url(collected)


def apply_collection_pool(
    settings: Settings,
    articles: list[Article],
    *,
    log: Callable[[str], None] | None = print,
) -> list[Article]:
    """专业模式：每个来源只保留最新 N 条进入后续打分与主编。"""
    if not settings.digest_professional_mode:
        return articles
    cap = settings.digest_collect_max_per_source
    pooled = cap_articles_per_source(articles, cap)
    if log:
        log(
            f"tect_news: 专业模式采集池 每来源最多 {cap} 条"
            f" → {len(pooled)}/{len(articles)}。"
        )
    return pooled


def filter_for_digest(
    settings: Settings,
    articles: list[Article],
    *,
    log: Callable[[str], None] | None = print,
) -> list[Article]:
    if settings.digest_professional_mode:
        return filter_articles_professional(settings, articles, log=log)
    if settings.digest_cs_filter:
        return filter_articles_by_cs_depth(settings, articles, log=log)
    return articles


def run_pipeline(
    settings: Settings | None = None,
    xhs_seed_path: Path | None = None,
    output_dir: Path | None = None,
    now_utc: datetime | None = None,
    pre_collected: list[Article] | None = None,
) -> Path:
    settings = settings or load_settings()
    now = now_utc or datetime.now(UTC)
    week_since, until, collect_since = collection_bounds(settings, now)
    wl = week_label_for(settings.digest_tz, now)

    root = Path(__file__).resolve().parent.parent
    seed = xhs_seed_path or default_seed_path(root)
    out_dir = output_dir or (root / "output")
    out_dir.mkdir(parents=True, exist_ok=True)

    if pre_collected is not None:
        collected = pre_collected
    else:
        collected = collect_articles(
            settings, since_utc=collect_since, until_utc=until, seed_path=seed
        )
        collected = apply_collection_pool(settings, collected, log=print)

    enrich_article_bodies(collected, settings, log=print)
    enrich_articles_agent_scores(collected, settings, log=print)
    collected = filter_for_digest(settings, collected, log=print)

    bundle = generate_digest_bundle(
        settings, collected, wl, generated_at=now.isoformat()
    )
    digest = bundle.markdown
    header = (
        f"<!-- generated_at={now.isoformat()} window=[{week_since.isoformat()}, {until.isoformat()}) "
        f"collect_since={collect_since.isoformat()} articles={len(collected)} "
        f"professional_mode={int(settings.digest_professional_mode)} "
        f"pool_per_source={settings.digest_collect_max_per_source if settings.digest_professional_mode else 0} "
        f"llm={settings.openai_model} fetch_body={int(settings.digest_fetch_body)} "
        f"cs_filter={int(settings.digest_cs_filter)} agent_score={int(settings.digest_agent_score)} "
        f"{bundle.verification.to_header_comment()} "
        f"source_allowlist_filtered={bundle.filtered_item_count} -->\n\n"
    )
    outfile = out_dir / f"digest-{wl}.md"
    outfile.write_text(header + digest, encoding="utf-8")
    if bundle.html:
        html_path = out_dir / f"digest-{wl}.html"
        html_path.write_text(bundle.html, encoding="utf-8")
    return outfile
