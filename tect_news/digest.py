from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from openai import AuthenticationError, BadRequestError, OpenAI, PermissionDeniedError

from tect_news.scoring_display import format_score_inline
from tect_news.config import Settings
from tect_news.models import Article
from tect_news.urlnorm import normalize_url
from tect_news.digest_html import render_digest_html
from tect_news.verification import VerificationResult, verify_urls_subset

UTC = timezone.utc

# 超过此条数时按来源分批调用主编，避免单次 JSON 过大被截断或语法错误。
_EDITOR_BATCH_THRESHOLD = 24

_warned_no_json_object = False

_ITEM_WRITING_RULES = """- **每条 item 须「做什么 + 怎么做」**：
  - claim = **做什么**（结论/变化/产物，≤45 字）；
  - context = **怎么做**（机制/架构/关键工程手段，1–2 句合计≤120 字，**不得为空**）。
- 禁止 context 只写 star 数、融资额等人气指标。
- 每个 item 的 source_url 必须来自用户枚举的 URL，逐字一致。
- 顶层须为**单行紧凑 JSON**；字符串内换行写 \\n。"""


def _json_instructions(items_per_source: int) -> str:
    n = items_per_source
    return f"""输出唯一一个 JSON 对象（不要 markdown 围栏），结构如下：
{{
  "headline": "本周一句话标题（≤32 字，概括主线，不用感叹号堆砌）",
  "keywords": ["关键词1", "关键词2", "..."],
  "summary_paragraphs": [
    "第一段主编综述正文（2-4 段中的第 1 段）……",
    "第二段……",
    "可选第三段……",
    "可选第四段……"
  ],
  "sections": [
    {{
      "title": "与候选「## 来源: …」中的来源名一致（可略作中文友好化，但须能对应回该来源）",
      "tags": ["小节标签1", "小节标签2"],
      "items": [
        {{
          "claim": "做什么：一句话结论（发布了什么 / 解决了什么 / 核心变化；≤45 字；勿标题党复述）",
          "context": "怎么做：1–2 句简练说明关键机制、方法、架构或实现路径（须来自候选摘要/正文；合计≤120 字）",
          "tags": ["条目标签1", "条目标签2"],
          "source_url": "必须从用户给出的 URL 列表中原样复制的一条"
        }}
      ]
    }}
  ],
  "trivia": [
    {{
      "claim": "...",
      "context": "",
      "tags": [],
      "source_url": "同上，须来自列表"
    }}
  ]
}}
keywords：6–12 个**中文短语**（每条 2–8 字），覆盖本周技术主线（如「大模型」「开源」「安全」「云原生」），禁止空泛词（「科技」「动态」）。
sections[*].tags：0–3 个小节级标签；items[*].tags：每条 1–3 个更细标签，须与 claim 内容一致。
对 summary_paragraphs 的正文要求：先写「本周最值得关注的 2-3 条技术脉络/矛盾/共识」，再点出跨领域的共同点；像编辑手记（归纳、串联），禁止出现 http 或裸 URL；事实与判断须与候选条目及下文 claim 对齐，禁止凭空主体或编造数字。

编辑约束（与简单聚合的区别）：
- 先在心里完成「选题」：本周真正重要的变化是什么？哪些是噪声或重复报道？
- **分桶维度是采集来源**：sections 与候选中的「## 来源: …」块一一对应；每个有候选条目的来源单独成节，不要用主题分类替代来源分类。
- **每个来源 section 目标收录 {n} 条 item**：候选≥{n} 则选信息密度最高、技术实质最强的 {n} 条；候选<{n} 则尽量全收；不得因跨来源合并而减少各平台条目数。
- 合并仅限**同一来源、同一事件**的重复报道（合并后仍计为 1 条）。
- **每条 item 须「做什么 + 怎么做」两层信息**：
  - claim = **做什么**（结论/变化/产物，短而具体）；
  - context = **怎么做**（机制、算法思路、系统架构、关键工程手段、协议/接口要点等；从语料提炼，禁止空泛形容词）。
- sections[*].items[*].context **不得为空**（除非候选仅有标题无任何技术信息）；禁止 context 只写 star 数、融资额、排名等人气指标代替技术说明。
- 示例：claim「NVIDIA 发布 Gated DeltaNet-2，线性注意力层将 delta 规则中的 erase 与 write 解耦」；context「通过独立门控分别控制状态擦除与写入，减轻长序列下状态覆盖冲突，便于推理部署。」
- trivia 至多 3 条：各来源 section 未收录的边角料；可空数组；trivia 的 context 仍尽量写「怎么做」，能省略则短写。
- 每个 item 的 source_url 必须来自用户枚举的 URL，逐字一致，禁止编造链接。
- 不要输出列表以外的字段。
- 顶层须为**单行紧凑 JSON**（不要为了可读性换行）；各字符串值内若需换行一律写为 \\n，禁止在引号对之间出现字面换行。"""

