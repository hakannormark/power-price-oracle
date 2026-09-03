"""Build the feature frame every model predicts from: one row per (ts, zone)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pandas as pd

from ..config import FEATURE_HISTORY_DAYS, HORIZON_HOURS, ZONES
from ..timeutil import TZ, hour_range, now_local, parse_iso, start_of_day

log = logging.getLogger(__name__)

FEATURE_COLUMNS = [
    "ts",
    "zone",
    "hour",
    "dow",
    "is_weekend",
    "is_holiday_se",
    "month",
    "wind_index_local",
    "wind_index_north",
    "wind_index_south",
    "temp_local",
    "temp_anomaly_local",
    "solar_index_local",
    "load_forecast_mw",
    "wind_forecast_mw",
    "solar_forecast_mw",
    "actual_price",
    "price_lag_24h",
    "price_lag_48h",
    "price_lag_168h",
    "price_zone_yesterday_profile",
]


def _swedish_holidays(years: list[int]) -> set:
    """Swedish public holidays, minus plain Sundays.

    The holidays package lists every Sunday as "Söndag" for SE. That is formally
    correct but useless as a load signal — is_weekend already carries it, and
    keeping it here would flag a red day in almost every forecast window.
    """
    try:
        import holidays  # type: ignore

        calendar = holidays.country_holidays("SE", years=years)
        return {
            day
            for day, name in calendar.items()
            if not (day.weekday() == 6 and name.strip() == "Söndag")
        }
    except Exception as exc:  # noqa: BLE001 - calendar is a nice-to-have
        log.warning("Swedish holiday calendar unavailable: %s", exc)
        return set()


def build_features(
    actuals: list[dict],
    weather: pd.DataFrame,
    regional: pd.DataFrame,
    fundamentals: pd.DataFrame | None = None,
    now: datetime | None = None,
) -> pd.DataFrame:
    """Frame covering [today - FEATURE_HISTORY_DAYS, today + 7d] for all four zones.

    History reaches further back than the spec's 7 days because seasonal_naive
    needs roughly eight weeks of same-hour samples for its quantiles.
    """
    now = now or now_local()
    start = start_of_day(now) - timedelta(days=FEATURE_HISTORY_DAYS)
    end = start_of_day(now) + timedelta(hours=HORIZON_HOURS + 24)

    timestamps = hour_range(start, end)
    index = pd.MultiIndex.from_product(
        [pd.DatetimeIndex(timestamps), list(ZONES)], names=["ts", "zone"]
    )
    frame = pd.DataFrame(index=index).reset_index()

    # ---- calendar
    local = frame["ts"].dt.tz_convert(TZ)
    frame["hour"] = local.dt.hour
    frame["dow"] = local.dt.dayofweek
    frame["is_weekend"] = frame["dow"] >= 5
    frame["month"] = local.dt.month
    holiday_dates = _swedish_holidays(sorted({d.year for d in timestamps}))
    frame["is_holiday_se"] = local.dt.date.isin(holiday_dates)

    # ---- weather, per zone's own point
    if weather is not None and not weather.empty:
        local_weather = weather[weather["point"].isin(ZONES)][
            ["ts", "point", "temp", "wind_index", "temp_anomaly", "solar_index"]
        ].rename(
            columns={
                "point": "zone",
                "temp": "temp_local",
                "wind_index": "wind_index_local",
                "temp_anomaly": "temp_anomaly_local",
                "solar_index": "solar_index_local",
            }
        )
        frame = frame.merge(local_weather, on=["ts", "zone"], how="left")
    else:
        for column in ("temp_local", "wind_index_local", "temp_anomaly_local", "solar_index_local"):
            frame[column] = pd.NA

    if regional is not None and not regional.empty:
        frame = frame.merge(regional, on="ts", how="left")
    else:
        frame["wind_index_north"] = pd.NA
        frame["wind_index_south"] = pd.NA

    # ---- fundamentals (optional)
    if fundamentals is not None and not fundamentals.empty:
        frame = frame.merge(fundamentals, on=["ts", "zone"], how="left")
    for column in ("load_forecast_mw", "wind_forecast_mw", "solar_forecast_mw"):
        if column not in frame.columns:
            frame[column] = pd.NA

    # ---- official prices and lags
    if actuals:
        prices = pd.DataFrame(
            [
                {
                    "ts": parse_iso(row["ts"]),
                    "zone": row["zone"],
                    "actual_price": float(row["price_eur_mwh"]),
                }
                for row in actuals
            ]
        )
        prices["ts"] = pd.to_datetime(prices["ts"], utc=True).dt.tz_convert(TZ)
        prices = prices.drop_duplicates(subset=["ts", "zone"], keep="last")
        frame = frame.merge(prices, on=["ts", "zone"], how="left")

        for label, hours in (("price_lag_24h", 24), ("price_lag_48h", 48), ("price_lag_168h", 168)):
            shifted = prices.copy()
            shifted["ts"] = shifted["ts"] + pd.Timedelta(hours=hours)
            shifted = shifted.rename(columns={"actual_price": label})
            frame = frame.merge(shifted, on=["ts", "zone"], how="left")
    else:
        for column in ("actual_price", "price_lag_24h", "price_lag_48h", "price_lag_168h"):
            frame[column] = pd.NA

    frame["price_zone_yesterday_profile"] = frame["price_lag_24h"]

    numeric = [
        "wind_index_local",
        "wind_index_north",
        "wind_index_south",
        "temp_local",
        "temp_anomaly_local",
        "solar_index_local",
        "load_forecast_mw",
        "wind_forecast_mw",
        "solar_forecast_mw",
        "actual_price",
        "price_lag_24h",
        "price_lag_48h",
        "price_lag_168h",
        "price_zone_yesterday_profile",
    ]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    frame = frame[FEATURE_COLUMNS].sort_values(["zone", "ts"]).reset_index(drop=True)
    log.info(
        "Features: %s rows, %s with official price",
        len(frame),
        int(frame["actual_price"].notna().sum()),
    )
    return frame
