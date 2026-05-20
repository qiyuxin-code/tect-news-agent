from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


def week_bounds_utc(now_utc: datetime, digest_tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Return [week_start, week_end) in UTC for the calendar week (Mon–Sun) in digest_tz."""
    now_local = now_utc.astimezone(digest_tz)
    monday = now_local.date() - timedelta(days=now_local.weekday())
    week_start_local = datetime.combine(monday, datetime.min.time(), tzinfo=digest_tz)
    next_monday = week_start_local + timedelta(days=7)
    return week_start_local.astimezone(UTC), next_monday.astimezone(UTC)
