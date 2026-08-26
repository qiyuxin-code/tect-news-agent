# 发现与决策

## 需求
- 把「带 LLM 步骤的确定性 pipeline」改造成**真正的 agent**：具备 tools / function call / skills / 可选 MCP
- 用户选择：**基于 Pydantic AI**（项目已在用）构建 harness，不引其他框架
- 复用现有采集/打分/校验逻辑，保留旧 pipeline 作为降级路径

## 研究发现（现状盘点 + Pydantic AI 能力确认）
- **无**任何 tools / function call / MCP / skill 命中（全库 grep `tools|function|mcp|skill|tool_call` = 0 个 Python 命中）
- LLM 调用只有 3 处，全是**单次调用**：
  1. `digest.py` 主编：`client.chat.completions.create` / `client.responses.create`，单发 JSON
  2. `digest.py` CS 打分：`_chat_completion_json` 单发 JSON
  3. `agent_scoring.py` PydanticAI `Agent.run_sync`：单轮结构化打分，**无 tools、无循环**
- 编排是硬编码：`pipeline.py:run_pipeline`（collect→dedupe→enrich→score→filter→digest→verify→write）
- 已有可复用资产：
  - OpenAI 兼容网关封装：`_call_editor_llm`（chat/responses 双 wire、`response_format` 400 降级）
  - JSON 解析容错：`_parse_json_object`（拆围栏/配对括号/去尾逗号）
  - 校验：`verification.verify_urls_subset` + `_filter_by_allowlist`
  - 写作约束：`EDITOR_WORKFLOW`、`_json_instructions`、`_EDITOR_ROLE`（可抽成 skill）
  - 来源抽象：`sources/base.Source`（所有采集器已实现）
- **Pydantic AI 1.x 官方文档确认的能力**：Agent / @agent.tool + RunContext(deps 注入) / output_type 结构化输出 / @agent.output_validator / @agent.system_prompt 动态注入 / ModelSettings / message_history / agent.hooks 可观测 / RunResult.usage() 计费 / CancellationToken / 多 agent 委托 / pydantic_ai.mcp.MCPServerHTTP / agent.iter() 图级控制
- **环境约束**：默认 python3=3.9.6 不满足 pydantic-ai（≥3.10）；机器有 /opt/homebrew/bin/python3.12，需建 venv

## 技术决策
| 决策 | 理由 |
|------|------|
| 基于 Pydantic AI 1.x 构建，`tect_news/agent_editor.py` + `tect_news/harness/` | 项目已用；自带循环/工具/输出/校验/MCP/hooks，零自研 loop |
| 工具 = `@agent.tool` 包装现有函数，签名由类型注解+docstring 生成 | 最大化复用，LLM 可控 |
| deps_type=`DigestDeps`：settings/时间窗/collected/output_dir/log | Pydantic AI 官方 deps 注入模式 |
| 结构化输出 = `output_type=DigestDraft`，校验失败自动重试 | 取代手写 JSON 容错 |
| 白名单校验 = `@agent.output_validator`（不通过则重试） | 优于事后 `_filter_by_allowlist` |
| skill = SKILL.md 目录，经 `@agent.system_prompt` 动态注入 | 轻量、可组合 |
| MCP = `pydantic_ai.mcp.MCPServerHTTP` 按需加载 | optional，默认关闭 |
| 网关不支持 tools（结构化输出走 tool call）→ 回退旧路径 | 兼容 DeepSeek 等网关，保证可用 |
| 环境用 Python 3.12 venv | pydantic-ai ≥3.10 要求 |

## 工具清单（阶段 4）
| 工具 | 包装 | 说明 |
|------|------|------|
| `collect_news` | `collect_articles`+dedupe+pool | 传时间窗/源过滤 |
| `enrich_bodies` | `enrich_article_bodies` | 抓正文 |
| `score_articles` | `agent_scoring`+`score_articles_cs_depth` | 多维/CS 打分 |
| `filter_by_cs` | `filter_articles_by_cs_depth`/`professional` | 砍条目 |
| `draft_digest` | `_llm_json_draft`+`_normalize_draft`+`_render_markdown` | 产出 JSON draft（工具内再调一次 LLM） |
| `verify_urls` | `verify_urls_subset` | 校验白名单 |
| `save_digest` | 写 output/digest-<week>.md/.html | 落盘 |
| `search_archived` | 读 output/ 历史 | 往期交叉引用（可选） |

## 遇到的问题
| 问题 | 解决方案 |
|------|---------|
| 工具结果（打分/正文）过大 | state 存全量，回填 prompt 用 `_article_teaser_text` 式截断 |
| 网关是否支持 tools | 复用 `_chat_completion_json` 的降级思路：tools 不支持时回退单次 JSON（即旧路径） |
| 死循环风险 | max_steps 硬上限 + 每步 token 预算 + 终稿校验 |

## 资源
- 现有模块：`pipeline.py` `digest.py` `agent_scoring.py` `verification.py` `enrich.py` `config.py`
- 写作规则源：`digest.py:EDITOR_WORKFLOW`、`_json_instructions`、`_EDITOR_ROLE`
- OpenAI function calling 文档（chat.completions `tools` 参数）

## 视觉/浏览器发现
- 无浏览器操作
