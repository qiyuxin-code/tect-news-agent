"""Pydantic AI harness：主编 Agent 自主编排「采集 → 精选 → 校验 → 生成周报」。

与 `pipeline.run_pipeline` 平级；通过 `DIGEST_AGENT_MODE=1` 或 CLI `--agent` 启用。
- deps 注入 `DigestDeps`（settings / 时间窗 / 工作台语料 / 输出目录）
- 工具包装现有 pipeline 函数，LLM 自主决定调用顺序与取舍
- `output_type=DigestDraft` 结构化输出，校验失败自动重试
- `@agent.output_validator` 强制 source_url ⊆ 语料白名单
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.capabilities import Hooks, MCP
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from tect_news.agent_scoring import enrich_articles_agent_scores
from tect_news.article_pool import cap_articles_per_source
from tect_news.config import Settings, load_settings
from tect_news.digest import (
    _EDITOR_ROLE,
    _articles_prompt_block,
    _personality_prefix,
    _render_markdown,
    score_articles_cs_depth,
    week_label_for,
)
from tect_news.digest_html import render_digest_html
from tect_news.enrich import enrich_article_bodies
from tect_news.models import Article
from tect_news.pipeline import (
    apply_collection_pool,
    collect_articles,
    collection_bounds,
    default_seed_path,
)
from tect_news.scoring_display import format_score_inline
from tect_news.urlnorm import normalize_url
from tect_news.verification import verify_urls_subset

UTC = timezone.utc


# ---------------------------------------------------------------------------
# 结构化输出模型（取代手写 JSON 容错 / normalize）
# ---------------------------------------------------------------------------
class DigestItem(BaseModel):
    claim: str = Field(description="做什么：一句话结论（≤45 字）")
    context: str = Field(description="怎么做：1–2 句关键机制/工程手段（≤120 字，不得为空）")
    tags: list[str] = Field(default_factory=list, description="1–3 个条目标签")
    source_url: str = Field(description="必须来自语料 URL 列表，逐字一致")


class DigestSection(BaseModel):
    title: str = Field(description="与采集来源名对应（可中文友好化）")
    tags: list[str] = Field(default_factory=list, description="0–3 个小节标签")
    items: list[DigestItem] = Field(default_factory=list)


class DigestTrivia(BaseModel):
    claim: str = Field(description="一句话")
    context: str = Field(default="", description="怎么做，可短写")
    tags: list[str] = Field(default_factory=list)
    source_url: str = Field(description="必须来自语料 URL 列表")


class DigestDraft(BaseModel):
    headline: str = Field(description="本周一句话标题（≤32 字）")
    keywords: list[str] = Field(default_factory=list, description="6–12 个中文短语（每条 2–8 字）")
    summary_paragraphs: list[str] = Field(
        default_factory=list, description="2–4 段主编综述（禁止含 URL）"
    )
    sections: list[DigestSection] = Field(default_factory=list)
    trivia: list[DigestTrivia] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 依赖注入（工作台）
# ---------------------------------------------------------------------------
@dataclass
class DigestDeps:
    settings: Settings
    week_label: str
    collect_since: datetime
    until: datetime
    seed_path: Path
    output_dir: Path
    generated_at: str = ""
    collected: list[Article] = field(default_factory=list)
    pre_collected: list[Article] | None = None
    log: Callable[[str], None] = print


def _say(deps: DigestDeps, msg: str) -> None:
    if deps.log:
        deps.log(f"tect_news[agent]: {msg}")


# ---------------------------------------------------------------------------
# 写作规则（系统提示词）
# ---------------------------------------------------------------------------
_WRITING_RULES = """输出对象是 DigestDraft（Pydantic 结构化结果，直接给出字段值）：
- headline：本周一句话标题（≤32 字，概括主线，不用感叹号堆砌）。
- keywords：6–12 个中文短语（每条 2–8 字），覆盖本周技术主线；禁止空泛词（「科技」「动态」）。
- summary_paragraphs：2–4 段主编综述；先写「本周最值得关注的 2-3 条技术脉络/矛盾/共识」，再点出跨领域共同点；禁止出现 http 或裸 URL；事实与判断须与语料对齐，禁止凭空主体或编造数字。
- sections：按采集来源分节，与 collect_news 返回的「## 来源: …」一一对应；每节目标收录最多 @N@ 条 item。
  - title 与该来源名对应（可中文友好化，须能对应回该来源）。
  - 每条 item：claim=做什么（≤45 字，结论/变化/产物）；context=怎么做（1–2 句关键机制、算法思路、系统架构或工程手段，≤120 字，不得为空；禁止只写 star 数/融资额/排名等人气指标）；tags 1–3 个；source_url 必须来自语料 URL 列表，逐字一致。
  - 只在同一来源内合并同一事件的重复报道（合并后仍计 1 条）；跨来源相似话题不要合并。
