"""Best-effort ENTSO-E fundamentals: load forecast and wind/solar generation forecast.

Everything here is optional. When it works, weather_scaled prefers the published MW
forecasts over raw wind speed; when it does not, the pipeline carries on with weather
only and records the failure in status.json.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

import pandas as pd

from ..config import FUNDAMENTALS_CACHE_PATH, ZONES
from ..store import read_jsonl, write_jsonl
from ..timeutil import TZ, iso, now_local

log = logging.getLogger(__name__)

EMPTY_COLUMNS = ["ts", "zone", "load_forecast_mw", "wind_forecast_mw", "solar_forecast_mw"]
VALUE_COLUMNS = EMPTY_COLUMNS[2:]

# How old a cached forecast may be and still stand in. ENTSO-E republishes
# these daily; beyond a day and a half the next day's figures are stale.
CACHE_MAX_AGE = timedelta(hours=36)
CACHE_KEEP_BEHIND = timedelta(days=2)


def _resolve_area(zone: str) -> str | None:
    """Find the alias entsoe-py expects for a Swedish bidding zone.

    The library has renamed these between releases (SE_1 vs SE1), so resolve
    against the installed package instead of trusting a hard-coded string.
    """
    try:
        from entsoe.mappings import Area  # type: ignore
    except Exception:  # noqa: BLE001
        return zone

    eic = ZONES[zone]["eic"]
    for member in Area:
        if getattr(member, "value", None) == eic or getattr(member, "code", None) == eic:
            return member.name
    for candidate in (ZONES[zone]["entsoe_code"], zone):
        if candidate in Area.__members__:
            return candidate
    return None


def _to_local_series(series: pd.Series) -> pd.Series:
    index = pd.to_datetime(series.index, utc=True).tz_convert(TZ)
    return pd.Series(series.to_numpy(), index=index)


def fetch_fundamentals(
    start: datetime | None = None, end: datetime | None = None
) -> tuple[pd.DataFrame, dict]:
    token = os.environ.get("ENTSOE_TOKEN", "").strip()
    if not token:
        return pd.DataFrame(columns=EMPTY_COLUMNS), {"ok": False, "error": "ENTSOE_TOKEN is not set"}

    try:
        from entsoe import EntsoePandasClient  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return pd.DataFrame(columns=EMPTY_COLUMNS), {"ok": False, "error": f"entsoe-py unavailable: {exc}"[:200]}

    now = now_local()
    start = start or (now - timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
    end = end or (now + timedelta(days=3)).replace(hour=0, minute=0, second=0, microsecond=0)
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)

    client = EntsoePandasClient(api_key=token)
    rows: list[pd.DataFrame] = []
    errors: list[str] = []

    for zone in ZONES:
        area = _resolve_area(zone)
        if area is None:
            errors.append(f"{zone}: no area alias")
            continue

        frame = pd.DataFrame()
        try:
            load = client.query_load_forecast(area, start=start_ts, end=end_ts)
            if isinstance(load, pd.DataFrame):
                load = load.iloc[:, 0]
            frame["load_forecast_mw"] = _to_local_series(load)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{zone} load: {type(exc).__name__}")

        try:
            generation = client.query_wind_and_solar_forecast(area, start=start_ts, end=end_ts)
            if isinstance(generation, pd.Series):
                generation = generation.to_frame("Wind Onshore")
            generation.index = pd.to_datetime(generation.index, utc=True).tz_convert(TZ)
            wind_columns = [c for c in generation.columns if "Wind" in str(c)]
            solar_columns = [c for c in generation.columns if "Solar" in str(c)]
            if wind_columns:
                frame["wind_forecast_mw"] = generation[wind_columns].sum(axis=1)
            if solar_columns:
                frame["solar_forecast_mw"] = generation[solar_columns].sum(axis=1)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{zone} generation: {type(exc).__name__}")

        if frame.empty:
            continue
        frame = frame.resample("1h").mean()
        frame = frame.reset_index(names="ts")
        frame["zone"] = zone
        rows.append(frame)

    if not rows:
        return pd.DataFrame(columns=EMPTY_COLUMNS), {
            "ok": False,
            "error": ("; ".join(errors) or "no data")[:200],
        }

    fundamentals = pd.concat(rows, ignore_index=True)
    for column in EMPTY_COLUMNS:
        if column not in fundamentals.columns:
            fundamentals[column] = pd.NA

    status: dict = {"ok": True, "rows": len(fundamentals)}
    if errors:
        status["error"] = "; ".join(errors)[:200]
    log.info("ENTSO-E fundamentals: %s rows", len(fundamentals))
    return fundamentals[EMPTY_COLUMNS], status


# ------------------------------------------------------------------ fallback
#
# ENTSO-E failed in seven of twelve runs in the first week, sometimes for one
# zone and one series only. weather_scaled then fell back from the residual
# load to the wind-speed formula for those hours, so the model changed between
# runs for reasons that had nothing to do with the weather. The last values
# ENTSO-E did deliver are kept per (hour, zone, series) and fill only what the
# current run is missing.


def _long(frame: pd.DataFrame, fetched_at: str) -> list[dict]:
    rows = []
    for record in frame.to_dict("records"):
        stamp = iso(pd.Timestamp(record["ts"]).to_pydatetime())
        for column in VALUE_COLUMNS:
            value = record.get(column)
            if value is None or pd.isna(value):
                continue
            rows.append(
                {"ts": stamp, "zone": record["zone"], "series": column,
                 "value": round(float(value), 1), "fetched_at": fetched_at}
            )
    return rows


def _wide(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=EMPTY_COLUMNS)
    frame = pd.DataFrame(rows)
    wide = frame.pivot_table(index=["ts", "zone"], columns="series", values="value", aggfunc="last").reset_index()
    wide["ts"] = pd.to_datetime(wide["ts"], utc=True).dt.tz_convert(TZ)
    for column in VALUE_COLUMNS:
        if column not in wide.columns:
            wide[column] = pd.NA
    return wide[EMPTY_COLUMNS]


def with_cache(fresh: pd.DataFrame, status: dict, now: datetime | None = None) -> tuple[pd.DataFrame, dict]:
    """Fill what this run could not fetch from the last run that could, and refresh the cache.

    Fresh values always win. A cached value is used only if it was fetched
    within CACHE_MAX_AGE; the failure itself stays in `status`, with a count
    of the cells filled in.
    """
    now = now or now_local()
    oldest = now - CACHE_MAX_AGE
    horizon_start = now - CACHE_KEEP_BEHIND

    cached = [
        r for r in read_jsonl(FUNDAMENTALS_CACHE_PATH)
        if pd.Timestamp(r["fetched_at"]) >= pd.Timestamp(oldest) and pd.Timestamp(r["ts"]) >= pd.Timestamp(horizon_start)
    ]
    fresh_rows = _long(fresh, iso(now)) if fresh is not None and not fresh.empty else []

    merged: dict[tuple, dict] = {(r["ts"], r["zone"], r["series"]): r for r in cached}
    filled = sum(1 for key in merged if key not in {(r["ts"], r["zone"], r["series"]) for r in fresh_rows})
    for row in fresh_rows:
        merged[(row["ts"], row["zone"], row["series"])] = row

    if fresh_rows:
        # Only a run that fetched something rewrites the cache; a failed one
        # must not age the good values out.
        FUNDAMENTALS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        write_jsonl(FUNDAMENTALS_CACHE_PATH, sorted(merged.values(), key=lambda r: (r["zone"], r["series"], r["ts"])))

    combined = _wide(list(merged.values()))
    status = dict(status)
    if filled:
        status["cache_filled"] = filled
        status["ok"] = True
    return combined, status
