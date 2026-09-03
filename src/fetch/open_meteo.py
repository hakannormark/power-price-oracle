"""Weather from Open-Meteo (no API key) plus the derived price-driver indices."""

from __future__ import annotations

import json
import logging
from datetime import datetime

import pandas as pd

from ..config import (
    ALL_WEATHER_POINTS,
    CLIMATOLOGY_PATH,
    NORTH_WIND_POINTS,
    OPEN_METEO_URL,
    SOUTH_WIND_POINTS,
)
from ..timeutil import TZ, now_local
from .http import get

log = logging.getLogger(__name__)

HOURLY_VARIABLES = [
    "temperature_2m",
    "wind_speed_10m",
    "wind_gusts_10m",
    "shortwave_radiation",
    "precipitation",
]

COLUMN_MAP = {
    "temperature_2m": "temp",
    "wind_speed_10m": "wind",
    "wind_gusts_10m": "gust",
    "shortwave_radiation": "solar",
    "precipitation": "precip",
}

PAST_DAYS = 7
FORECAST_DAYS = 10

# Used until the persisted climatology has seen enough days.
DEFAULT_WIND_MS = 5.0
WIND_INDEX_MIN, WIND_INDEX_MAX = 0.4, 2.5
SOLAR_REFERENCE_WM2 = 400.0
CLIMATOLOGY_ALPHA = 0.1  # updated at most once per day -> ~10 day memory


def fetch_weather() -> tuple[pd.DataFrame, dict]:
    """One request for every point. Returns a long frame (ts, point, ...) in local time.

    The request is made in UTC and converted here: asking Open-Meteo for local
    timestamps makes the hour repeated at the DST fall-back ambiguous, and the
    market has two distinct prices for those two hours.
    """
    names = list(ALL_WEATHER_POINTS)
    lats = ",".join(str(ALL_WEATHER_POINTS[n][0]) for n in names)
    lons = ",".join(str(ALL_WEATHER_POINTS[n][1]) for n in names)

    params = {
        "latitude": lats,
        "longitude": lons,
        "hourly": ",".join(HOURLY_VARIABLES),
        "past_days": PAST_DAYS,
        "forecast_days": FORECAST_DAYS,
        "timezone": "UTC",
    }

    try:
        payload = get(OPEN_METEO_URL, params=params).json()
    except Exception as exc:  # noqa: BLE001 - the pipeline degrades instead of dying
        log.warning("Open-Meteo failed: %s", exc)
        return pd.DataFrame(columns=["ts", "point", *COLUMN_MAP.values()]), {
            "ok": False,
            "error": str(exc)[:200],
        }

    locations = payload if isinstance(payload, list) else [payload]
    frames: list[pd.DataFrame] = []
    for name, location in zip(names, locations):
        hourly = location.get("hourly") or {}
        times = hourly.get("time") or []
        if not times:
            continue
        frame = pd.DataFrame({"ts": pd.to_datetime(times, utc=True)})
        for source, target in COLUMN_MAP.items():
            frame[target] = pd.to_numeric(pd.Series(hourly.get(source, [])), errors="coerce")
        frame["ts"] = frame["ts"].dt.tz_convert(TZ)
        frame["point"] = name
        frames.append(frame)

    if not frames:
        return pd.DataFrame(columns=["ts", "point", *COLUMN_MAP.values()]), {
            "ok": False,
            "error": "empty response",
        }

    weather = pd.concat(frames, ignore_index=True)
    log.info("Open-Meteo: %s rows across %s points", len(weather), weather["point"].nunique())
    return weather, {"ok": True, "points": int(weather["point"].nunique()), "rows": len(weather)}


# ------------------------------------------------------------------ climatology


