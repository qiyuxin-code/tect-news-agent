from __future__ import annotations

import re
from dataclasses import dataclass, field

from tect_news.urlnorm import normalize_url

# Markdown links；综述中若出现裸 URL 也会被校验
_MD_LINK = re.compile(r"\[[^\]]*\]\((https?://[^\s)]+)\)", re.I)
_BARE_URL = re.compile(r"(?<!\()(https?://[^\s<>\[\]()\"']+)", re.I)


def _strip_trailing_punct(url: str) -> str:
    return url.rstrip(".,;:\"')]}>")


@dataclass
class VerificationResult:
    ok: bool
    urls_in_output: list[str] = field(default_factory=list)
    urls_unknown: list[str] = field(default_factory=list)

    def to_header_comment(self) -> str:
        parts = [
            f"verification_ok={self.ok}",
            f"urls_in_doc={len(self.urls_in_output)}",
            f"urls_unknown={len(self.urls_unknown)}",
        ]
        if self.urls_unknown:
            parts.append("unknown=" + ",".join(self.urls_unknown[:20]))
        return " ".join(parts)


def extract_urls_from_markdown(text: str) -> list[str]:
    found: list[str] = []
    for m in _MD_LINK.finditer(text):
        found.append(_strip_trailing_punct(m.group(1).strip()))
    for m in _BARE_URL.finditer(text):
        found.append(_strip_trailing_punct(m.group(1).strip()))
    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for u in found:
        k = normalize_url(u)
        if k in seen:
            continue
        seen.add(k)
        out.append(u)
    return out


def verify_urls_subset(markdown: str, allowed_normalized: set[str]) -> VerificationResult:
    raw = extract_urls_from_markdown(markdown)
    unknown: list[str] = []
    for u in raw:
        if normalize_url(u) not in allowed_normalized:
            unknown.append(u)
    return VerificationResult(
        ok=len(unknown) == 0,
        urls_in_output=raw,
        urls_unknown=sorted(set(unknown)),
    )