- trivia：至多 3 条边角料；context 尽量写怎么做，能省略则短写。
先在心里完成选题：本周真正重要的变化是什么？哪些是噪声或重复报道？然后收敛、精炼，让读者用很少时间理解「发生了什么、技术上怎么做的、为何重要」。"""

_EDITOR_USER_TEMPLATE = """本周标识: {week_label}（时间窗 [{since}, {until})）

任务：产出一份《中文技术快报》。

请按需调用工具完成，不要跳过采集：
1) collect_news —— 先把本周语料采集到工作台（拿到底稿后立刻在心里选题）；
2) 可选 enrich_bodies / score_articles / filter_by_cs —— 提升归纳依据、收敛噪声；
3) 依据工作台语料输出 DigestDraft（选题、收敛、再表达）。

硬性要求：每个 item 的 source_url 必须来自 collect_news 返回的 URL 列表，逐字一致；禁止编造链接、主体或数字。"""


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _collect_into(deps: DigestDeps) -> None:
    if deps.collected:
        return
    if deps.pre_collected is not None:
        arts = list(deps.pre_collected)
    else:
        arts = collect_articles(
            deps.settings,
            since_utc=deps.collect_since,
            until_utc=deps.until,
            seed_path=deps.seed_path,
        )
        arts = apply_collection_pool(deps.settings, arts, log=lambda m: _say(deps, m))
    deps.collected[:] = arts
    _say(deps, f"collect_news 采集 {len(deps.collected)} 条")


def register_tools(agent: Agent[DigestDeps, DigestDraft]) -> None:
    @agent.tool
    def collect_news(
        ctx: RunContext[DigestDeps],
        max_per_source: int = 0,
        sources: list[str] | None = None,
    ) -> str:
        """采集本周（DIGEST_TZ 周窗口）各数据源语料到工作台。
        max_per_source>0 时展示列表每源只保留最新 N 条（默认用快报配额）；sources 给定则只保留这些来源。
        返回当前语料清单（含标题/URL/时间/摘要），作为选题与写 source_url 的唯一依据。
        """
        deps = ctx.deps
        _collect_into(deps)
        if sources:
            allowed_src = set(sources)
            deps.collected[:] = [a for a in deps.collected if a.source in allowed_src]
        cap = max_per_source or deps.settings.digest_items_per_source
        display = cap_articles_per_source(deps.collected, cap) if cap > 0 else deps.collected
        _say(deps, f"collect_news 展示 {len(display)}/{len(deps.collected)} 条")
        return _articles_prompt_block(
            display, items_per_source=deps.settings.digest_items_per_source
        )

    @agent.tool
    def enrich_bodies(ctx: RunContext[DigestDeps]) -> str:
        """抓取工作台语料的正文（受 DIGEST_FETCH_BODY / DIGEST_FETCH_MAX_ARTICLES 控制），提高归纳依据。"""
        deps = ctx.deps
        if not deps.collected:
            return "工作台为空，请先调用 collect_news。"
        enrich_article_bodies(deps.collected, deps.settings, log=lambda m: _say(deps, m))
        ok = sum(
            1 for a in deps.collected if (a.extra or {}).get("body_fetch_status") == "ok"
        )
        return f"正文抓取完成：{ok}/{len(deps.collected)} 条获得正文。"

    @agent.tool
    def score_articles(ctx: RunContext[DigestDeps], top_n: int = 0) -> str:
        """对工作台语料做多维打分（technical_signal/credibility/source_trust/signal_to_noise）与 CS 深度分（1–5），写入各条。
        top_n>0 只返回分数最高的前 N 条摘要。打分是草稿先验，不是事实核查结论。"""
        deps = ctx.deps
        if not deps.collected:
            return "工作台为空，请先调用 collect_news。"
        settings = deps.settings
        if settings.digest_agent_score:
            enrich_articles_agent_scores(deps.collected, settings, log=lambda m: _say(deps, m))
        score_articles_cs_depth(settings, deps.collected, log=lambda m: _say(deps, m))
        rows: list[str] = []
        for i, a in enumerate(deps.collected, 1):
            scr = format_score_inline(a.extra) if isinstance(a.extra, dict) else None
            cs = a.extra.get("cs_depth_score") if isinstance(a.extra, dict) else None
            parts: list[str] = []
            if scr:
                parts.append(scr)
            if cs is not None:
                parts.append(f"cs={cs}")
            rows.append(
                f"{i}. [{a.source}] {a.title} | "
                + (" ".join(parts) if parts else "未打分")
            )
        if top_n > 0:
            rows = rows[: max(0, top_n)]
        return "\n".join(rows) if rows else "工作台没有条目。"

    @agent.tool
    def filter_by_cs(
        ctx: RunContext[DigestDeps],
        min_score: int = 3,
        per_source: int = 0,
        top_k: int = 0,
    ) -> str:
        """按 CS 深度分筛选工作台（min_score=最低保留分 1–5；per_source>0 每源保留最新 N 条；top_k>0 全局只留分数最高 N 条）。
        需要先调用 score_articles。返回筛选后的语料清单。"""
        deps = ctx.deps
        if not deps.collected:
            return "工作台为空，请先调用 collect_news。"
        has_score = any(
            isinstance((a.extra or {}).get("cs_depth_score"), int) for a in deps.collected
        )
        if not has_score:
            return "还没有 CS 分，请先调用 score_articles。"
        ms = min(5, max(1, min_score))
        kept = [
            a
            for a in deps.collected
            if isinstance((a.extra or {}).get("cs_depth_score"), int)
            and a.extra["cs_depth_score"] >= ms
        ]
        if per_source > 0:
            kept = cap_articles_per_source(kept, per_source)
        if top_k > 0:
            kept = sorted(
                kept, key=lambda a: a.extra.get("cs_depth_score", 0), reverse=True
            )[: top_k]
        deps.collected[:] = kept
        _say(deps, f"filter_by_cs → {len(kept)} 条")
        return _articles_prompt_block(
            kept, items_per_source=deps.settings.digest_items_per_source
        )

    @agent.tool
    def verify_urls(ctx: RunContext[DigestDeps], markdown: str) -> str:
        """检查一段 Markdown 的所有链接是否都来自工作台语料（白名单）。返回校验结果与未知链接列表，便于自查后修正。"""
        deps = ctx.deps
        allowed = {normalize_url(a.url) for a in deps.collected}
        res = verify_urls_subset(markdown, allowed)
        return (
            f"ok={res.ok} urls_in_doc={len(res.urls_in_output)} "
            f"unknown={res.urls_unknown!r}"
        )

    @agent.output_validator
    def _validate_urls(ctx: RunContext[DigestDeps], draft: DigestDraft) -> DigestDraft:
        allowed = {normalize_url(a.url) for a in ctx.deps.collected}
        if not allowed:
            raise ModelRetry("工作台没有语料：请先调用 collect_news 采集本周语料再生成。")
        bad: list[str] = []
        for sec in draft.sections:
            for it in sec.items:
                if normalize_url(it.source_url) not in allowed:
                    bad.append(it.source_url)
        for it in draft.trivia:
            if normalize_url(it.source_url) not in allowed:
                bad.append(it.source_url)
        if bad:
            raise ModelRetry(
                "以下 source_url 不在语料白名单内，请改成 collect_news 返回列表中逐字一致的 URL："
                + repr(bad[:20])
            )
        return draft


# ---------------------------------------------------------------------------
# 可观测性 hooks
# ---------------------------------------------------------------------------
async def _hook_before_tool_execute(
    ctx: RunContext[DigestDeps], *, call: Any, tool_def: Any, args: Any
) -> Any:
    deps: DigestDeps = ctx.deps
    _say(deps, f"调用工具 {getattr(call, 'tool_name', '?')} args={args}")
    return args


async def _hook_after_run(
    ctx: RunContext[DigestDeps], *, result: Any
) -> Any:
    deps: DigestDeps = ctx.deps
    usage = getattr(result, "usage", None)
    _say(deps, f"run 完成 usage={usage}")
    return result


# ---------------------------------------------------------------------------
# Agent 构建
# ---------------------------------------------------------------------------
def _build_model(settings: Settings) -> OpenAIChatModel:
    if not settings.openai_api_key:
        raise RuntimeError(
            "缺少 API key：请设置 OPENAI_API_KEY / DEEPSEEK_API_KEY，或启用 OPENAI_PROMPT_KEY=1。"
        )
    api_base = settings.openai_base_url or "https://api.openai.com/v1"
    return OpenAIChatModel(
        settings.openai_model,
        provider=OpenAIProvider(api_key=settings.openai_api_key, base_url=api_base),
    )


def _mcp_capabilities(settings: Settings) -> list[Any]:
    """从 settings.mcp_config 构建 MCP capability（可选）。连接在 run 时建立，构建期不触网。"""
    caps: list[Any] = []
    for cfg in settings.mcp_config:
        url = str(cfg.get("url") or cfg.get("server_url") or "").strip()
        if not url:
            continue
        try:
            caps.append(MCP(url=url, headers=cfg.get("headers") or None))
        except Exception as exc:  # 坏配置只告警，不拖垮整个 agent
            print(f"tect_news[agent]: MCP 配置被跳过 {url!r} ({type(exc).__name__}: {exc})", file=sys.stderr)
    return caps


def build_editor_agent(
    settings: Settings,
    *,
    model: Any | None = None,
    extra_system_prompt: str = "",
) -> Agent[DigestDeps, DigestDraft]:
    """组装主编 Agent：指令 + 规则 + 工具 + 输出模型 + 白名单校验 + 可选 MCP。"""
    rules = _WRITING_RULES.replace("@N@", str(settings.digest_items_per_source))
    system = (
        _personality_prefix(settings)
        + _EDITOR_ROLE
        + "\n\n"
        + rules
        + (("\n\n" + extra_system_prompt) if extra_system_prompt else "")
    )
    hooks = Hooks(
        before_tool_execute=_hook_before_tool_execute,
        after_run=_hook_after_run,
    )
    model_settings: dict = {
        "temperature": settings.digest_llm_temperature,
        "max_tokens": settings.digest_llm_max_completion_tokens,
    }
    # DeepSeek V4 默认启用 thinking mode，但该模式下不接受 tool_choice（pydantic_ai
    # 有工具即发送 tool_choice='auto'，网关会回 400）。显式关闭 thinking 以兼容工具调用。
    if settings.openai_provider_profile in ("deepseek", "ds"):
        model_settings["extra_body"] = {"thinking": {"type": "disabled"}}
    agent = Agent[DigestDeps, DigestDraft](
        model or _build_model(settings),
        deps_type=DigestDeps,
        output_type=DigestDraft,
        retries=2,
        model_settings=ModelSettings(**model_settings),
        system_prompt=system,
        capabilities=[hooks, *_mcp_capabilities(settings)],
    )
    register_tools(agent)
    return agent


# ---------------------------------------------------------------------------
# 高层入口
# ---------------------------------------------------------------------------
def _user_prompt(deps: DigestDeps, since: datetime, until: datetime) -> str:
    return _EDITOR_USER_TEMPLATE.format(
        week_label=deps.week_label,
        since=since.isoformat(),
        until=until.isoformat(),
    )


def run_agent_digest(
    settings: Settings | None = None,
    xhs_seed_path: Path | None = None,
    output_dir: Path | None = None,
    now_utc: datetime | None = None,
    pre_collected: list[Article] | None = None,
) -> Path:
    """Agent 版全链路：采集（或注入 pre_collected）→ LLM 自主编排 → 校验 → 落盘。"""
    settings = settings or load_settings()
    now = now_utc or datetime.now(UTC)
    week_since, until, collect_since = collection_bounds(settings, now)
    wl = week_label_for(settings.digest_tz, now)

    root = Path(__file__).resolve().parent.parent
    seed = xhs_seed_path or default_seed_path(root)
    out_dir = output_dir or (root / "output")
    out_dir.mkdir(parents=True, exist_ok=True)

    deps = DigestDeps(
        settings=settings,
        week_label=wl,
        collect_since=collect_since,
        until=until,
        seed_path=seed,
        output_dir=out_dir,
        generated_at=now.isoformat(),
        pre_collected=pre_collected,
    )

    from tect_news.harness.skills import load_skills_text

    skills_text = load_skills_text(settings.skills_dir)
    agent = build_editor_agent(settings, extra_system_prompt=skills_text)
    result = agent.run_sync(
        _user_prompt(deps, week_since, until),
        deps=deps,
        usage_limits=UsageLimits(
            request_limit=settings.digest_agent_request_limit,
            total_tokens_limit=settings.digest_agent_total_tokens_limit,
        ),
    )
    draft: DigestDraft = result.output
    if not deps.collected:
        raise RuntimeError("agent 未产出语料：请检查 collect_news 是否成功。")

    allowed = {normalize_url(a.url) for a in deps.collected}
    url_to_title = {normalize_url(a.url): a.title for a in deps.collected}
    data = draft.model_dump()
    md = _render_markdown(data, url_to_title)
    verification = verify_urls_subset(md, allowed)

    warn_note = ""
    if not verification.ok and settings.digest_strict_urls:
        raise RuntimeError(
            "快报校验失败（agent 版）：存在未收录语料的链接。"
            f" unknown={verification.urls_unknown!r}。"
            " 可将 DIGEST_STRICT_URLS=0 改为仅告警。"
        )
    if not verification.ok:
        warn_note = "下列 URL 不在本周采集白名单内，请人工复核： " + "；".join(
            verification.urls_unknown
        )
        md += (
            "\n---\n\n> **校验提醒**：下列 URL 不在本周采集白名单内，请人工复核：\n> - "
            + "\n> - ".join(verification.urls_unknown)
            + "\n"
        )

    header = (
        f"<!-- generated_at={now.isoformat()} window=[{week_since.isoformat()}, {until.isoformat()}) "
        f"collect_since={collect_since.isoformat()} articles={len(deps.collected)} "
        f"agent_mode=1 llm={settings.openai_model} "
        f"fetch_body={int(settings.digest_fetch_body)} "
        f"cs_filter={int(settings.digest_cs_filter)} agent_score={int(settings.digest_agent_score)} "
        f"{verification.to_header_comment()} -->\n\n"
    )
    outfile = out_dir / f"digest-{wl}.md"
    outfile.write_text(header + md, encoding="utf-8")
    if settings.digest_output_html:
        html_path = out_dir / f"digest-{wl}.html"
        html_path.write_text(
            render_digest_html(
                data,
                week_label=wl,
                resolve_title=lambda u: url_to_title.get(normalize_url(u), "来源"),
                generated_at=now.isoformat(),
                articles_count=len(deps.collected),
                verification_note=warn_note,
            ),
            encoding="utf-8",
        )
    return outfile


def main() -> None:
    """CLI 入口：python -m tect_news.agent_editor [--output-dir PATH]"""
    import argparse

    parser = argparse.ArgumentParser(description="Pydantic AI 主编 Agent 生成周报")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--xiaohongshu-seed", type=Path, default=None)
    args = parser.parse_args()
    out = run_agent_digest(
        xhs_seed_path=args.xiaohongshu_seed, output_dir=args.output_dir
    )
    print(out.resolve())


if __name__ == "__main__":
    main()
