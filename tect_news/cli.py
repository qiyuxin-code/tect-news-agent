from __future__ import annotations

import argparse
import getpass
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from tect_news.config import load_settings
from tect_news.pipeline import (
    apply_collection_pool,
    collect_articles,
    collection_bounds,
    default_seed_path,
    run_pipeline,
)
from tect_news.digest import week_label_for

UTC = timezone.utc


def main() -> None:
    parser = argparse.ArgumentParser(description="生成本周技术快报（OpenAI 兼容 Chat Completions）")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="输出目录，默认仓库根下 output/",
    )
    parser.add_argument(
        "--xiaohongshu-seed",
        type=Path,
        default=None,
        help="小红书种子 JSON 路径，默认 data/xiaohongshu_seed.json",
    )
    parser.add_argument(
        "--dry-collect",
        action="store_true",
        help="只打印采集到的条目数量与来源，不调用大模型",
    )
    args = parser.parse_args()

    settings = load_settings()
    if not args.dry_collect and settings.openai_prompt_key:
        key = getpass.getpass("OPENAI-compatible API key: ").strip()
        if not key:
            raise SystemExit("未输入 API key，已退出。")
        settings = replace(settings, openai_api_key=key)

    now = datetime.now(UTC)
    week_since, until, collect_since = collection_bounds(settings, now)
    root = Path(__file__).resolve().parent.parent
    seed = args.xiaohongshu_seed or default_seed_path(root)

    collected = collect_articles(
        settings, since_utc=collect_since, until_utc=until, seed_path=seed
    )
    collected = apply_collection_pool(settings, collected, log=None)

    if args.dry_collect:
        wl = week_label_for(settings.digest_tz, now)
        mode = "professional" if settings.digest_professional_mode else "weekly"
        print(
            f"week={wl} mode={mode} count={len(collected)} "
            f"window=[{week_since}, {until}) collect_since={collect_since}"
        )
        for a in collected[:80]:
            print(f"- [{a.source}] {a.title} {a.url}")
        if len(collected) > 80:
            print(f"... ({len(collected) - 80} more)")
        return

    path = run_pipeline(
        settings=settings,
        xhs_seed_path=args.xiaohongshu_seed,
        output_dir=args.output_dir,
        now_utc=now,
        pre_collected=collected,
    )
    print(path.resolve())
    if settings.digest_output_html:
        html_path = path.with_suffix(".html")
        if html_path.is_file():
            print(html_path.resolve())


if __name__ == "__main__":
    main()
