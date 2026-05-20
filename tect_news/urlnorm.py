from __future__ import annotations


def normalize_url(url: str) -> str:
    """Stable key for allowlist checks (fragment stripped, no trailing slash)."""
    return url.strip().split("#", 1)[0].rstrip("/")
