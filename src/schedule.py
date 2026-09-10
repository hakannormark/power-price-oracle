"""When the pipeline runs, in Stockholm wall-clock time.

GitHub's cron speaks only UTC, and over the first week it started every
scheduled run four to four and a half hours late. Both break the one timing
that matters here: a run before the 12:45 auction is the only kind that issues a
scoreable day-1 forecast, and a UTC entry that lands after the auction in
summer lands before it in winter.

So the workflow polls every half hour and asks this module whether a slot has
passed without a run. Slots are local times, which keeps them right across DST,
and a late or dropped poll is caught up by the next one.

    python -m src.schedule api/v1/status.json    # prints due=true|false
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .timeutil import TZ, now_local, parse_iso, to_local

# Stockholm wall-clock. Two slots before the auction, because a single missed
# morning run would leave that day with no genuine day-1 forecast at all; one
# after it to pick up the result; one in the evening for fresh weather.
SLOTS_LOCAL = [(6, 30), (10, 15), (13, 30), (18, 0)]


def _slots_around(dt: datetime) -> list[datetime]:
    day = to_local(dt).date()
    out = []
    for offset in (-1, 0, 1):
        d = day + timedelta(days=offset)
        for hour, minute in SLOTS_LOCAL:
            out.append(datetime(d.year, d.month, d.day, hour, minute, tzinfo=TZ))
    return out


def slot_start(dt: datetime) -> datetime:
    """The most recent slot at or before `dt`."""
    local = to_local(dt)
    return max(s for s in _slots_around(local) if s <= local)


def next_slot(dt: datetime) -> datetime:
    """The first slot after `dt`."""
    local = to_local(dt)
    return min(s for s in _slots_around(local) if s > local)


def is_due(last_run: datetime | None, now: datetime | None = None) -> bool:
    """True when a slot has passed since the last run."""
    now = now or now_local()
    return last_run is None or to_local(last_run) < slot_start(now)


def last_run_from_status(path: Path | str) -> datetime | None:
    """When the committed status.json was generated, or None if unreadable."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return parse_iso(payload["generated_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    path = argv[0] if argv else "api/v1/status.json"
    now = now_local()
    last = last_run_from_status(path)
    due = is_due(last, now)
    print(f"due={'true' if due else 'false'}")
    print(f"last run {last}, current slot {slot_start(now)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
