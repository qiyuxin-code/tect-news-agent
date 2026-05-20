from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from tect_news.config import Settings, load_settings
from tect_news.agent_scoring import enrich_articles_agent_scores
from tect_news.digest import filter_articles_by_cs_depth, generate_digest_bundle, week_label_for
from tect_news.enrich import enrich_article_bodies
from tect_news.models import Article
from tect_news.sources.github_repos import GitHubRepoSource
from tect_news.sources.hackernews import HackerNewsSource
from tect_news.sources.infoq import InfoQSource
from tect_news.sources.jiqizhixin import JiqizhixinSource
from tect_news.sources.lobsters import LobstersSource
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


def collect_articles(
    settings: Settings,
    *,
    since_utc: datetime,
    until_utc: datetime,
    seed_path: Path,
) -> list[Article]:
    # RSS：与 DIGEST_FETCH_TIMEOUT_SEC 对齐，且不低于 45s（经代理时常更慢）。
    rss_timeout_sec = max(45.0, float(settings.digest_fetch_timeout_sec))

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
        ),
        XiaohongshuSeedSource(seed_path),
        HackerNewsSource(timeout_sec=rss_timeout_sec),
        LobstersSource(timeout_sec=rss_timeout_sec),
        InfoQSource(timeout_sec=rss_timeout_sec),
        JiqizhixinSource(),
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


def run_pipeline(
    settings: Settings | None = None,
    xhs_seed_path: Path | None = None,
    output_dir: Path | None = None,
    now_utc: datetime | None = None,
    pre_collected: list[Article] | None = None,
) -> Path:
    settings = settings or load_settings()
    now = now_utc or datetime.now(UTC)
    since, until = week_bounds_utc(now, settings.digest_tz)
    wl = week_label_for(settings.digest_tz, now)

    root = Path(__file__).resolve().parent.parent
    seed = xhs_seed_path or default_seed_path(root)
    out_dir = output_dir or (root / "output")
    out_dir.mkdir(parents=True, exist_ok=True)

    if pre_collected is not None:
        collected = pre_collected
    else:
        collected = collect_articles(settings, since_utc=since, until_utc=until, seed_path=seed)

    enrich_article_bodies(collected, settings, log=print)
    enrich_articles_agent_scores(collected, settings, log=print)
    collected = filter_articles_by_cs_depth(settings, collected, log=print)

    bundle = generate_digest_bundle(settings, collected, wl)
    digest = bundle.markdown
    header = (
        f"<!-- generated_at={now.isoformat()} window=[{since.isoformat()}, {until.isoformat()}) "
        f"articles={len(collected)} llm={settings.openai_model} fetch_body={int(settings.digest_fetch_body)} "
        f"cs_filter={int(settings.digest_cs_filter)} agent_score={int(settings.digest_agent_score)} "
        f"{bundle.verification.to_header_comment()} "
        f"source_allowlist_filtered={bundle.filtered_item_count} -->\n\n"
    )
    outfile = out_dir / f"digest-{wl}.md"
    outfile.write_text(header + digest + "\n", encoding="utf-8")
    return outfile
