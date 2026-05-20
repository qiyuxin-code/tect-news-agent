from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from tect_news.models import Article


class Source(ABC):
    name: str

    @abstractmethod
    def fetch(self, since_utc: datetime, until_utc: datetime) -> list[Article]:
        """Return articles with published time in [since_utc, until_utc)."""
        raise NotImplementedError