_EDITOR_ROLE = (
    "你是资深技术媒体的主笔 + 主编。你的任务不是搬运标题，而是**选题、收敛、再表达**："
    "把大量候选压成少量高信噪比的判断，让读者用很少时间理解「发生了什么、**技术上怎么做的**、为何重要」。"
    "每条正文须同时交代「做什么」与「怎么做」（后者来自摘要/正文中的可核实机制，简练即可）。"
    "你只能使用用户提供的条目与 URL；无法核实的传闻不写进 claim 或 context。"
)


def _full_system_instructions(settings: Settings, articles: list[Article]) -> str:
    return _personality_prefix(settings) + _EDITOR_ROLE + "\n\n" + _json_instructions(
        settings.digest_items_per_source
    ) + _agent_score_note(settings, articles)


def _personality_prefix(settings: Settings) -> str:
    pers = (settings.openai_personality or "").strip().lower()
    if pers == "pragmatic":
        return (
            "[写作人格: pragmatic / 务实] 优先可核对的事实与对工程师的可操作含义；"
            "少用形容词与营销腔；不确定处不写死。\n\n"
        )
    if (settings.openai_personality or "").strip():
        return f"[写作人格: {settings.openai_personality.strip()}]\n\n"
    return ""


def _agent_score_note(settings: Settings, articles: list[Article]) -> str:
    if settings.digest_agent_score and any(
        isinstance(a.extra, dict) and a.extra.get("digest_agent_scores") for a in articles
    ):
        return (
            "\n\n[预打分] 条目块中如出现「agent分」行，为上游 PydanticAI 对各条目的草稿评分（非审计结论），"
            "仅可作选题弱先验；综述与每条 claim 仍须严格遵守「仅用语料事实」，不得仅凭分数捏造内容。\n"
        )
    return ""


def _section_json_instructions() -> str:
    return f"""输出唯一一个 JSON 对象（不要 markdown 围栏），结构如下：
{{
  "title": "与下方「来源: …」一致（可略作中文友好化）",
  "tags": ["小节标签1", "小节标签2"],
  "items": [
    {{
      "claim": "做什么：一句话结论",
      "context": "怎么做：1–2 句关键机制/方法",
      "tags": ["条目标签1"],
      "source_url": "必须从用户 URL 列表原样复制"
    }}
  ]
}}
sections[*].tags：0–3 个；items[*].tags：1–3 个。
候选已预筛选：**须为每条候选各写一条 item，不要删减**。
{_ITEM_WRITING_RULES}"""


def _overview_json_instructions() -> str:
    return """输出唯一一个 JSON 对象（不要 markdown 围栏），结构如下：
{
  "headline": "本周一句话标题（≤32 字）",
  "keywords": ["关键词1", "..."],
  "summary_paragraphs": ["第一段…", "第二段…"],
  "trivia": []
}
keywords：6–12 个中文短语（每条 2–8 字）。
summary_paragraphs：2–4 段主编综述，串联下方各来源 claim 的主线；禁止 http/裸 URL。
trivia 至多 3 条或空数组。
单行紧凑 JSON。"""


def _section_system_instructions(settings: Settings) -> str:
    return (
        _personality_prefix(settings)
        + _EDITOR_ROLE
        + "\n\n"
        + _section_json_instructions()
    )


def _overview_system_instructions(settings: Settings) -> str:
    return _personality_prefix(settings) + _EDITOR_ROLE + "\n\n" + _overview_json_instructions()


def _group_articles_by_source(articles: list[Article]) -> list[tuple[str, list[Article]]]:
    by_source: dict[str, list[Article]] = defaultdict(list)
    for a in articles:
        by_source[a.source].append(a)
    return [(k, by_source[k]) for k in sorted(by_source)]


