"""独立于 PydanticAI 的条目分数字符串格式化（digest 可随时 import）。"""
from __future__ import annotations


def format_score_inline(extra: dict | None) -> str | None:
    if not isinstance(extra, dict):
        return None
    raw = extra.get("digest_agent_scores")
    if not isinstance(raw, dict):
        return None
    try:
        t = int(raw["technical_signal"])
        c = int(raw["credibility"])
        tr = int(raw["source_trust"])
        sn = int(raw["signal_to_noise"])
    except (KeyError, TypeError, ValueError):
        return None
    return f"agent分 tech={t} cred={c} trust={tr} clean={sn}"
