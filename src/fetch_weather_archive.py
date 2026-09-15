"""Backfill historical weather so the weather model can be tested at all.

    python -m src.fetch_weather_archive --years 4     # first fill, rewrites
    python -m src.fetch_weather_archive --refresh     # top up what is missing

Open-Meteo's forecast endpoint only reaches seven days back, which is enough to
run the model and not nearly enough to check whether it works. The archive
endpoint serves ERA5 reanalysis for the same variables, free and unauthenticated,
so every weather coefficient can be scored against four years of outcomes
instead of being asserted.

The refresh mode exists because the first fill was a one-off: the archive stood
still at 2026-08-29 while the models kept being fitted on it, so the training
data aged a day for every day the site ran. It re-requests the last few days as
well as the missing ones, because ERA5 publishes provisional values that are
later revised.

Stored as data/weather/archive/<point>.jsonl, one file per location.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta

from .config import ALL_WEATHER_POINTS, WEATHER_ARCHIVE_DIR, WEATHER_ARCHIVE_URL, ensure_dirs
from .fetch.http import get
from .store import r3, read_jsonl, write_jsonl
from .timeutil import TZ, now_local

log = logging.getLogger("weather-archive")

VARIABLES = ["temperature_2m", "wind_speed_10m", "shortwave_radiation", "precipitation"]
COLUMNS = {
    "temperature_2m": "temp",
    "wind_speed_10m": "wind",
    "shortwave_radiation": "solar",
    "precipitation": "precip",
}

# ERA5 lags real time by several days; stop short of the edge rather than store
# hours the reanalysis has not settled.
ARCHIVE_LAG_DAYS = 6
# Re-request this much of the stored tail: provisional values get revised.
REFRESH_OVERLAP_DAYS = 3


def _point_path(name: str):
    return WEATHER_ARCHIVE_DIR / f"{name}.jsonl"


def last_stored_ts(name: str) -> str | None:
    """The newest hour already stored for a point, as the file's own timestamp string."""
    newest: str | None = None
    for row in read_jsonl(_point_path(name)):
        stamp = row.get("ts")
        if stamp and (newest is None or stamp > newest):
            newest = stamp
    return newest


def merge_rows(existing: list[dict], fresh: list[dict]) -> list[dict]:
    """Fresh values win for the hours they cover; everything else is kept.

    ERA5 revises recent hours, so an overlapping request is a correction rather
    than a duplicate.
    """
    merged = {row["ts"]: row for row in existing}
    for row in fresh:
        merged[row["ts"]] = row
    return [merged[ts] for ts in sorted(merged)]


def fetch_rows(name: str, lat: float, lon: float, start: datetime, end: datetime) -> list[dict]:
    """One request per location; the archive returns the whole span at once."""
    payload = get(
        WEATHER_ARCHIVE_URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "start_date": start.date().isoformat(),
            "end_date": end.date().isoformat(),
            "hourly": ",".join(VARIABLES),
            # Requested in UTC and converted here: local timestamps make the
            # repeated hour at the DST fall-back ambiguous.
            "timezone": "UTC",
        },
    ).json()

    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    rows = []
    for index, stamp in enumerate(times):
        row = {"ts": stamp, "point": name}
        for source, target in COLUMNS.items():
            series = hourly.get(source) or []
            value = series[index] if index < len(series) else None
            row[target] = r3(value) if value is not None else None
        rows.append(row)
    return rows


def store_point(name: str, rows: list[dict], merge: bool) -> int:
    WEATHER_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    if merge:
        rows = merge_rows(list(read_jsonl(_point_path(name))), rows)
    write_jsonl(_point_path(name), rows)
    return len(rows)


def refresh_start(name: str, now: datetime, years: float) -> datetime:
    """Where a top-up should begin: just behind what is already stored."""
    newest = last_stored_ts(name)
    if newest is None:
        return now - timedelta(days=int(365 * years))
    try:
        stored = datetime.fromisoformat(newest)
    except ValueError:
        return now - timedelta(days=int(365 * years))
    if stored.tzinfo is None:
        stored = stored.replace(tzinfo=TZ)
    return stored - timedelta(days=REFRESH_OVERLAP_DAYS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill ERA5 weather history")
    parser.add_argument("--years", type=float, default=4)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Fetch only what is missing since the stored tail, and merge it in",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    ensure_dirs()
    now = now_local()
    end = now - timedelta(days=ARCHIVE_LAG_DAYS)

    total = 0
    for name, (lat, lon) in ALL_WEATHER_POINTS.items():
        start = refresh_start(name, now, args.years) if args.refresh else now - timedelta(
            days=int(365 * args.years)
        )
        if start >= end:
            print(f"  {name}: already current")
            continue
        try:
            rows = fetch_rows(name, lat, lon, start, end)
            count = store_point(name, rows, merge=args.refresh)
        except Exception as exc:  # noqa: BLE001
            print(f"  {name}: FAILED — {str(exc)[:90]}")
            continue
        total += count
        span = f"{start.date()} .. {end.date()}"
        print(f"  {name}: {len(rows):,} new hours ({span}), {count:,} stored")

    print(f"weather archive: {total:,} hours across {len(ALL_WEATHER_POINTS)} points")
    return 0


if __name__ == "__main__":
    sys.exit(main())