def _section_outline_for_overview(sections: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for sec in sections:
        title = str(sec.get("title") or "").strip()
        items = sec.get("items") or []
        lines = [
            f"- {str(it.get('claim') or '').strip()}"
            for it in items
            if isinstance(it, dict) and str(it.get("claim") or "").strip()
        ]
        if title and lines:
            parts.append(f"## {title}\n" + "\n".join(lines))
    return "\n\n".join(parts) if parts else "(无)"


def _normalize_section_payload(data: dict[str, Any], *, fallback_title: str) -> dict[str, Any]:
    title = str(data.get("title") or fallback_title).strip() or fallback_title
    items_out: list[dict[str, str]] = []
    items_in = data.get("items")
    if isinstance(items_in, list):
        for it in items_in:
            if not isinstance(it, dict):
                continue
            su = str(it.get("source_url") or "").strip()
            if not su:
                continue
            items_out.append(
                {
                    "claim": str(it.get("claim") or "").strip(),
                    "context": str(it.get("context") or "").strip(),
                    "tags": _normalize_tags(it.get("tags"), max_n=3),
                    "source_url": su,
                }
            )
    return {
        "title": title,
        "tags": _normalize_tags(data.get("tags"), max_n=3),
        "items": items_out,
    }


def _call_editor_llm(
    client: OpenAI,
    settings: Settings,
    api_base: str,
    system_instructions: str,
    user_content: str,
    *,
    token_budget: int,
) -> tuple[str, str | None]:
    wire = settings.openai_wire_api
    if wire == "responses":
        kwargs: dict[str, Any] = {
            "model": settings.openai_model,
            "instructions": system_instructions,
            "input": user_content,
            "temperature": settings.digest_llm_temperature,
            "text": {"format": {"type": "json_object"}},
        }
        effort = (settings.openai_reasoning_effort or "").strip()
        if effort:
            kwargs["reasoning"] = {"effort": effort}
        try:
            resp = client.responses.create(**kwargs)
            return resp.output_text, None
        except PermissionDeniedError as pde:
            if not settings.openai_responses_fallback_chat:
                raise RuntimeError(
                    "/v1/responses 被拒绝（403 / Your request was blocked）。"
                    "请改用 OPENAI_WIRE_API=chat。"
                ) from pde
            print(
                "tect_news: /v1/responses 不可用，已回退到 /v1/chat/completions。",
                file=sys.stderr,
            )
    try:
        return _chat_completion_json(
            client,
            settings,
            system_instructions,
            user_content,
            max_completion_tokens=token_budget,
        )
    except AuthenticationError as err:
        if "api.openai.com" in api_base:
            raise RuntimeError(
                "401：请求发往 OpenAI 官方地址。请检查 .env 中的 OPENAI_BASE_URL 与 API key。"
            ) from err
        raise


def _token_budget_for_batch(*, item_count: int, base: int) -> int:
    return min(32_768, max(base, item_count * 420 + 2048))


def _llm_one_section_draft(
    settings: Settings,
    client: OpenAI,
    api_base: str,
    *,
    source: str,
    batch: list[Article],
    week_label: str,
) -> dict[str, Any]:
    system = _section_system_instructions(settings)
    user = "\n\n".join(
        [
            f"本周标识: {week_label}",
            f"来源: {source}",
            "为下列每条候选各写一条 item（claim + context 必填）：",
            _articles_prompt_block(batch, items_per_source=len(batch)),
            "你必须只使用下列 URL 作为 source_url（原样复制）：",
            _numbered_urls(batch),
        ]
    )
    token_budget = _token_budget_for_batch(
        item_count=len(batch), base=settings.digest_llm_max_completion_tokens
    )
    retry_tail = ""
    last_err: RuntimeError | None = None
    for attempt in range(2):
        user_block = user + (("\n\n" + retry_tail) if retry_tail else "")
        message, finish_reason = _call_editor_llm(
            client, settings, api_base, system, user_block, token_budget=token_budget
        )
        if not message:
            raise RuntimeError(f"来源 {source!r} 主编返回为空")
        try:
            data = _parse_json_object(message)
            if not isinstance(data, dict):
                raise RuntimeError("section JSON 根须为对象")
            return _normalize_section_payload(data, fallback_title=source)
        except RuntimeError as err:
            last_err = err
            if attempt == 0:
                print(
                    f"tect_news: 来源 {source!r} JSON 解析失败"
                    + (f"（finish={finish_reason}）" if finish_reason else "")
                    + "，重试一次。",
                    file=sys.stderr,
                )
                token_budget = min(32_768, token_budget + 4096)
                retry_tail = (
                    "重要：上一轮 JSON 无效或未闭合。请输出**完整单行**合法 JSON；"
                    "items 数量须与候选条数一致；字符串内勿出现未转义换行或引号。"
                )
                continue
            raise
    assert last_err is not None
    raise last_err


def _llm_overview_draft(
    settings: Settings,
    client: OpenAI,
    api_base: str,
    *,
    week_label: str,
    sections: list[dict[str, Any]],
) -> dict[str, Any]:
    system = _overview_system_instructions(settings)
    user = "\n\n".join(
        [
            f"本周标识: {week_label}",
            "各来源条目 claim 如下，请写 headline、keywords、summary_paragraphs（2–4 段）：",
            _section_outline_for_overview(sections),
        ]
    )
    token_budget = min(8192, settings.digest_llm_max_completion_tokens)
    message, _fr = _call_editor_llm(
        client, settings, api_base, system, user, token_budget=token_budget
    )
    if not message:
        raise RuntimeError("综述 JSON 返回为空")
    data = _parse_json_object(message)
    norm = _normalize_draft(
        {
            "headline": data.get("headline"),
            "keywords": data.get("keywords"),
            "summary_paragraphs": data.get("summary_paragraphs"),
            "sections": [],
            "trivia": data.get("trivia"),
        }
    )
    return norm


def _llm_json_draft_by_source(
    settings: Settings, articles: list[Article], week_label: str
) -> dict[str, Any]:
    client, api_base = _openai_client_for_digest(settings)
    sections: list[dict[str, Any]] = []
    for source, batch in _group_articles_by_source(articles):
        print(f"tect_news: 主编分批 → 来源 {source!r}（{len(batch)} 条）", file=sys.stderr)
        sections.append(
            _llm_one_section_draft(
                settings,
                client,
                api_base,
                source=source,
                batch=batch,
                week_label=week_label,
            )
        )
    overview = _llm_overview_draft(
        settings, client, api_base, week_label=week_label, sections=sections
    )
    overview["sections"] = sections
    return overview


@dataclass
class DigestBundle:
    markdown: str
    html: str | None
    verification: VerificationResult
    filtered_item_count: int


EDITOR_WORKFLOW = """请先在心里按下列步骤执行（不必输出步骤，只输出最终 JSON）：
1) 通读候选：按「## 来源: …」分块，标出各来源内「主线话题」与「重复/低信息」条目。
2) 收敛：仅在同一来源内合并同一事件的重复报道；跨来源的相似话题**不要**合并为一条。
3) 分桶：每个采集来源单独成 section，与候选来源块一一对应。
4) 综述：用主编口吻写 2-4 段，串联跨来源主线与张力，避免「如下」「此外」式清单感。
5) 定稿：各来源 section 按配额收录条目；每条 item 的 claim 写「做什么」、context 写「怎么做」；两句合读应能懂技术要点；链接仅作证据锚点。"""


def _articles_prompt_block(articles: list[Article], *, items_per_source: int) -> str:
    lines: list[str] = []
    by_source: dict[str, list[Article]] = defaultdict(list)
    for a in articles:
        by_source[a.source].append(a)
    quota = items_per_source
    for source in sorted(by_source.keys()):
        batch = by_source[source]
        lines.append(
            f"## 来源: {source}（候选 {len(batch)} 条，快报目标收录最多 {quota} 条）"
        )
        for a in by_source[source]:
            ts = a.published_at.isoformat() if a.published_at else "?"
            sm = (a.summary or "").replace("\n", " ")
            star = a.extra.get("stars")
            tail = f" | stars={star}" if star is not None else ""
            score_s = ""
            scr = format_score_inline(a.extra) if isinstance(a.extra, dict) else None
            if scr:
                score_s = f" | {scr}"
            cs = a.extra.get("cs_depth_score") if isinstance(a.extra, dict) else None
            if cs is not None:
                score_s += f" | cs={cs}"
            lines.append(f"- [{a.title}]({a.url}) | {ts}{tail}{score_s}")
            if sm:
                lines.append(f"  摘要: {sm[:400]}")
    return "\n".join(lines)


def _numbered_urls(articles: list[Article]) -> str:
    lines = []
    for i, a in enumerate(articles, start=1):
        lines.append(f"{i}. {a.url}")
    return "\n".join(lines) if lines else "(无)"


def _strip_json_fences(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```\w*\n?", "", s)
        s = re.sub(r"\n?```\s*$", "", s)
    return s.strip()


def _extract_balanced_json_object(s: str) -> str | None:
    """从首个 `{` 起截取与之配对的顶层 JSON 对象（忽略字符串内的括号）。"""
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(s)):
        c = s[i]
        if escape:
            escape = False
            continue
        if in_string:
            if c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return None


def _drop_trailing_commas_json(s: str) -> str:
    """删掉对象/数组末尾多余逗号（不在字符串内），常见于未开 json_object 时的模型输出。"""
    out: list[str] = []
    i = 0
    n = len(s)
    in_string = False
    escape = False
    while i < n:
        c = s[i]
        if escape:
            out.append(c)
            escape = False
            i += 1
            continue
        if in_string:
            if c == "\\":
                escape = True
                out.append(c)
            elif c == '"':
                in_string = False
                out.append(c)
            else:
                out.append(c)
            i += 1
            continue
        if c == '"':
            in_string = True
            out.append(c)
            i += 1
            continue
        if c == ",":
            j = i + 1
            while j < n and s[j] in " \t\n\r":
                j += 1
            if j < n and s[j] in "}]":
                i += 1
                continue
        out.append(c)
        i += 1
    return "".join(out)


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = _strip_json_fences(raw.strip().lstrip("\ufeff"))
    candidates: list[str] = []
    for c in (text, _extract_balanced_json_object(text) or ""):
        if c and c not in candidates:
            candidates.append(c)
    last_err: json.JSONDecodeError | None = None
    last_failed: str | None = None
    for cand in candidates:
        for repaired in (cand, _drop_trailing_commas_json(cand)):
            try:
                data = json.loads(repaired, strict=False)
                if isinstance(data, dict):
                    return data
                raise ValueError("JSON 根须为对象")
            except json.JSONDecodeError as e:
                last_err = e
                last_failed = repaired
                continue
    tail = ""
    if last_err is not None and last_failed is not None and 0 <= last_err.pos < len(
        last_failed
    ):
        pos = last_err.pos
        lo = max(0, pos - 100)
        hi = min(len(last_failed), pos + 100)
        snippet = last_failed[lo:hi].replace("\n", "\\n")
        tail = f" 解析位置约 {pos}，片段: …{snippet}…"
    raise RuntimeError(
        "主编 JSON 无法解析（常见于网关未启用 json_object、模型夹杂说明文字或 JSON 里有多余逗号）。"
        "可换支持 response_format 的网关，或稍后重试。"
        + (f" {tail}" if tail else f" ({last_err!s})")
    ) from last_err


def _normalize_tags(raw: Any, *, max_n: int = 12, max_len: int = 24) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for x in raw:
        t = str(x).strip()
        if not t or t in out:
            continue
        out.append(t[:max_len])
        if len(out) >= max_n:
            break
    return out


def _normalize_draft(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "headline": str(data.get("headline") or "").strip()[:80],
        "keywords": _normalize_tags(data.get("keywords"), max_n=12),
        "summary_paragraphs": [],
        "sections": [],
        "trivia": [],
    }
    sp = data.get("summary_paragraphs")
    if isinstance(sp, list):
        paras = [str(x).strip() for x in sp if str(x).strip()]
        out["summary_paragraphs"] = paras[:4]
    for key in ("sections",):
        secs = data.get(key)
        if not isinstance(secs, list):
            continue
        norm_secs: list[dict[str, Any]] = []
        for sec in secs:
            if not isinstance(sec, dict):
                continue
            title = str(sec.get("title") or "").strip() or "未命名主题"
            items_in = sec.get("items")
            items_out: list[dict[str, str]] = []
            if isinstance(items_in, list):
                for it in items_in:
                    if not isinstance(it, dict):
                        continue
                    su = str(it.get("source_url") or "").strip()
                    if not su:
                        continue
                    items_out.append(
                        {
                            "claim": str(it.get("claim") or "").strip(),
                            "context": str(it.get("context") or "").strip(),
                            "tags": _normalize_tags(it.get("tags"), max_n=3),
                            "source_url": su,
                        }
                    )
            norm_secs.append(
                {
                    "title": title,
                    "tags": _normalize_tags(sec.get("tags"), max_n=3),
                    "items": items_out,
                }
            )
        out["sections"] = norm_secs
    tr = data.get("trivia")
    if isinstance(tr, list):
        trivia_out: list[dict[str, str]] = []
        for it in tr[:3]:
            if not isinstance(it, dict):
                continue
            su = str(it.get("source_url") or "").strip()
            if not su:
                continue
            trivia_out.append(
                {
                    "claim": str(it.get("claim") or "").strip(),
                    "context": str(it.get("context") or "").strip(),
                    "tags": _normalize_tags(it.get("tags"), max_n=3),
                    "source_url": su,
                }
            )
        out["trivia"] = trivia_out
    return out


def _filter_by_allowlist(draft: dict[str, Any], allowed: set[str]) -> tuple[dict[str, Any], int]:
    dropped = 0

    def filt(items: list[dict[str, str]]) -> list[dict[str, str]]:
        nonlocal dropped
        kept: list[dict[str, str]] = []
        for it in items:
            if normalize_url(it["source_url"]) in allowed:
                kept.append(it)
            else:
                dropped += 1
        return kept

    new = dict(draft)
    new_sections: list[dict[str, Any]] = []
    for sec in draft.get("sections") or []:
        items = filt(list(sec.get("items") or []))
        new_sections.append({**sec, "items": items})
    new["sections"] = new_sections
    new["trivia"] = filt(list(draft.get("trivia") or []))
    return new, dropped


def _resolve_title(url: str, url_to_title: dict[str, str]) -> str:
    key = normalize_url(url)
    return url_to_title.get(key, "来源")


def _format_item_tags(tags: list[str]) -> str:
    if not tags:
        return ""
    return " `" + "` `".join(tags) + "`"


def _render_markdown(draft: dict[str, Any], url_to_title: dict[str, str]) -> str:
    parts: list[str] = []
    headline = str(draft.get("headline") or "").strip()
    if headline:
        parts.append(f"# {headline}\n\n")

    keywords = draft.get("keywords") or []
    if keywords:
        parts.append("**本周关键词**：" + " · ".join(keywords) + "\n\n")

    paras = draft.get("summary_paragraphs") or []
    if paras:
        parts.append("## 本周综述\n\n")
        for p in paras:
            parts.append(f"{p}\n\n")

    for sec in draft.get("sections") or []:
        title = sec.get("title") or "主题"
        sec_tags = sec.get("tags") or []
        tag_line = ""
        if sec_tags:
            tag_line = "  \n*" + " · ".join(sec_tags) + "*\n"
        parts.append(f"## {title}{tag_line}\n")
        for it in sec.get("items") or []:
            claim = it.get("claim") or ""
            ctx = (it.get("context") or "").strip()
            su = it.get("source_url") or ""
            lt = _resolve_title(su, url_to_title)
            tags_s = _format_item_tags(it.get("tags") or [])
            body = f"**{claim}**{tags_s}"
            if ctx:
                body += f"  \n怎么做：{ctx}"
            parts.append(f"- {body}  \n  [{lt}]({su})\n")

    trivia = draft.get("trivia") or []
    if trivia:
        parts.append("\n## 边角短讯\n\n")
        for it in trivia:
            claim = it.get("claim") or ""
            ctx = (it.get("context") or "").strip()
            su = it.get("source_url") or ""
            lt = _resolve_title(su, url_to_title)
            tags_s = _format_item_tags(it.get("tags") or [])
            body = f"**{claim}**{tags_s}"
            if ctx:
                body += f"  \n怎么做：{ctx}"
            parts.append(f"- {body}  \n  [{lt}]({su})\n")

    return "".join(parts).strip() + "\n"


def _unsupported_json_response_format(err: BadRequestError) -> bool:
    """部分兼容网关 / 模型拒绝 response_format=json_object（如部分方舟 MiniMax）。"""
    s = str(err).lower()
    return "json_object" in s and ("not support" in s or "invalid" in s or "parameter" in s)


def _chat_completion_json(
    client: OpenAI,
    settings: Settings,
    system_instructions: str,
    user_content: str,
    *,
    temperature: float | None = None,
    max_completion_tokens: int | None = None,
) -> tuple[str, str | None]:
    cap = (
        max_completion_tokens
        if max_completion_tokens is not None
        else settings.digest_llm_max_completion_tokens
    )
    kwargs: dict[str, Any] = {
        "model": settings.openai_model,
        "temperature": (
            temperature if temperature is not None else settings.digest_llm_temperature
        ),
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_instructions},
            {"role": "user", "content": user_content},
        ],
    }
    if cap > 0:
        kwargs["max_tokens"] = cap
    try:
        completion = client.chat.completions.create(**kwargs)
    except BadRequestError as bre:
        global _warned_no_json_object
        if kwargs.get("response_format") is not None and _unsupported_json_response_format(
            bre
        ):
            if not _warned_no_json_object:
                print(
                    "tect_news: 网关不支持 response_format=json_object，已降级为自由文本并从内容解析 JSON。",
                    file=sys.stderr,
                )
                _warned_no_json_object = True
            retry = {k: v for k, v in kwargs.items() if k != "response_format"}
            completion = client.chat.completions.create(**retry)
        else:
            raise
    ch = completion.choices[0]
    return (ch.message.content or "", getattr(ch, "finish_reason", None))


