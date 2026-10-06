"""The feature matrix of lightgbm_v3, built the same way for training and for serving.

One function builds the matrix from plain arrays, and both the trainer and the
live model call it. The earlier LightGBM models computed their features twice,
in two files, and the two drifted: training normalised temperature against a
weekly seasonal normal, serving against a running hour-of-day mean. Nothing here
is normalised against a climatology at all, so there is nothing to drift.

Everything is indexed by hour: index i is `hour0 + i` hours since the Unix epoch,
UTC. A sample is a (zone, target index, lead) triple, where **lead** is the
number of whole days between the last day whose prices were published and the
target day. Only prices at least `24 * lead` hours before the target are ever
read, which is exactly what is known when the forecast is issued:

    issued 10:15 on day D, before the auction   last known day K = D      lead 1..7
    issued 13:30 on day D, after the auction    last known day K = D + 1  lead 1..6

Weather has an age as well: column `a` of a weather array is the forecast made
`a` days before the hour. Training passes the column that matches the lead;
serving has only the current forecast, in column 0.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from ..timeutil import TZ

ZONE_ORDER = ("SE1", "SE2", "SE3", "SE4")
POINTS = ("SE1", "SE2", "SE3", "SE4", "DK2", "DE_NORTH")
NORTH = ("SE1", "SE2")
SOUTH = ("SE3", "SE4", "DK2")
MAX_LEAD = 7
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

FEATURE_NAMES = [
    "zone", "hour", "dow", "month", "is_weekend", "is_holiday", "lead",
    # prices known at issue
    "p_last", "p_lag168", "p_lag336", "shrunk",
    "day_mean", "day_min", "day_max", "week_mean",
    "spread_se4_se3", "spread_se3_se2",
    # weather at the target hour, at the age a forecast for it really has
    "temp", "hdd", "wind", "solar", "wind_north", "wind_south", "wind_de", "solar_de", "wind_dk",
    # the same hour on the last known day, so the trees see the change
    "temp_ref", "wind_ref", "wind_south_ref", "wind_de_ref", "solar_ref",
    # fuels, last close before the last known day
    "ttf", "eua",
]
CATEGORICAL = ["zone"]


def hour_index(ts: datetime) -> int:
    return int((ts.astimezone(timezone.utc) - EPOCH).total_seconds() // 3600)


def hour_indices(series: pd.Series) -> np.ndarray:
    """hour_index for a tz-aware pandas series, whatever its resolution."""
    delta = series.dt.tz_convert("UTC") - pd.Timestamp("1970-01-01", tz="UTC")
    return (delta // pd.Timedelta(hours=1)).to_numpy(dtype=np.int64)


def _shift(values: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """values[idx] with NaN where idx falls outside the array."""
    out = np.full(len(idx), np.nan)
    ok = (idx >= 0) & (idx < len(values))
    out[ok] = values[idx[ok]]
    return out


def _mean_of(columns: list[np.ndarray]) -> np.ndarray:
    stacked = np.vstack(columns)
    with np.errstate(invalid="ignore"):
        counts = np.sum(~np.isnan(stacked), axis=0)
        total = np.nansum(stacked, axis=0)
    return np.where(counts > 0, total / np.maximum(counts, 1), np.nan)


def daily_stats(prices: np.ndarray, hour0: int) -> dict[str, np.ndarray]:
    """Per hour index: mean, min and max of the local day that hour belongs to,
    and the mean of the seven days ending with it. NaN for a day not fully priced."""
    n = len(prices)
    local = pd.to_datetime(hour0 * 3600 + np.arange(n) * 3600, unit="s", utc=True).tz_convert(TZ)
    # An id per local day. Built from the calendar fields, not from the integer
    # behind the timestamps: that integer is seconds or nanoseconds depending on
    # how the index was made, and dividing it by a day of nanoseconds put every
    # hour in day 0, which let a day's mean read hours not yet published.
    day = (local.year * 10000 + local.month * 100 + local.day).to_numpy(dtype=np.int64)
    frame = pd.DataFrame({"day": day, "p": prices})
    grouped = frame.groupby("day")["p"]
    stats = grouped.agg(["mean", "min", "max", "count"])
    stats.loc[stats["count"] < 23, ["mean", "min", "max"]] = np.nan
    week = stats["mean"].rolling(7, min_periods=4).mean()
    lookup = stats.assign(week=week)
    idx = lookup.index.get_indexer(day)
    return {
        "mean": lookup["mean"].to_numpy()[idx], "min": lookup["min"].to_numpy()[idx],
        "max": lookup["max"].to_numpy()[idx], "week": lookup["week"].to_numpy()[idx],
    }


def build_matrix(
    zone: str,
    target: np.ndarray,
    lead: np.ndarray,
    prices: dict[str, np.ndarray],
    weather: dict[str, dict[str, np.ndarray]],
    fuels: dict[str, np.ndarray],
    hour0: int,
    holidays: set[date],
    weather_age: np.ndarray | None = None,
    stats: dict[str, dict[str, np.ndarray]] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """(X, shrunk level) for the samples of one zone.

    prices[z]            float [n], NaN where no price is published
    weather[point][var]  float [n, ages]; var in temp, wind, solar
    fuels[name]          float [n], forward-filled daily closes; name in ttf, eua
    target, lead         int [m]: target hour index and lead in days (1..MAX_LEAD)
    weather_age          int [m]: which age column to read at the target; default 0
    """
    target = np.asarray(target, dtype=np.int64)
    lead = np.clip(np.asarray(lead, dtype=np.int64), 1, MAX_LEAD)
    m = len(target)
    age = np.zeros(m, dtype=np.int64) if weather_age is None else np.asarray(weather_age, dtype=np.int64)
    stats = stats or {z: daily_stats(prices[z], hour0) for z in ("SE2", "SE3", "SE4", zone)}

    local = pd.to_datetime((hour0 + target) * 3600, unit="s", utc=True).tz_convert(TZ)
    ref = target - 24 * lead  # the same hour on the last known day

    own = prices[zone]
    p_last = _shift(own, ref)
    # Weekly lags, but never one that is still in the future at issue time.
    weekly = [np.where(k * 168 >= 24 * lead, _shift(own, target - k * 168), np.nan) for k in (1, 2, 3, 4)]
    with np.errstate(all="ignore"):
        median = np.nanmedian(np.vstack(weekly), axis=0)
    lag168 = np.where(np.isnan(weekly[0]), p_last, weekly[0])
    shrunk = 0.70 * lag168 + 0.30 * np.where(np.isnan(median), lag168, median)

    def at(point: str, var: str, idx: np.ndarray, ages: np.ndarray) -> np.ndarray:
        arr = weather[point][var]
        out = np.full(len(idx), np.nan)
        ok = (idx >= 0) & (idx < arr.shape[0])
        a = np.minimum(ages[ok], arr.shape[1] - 1)
        out[ok] = arr[idx[ok], a]
        return out

    zero = np.zeros(m, dtype=np.int64)
    temp = at(zone, "temp", target, age)
    columns = {
        "zone": np.full(m, ZONE_ORDER.index(zone), dtype=float),
        "hour": local.hour.to_numpy(dtype=float),
        "dow": local.dayofweek.to_numpy(dtype=float),
        "month": local.month.to_numpy(dtype=float),
        "is_weekend": (local.dayofweek >= 5).astype(float),
        "is_holiday": np.array([d in holidays for d in local.date], dtype=float),
        "lead": lead.astype(float),
        "p_last": p_last,
        "p_lag168": lag168,
        "p_lag336": weekly[1],
        "shrunk": shrunk,
        "day_mean": _shift(stats[zone]["mean"], ref),
        "day_min": _shift(stats[zone]["min"], ref),
        "day_max": _shift(stats[zone]["max"], ref),
        "week_mean": _shift(stats[zone]["week"], ref),
        "spread_se4_se3": _shift(stats["SE4"]["mean"], ref) - _shift(stats["SE3"]["mean"], ref),
        "spread_se3_se2": _shift(stats["SE3"]["mean"], ref) - _shift(stats["SE2"]["mean"], ref),
        "temp": temp,
        "hdd": np.maximum(0.0, 15.0 - temp),
        "wind": at(zone, "wind", target, age),
        "solar": at(zone, "solar", target, age),
        "wind_north": _mean_of([at(p, "wind", target, age) for p in NORTH]),
        "wind_south": _mean_of([at(p, "wind", target, age) for p in SOUTH]),
        "wind_de": at("DE_NORTH", "wind", target, age),
        "solar_de": at("DE_NORTH", "solar", target, age),
        "wind_dk": at("DK2", "wind", target, age),
        "temp_ref": at(zone, "temp", ref, zero),
        "wind_ref": at(zone, "wind", ref, zero),
        "wind_south_ref": _mean_of([at(p, "wind", ref, zero) for p in SOUTH]),
        "wind_de_ref": at("DE_NORTH", "wind", ref, zero),
        "solar_ref": at(zone, "solar", ref, zero),
        # A close is known the evening it is set; use the one before the last known day.
        "ttf": _shift(fuels["ttf"], ref - 24),
        "eua": _shift(fuels["eua"], ref - 24),
    }
    return np.column_stack([columns[name] for name in FEATURE_NAMES]), shrunk


def fuel_arrays(rows: list[dict], hour0: int, n: int) -> dict[str, np.ndarray]:
    """Daily closes spread over the hour grid and carried forward."""
    out = {"ttf": np.full(n, np.nan), "eua": np.full(n, np.nan)}
    for row in rows:
        series = row.get("series")
        if series not in out or row.get("close") is None:
            continue
        day = datetime.fromisoformat(row["date"]).replace(tzinfo=timezone.utc)
        i = hour_index(day) - hour0 + 18  # the close exists from that evening
        if 0 <= i < n:
            out[series][i] = float(row["close"])
        elif i < 0:
            out[series][0] = float(row["close"]) if np.isnan(out[series][0]) else out[series][0]
    for key, arr in out.items():
        out[key] = pd.Series(arr).ffill().to_numpy()
    return out
