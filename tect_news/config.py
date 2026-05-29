from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# 仓库根目录（含 .env），与进程 cwd 无关
_REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None
    openai_base_url: str | None
    openai_model: str
    openai_wire_api: str  # "chat" | "responses"
    openai_reasoning_effort: str | None
    openai_personality: str | None
    openai_prompt_key: bool
    openai_provider_profile: str
    openai_responses_fallback_chat: bool
    github_token: str | None
    github_api_base_url: str
    xhs_session_cookie: str | None
    xhs_api_base_url: str | None
    xhs_api_key: str | None
    rss_feed_urls: list[str]
    infoq_feed_urls: list[str]
    jiqizhixin_api_base_url: str
    jiqizhixin_max_pages: int
    digest_tz: ZoneInfo
    digest_strict_urls: bool
    digest_output_html: bool
    digest_llm_temperature: float
    digest_llm_max_completion_tokens: int
    digest_fetch_body: bool
    digest_fetch_max_articles: int
    digest_fetch_timeout_sec: float
    digest_fetch_max_response_bytes: int
    digest_body_max_chars: int
    digest_body_prompt_chars: int
    digest_cs_filter: bool
    digest_cs_min_score: int
    digest_cs_top_k: int
    digest_items_per_source: int
    digest_professional_mode: bool
    digest_collect_max_per_source: int
    digest_collect_lookback_days: int
    digest_agent_score: bool
    digest_agent_score_max_articles: int
    digest_agent_score_batch_size: int
    digest_agent_score_temperature: float


def _split_urls(raw: str | None) -> list[str]:
    if not raw or not raw.strip():
        return []
    return [u.strip() for u in raw.split(",") if u.strip()]


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _normalize_wire_api(raw: str) -> str:
    r = raw.strip().lower().replace("-", "_")
    if r in ("responses", "response"):
        return "responses"
    return "chat"


def normalize_openai_base_url(url: str | None) -> str | None:
    """Strip、补全 https://、去掉末尾斜杠；空则 None（由 SDK 走官方默认）。"""
    if url is None:
        return None
    u = str(url).strip()
    if not u:
        return None
    u = u.rstrip("/")
    if not u.startswith(("http://", "https://")):
        u = "https://" + u.lstrip("/")
    return u


def _env_secret(key: str) -> str | None:
    v = os.getenv(key)
    if v is None:
        return None
    s = str(v).strip().strip('"').strip("'")
    return s if s else None


def _resolve_openai_api_key(profile: str) -> str | None:
    """按 profile 优先读取专用密钥，避免 shell 里残留的 OPENAI_API_KEY 盖掉 .env 的 DEEPSEEK_API_KEY。"""
    legacy = (
        _env_secret("SMARTINGREDIENTS_API_KEY")
        or _env_secret("ANTHROPIC_AUTH_TOKEN")
        or _env_secret("ANTHROPIC_API_KEY")
    )
    if profile in ("deepseek", "ds"):
        return _env_secret("DEEPSEEK_API_KEY") or _env_secret("OPENAI_API_KEY") or legacy
    if profile == "smartingredients":
        return _env_secret("SMARTINGREDIENTS_API_KEY") or _env_secret("OPENAI_API_KEY") or legacy
    return _env_secret("OPENAI_API_KEY") or _env_secret("DEEPSEEK_API_KEY") or legacy


