"""Timezone-aware helpers. Everything user-facing is Europe/Stockholm local time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import (
    AUCTION_CUTOFF_HOUR,
    AUCTION_CUTOFF_MINUTE,
    HORIZON_BUCKETS,
    TIMEZONE,
)

TZ = ZoneInfo(TIMEZONE)
UTC = timezone.utc


def now_local() -> datetime:
    return datetime.now(TZ)


def now_utc() -> datetime:
    return datetime.now(UTC)


def to_local(dt: datetime) -> datetime:
    """Attach or convert to Europe/Stockholm. Naive input is assumed to be UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(TZ)


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(UTC)


def iso(dt: datetime) -> str:
    """ISO-8601 with offset, seconds precision: 2026-09-04T13:18:00+02:00."""
    return to_local(dt).replace(microsecond=0).isoformat()


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 string (with offset or trailing Z) into local time."""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def floor_hour(dt: datetime) -> datetime:
    return to_local(dt).replace(minute=0, second=0, microsecond=0)


def start_of_day(dt: datetime) -> datetime:
    return to_local(dt).replace(hour=0, minute=0, second=0, microsecond=0)


def hour_range(start: datetime, end: datetime) -> list[datetime]:
    """Wall-clock hourly steps from start (inclusive) to end (exclusive).

    Stepping in UTC keeps the sequence correct across DST transitions; the
    returned values are local and may repeat/skip a wall-clock hour, which is
    exactly what the day-ahead market does.
    """
    out: list[datetime] = []
    cur = to_utc(floor_hour(start))
    stop = to_utc(floor_hour(end))
    while cur < stop:
        out.append(cur.astimezone(TZ))
        cur += timedelta(hours=1)
    return out


def horizon_hours(issued_at: datetime, target_ts: datetime) -> int:
    """Whole hours from issue to delivery (floor). Negative for past targets."""
    delta = to_utc(target_ts) - to_utc(issued_at)
    return int(delta.total_seconds() // 3600)


def bucket_for_horizon(horizon_h: int) -> str | None:
    for label, low, high in HORIZON_BUCKETS:
        if low <= horizon_h < high:
            return label
    return None


def auction_publication_time(target_ts: datetime) -> datetime:
    """When the day-ahead result covering target_ts became public knowledge."""
    delivery_day = start_of_day(target_ts)
    return (delivery_day - timedelta(days=1)).replace(
        hour=AUCTION_CUTOFF_HOUR, minute=AUCTION_CUTOFF_MINUTE
    )


def is_official_known(issued_at: datetime, target_ts: datetime) -> bool:
    """True when the official price for target_ts was already published at issue.

    A forecast for an hour whose price is already on the exchange is a copy, not
    a prediction, so it must never be counted as model skill.
    """
    return to_utc(issued_at) >= to_utc(auction_publication_time(target_ts))


def run_id_for(dt: datetime) -> str:
    return to_utc(dt).strftime("%Y%m%dT%H%MZ")
