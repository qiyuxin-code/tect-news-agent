"""PydanticAI：批量对条目做技术性 / 可信度等结构化打分，供快报生成引用。

需要 **Python 3.10+** 与 ``pip install pydantic-ai``。分数写入 ``Article.extra["digest_agent_scores"]``。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import BaseModel, Field

from tect_news.config import Settings
from tect_news.models import Article

UTC = timezone.utc


def _deps() -> tuple[Any, Any, Any, Any]:
    try:
        from pydantic_ai import Agent
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider
        from pydantic_ai.settings import ModelSettings
    except ImportError as e:
        raise RuntimeError(
            "DIGEST_AGENT_SCORE 需要 pydantic-ai，且推荐使用 Python≥3.10："
            "`pip install -r requirements.txt`"
        ) from e
    return Agent, OpenAIChatModel, OpenAIProvider, ModelSettings


_SCORER_SYSTEM = """你是技术信息流编辑助理。你只根据给定条目的标题、来源标签、摘要与可选正文节选做**粗粒度草稿评分**，
不是事实核查结论；条目未展示的信息不得臆测。
每个维度均为 1–5 的整数，含义如下：

- technical_signal：对执业工程师/研究者而言，条目是否显露**可实现或可归因的技术信息量**（机制、实现、benchmark、开源、工程等）；5 信息密度最高。
- credibility：**陈述是否像可落地的可核对事实**，而非口号、传闻或未指明出处的论断；5 更可核对。
- source_trust：仅根据 **URL 域名**与来源标签的工程/学术社区观感给先验可信度；不偏袒任何品牌。
- signal_to_noise：标题与摘要观感上**营销噱头、情绪挑动、空洞形容词**越低分越高（5 最克制、信息含量高）。

必须为批次内每一条输入都给出一行 evaluations；index 与用户给出的全局编号完全一致。"""


class ArticleScoreRow(BaseModel):
    index: int = Field(ge=1, description="与提示中的条目编号完全一致")
    technical_signal: int = Field(ge=1, le=5)
    credibility: int = Field(ge=1, le=5)
    source_trust: int = Field(ge=1, le=5)
    signal_to_noise: int = Field(ge=1, le=5)
    rationale: str = Field(
        "",
        description="≤80 字简短理由；无则 \"\"",
        max_length=200,
    )


class ScoreEnvelope(BaseModel):
    evaluations: list[ArticleScoreRow]


def _teaser_for_score(a: Article, max_chars: int) -> str:
    bt = (a.extra or {}).get("body_text") if isinstance(a.extra, dict) else ""
    if isinstance(bt, str) and bt.strip():
        t = bt[:max_chars].replace("\n", " ").strip()
        return t
    sm = ((a.summary or "").replace("\n", " ").strip())
    return sm[:max_chars].strip()


def _batch_user_prompt(chunk: list[Article], *, base_index: int, max_chars: int) -> str:
    parts: list[str] = []
    for i, a in enumerate(chunk):
        gid = base_index + i
        teaser = _teaser_for_score(a, max_chars) or "(无摘要与正文节选)"
        parts.append(
            f"[{gid}] source={a.source}\n标题: {a.title}\nURL: {a.url}\n节选: {teaser[:1800]}"
        )
    return (
        "对下列条目逐条打分。只依据块内文本。输出必须符合 schema。\n\n" + "\n\n".join(parts)
    )


def enrich_articles_agent_scores(
    articles: list[Article],
    settings: Settings,
    *,
    log: Callable[[str], None] | None = None,
) -> None:
    """就地写入 ``digest_agent_scores``；未打分条目无该键。"""
    if not settings.digest_agent_score:
        return
    if not articles:
        return

    def _log(msg: str) -> None:
        if log:
            log(msg)

    Agent, OpenAIChatModel, OpenAIProvider, ModelSettings = _deps()
    max_n = max(0, settings.digest_agent_score_max_articles)
    if max_n == 0:
        return

    ordered = sorted(
        articles,
        key=lambda a: a.published_at or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    slice_ = ordered[:max_n]
    bs = max(4, settings.digest_agent_score_batch_size)

    if not settings.openai_api_key:
        raise RuntimeError(
            "DIGEST_AGENT_SCORE=1 需要 OPENAI_API_KEY（或 PROMPT_KEY 输入），与快报模型共用网关。"
        )
    api_base = settings.openai_base_url or "https://api.openai.com/v1"
    model = OpenAIChatModel(
        settings.openai_model,
        provider=OpenAIProvider(api_key=settings.openai_api_key, base_url=api_base),
    )
    agent = Agent(
        model,
        output_type=ScoreEnvelope,
        system_prompt=_SCORER_SYSTEM,
    )

    teaser_cap = max(200, settings.digest_body_prompt_chars)
    idx_to_article = {i: slice_[i - 1] for i in range(1, len(slice_) + 1)}
    ms = ModelSettings(temperature=settings.digest_agent_score_temperature)

    for start in range(0, len(slice_), bs):
        chunk = slice_[start : start + bs]
        base = start + 1
        payload = _batch_user_prompt(chunk, base_index=base, max_chars=teaser_cap)
        result = agent.run_sync(payload, model_settings=ms)
        envelope = result.output
        rows = getattr(envelope, "evaluations", None) or []
        for row in rows:
            art = idx_to_article.get(row.index)
            if art is None:
                _log(f"tect_news: agent 打分收到未知 index={row.index}，已跳过。")
                continue
            if not isinstance(art.extra, dict):
                art.extra = {}
            art.extra["digest_agent_scores"] = {
                "technical_signal": row.technical_signal,
                "credibility": row.credibility,
                "source_trust": row.source_trust,
                "signal_to_noise": row.signal_to_noise,
                "rationale": (row.rationale or "").strip()[:120],
            }
        got = {r.index for r in rows}
        missing = set(range(base, base + len(chunk))) - got
        for m in sorted(missing):
            _log(f"tect_news: agent 打分缺失 index={m}，写入占位默认 3。")
            art = idx_to_article.get(m)
            if art is None:
                continue
            if not isinstance(art.extra, dict):
                art.extra = {}
            art.extra.setdefault(
                "digest_agent_scores",
                {
                    "technical_signal": 3,
                    "credibility": 3,
                    "source_trust": 3,
                    "signal_to_noise": 3,
                    "rationale": "",
                },
            )

    scored = sum(
        1
        for a in articles
        if isinstance(getattr(a, "extra", None), dict) and a.extra.get("digest_agent_scores")
    )
    _log(f"tect_news: PydanticAI 条目打分覆盖 {scored}/{len(articles)}（评分上限条数={max_n}）。")
