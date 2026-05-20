from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Article:
    title: str
    url: str
    source: str
    summary: str | None = None
    published_at: datetime | None = None
    extra: dict = field(default_factory=dict)