def _openai_client_for_digest(settings: Settings) -> tuple[OpenAI, str]:
    """OpenAI-compatible client；api_base 供错误提示判别。"""
    if not settings.openai_api_key:
        raise RuntimeError(
            "缺少 API key：请设置 OPENAI_API_KEY，或启用 OPENAI_PROMPT_KEY=1 在启动时输入。"
        )
    api_base = settings.openai_base_url or "https://api.openai.com/v1"
    return OpenAI(api_key=settings.openai_api_key, base_url=api_base), api_base


_CS_SCORE_SYSTEM = """你是技术编辑助手。对每条候选条目从 1–5 打分（仅整数），代表「计算机/工程可核实技术信息密度」（非人气）：
5=核心理论、系统、语言、运行时、算法、硬件、网络、安全等实质技术深度；
4=有清晰技术要点或约束，可指导工程师；
3=泛泛技术新闻或产品介绍；
2=市场营销、融资、人事为主；
1=几乎无可核对技术信息量。
你只依据用户给的标题与摘要/节选打分；条目相互独立。
必须输出唯一 JSON 对象：{\"scores\":[{\"index\":<与用户方括号编号相同>,\"score\":<1-5 整数>},...]}，
index 与用户枚举一致，每条输入须有对应 score，不要有漏项。
不要输出其它字段。"""