def load_settings() -> Settings:
    # 始终加载仓库根下 .env（即使从别的目录运行 python -m tect_news）。
    # override=True：避免 shell 里误 export 了空变量导致无法读到文件里的值。
    env_repo = _REPO_ROOT / ".env"
    if env_repo.is_file():
        load_dotenv(env_repo, override=True)
    load_dotenv(override=False)

    # OpenAI SDK：若环境变量为 OPENAI_BASE_URL=（空串），会当作合法 base_url 传给 httpx，触发
    # UnsupportedProtocol。空值按「未设置」处理。
    raw_openai_base = os.environ.get("OPENAI_BASE_URL")
    if raw_openai_base is not None and not str(raw_openai_base).strip():
        os.environ.pop("OPENAI_BASE_URL", None)

    default_feeds = [
        "https://www.qbitai.com/feed",
        "https://github.blog/feed/",
    ]

    profile_raw = os.getenv("OPENAI_PROVIDER_PROFILE")
    profile = (
        profile_raw.strip().lower()
        if profile_raw and profile_raw.strip()
        else "deepseek"
    )

    default_base: str | None = None
    default_wire = "chat"
    default_effort: str | None = None
    default_personality: str | None = None
    default_openai_model = "deepseek-v4-pro"
    if profile == "smartingredients":
        default_base = "https://ai.smartingredients.my/v1"
        default_wire = "responses"
        default_effort = "xhigh"
        default_personality = "pragmatic"
        default_openai_model = "gpt-5.4"
    elif profile in ("deepseek", "ds"):
        default_base = "https://api.deepseek.com"
    elif profile in ("volcengine", "ark", "coding"):
        default_base = "https://ark.cn-beijing.volces.com/api/coding/v3"
        default_openai_model = "minimax-m2.7"

    extra = _split_urls(os.getenv("RSS_FEED_URLS"))
    feeds = extra if extra else default_feeds

    infoq_custom = _split_urls(os.getenv("INFOQ_FEED_URLS"))
    if infoq_custom:
        infoq_feeds = infoq_custom
    else:
        infoq_feeds = ["https://www.infoq.cn/feed"]
        if _env_bool("INFOQ_INCLUDE_EN", False):
            infoq_feeds.append("https://www.infoq.com/rss/rss.action")

    tz_name = os.getenv("DIGEST_TZ", "Asia/Shanghai")
    digest_tz = ZoneInfo(tz_name)

    base_url_raw = os.getenv("OPENAI_BASE_URL")
    if base_url_raw is None:
        base_url = default_base
    else:
        base_url = base_url_raw.strip() or None
    base_url = normalize_openai_base_url(base_url)

    wire_raw = os.getenv("OPENAI_WIRE_API")
    if wire_raw and wire_raw.strip():
        openai_wire = _normalize_wire_api(wire_raw)
    else:
        openai_wire = _normalize_wire_api(default_wire)

    effort_raw = os.getenv("OPENAI_REASONING_EFFORT")
    if effort_raw is None:
        effort_val = default_effort
    elif effort_raw.strip():
        effort_val = effort_raw.strip()
    else:
        effort_val = None

    personality_raw = os.getenv("OPENAI_PERSONALITY")
    if personality_raw is None:
        personality_val = default_personality
    elif personality_raw.strip():
        personality_val = personality_raw.strip()
    else:
        personality_val = None

    _model_env = os.getenv("OPENAI_MODEL")
    model = (
        _model_env.strip()
        if _model_env is not None and str(_model_env).strip()
        else default_openai_model
    ).strip() or default_openai_model

    pk_raw = os.getenv("OPENAI_PROMPT_KEY")
    if pk_raw is None or pk_raw.strip() == "":
        openai_prompt_key = False
    else:
        openai_prompt_key = _env_bool("OPENAI_PROMPT_KEY", False)

    openai_api_key = _resolve_openai_api_key(profile)

    return Settings(
        openai_api_key=openai_api_key,
        openai_base_url=base_url,
        openai_model=model,
        openai_wire_api=openai_wire,
        openai_reasoning_effort=effort_val,
        openai_personality=personality_val,
        openai_prompt_key=openai_prompt_key,
        openai_provider_profile=profile,
        openai_responses_fallback_chat=_env_bool("OPENAI_RESPONSES_FALLBACK_CHAT", True),
        github_token=os.getenv("GITHUB_TOKEN") or None,
        github_api_base_url=os.getenv("GITHUB_API_BASE_URL", "https://api.github.com").rstrip("/"),
        xhs_session_cookie=os.getenv("XHS_SESSION_COOKIE") or None,
        xhs_api_base_url=os.getenv("XHS_API_BASE_URL") or None,
        xhs_api_key=os.getenv("XHS_API_KEY") or None,
        rss_feed_urls=feeds,
        infoq_feed_urls=infoq_feeds,
        jiqizhixin_api_base_url=os.getenv(
            "JIQIZHIXIN_API_BASE_URL", "https://www.jiqizhixin.com"
        ).rstrip("/"),
        jiqizhixin_max_pages=max(1, _env_int("JIQIZHIXIN_MAX_PAGES", 8)),
        digest_tz=digest_tz,
        digest_strict_urls=_env_bool("DIGEST_STRICT_URLS", True),
        digest_output_html=_env_bool("DIGEST_OUTPUT_HTML", True),
        digest_llm_temperature=_env_float("DIGEST_LLM_TEMPERATURE", 0.35),
        digest_llm_max_completion_tokens=min(
            128_000,
            max(1024, _env_int("DIGEST_LLM_MAX_COMPLETION_TOKENS", 16384)),
        ),
        digest_fetch_body=_env_bool("DIGEST_FETCH_BODY", True),
        digest_fetch_max_articles=max(0, _env_int("DIGEST_FETCH_MAX_ARTICLES", 40)),
        digest_fetch_timeout_sec=max(3.0, _env_float("DIGEST_FETCH_TIMEOUT_SEC", 22.0)),
        digest_fetch_max_response_bytes=max(
            50_000, _env_int("DIGEST_FETCH_MAX_RESPONSE_BYTES", 2_000_000)
        ),
        digest_body_max_chars=max(500, _env_int("DIGEST_BODY_MAX_CHARS", 12_000)),
        digest_body_prompt_chars=max(200, _env_int("DIGEST_BODY_PROMPT_CHARS", 6_000)),
        digest_cs_filter=_env_bool("DIGEST_CS_FILTER", False),
        digest_cs_min_score=min(5, max(1, _env_int("DIGEST_CS_MIN_SCORE", 3))),
        digest_cs_top_k=max(0, _env_int("DIGEST_CS_TOP_K", 0)),
        digest_items_per_source=max(1, _env_int("DIGEST_ITEMS_PER_SOURCE", 20)),
        digest_professional_mode=_env_bool("DIGEST_PROFESSIONAL_MODE", True),
        digest_collect_max_per_source=max(1, _env_int("DIGEST_COLLECT_MAX_PER_SOURCE", 100)),
        digest_collect_lookback_days=max(1, _env_int("DIGEST_COLLECT_LOOKBACK_DAYS", 90)),
        digest_agent_score=_env_bool("DIGEST_AGENT_SCORE", False),
        digest_agent_score_max_articles=max(0, _env_int("DIGEST_AGENT_SCORE_MAX_ARTICLES", 48)),
        digest_agent_score_batch_size=max(4, _env_int("DIGEST_AGENT_SCORE_BATCH_SIZE", 18)),
        digest_agent_score_temperature=_env_float("DIGEST_AGENT_SCORE_TEMPERATURE", 0.15),
    )
