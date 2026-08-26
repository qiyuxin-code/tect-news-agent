# 任务计划：把 tect-news-agent 从「LLM-as-function」pipeline 改造成真正的 agent（自带 harness）

## 目标
在不破坏现有确定性 pipeline 的前提下，基于 **Pydantic AI** 构建 agent harness（tools + 内置 agent 循环 + skill 系统 + 可选 MCP），
让主编 LLM 能自主调用 collect / enrich / score / filter / draft / verify / save 工具，多轮迭代产出周报。

## 当前阶段
阶段 9（交付）— 实现已全部完成，6 个测试通过

## 各阶段

### 阶段 1：需求与发现 ✅
- [x] 通读 pipeline / digest / agent_scoring / cli / config / models
- [x] 确认现状：无 tools、无 function call、无 MCP、无 skill；LLM 全部是单次 JSON 调用
- [x] 确认用户意图：基于 Pydantic AI（项目已用）构建 harness
- [x] 确认约束：保留 `run_pipeline` 旧路径作为默认/降级；复用现有函数，不重写采集
- **状态：** complete

### 阶段 2：规划与结构 ✅
- [x] 设计 harness 架构（基于 Pydantic AI Agent，不写自研 loop）
- [x] 确定工具清单（@agent.tool 包装现有 pipeline 函数）
- [x] 记录决策及理由（写 findings.md）
- **状态：** complete

### 阶段 3：agent 基建与依赖 ✅
- [x] python3.12 venv（.venv）+ pydantic-ai 2.33.0 + socksio
- [x] `DigestDeps`（deps_type）+ 主编 Agent 定义
- [x] `output_type` Pydantic 模型：`DigestDraft`
- [x] `@agent.system_prompt` 动态注入：写作规则 + 人格 + skill 内容
- **状态：** complete

### 阶段 4：把 pipeline 步骤做成 @agent.tool ✅
- [x] `collect_news` / `enrich_bodies` / `score_articles` / `filter_by_cs` / `verify_urls`（5 工具；draft/save 由 output_type + 落盘逻辑承担，未做成工具）
- [x] `run_agent_digest()` 入口 + cli `--agent` 开关
- **状态：** complete

### 阶段 5：skill 系统 ✅
- [x] `tect_news/harness/skills.py` 目录加载，注入 system prompt
- [x] 内置 `editor-workflow`、`compliance` skill
- [x] `.env.example` 加 SKILLS_DIR 配置
- **状态：** complete

### 阶段 6：可选 MCP 接入 ✅
- [x] `pydantic_ai.capabilities.MCP` 从 `MCP_CONFIG` 构建，并入同一循环（连接延迟到 run）
- [x] `MCP_CONFIG` JSON 配置；未配置跳过；坏配置告警不拖垮
- **状态：** complete

### 阶段 7：护栏与可观测性 ✅
- [x] `UsageLimits(request_limit / total_tokens_limit)` + Agent retries + `ModelRetry`
- [x] Hooks（before_tool_execute / after_run 打印）
- [x] 终稿强制 `verify_urls`（strict 时失败即 raise）
- **状态：** complete

### 阶段 8：测试与验证 ✅
- [x] 6 个单测（loop/validator retry/skills×2/MCP build/run_agent_digest 落盘）
- [x] `python -m tect_news --dry-collect` 跑通
- [x] 缺 key 守护报错验证
- **状态：** complete

### 阶段 9：交付 ✅
- [x] 更新 CLAUDE.md / README / .env.example / requirements-agent.txt
- **状态：** complete

## 关键问题
1. function calling 支持度：Pydantic AI 结构化输出走 tool call（`final_result`），依赖网关支持 function calling（DeepSeek 支持）。不支持则回退 `_chat_completion_json` 旧路径。
2. 工具结果回填体积：score/enrich 结果大，需截断摘要喂给下一步 → deps 里存全量，工具返回精简。
3. MCP 是否本期必须 → 可选，默认关闭。

## 已做决策
| 决策 | 理由 |
|------|------|
| 基于 Pydantic AI 构建 harness，不引其他框架 | 项目已在用 pydantic-ai（agent_scoring.py）；自带 agent 循环/tools/结构化输出/MCP/hooks |
| 旧 pipeline 保留为默认，agent 版用 `--agent` 开启 | 降级安全，不破坏现有产出 |
| 工具 = `@agent.tool` 包装现有函数，deps 注入 Settings/文章/输出目录 | 最大化复用已验证逻辑 |
| 结构化输出 = `output_type=DigestDraft` Pydantic 模型 | 取代手写 `_parse_json_object`/`_normalize_draft`，自动校验+重试 |
| URL 白名单 = `@agent.output_validator` | 模型输出非法 URL 时自动重试修正，优于事后过滤 |
| skill = SKILL.md 目录 + `@agent.system_prompt` 动态注入 | 轻量、复用现有写作规则 |
| MCP = `pydantic_ai.mcp.MCPServerHTTP`（optional） | 官方支持，默认关闭 |
| 环境：Python 3.12 venv（当前默认 3.9.6 不满足 pydantic-ai） | pydantic-ai 要求 ≥3.10；机器上有 /opt/homebrew/bin/python3.12 |

## 遇到的错误
| 错误 | 尝试次数 | 解决方案 |
|------|---------|---------|
| 暂无 | 1 | |

## 备注
- 阶段 3-7 每个子项完成后更新本文件状态
- 任何 API 调用失败：先看错误，换方案，绝不盲目重试
- 实现前先读本计划刷新目标