def _article_teaser_text(a: Article, max_chars: int) -> str:
    bt = ""
    raw = (a.extra or {}).get("body_text") if isinstance(a.extra, dict) else ""
    if isinstance(raw, str) and raw.strip():
        bt = raw.strip()
    if bt:
        return bt[:max_chars].replace("\n", " ").strip()
    sm = ((a.summary or "").replace("\n", " ").strip())
    return sm[:max_chars].strip()


def score_articles_cs_depth(
    settings: Settings,
    articles: list[Article],
    *,
    log: Callable[[str], None] | None = None,
) -> list[tuple[Article, int]]:
    """对全部条目打 CS 深度分（1–5），写入 ``extra['cs_depth_score']``。"""
    if not articles:
        return []

    def _say(msg: str) -> None:
        if log:
            log(msg)

    client, _api_base = _openai_client_for_digest(settings)
    max_snip = max(120, settings.digest_body_prompt_chars)
    batch = 28
    all_scores: dict[int, int] = {}

    for start in range(0, len(articles), batch):
        chunk = articles[start : start + batch]
        base_idx = start + 1
        lines: list[str] = []
        for i, art in enumerate(chunk):
            gid = base_idx + i
            teaser = _article_teaser_text(art, max_snip) or "(无摘要与正文节选)"
            lines.append(
                f"[{gid}] {art.title}\n    URL: {art.url}\n    节选: {teaser[:2200]}"
            )
        user_content = (
            "下列条目每条一行块，方括号内为全局 index（必须与输出完全一致）：\n\n"
            + "\n\n".join(lines)
        )
        raw, _fr = _chat_completion_json(
            client,
            settings,
            _CS_SCORE_SYSTEM,
            user_content,
            temperature=min(0.25, settings.digest_llm_temperature),
        )
        data = _parse_json_object(raw)
        arr = data.get("scores")
        if not isinstance(arr, list):
            raise RuntimeError(f"模型 JSON 缺少 scores 列表：{data!r}")
        for row in arr:
            if not isinstance(row, dict):
                continue
            ix = row.get("index")
            sc = row.get("score")
            try:
                idx_i = int(ix)
                sc_i = int(sc)
            except (TypeError, ValueError):
                continue
            sc_i = min(5, max(1, sc_i))
            all_scores[idx_i] = sc_i

    scored: list[tuple[Article, int]] = []
    for i, art in enumerate(articles):
        sc = all_scores.get(i + 1)
        if sc is None:
            _say(f"tect_news: cs 打分缺失 index={i + 1}，按 Neutral=3 保留。")
            sc = 3
        if not isinstance(art.extra, dict):
            art.extra = {}
        art.extra["cs_depth_score"] = sc
        scored.append((art, sc))
    return scored


