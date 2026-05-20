# CLAUDE.md

本文档概括 **tect-news-agent** 的产品方向与**全链路「agent」编排**（采集 → 预处理 → 多路 LLM → 校验落盘）；实现以代码为准。

## 产品方向

- **目标**：多平台素材只做原材料；**主编式大模型**完成选题、收敛与精炼——把大批条目压成少量高信噪比判断，形成**中文技术快报**（MVP 使用「本周」时间窗）。
- **用户价值**：少刷、读懂、记得住——一篇周报回答「这周技术世界发生了什么、为何重要」，不是链接堆砌。
- **质量手段（当前）**：
  - 采集合规 + **URL 去重**；
  - 可选 **正文抓取**（`enrich.py`），提高归纳依据；
  - 可选 **PydanticAI 多维条目打分**，把「技术性 / 可信度感」草稿注入主编提示；
  - 可选 **CS 深度 JSON 打分筛选**；
  - **结构化生成**：主编阶段 JSON schema（含 `headline` / `keywords` / 条目标签）→ Markdown + 可选 **HTML 卡片**（`digest_html.py`），**`source_url` 必须锚定本周语料**；
  - **程序校验**：`DIGEST_STRICT_URLS` 时正文链接 ⊆ 采集白名单（`verification.py` + `urlnorm`）。

---

## Agent 分层架构（必读）

工程中「agent」泛指 **带 LLM 推理/结构化输出的自动化步骤**，分三层：

```text
┌─────────────────────────────────────────────────────────────────┐
│ 主编 Agent（digest._llm_json_draft）                              │
│ OpenAI SDK：chat.completions 或（profile 允许时）responses        │
│ 输出：周报 JSON → Markdown + URL 白名单校验                        │
└─────────────────────────────────────────────────────────────────┘
                              ▲
                              │ 条目列表（可含正文 / 打分 / CS 筛选后）
┌─────────────────────────────┴───────────────────────────────────┐
│ 可选：条目打分 Agent（agent_scoring.enrich_articles_agent_scores） │
│ pydantic_ai.Agent + Pydantic 结构化输出 ScoreEnvelope               │
│ 写入 Article.extra["digest_agent_scores"]                        │
│ 依赖：Python ≥3.10；`requirements-agent.txt` 安装 pydantic-ai       │
└─────────────────────────────────────────────────────────────────┘
                              ▲
┌─────────────────────────────┴───────────────────────────────────┐
│ 可选：CS 深度筛选（digest.filter_articles_by_cs_depth）            │
│ 另一路 OpenAI SDK JSON；按分砍条目                                  │
└─────────────────────────────────────────────────────────────────┘
                              ▲
┌─────────────────────────────┴───────────────────────────────────┐
│ 正文 Enrich（enrich.enrich_article_bodies，非 LLM）                │
│ HTTP + trafilatura；条数上限见 DIGEST_FETCH_MAX_ARTICLES            │
└─────────────────────────────────────────────────────────────────┘
                              ▲
┌─────────────────────────────┴───────────────────────────────────┐
│ 采集：多 Source.fetch → dedupe_by_url                             │
└─────────────────────────────────────────────────────────────────┘
```

- **主编**与 **打分 / CS** 默认 **共用** `Settings` 里的 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`。网关须兼容 OpenAI Chat Completions；部分网关不支持 `response_format=json_object`，`digest._chat_completion_json` 已对 400 **降级重试**（去掉 `response_format`）。
- **PydanticAI** 仅在 `DIGEST_AGENT_SCORE=1` 时按需 `import`；未安装或未满足 Python 版本会报错提示。

---

## 系统数据流（pipeline）

```text
环境变量 (.env) → Settings (config.load_settings)
       ↓
collect_articles：多 Source.fetch(since_utc, until_utc)
       ↓
dedupe_by_url
       ↓
enrich_article_bodies（DIGEST_FETCH_BODY / 限额）
       ↓
enrich_articles_agent_scores（DIGEST_AGENT_SCORE）
       ↓
filter_articles_by_cs_depth（DIGEST_CS_FILTER）
       ↓