def load_climatology() -> dict:
    if not CLIMATOLOGY_PATH.exists():
        return {"points": {}, "last_update": None}
    try:
        return json.loads(CLIMATOLOGY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log.warning("Climatology file unreadable, starting over")
        return {"points": {}, "last_update": None}


def update_climatology(weather: pd.DataFrame, today: datetime | None = None) -> dict:
    """Fold the observed past window into a slowly moving per-hour climatology.

    Open-Meteo only hands back 7 past days per call, so the 30-day normal the
    indices want is accumulated across runs instead of fetched.
    """
    clim = load_climatology()
    if weather.empty:
        return clim

    today = (today or now_local()).date()
    if clim.get("last_update") == today.isoformat():
        return clim

    past = weather[weather["ts"] <= pd.Timestamp(now_local())]
    if past.empty:
        return clim

    points = clim.setdefault("points", {})
    for name, group in past.groupby("point"):
        entry = points.setdefault(name, {})
        hourly_temp = group.groupby(group["ts"].dt.hour)["temp"].mean()
        previous_temp = entry.get("temp_hour_mean") or [None] * 24
        merged: list[float | None] = []
        for hour in range(24):
            observed = hourly_temp.get(hour)
            old = previous_temp[hour] if hour < len(previous_temp) else None
            if observed is None or pd.isna(observed):
                merged.append(old)
            elif old is None:
                merged.append(round(float(observed), 3))
            else:
                merged.append(round((1 - CLIMATOLOGY_ALPHA) * old + CLIMATOLOGY_ALPHA * float(observed), 3))
        entry["temp_hour_mean"] = merged

        wind_mean = float(group["wind"].mean()) if group["wind"].notna().any() else None
        if wind_mean is not None:
            old_wind = entry.get("wind_mean")
            entry["wind_mean"] = round(
                wind_mean if old_wind is None else (1 - CLIMATOLOGY_ALPHA) * old_wind + CLIMATOLOGY_ALPHA * wind_mean,
                3,
            )
        entry["days_seen"] = int(entry.get("days_seen", 0)) + 1

    clim["last_update"] = today.isoformat()
    CLIMATOLOGY_PATH.parent.mkdir(parents=True, exist_ok=True)
    CLIMATOLOGY_PATH.write_text(json.dumps(clim, ensure_ascii=False, indent=1), encoding="utf-8")
    return clim


# ------------------------------------------------------------------ indices


def _fallback_normals(weather: pd.DataFrame, point: str) -> tuple[float, list[float | None]]:
    """Normals from the currently fetched window when no history exists yet."""
    group = weather[weather["point"] == point]
    wind = float(group["wind"].mean()) if group["wind"].notna().any() else DEFAULT_WIND_MS
    hourly = group.groupby(group["ts"].dt.hour)["temp"].mean()
    temps = [float(hourly[h]) if h in hourly.index and pd.notna(hourly[h]) else None for h in range(24)]
    return wind, temps


def add_indices(weather: pd.DataFrame, clim: dict) -> pd.DataFrame:
    """Add wind_index / temp_anomaly / solar_index per (ts, point)."""
    if weather.empty:
        return weather.assign(wind_index=[], temp_anomaly=[], solar_index=[])

    frames = []
    points = clim.get("points", {})
    for point, group in weather.groupby("point"):
        group = group.sort_values("ts").copy()
        entry = points.get(point, {})
        fallback_wind, fallback_temps = _fallback_normals(weather, point)

        wind_normal = entry.get("wind_mean")
        if not wind_normal or wind_normal <= 0.5:
            wind_normal = fallback_wind if fallback_wind and fallback_wind > 0.5 else DEFAULT_WIND_MS

        temp_normal = entry.get("temp_hour_mean") or [None] * 24
        hours = group["ts"].dt.hour
        normals = [
            temp_normal[h] if h < len(temp_normal) and temp_normal[h] is not None else fallback_temps[h]
            for h in hours
        ]

        group["wind_index"] = (group["wind"] / wind_normal).clip(WIND_INDEX_MIN, WIND_INDEX_MAX)
        group["temp_anomaly"] = group["temp"] - pd.Series(normals, index=group.index, dtype="float64")
        group["solar_index"] = (group["solar"] / SOLAR_REFERENCE_WM2).clip(0, 2).fillna(0.0)
        frames.append(group)

    return pd.concat(frames, ignore_index=True)


def regional_indices(weather: pd.DataFrame) -> pd.DataFrame:
    """North/south wind indices per timestamp, averaged over their member points."""
    if weather.empty:
        return pd.DataFrame(columns=["ts", "wind_index_north", "wind_index_south"])

    north = (
        weather[weather["point"].isin(NORTH_WIND_POINTS)]
        .groupby("ts")["wind_index"]
        .mean()
        .rename("wind_index_north")
    )
    south = (
        weather[weather["point"].isin(SOUTH_WIND_POINTS)]
        .groupby("ts")["wind_index"]
        .mean()
        .rename("wind_index_south")
    )
    return pd.concat([north, south], axis=1).reset_index()
