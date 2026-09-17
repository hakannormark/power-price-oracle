"""Train LightGBM quantile regression models (p10, p50, p90) for power price forecasting.

Usage:
    .venv/bin/python -m src.models.train_lightgbm
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from ..config import (
    ALL_WEATHER_POINTS,
    NORTH_WIND_POINTS,
    SOUTH_WIND_POINTS,
    WEATHER_ARCHIVE_DIR,
    ZONES,
)
from ..features.build import _swedish_holidays
from ..research.backtest import load_weather_archive
from ..store import load_actuals
from ..timeutil import TZ, parse_iso

log = logging.getLogger("train_lightgbm")

ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"

FEATURE_NAMES = [
    "zone_idx",
    "hour",
    "dow",
    "month",
    "is_weekend",
    "is_holiday_se",
    "wind_local",
    "wind_north",
    "wind_south",
    "temp_local",
    "temp_anomaly",
    "hdd",
    "solar_local",
    "price_lag_24h",
    "price_lag_168h",
    "price_lag_336h",
    "shrunk_level",
]


def build_training_data() -> pd.DataFrame:
    """Build a consolidated dataset from stored actuals and weather archive."""
    log.info("Loading actual prices...")
    actual_rows = load_actuals()
    if not actual_rows:
        raise RuntimeError("No actuals found in data/actuals!")

    prices: dict[tuple[str, pd.Timestamp], float] = {}
    for r in actual_rows:
        ts = pd.Timestamp(parse_iso(r["ts"])).tz_convert(TZ)
        prices[(r["zone"], ts)] = float(r["price_eur_mwh"])

    log.info("Loading weather archive...")
    weather = load_weather_archive()
    if not weather:
        raise RuntimeError("No weather archive found in data/weather/archive/!")

    def get_regional(ts: pd.Timestamp, points: list[str], field: str) -> float:
        dt = ts.to_pydatetime()
        values = [weather[p][dt][field] for p in points if p in weather and dt in weather[p]]
        values = [v for v in values if v == v]
        return float(sum(values) / len(values)) if values else (1.0 if "index" in field else 0.0)

    timestamps = sorted({ts for _, ts in prices})
    holiday_years = sorted({ts.year for ts in timestamps})
    holiday_dates = _swedish_holidays(holiday_years)

    rows: list[dict] = []
    zone_list = sorted(ZONES.keys())

    log.info("Building feature rows across %s zones and %s timestamps...", len(zone_list), len(timestamps))
    for zone_idx, zone in enumerate(zone_list):
        for ts in timestamps:
            target_price = prices.get((zone, ts))
            if target_price is None:
                continue

            lag168 = prices.get((zone, ts - pd.Timedelta(hours=168)))
            lag336 = prices.get((zone, ts - pd.Timedelta(hours=336)))
            lag504 = prices.get((zone, ts - pd.Timedelta(hours=504)))
            lag672 = prices.get((zone, ts - pd.Timedelta(hours=672)))

            # Skip early warmup period where baseline weekly lags do not exist
            if lag168 is None or lag336 is None:
                continue

            lags = [l for l in (lag168, lag336, lag504, lag672) if l is not None]
            med = float(np.median(lags))
            shrunk = 0.70 * lag168 + 0.30 * med

            lag24 = prices.get((zone, ts - pd.Timedelta(hours=24)))

            dt = ts.to_pydatetime()
            loc_weather = weather.get(zone, {}).get(dt, {})
            wind_local = float(loc_weather.get("wind_index", 1.0) or 1.0)
            temp_local = float(loc_weather.get("temp", 10.0) if "temp" in loc_weather else 10.0)
            temp_anom = float(loc_weather.get("temp_anomaly", 0.0) or 0.0)
            solar_local = float(loc_weather.get("solar_index", 0.0) or 0.0)

            wind_n = get_regional(ts, NORTH_WIND_POINTS, "wind_index")
            wind_s = get_regional(ts, SOUTH_WIND_POINTS, "wind_index")

            hdd = max(0.0, 15.0 - temp_local)
            is_hol = ts.date() in holiday_dates

            rows.append({
                "zone_idx": zone_idx,
                "zone": zone,
                "ts": ts,
                "hour": ts.hour,
                "dow": ts.dayofweek,
                "month": ts.month,
                "is_weekend": int(ts.dayofweek >= 5),
                "is_holiday_se": int(is_hol),
                "wind_local": wind_local,
                "wind_north": wind_n,
                "wind_south": wind_s,
                "temp_local": temp_local,
                "temp_anomaly": temp_anom,
                "hdd": hdd,
                "solar_local": solar_local,
                "price_lag_24h": lag24,
                "price_lag_168h": lag168,
                "price_lag_336h": lag336,
                "shrunk_level": shrunk,
                "target": target_price,
            })

    df = pd.DataFrame(rows)
    log.info("Constructed %s training samples.", len(df))
    return df


def train_and_save():
    df = build_training_data()
    if df.empty:
        raise RuntimeError("No data generated for training!")

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    # In production, price_lag_24h is only available for the first ~24-36 hours.
    # To teach the model to handle both states without overfitting on lag24,
    # we duplicate half the rows with price_lag_24h set to NaN (simulating horizon >= 2d).
    df_masked = df.copy()
    df_masked["price_lag_24h"] = np.nan
    augmented = pd.concat([df, df_masked], ignore_index=True)

    x_train = augmented[FEATURE_NAMES]
    y_train = augmented["target"]

    quantiles = [0.10, 0.50, 0.90]
    train_data = lgb.Dataset(x_train, label=y_train, feature_name=FEATURE_NAMES, free_raw_data=False)

    for alpha in quantiles:
        name = f"q{int(alpha * 100)}"
        log.info("Training LightGBM quantile regression model for alpha=%s (%s)...", alpha, name)
        params = {
            "objective": "quantile",
            "alpha": alpha,
            "metric": "quantile",
            "boosting_type": "gbdt",
            "num_leaves": 31,
            "learning_rate": 0.06,
            "min_child_samples": 25,
            "subsample": 0.85,
            "colsample_bytree": 0.85,
            "random_state": 42,
            "verbose": -1,
        }
        booster = lgb.train(
            params,
            train_data,
            num_boost_round=160,
        )

        artifact_file = ARTIFACTS_DIR / f"lightgbm_{name}.txt"
        booster.save_model(str(artifact_file))
        log.info("Saved model artifact to %s", artifact_file)

    meta = {
        "features": FEATURE_NAMES,
        "quantiles": quantiles,
        "samples": len(x_train),
        "zones": sorted(ZONES.keys()),
    }
    (ARTIFACTS_DIR / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    log.info("All models successfully trained and exported.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    train_and_save()