generate_digest_bundle → output/digest-<年>-W<周>.md
```

- **时间窗**：`time_window.week_bounds_utc` + `DIGEST_TZ`（周一 00:00 起，左闭右开至下周一）。
- **CLI**：`tect_news/cli.py` → `run_pipeline(..., pre_collected=...)`，避免采集跑两次。
- **`--dry-collect`**：只执行采集 + `week_bounds` + 打印，`run_pipeline` 与所有 LLM 均不执行。

---

## 命令一览

| 调用 | 作用 |
|------|------|
| `python -m tect_news` | 全链路生成周报 |
| `python -m tect_news --dry-collect` | 仅采集与列表预览 |
| `python -m tect_news --output-dir PATH` | 输出目录 |
| `python -m tect_news --xiaohongshu-seed PATH` | 小红书种子路径 |

运行时若 `OPENAI_PROMPT_KEY=1`，主编（及可走同一配置的步骤）前先交互输入密钥（见 `cli.py`）。

---

## 目录与模块职责

| 路径 | 职责 |
|------|------|
| `tect_news/config.py` | `.env` → `Settings`（OpenAI 兼容网关、RSS、DIGEST_*、DIGEST_AGENT_*、GitHub …） |
| `tect_news/models.py` | `Article`：`extra` 存 `body_text`、`digest_agent_scores`、Github `stars` 等 |
| `tect_news/pipeline.py` | 采集注册、编排 enrich → 打分 → CS → digest、写 HTML 注释头 |
| `tect_news/enrich.py` | 正文抓取与截断策略 |
| `tect_news/agent_scoring.py` | PydanticAI 批量条目打分 |
| `tect_news/scoring_display.py` | `format_score_inline`（主编展示用；**无** pydantic-ai 依赖） |
| `tect_news/digest.py` | 主编提示词、`_llm_json_draft`、`generate_digest_bundle`、可选 CS 筛选、周报 Markdown |
| `tect_news/digest_html.py` | 周报 HTML 卡片渲染（`DIGEST_OUTPUT_HTML`） |
| `tect_news/verification.py` | 快报正文 URL ⊆ 允许集 |
| `tect_news/urlnorm.py` | URL 规范化 |
| `tect_news/cli.py` | argparse |
| `tect_news/sources/*` | 各数据源 `Source` |
| `data/xiaohongshu_seed.json` | 小红书种子 |
| `output/` | 产出目录 |

数据源现状：

| 源 | 状态 |
|----|------|
| RSS（量子位、GitHub Blog 等） | 在线 |
| GitHub 仓库搜索 | 在线 API |
| Hacker News / Lobsters / InfoQ 中文 | 在线 RSS；`collect_articles` 单源异常仅 stderr 跳过 |
| 机器之心 `jiqizhixin` | 文章库 JSON API（`JIQIZHIXIN_*`） |
| InfoQ 英文 | 可选 `INFOQ_INCLUDE_EN=1` |
| 小红书种子 | **仅本地 JSON**，无 API；`XHS_*` 未接线 |

---

## 扩展与配置

- **依赖文件**：`requirements.txt` 为核心（含 `python-dotenv`）；**PydanticAI 打分**用 `requirements-agent.txt`；`requirements-full.txt` 为二者合并。避免因 `pydantic-ai` 传递依赖过多导致一次安装易断网失败。
- **新数据源**：实现 `sources/base.Source`，在 `collect_articles` 列表中注册；纯 RSS 可复用 `RssSource`。
- **密钥与 profile**：默认 `OPENAI_PROVIDER_PROFILE` 常为 `volcengine`（方舟 Coding）；可切 `smartingredients` 等，见 `config.load_settings`。兼容键名：`SMARTINGREDIENTS_*`、`ANTHROPIC_*` 仅作密钥回退读取，**不要求** Anthropic SDK。
- **合规**：抓取遵守 robots/TOS；生产建议单源故障隔离与重试（当前 MVP 简化）。

---

## 后续演进（非必选）

来源质量分、审核队列、「润色不改事实」二遍、定时任务推送、往期向量检索与问答。
