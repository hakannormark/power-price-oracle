"""Backfill historical weather so the weather model can be tested at all.

    python -m src.fetch_weather_archive --years 4

Open-Meteo's forecast endpoint only reaches seven days back, which is enough to
run the model and not nearly enough to check whether it works. The archive
endpoint serves ERA5 reanalysis for the same variables, free and unauthenticated,
so every weather coefficient can be scored against four years of outcomes
instead of being asserted.

Stored as data/weather/archive/<point>.jsonl, one file per location.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import timedelta

from .config import ALL_WEATHER_POINTS, WEATHER_ARCHIVE_DIR, WEATHER_ARCHIVE_URL, ensure_dirs
from .fetch.http import get
from .store import r3, write_jsonl
from .timeutil import TZ, now_local

log = logging.getLogger("weather-archive")

VARIABLES = ["temperature_2m", "wind_speed_10m", "shortwave_radiation", "precipitation"]
COLUMNS = {
    "temperature_2m": "temp",
    "wind_speed_10m": "wind",
    "shortwave_radiation": "solar",
    "precipitation": "precip",
}


def fetch_point(name: str, lat: float, lon: float, start, end) -> int:
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

    WEATHER_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    write_jsonl(WEATHER_ARCHIVE_DIR / f"{name}.jsonl", rows)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill ERA5 weather history")
    parser.add_argument("--years", type=float, default=4)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    ensure_dirs()
    now = now_local()
    # The archive lags real time by a few days; stop short of it.
    end = now - timedelta(days=6)
    start = now - timedelta(days=int(365 * args.years))

    total = 0
    for name, (lat, lon) in ALL_WEATHER_POINTS.items():
        try:
            count = fetch_point(name, lat, lon, start, end)
        except Exception as exc:  # noqa: BLE001
            print(f"  {name}: FAILED — {str(exc)[:90]}")
            continue
        total += count
        print(f"  {name}: {count:,} hours")

    print(f"weather archive: {total:,} hours across {len(ALL_WEATHER_POINTS)} points")
    return 0


if __name__ == "__main__":
    sys.exit(main())
