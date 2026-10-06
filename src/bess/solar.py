"""Hourly sun and temperature per bidding zone, measured, for the home-battery pages.

    python -m src.bess.solar            # fill or top up data/bess/solar/<year>.json

Stored per zone reference point, hourly from 2015, from ERA5 (Open-Meteo's archive):

* radiation on a panel tilted 35 degrees facing south,
* the mean of the same panel facing east and facing west (a roof with half of
  the panels on each side),
* air temperature at two metres, which drives the heating part of the load.

Production is

    kWh per kWp and hour = radiation in the panel's plane [W/m2] / 1000 * PERFORMANCE_RATIO

PERFORMANCE_RATIO is what is lost between the panel's rating and the meter:
inverter, cables, heat, dirt, snow, mismatch. 0.80 is in the lower part of the
0.80-0.85 usually quoted for Swedish rooftops, chosen because ERA5 is known to
be a few per cent generous with radiation in northern Europe. The result was
checked against PVGIS for the same four points; see tests/test_bess.py.

The files are tracked: a finished year never changes, and the pipeline in CI
must be able to read them without downloading anything. ERA5 runs about a week
behind, so the most recent days are filled with the mean of the same hour over
the fortnight before.
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

log = logging.getLogger(__name__)

DIR = DATA_DIR / "bess" / "solar"
FIRST_YEAR = 2015
TILT_DEG = 35
PERFORMANCE_RATIO = 0.80
ORIENTATIONS = ("south", "eastwest")
ARCHIVE_LAG_DAYS = 6
FORMAT = 2
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _hour(ts: datetime) -> int:
    return int((ts - EPOCH).total_seconds() // 3600)


def _year_path(year: int):
    return DIR / f"{year}.json"


def _load_year(year: int) -> dict | None:
    try:
        return json.loads(_year_path(year).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _fetch(lat: float, lon: float, start: date, end: date, variable: str, azimuth: int = 0) -> tuple[int, list[float | None]]:
    payload = get(WEATHER_ARCHIVE_URL, params={
        "latitude": lat, "longitude": lon, "hourly": variable, "tilt": TILT_DEG, "azimuth": azimuth,
        "start_date": start.isoformat(), "end_date": end.isoformat(), "timezone": "UTC",
    }).json()
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        raise RuntimeError(f"solar: empty response {start}..{end}")
    first = _hour(datetime.fromisoformat(times[0]).replace(tzinfo=timezone.utc))
    return first, hourly.get(variable) or []


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
        if old and old.get("format") == FORMAT and old.get("complete_through") == end.isoformat():
            stored[year] = n
            continue
        out: dict[str, dict[str, list]] = {"south": {}, "eastwest": {}, "temp": {}}
        for zone, info in ZONES.items():
            def series(variable: str, azimuth: int = 0) -> list[float | None]:
                first, values = _fetch(info["lat"], info["lon"], start, end, variable, azimuth)
                assert first == hour0, f"solar: unexpected first hour for {zone} {year}"
                time.sleep(0.4)
                return values[:n]

            south = series("global_tilted_irradiance", 0)      # Open-Meteo: 0 = south, -90 = east, 90 = west
            east = series("global_tilted_irradiance", -90)
            west = series("global_tilted_irradiance", 90)
            temp = series("temperature_2m")
            out["south"][zone] = [None if v is None else int(round(v)) for v in south]
            out["eastwest"][zone] = [None if a is None or b is None else int(round((a + b) / 2)) for a, b in zip(east, west)]
            out["temp"][zone] = [None if v is None else int(round(v * 10)) for v in temp]
        _year_path(year).write_text(json.dumps({
            "source": "ERA5 via Open-Meteo archive, UTC hours. south/eastwest: global_tilted_irradiance at "
                      f"{TILT_DEG} degrees tilt, W/m2 (eastwest = mean of east and west). temp: temperature_2m, tenths of a degree C.",
            "format": FORMAT, "hour0": hour0, "complete_through": end.isoformat(), **out,
        }, separators=(",", ":")), encoding="utf-8")
        stored[year] = n
        log.info("solar %s: %s hours per zone", year, n)
    return stored


_CACHE: dict[tuple[str, str], tuple[int, np.ndarray]] = {}


def _series(kind: str, zone: str) -> tuple[int, np.ndarray]:
    """(first hour index, value per hour) over every stored year, NaN where missing."""
    key = (kind, zone)
    if key in _CACHE:
        return _CACHE[key]
    parts = []
    for path in sorted(DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        values = np.array([np.nan if v is None else v for v in (data.get(kind) or {}).get(zone, [])], dtype=float)
        parts.append((int(data["hour0"]), values))
    parts = [p for p in parts if len(p[1])]
    if not parts:
        _CACHE[key] = (0, np.zeros(0))
        return _CACHE[key]
    first = parts[0][0]
    last = max(h0 + len(v) for h0, v in parts)
    out = np.full(last - first, np.nan)
    for h0, values in parts:
        out[h0 - first : h0 - first + len(values)] = values
    _CACHE[key] = (first, out)
    return _CACHE[key]


def _lookup(kind: str, zone: str, timestamps: list[datetime]) -> np.ndarray | None:
    """The stored value for each timestamp. Hours after the series ends take the
    mean of the same hour of day over the last fourteen stored days."""
    first, values = _series(kind, zone)
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
    return out


def available(zone: str) -> bool:
    return len(_series("south", zone)[1]) > 0


def production_per_kwp(zone: str, timestamps: list[datetime], orientation: str = "south") -> np.ndarray | None:
    """kWh per installed kWp for each timestamp, or None if nothing is stored for the zone."""
    if orientation not in ORIENTATIONS:
        raise ValueError(f"unknown orientation {orientation!r}")
    values = _lookup(orientation, zone, timestamps)
    return None if values is None else np.clip(values, 0.0, None) / 1000.0 * PERFORMANCE_RATIO


def temperature(zone: str, timestamps: list[datetime]) -> np.ndarray | None:
    """Degrees C at two metres for each timestamp, or None if nothing is stored."""
    values = _lookup("temp", zone, timestamps)
    return None if values is None else values / 10.0


def main() -> int:  # pragma: no cover - network
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    stored = refresh()
    print({y: n for y, n in stored.items()})
    return 0 if stored else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
