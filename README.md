# tect-news-agent

多源采集 **本周** 技术条目，经（可选）正文抓取、（可选）LLM 条目打分、主编式大模型 **结构化 JSON → Markdown**，生成**中文技术快报**：`output/digest-<年>-W<周>.md`。

上游模型走 **OpenAI Python SDK 兼容** 的 HTTP 接口（默认 **DeepSeek V4**；也可换火山方舟、smartingredients 等兼容网关）。

---

## 环境与依赖

- **Python**：基线 **3.9** 可跑主编快报；启用 **PydanticAI 条目打分**（`DIGEST_AGENT_SCORE=1`）需 **≥3.10**，并额外安装打分依赖。
- **虚拟环境**（建议 Homebrew Python 3.12）：

```bash
cd /path/to/tect-news-agent
/opt/homebrew/bin/python3.12 -m venv .venv   # 换成你本机 python3.12 路径
source .venv/bin/activate
python -m pip install -U pip

# 只跑快报（体积小，推荐先试通）
pip install -r requirements.txt

pip install -r requirements-agent.txt# 需要 DIGEST_AGENT_SCORE=1 时再装（包多，含 grpcio 等，易受网络中断影响）


# 或一条命令装全量：pip install -r requirements-full.txt
```

若 `pip install` 中途报 **`IncompleteRead` / `Connection broken`**：多为下载大轮子时网络断开，可多试几次，或：

```bash
pip install --default-timeout=600 -r requirements-agent.txt
```

- 配置文件：复制 `.env.example` 为 `.env`，至少配置 **OpenAI 兼容** 的密钥与地址（见下文）。

---

## 命令（CLI）

入口：`python -m tect_news`（在项目目录下激活 venv 后执行）。

| 命令 | 说明 |
|------|------|
| `python -m tect_news` | 全链路：采集 → 正文 enrich（可关）→ **PydanticAI 打分**（可选）→ **CS 深度筛选**（可选）→ **主编 JSON 快报** → 写入 `output/` |
| `python -m tect_news --dry-collect` | 只采集、去重并打印条目（**不调任何大模型**）；用于测网络与时间窗 |
| `python -m tect_news --output-dir /自定义目录` | 指定输出目录 |
| `python -m tect_news --xiaohongshu-seed path/to.json` | 指定小红书种子 JSON（默认 `data/xiaohongshu_seed.json`） |

完整生成后，默认输出：

- `output/digest-<年>-W<周>.md`（含**本周关键词**、条目标签，可读性增强）
- `output/digest-<年>-W<周>.html`（卡片布局，浏览器直接打开；`DIGEST_OUTPUT_HTML=0` 可关）

终端会打印上述文件的绝对路径。

---

## 「Agent」在这条链路里是什么

1. **主编生成（必走，除非 `--dry-collect`）**  
   `digest.py`：用 **OpenAI SDK** `chat.completions`（或配置的 `responses` 线路）拉 JSON，再渲染 Markdown，并做 URL 白名单校验。  
   部分网关不支持 `response_format=json_object` 时会自动降级为普通文本再解析。

2. **PydanticAI 条目打分（可选，`DIGEST_AGENT_SCORE=1`）** — 需 `pip install -r requirements-agent.txt`  
   `agent_scoring.py`：`pydantic_ai.Agent` + 结构化输出，对条目批量打 **technical_signal / credibility / source_trust / signal_to_noise**（1–5），写入 `Article.extra["digest_agent_scores"]`。  
   主编提示里会带上 `agent分 tech=…` 等行作为**弱先验**，不取代事实核验。

3. **CS 深度筛选（可选，`DIGEST_CS_FILTER=1`）**  
   `digest.filter_articles_by_cs_depth`：另一类 JSON 打分调用，可在进入主编前砍掉部分条目。

以上 2、3 与主编 **共用** `DEEPSEEK_API_KEY`（或 `OPENAI_API_KEY`）、`OPENAI_BASE_URL`、`OPENAI_MODEL`。

---

## 数据源（采集）

默认在 `pipeline.collect_articles` 中注册（时间窗 **[since, until)**，`DIGEST_TZ` 定「本周」）：

| 类型 | 说明 |
|------|------|
| RSS | 默认量子位 + GitHub Blog；`RSS_FEED_URLS` 非空则**整表替换** |
| GitHub 仓库搜索 | `GITHUB_TOKEN` 可选 |
| 小红书种子 | `data/xiaohongshu_seed.json` |
| Hacker News / Lobsters | 官方 RSS |
| InfoQ 中文 | 默认 `https://www.infoq.cn/feed` |
| InfoQ 英文 | 可选：`INFOQ_INCLUDE_EN=1` 或 `INFOQ_FEED_URLS` |
| 机器之心 | 文章库 API `api/article_library/articles.json` |
| 小红书 | **未在线采集**：仅读本地 `data/xiaohongshu_seed.json`（默认可为空数组） |

合并后 **按 URL 去重**。单源网络失败会跳过并打 stderr，不中断其它源。

> Hacker News / Lobsters 等为境外 RSS，需代理或稳定网络；GitHub API 若经代理 TLS 异常可在 `.env` 配置 `NO_PROXY=api.github.com,github.com`。

---

## 正文抓取（enrich）

`enrich.py`：对条目链接拉 HTML，`trafilatura` 抽正文写入 `Article.extra["body_text"]`。  
限额由 `DIGEST_FETCH_MAX_ARTICLES`（默认 40）等与超时、体积上限一起在 `.env.example` 中说明——**不影响采集总数**，只限制「拉全文的篇数」。

---

## `.env` 要点（节选）

详见 `.env.example`。

- **LLM 网关**：`DEEPSEEK_API_KEY`（或 `OPENAI_API_KEY`）、`OPENAI_BASE_URL`、`OPENAI_MODEL`；可选 `OPENAI_PROVIDER_PROFILE`（默认 `deepseek`；亦可 `volcengine` / `smartingredients`）。
- **路由**：快报主编默认 `OPENAI_WIRE_API=chat`。
- **交互输入密钥**：`OPENAI_PROMPT_KEY=1`。
- **快报**：`DIGEST_STRICT_URLS`、`DIGEST_LLM_TEMPERATURE`、`DIGEST_TZ`、`DIGEST_FETCH_*`、`DIGEST_AGENT_SCORE*`、`DIGEST_CS_*`。

---

## 小红书种子格式

将与时间窗匹配的条目写入 `data/xiaohongshu_seed.json`，例如：

`[{"title":"...","url":"https://...","summary":"可选","published_at":"2026-05-08T10:00:00+08:00"}]`。

---

## 更多架构说明

- **`CLAUDE.md`**：面向人类与 Claude Code / 通用助手的完整架构说明。
- **`.cursor/rules/`**：Cursor 项目规则（`alwaysApply`），与 `CLAUDE.md` 互补。
