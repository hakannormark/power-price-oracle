"""Hourly solar production per installed kWp, per bidding zone, from measured radiation.

    python -m src.bess.solar            # fill or top up data/bess/solar/<year>.json

The household profile used to give every zone Malmö's sun: one latitude for the
solar height, one yield per kWp, and a clear sky every day of the year. A
battery in Luleå was valued against sun it does not get in winter, and no zone
ever had an overcast day, so the battery filled neatly every summer afternoon.

This module stores global horizontal radiation from ERA5 (Open-Meteo's archive)
at each zone's reference point, hourly from 2015, and turns it into production:

    kWh per kWp and hour = radiation [W/m2] / 1000 * SYSTEM_FACTOR

SYSTEM_FACTOR folds the gain from tilting the panels, the losses in the system
and ERA5's slightly generous radiation into one number. 0.88 gives about 1 000
kWh per kWp and year in Malmö, 910 in Stockholm and 800 in Luleå, in line with
what Swedish installations report. A flat
panel is assumed for the shape over the day and the year, which gives winter a
little less than a tilted panel gets.

The files are tracked: a year is about 100 kB, a finished year never changes,
and the pipeline in CI must be able to read them without downloading anything.
ERA5 runs about a week behind, so the most recent days are filled with the mean
of the same hour over the fortnight before.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import date, datetime, timedelta, timezone

import numpy as np

from ..config import DATA_DIR, WEATHER_ARCHIVE_URL, ZONES
from ..fetch.http import get

log = logging.getLogger("bess-solar")

DIR = DATA_DIR / "bess" / "solar"
FIRST_YEAR = 2015
SYSTEM_FACTOR = 0.88
ARCHIVE_LAG_DAYS = 6
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _hour(ts: datetime) -> int:
    return int((ts.astimezone(timezone.utc) - EPOCH).total_seconds() // 3600)


def _year_path(year: int):
    return DIR / f"{year}.json"


def _load_year(year: int) -> dict | None:
    try:
        return json.loads(_year_path(year).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _fetch(lat: float, lon: float, start: date, end: date) -> tuple[int, list[float | None]]:
    payload = get(WEATHER_ARCHIVE_URL, params={
        "latitude": lat, "longitude": lon, "hourly": "shortwave_radiation",
        "start_date": start.isoformat(), "end_date": end.isoformat(), "timezone": "UTC",
    }).json()
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        raise RuntimeError(f"solar: empty response {start}..{end}")
    first = _hour(datetime.fromisoformat(times[0]).replace(tzinfo=timezone.utc))
    return first, hourly.get("shortwave_radiation") or []


def refresh(until: date | None = None) -> dict[int, int]:
    """Download what is missing, year by year. A year already complete is left alone."""
    DIR.mkdir(parents=True, exist_ok=True)
    until = until or (datetime.now(timezone.utc).date() - timedelta(days=ARCHIVE_LAG_DAYS))
    stored: dict[int, int] = {}
    for year in range(FIRST_YEAR, until.year + 1):
        start, end = date(year, 1, 1), min(date(year, 12, 31), until)
        hour0 = _hour(datetime(year, 1, 1, tzinfo=timezone.utc))
        n = (end - start).days * 24 + 24
        old = _load_year(year)
        if old and all(len(old["ghi"].get(z, [])) >= n for z in ZONES) and old.get("complete_through") == end.isoformat():
            stored[year] = n
            continue
        ghi = {}
        for zone, info in ZONES.items():
            first, values = _fetch(info["lat"], info["lon"], start, end)
            assert first == hour0, f"solar: unexpected first hour for {zone} {year}"
            ghi[zone] = [None if v is None else int(round(v)) for v in values[:n]]
            time.sleep(0.5)
        _year_path(year).write_text(json.dumps({
            "source": "ERA5 via Open-Meteo archive, shortwave_radiation (global horizontal), W/m2, UTC hours",
            "hour0": hour0, "complete_through": end.isoformat(), "ghi": ghi,
        }, separators=(",", ":")), encoding="utf-8")
        stored[year] = n
        log.info("solar %s: %s hours per zone", year, n)
    return stored


_CACHE: dict[str, tuple[int, np.ndarray]] = {}


def _series(zone: str) -> tuple[int, np.ndarray]:
    """(first hour index, W/m2 per hour) over every stored year, NaN where missing."""
    if zone in _CACHE:
        return _CACHE[zone]
    parts = []
    for path in sorted(DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        values = np.array([np.nan if v is None else v for v in data["ghi"].get(zone, [])], dtype=float)
        parts.append((int(data["hour0"]), values))
    if not parts:
        _CACHE[zone] = (0, np.zeros(0))
        return _CACHE[zone]
    first = parts[0][0]
    last = max(h0 + len(v) for h0, v in parts)
    out = np.full(last - first, np.nan)
    for h0, values in parts:
        out[h0 - first : h0 - first + len(values)] = values
    _CACHE[zone] = (first, out)
    return _CACHE[zone]


def available(zone: str) -> bool:
    return len(_series(zone)[1]) > 0


def production_per_kwp(zone: str, timestamps: list[datetime]) -> np.ndarray | None:
    """kWh per installed kWp for each timestamp, or None if nothing is stored for the zone.

    Hours after the stored series ends take the mean of the same hour of day
    over the last fourteen stored days.
    """
    first, values = _series(zone)
    if len(values) == 0:
        return None
    idx = np.array([_hour(t) for t in timestamps], dtype=np.int64) - first
    out = np.full(len(idx), np.nan)
    inside = (idx >= 0) & (idx < len(values))
    out[inside] = values[idx[inside]]
    missing = np.isnan(out)
    if missing.any():
        known = values[~np.isnan(values)]
        tail_end = len(values) - (len(values) % 24) if len(values) >= 24 * 14 else len(values)
        tail = values[max(0, tail_end - 24 * 14) : tail_end]
        by_hour = np.array([np.nanmean(tail[h::24]) if len(tail[h::24]) else 0.0 for h in range(24)])
        tail_first_hour = (first + max(0, tail_end - 24 * 14)) % 24
        hours_of_day = (idx + first) % 24
        fill = by_hour[(hours_of_day - tail_first_hour) % 24]
        out[missing] = np.where(np.isnan(fill[missing]), float(np.mean(known)) if len(known) else 0.0, fill[missing])
    return np.clip(out, 0.0, None) / 1000.0 * SYSTEM_FACTOR


def main() -> int:  # pragma: no cover - network
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    stored = refresh()
    print({y: n for y, n in stored.items()})
    return 0 if stored else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