def _top_k_per_source(
    scored: list[tuple[Article, int]],
    *,
    k: int,
    min_score: int,
) -> list[Article]:
    by_source: dict[str, list[tuple[Article, int]]] = defaultdict(list)
    for art, sc in scored:
        by_source[art.source].append((art, sc))
    out: list[Article] = []
    for source in sorted(by_source):
        passed = [(a, s) for a, s in by_source[source] if s >= min_score]
        passed.sort(
            key=lambda t: (
                -t[1],
                -(t[0].published_at.timestamp() if t[0].published_at else 0),
            )
        )
        out.extend(a for a, _s in passed[:k])
    return out


def filter_articles_professional(
    settings: Settings,
    articles: list[Article],
    *,
    log: Callable[[str], None] | None = None,
) -> list[Article]:
    """专业模式：每来源 CS 打分后取 Top-K（``digest_items_per_source``）。"""
    if not settings.digest_professional_mode or not articles:
        return articles

    def _say(msg: str) -> None:
        if log:
            log(msg)

    scored = score_articles_cs_depth(settings, articles, log=log)
    k = settings.digest_items_per_source
    out = _top_k_per_source(
        scored, k=k, min_score=settings.digest_cs_min_score
    )
    _say(
        "tect_news: 专业模式筛选 "
        f"每来源 CS Top-{k}（min_score≥{settings.digest_cs_min_score}）"
        f" → {len(out)}/{len(articles)}。"
    )
    return out


