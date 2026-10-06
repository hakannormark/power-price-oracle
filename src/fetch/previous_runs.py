"""Weather forecasts as they looked N days before, for training on what a model really gets.

    python -m src.fetch.previous_runs            # fill or top up every point

Every weather-driven model here was fitted on ERA5 reanalysis: the weather that
happened. Live, a model is handed a forecast, and a forecast for day 7 is far
worse than one for day 1. A model trained on reanalysis has never seen that
decay, so it trusts day-7 weather exactly as much as day-1 weather.

Open-Meteo's Previous Runs API keeps, for each hour since January 2024, what
the forecast said one, two, ... seven days earlier (`<var>_previous_dayN`).
Training on the value with the right age removes the mismatch.

Stored per point as data/weather/archive/previous_runs/<point>.npz, which the
repository ignores like the rest of the archive: about 25 MiB that any machine
can rebuild in a few minutes.

    hours   int64, hours since the Unix epoch (UTC)
    temp    float32 [n, 8], column k = the forecast made k days before (0 = latest)
    wind    float32 [n, 8], km/h at 10 m, the unit the live forecast uses
    solar   float32 [n, 8], W/m2
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import date, datetime, timedelta, timezone

import numpy as np

from ..config import ALL_WEATHER_POINTS, WEATHER_ARCHIVE_DIR
from .http import get

log = logging.getLogger("previous-runs")

URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
DIR = WEATHER_ARCHIVE_DIR / "previous_runs"
FIRST_DAY = date(2024, 1, 1)   # the API's own start
MAX_AGE_DAYS = 7
CHUNK_DAYS = 60
SERIES = {"temp": "temperature_2m", "wind": "wind_speed_10m", "solar": "shortwave_radiation"}
PAUSE_S = 1.0  # stay far inside the free tier's per-minute limit
# The points lightgbm_v3 reads. The other archive points are not fetched: each
# costs seventeen requests and nothing uses them.
TRAINING_POINTS = ("SE1", "SE2", "SE3", "SE4", "DK2", "DE_NORTH")


def _variables() -> list[str]:
    out = []
    for name in SERIES.values():
        out.append(name)
        out.extend(f"{name}_previous_day{k}" for k in range(1, MAX_AGE_DAYS + 1))
    return out


def _fetch_chunk(lat: float, lon: float, start: date, end: date) -> dict[str, np.ndarray]:
    payload = get(URL, params={
        "latitude": lat, "longitude": lon, "hourly": ",".join(_variables()),
        "start_date": start.isoformat(), "end_date": end.isoformat(), "timezone": "UTC",
    }).json()
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        raise RuntimeError(f"previous runs: empty response for {start}..{end}")
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    hours = np.array(
        [int((datetime.fromisoformat(t).replace(tzinfo=timezone.utc) - epoch).total_seconds() // 3600) for t in times],
        dtype=np.int64,
    )
    out: dict[str, np.ndarray] = {"hours": hours}
    for key, name in SERIES.items():
        columns = [hourly.get(name)] + [hourly.get(f"{name}_previous_day{k}") for k in range(1, MAX_AGE_DAYS + 1)]
        out[key] = np.array(
            [[np.nan if v is None else float(v) for v in (col or [None] * len(times))] for col in columns],
            dtype=np.float32,
        ).T
    return out


def load_point(point: str) -> dict[str, np.ndarray] | None:
    path = DIR / f"{point}.npz"
    if not path.exists():
        return None
    with np.load(path) as data:
        return {k: data[k] for k in data.files}


def _merge(old: dict[str, np.ndarray] | None, new: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Later downloads win for the hours they cover: recent runs are still filling in."""
    if old is None:
        return new
    keep = ~np.isin(old["hours"], new["hours"])
    merged = {k: np.concatenate([old[k][keep], new[k]]) for k in new}
    order = np.argsort(merged["hours"])
    return {k: v[order] for k, v in merged.items()}


def refresh(points: list[str] | None = None, until: date | None = None) -> dict[str, int]:
    """Fill every point from where its file ends (minus a week) to `until`."""
    DIR.mkdir(parents=True, exist_ok=True)
    until = until or (datetime.now(timezone.utc).date() - timedelta(days=1))
    stored: dict[str, int] = {}
    for point in points or list(TRAINING_POINTS):
        lat, lon = ALL_WEATHER_POINTS[point]
        data = load_point(point)
        start = FIRST_DAY
        if data is not None and len(data["hours"]):
            last = datetime.fromtimestamp(int(data["hours"][-1]) * 3600, tz=timezone.utc).date()
            start = max(FIRST_DAY, last - timedelta(days=MAX_AGE_DAYS))
        while start <= until:
            end = min(until, start + timedelta(days=CHUNK_DAYS - 1))
            data = _merge(data, _fetch_chunk(lat, lon, start, end))
            log.info("%s: %s .. %s (%s hours stored)", point, start, end, len(data["hours"]))
            start = end + timedelta(days=1)
            time.sleep(PAUSE_S)
        if data is not None:
            np.savez_compressed(DIR / f"{point}.npz", **data)
            stored[point] = int(len(data["hours"]))
    return stored


def main() -> int:  # pragma: no cover - network
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    stored = refresh()
    for point, n in stored.items():
        print(f"  {point:9} {n} hours")
    return 0 if stored else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