def filter_articles_by_cs_depth(
    settings: Settings,
    articles: list[Article],
    *,
    log: Callable[[str], None] | None = None,
) -> list[Article]:
    """DIGEST_CS_FILTER 开启时调用模型打分并筛选（可选全局 TOP_K）。"""
    if not settings.digest_cs_filter:
        return articles
    if not articles:
        return articles

    def _say(msg: str) -> None:
        if log:
            log(msg)

    scored = score_articles_cs_depth(settings, articles, log=log)
    passed = [(a, s) for a, s in scored if s >= settings.digest_cs_min_score]
    if settings.digest_cs_top_k > 0:
        stable = sorted(enumerate(passed), key=lambda x: (-x[1][1], x[0]))
        stable = stable[: settings.digest_cs_top_k]
        out = [t[1][0] for t in stable]
    else:
        out = [a for a, _s in passed]

    _say(
        "tect_news: CS 深度筛选 "
        f"{len(out)}/{len(articles)} "
        f"(min_score≥{settings.digest_cs_min_score}"
        + (
            f", top_k={settings.digest_cs_top_k}"
            if settings.digest_cs_top_k > 0
            else ""
        )
        + ")。"
    )
    return out


def _llm_json_draft_monolithic(
    settings: Settings, articles: list[Article], week_label: str
) -> dict[str, Any]:
    client, api_base = _openai_client_for_digest(settings)
    system_instructions = _full_system_instructions(settings, articles)
    if settings.digest_professional_mode:
        quota_note = (
            f"候选已由 CS 专业性评分为每来源 Top-{settings.digest_items_per_source}。"
            "sections 须与「## 来源: …」一一对应，并尽量为每条候选各写一条 item（不要二次大幅删减）。"
            "每条 item 必须同时填写 claim（做什么）与 context（怎么做/关键机制），context 禁止留空或只写 stars。"
        )
        candidate_intro = "下面是经专业模式筛选后的候选（按来源分组）："
    else:
        quota_note = (
            f"各采集来源在快报 sections 中目标收录最多 {settings.digest_items_per_source} 条 item。"
            "每条须 claim（做什么）+ context（怎么做），context 禁止留空或只写人气数据。"
        )
        candidate_intro = "下面是本周候选条目（按来源分组；你需要在各来源配额内选题与精炼）："
    user_content = "\n\n".join(
        [
            f"本周标识: {week_label}",
            EDITOR_WORKFLOW,
            quota_note,
            candidate_intro,
            _articles_prompt_block(articles, items_per_source=settings.digest_items_per_source),
            "你必须只使用下列 URL 作为 source_url（原样复制）：",
            _numbered_urls(articles),
        ]
    )
    token_budget = _token_budget_for_batch(
        item_count=len(articles), base=settings.digest_llm_max_completion_tokens
    )
    retry_tail = ""
    last_parse_err: RuntimeError | None = None

    for attempt in range(2):
        user_block = user_content + (("\n\n" + retry_tail) if retry_tail else "")
        message, finish_reason = _call_editor_llm(
            client,
            settings,
            api_base,
            system_instructions,
            user_block,
            token_budget=token_budget,
        )
        if not message:
            raise RuntimeError("模型返回为空")
        try:
            data = _parse_json_object(message)
            return _normalize_draft(data)
        except RuntimeError as err:
            last_parse_err = err
            if attempt == 0:
                print(
                    "tect_news: 主编单次 JSON 解析失败"
                    + (f"（finish={finish_reason}）" if finish_reason else "")
                    + "，加大输出上限并重试。",
                    file=sys.stderr,
                )
                token_budget = min(32_768, max(token_budget * 2, token_budget + 4096))
                retry_tail = (
                    "重要：上一轮 JSON 无效或未闭合。请输出**完整单行**合法 JSON；"
                    "summary 与 claim 可略写，但结构须闭合；字符串内换行只能用 \\n。"
                )
                continue
            raise
    assert last_parse_err is not None
    raise last_parse_err


def _llm_json_draft(settings: Settings, articles: list[Article], week_label: str) -> dict[str, Any]:
    if len(articles) > _EDITOR_BATCH_THRESHOLD:
        print(
            f"tect_news: 候选 {len(articles)} 条（>{_EDITOR_BATCH_THRESHOLD}），"
            "按来源分批生成主编 JSON。",
            file=sys.stderr,
        )
        return _llm_json_draft_by_source(settings, articles, week_label)
    try:
        return _llm_json_draft_monolithic(settings, articles, week_label)
    except RuntimeError as err:
        if "JSON 无法解析" in str(err) or "返回为空" in str(err):
            print(
                "tect_news: 单次主编 JSON 失败，回退为按来源分批生成。",
                file=sys.stderr,
            )
            return _llm_json_draft_by_source(settings, articles, week_label)
        raise


def generate_digest_bundle(
    settings: Settings,
    articles: list[Article],
    week_label: str,
    *,
    generated_at: str = "",
) -> DigestBundle:
    allowed = {normalize_url(a.url) for a in articles}
    url_to_title = {normalize_url(a.url): a.title for a in articles}

    def resolve_title(url: str) -> str:
        return _resolve_title(url, url_to_title)

    draft = _llm_json_draft(settings, articles, week_label)
    draft, filtered = _filter_by_allowlist(draft, allowed)
    md = _render_markdown(draft, url_to_title)
    verification = verify_urls_subset(md, allowed)

    warn_note = ""
    if not verification.ok and settings.digest_strict_urls:
        raise RuntimeError(
            "快报校验失败：存在未收录语料的链接（疑为模型编造或综述含 URL）。"
            f" unknown={verification.urls_unknown!r}。"
            "可将 DIGEST_STRICT_URLS=0 改为仅告警。"
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

    html_out: str | None = None
    if settings.digest_output_html:
        html_out = render_digest_html(
            draft,
            week_label=week_label,
            resolve_title=resolve_title,
            generated_at=generated_at,
            articles_count=len(articles),
            verification_note=warn_note,
        )

    return DigestBundle(
        markdown=md,
        html=html_out,
        verification=verification,
        filtered_item_count=filtered,
    )


def generate_digest_markdown(settings: Settings, articles: list[Article], week_label: str) -> str:
    """兼容旧接口：仅返回 Markdown 正文。"""
    return generate_digest_bundle(settings, articles, week_label).markdown


def week_label_for(digest_tz, now_utc: datetime | None = None) -> str:
    now = now_utc or datetime.now(UTC)
    local = now.astimezone(digest_tz)
    return f"{local.year}-W{local.isocalendar()[1]:02d}"
