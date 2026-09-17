"""lightgbm_v1: Quantile regression using LightGBM gradient boosted trees.

Directly predicts p10, p50, and p90 quantiles from market and weather features.
Captures non-linear merit order dynamics, temperature heating response,
and diurnal interactions.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..config import ZONES
from ..timeutil import TZ
from .base import ForecastPoint, order_quantiles, target_window
from .seasonal_naive import baseline_stats
from .shrunk_scaled import shrunk_level

log = logging.getLogger(__name__)

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


class LightGbmV1:
    id = "lightgbm_v1"
    name_sv = "Gradient boosting (LightGBM)"
    description_sv = (
        "Kvantilregression (p10, p50, p90) tränad med LightGBM på 4 års historiska "
        "marknads- och väderdata. Modellerar elmarknadens icke-linjära merit order-kurva, "
        "temperaturtrösklar och samspel mellan tid på dygnet och väder."
    )
    quantiles = True
    derived = False

    def __init__(self):
        self._boosters: dict[str, Any] = {}
        self._loaded = False
        self._zone_map = {zone: idx for idx, zone in enumerate(sorted(ZONES.keys()))}

    def _ensure_loaded(self) -> bool:
        if self._loaded:
            return True

        try:
            import lightgbm as lgb

            for alpha_name in ("q10", "q50", "q90"):
                path = ARTIFACTS_DIR / f"lightgbm_{alpha_name}.txt"
                if not path.exists():
                    log.warning("LightGBM artifact %s not found", path)
                    return False
                self._boosters[alpha_name] = lgb.Booster(model_file=str(path))

            self._loaded = True
            return True
        except Exception as exc:
            log.warning("Could not load LightGBM boosters: %s", exc)
            return False

    def predict(self, features: pd.DataFrame, issued_at: datetime) -> list[ForecastPoint]:
        start, end = target_window(issued_at)
        zone_means, overall_mean = baseline_stats(features)

        targets = features[
            (features["ts"] >= pd.Timestamp(start)) & (features["ts"] <= pd.Timestamp(end))
        ].copy()

        if not self._ensure_loaded():
            log.warning("Falling back from lightgbm_v1 to shrunk_scaled levels due to missing boosters.")
            from .shrunk_scaled import ShrunkScaled

            return ShrunkScaled().predict(features, issued_at)

        # Build feature matrix matching training schema
        feature_rows: list[list[float]] = []
        for row in targets.itertuples(index=False):
            zone_idx = float(self._zone_map.get(row.zone, 0))
            hour = float(row.hour)
            dow = float(row.dow)
            month = float(row.month)
            is_weekend = float(row.is_weekend)
            is_holiday_se = float(row.is_holiday_se)

            wind_local = float(row.wind_index_local if not pd.isna(row.wind_index_local) else 1.0)
            wind_north = float(row.wind_index_north if not pd.isna(row.wind_index_north) else 1.0)
            wind_south = float(row.wind_index_south if not pd.isna(row.wind_index_south) else 1.0)

            temp_local = float(row.temp_local if not pd.isna(row.temp_local) else 10.0)
            temp_anomaly = float(row.temp_anomaly_local if not pd.isna(row.temp_anomaly_local) else 0.0)
            hdd = max(0.0, 15.0 - temp_local)
            solar_local = float(row.solar_index_local if not pd.isna(row.solar_index_local) else 0.0)

            lag24 = float(row.price_lag_24h) if not pd.isna(row.price_lag_24h) else np.nan
            lag168 = float(row.price_lag_168h) if not pd.isna(row.price_lag_168h) else np.nan
            lag336 = float(row.price_lag_336h) if not pd.isna(row.price_lag_336h) else np.nan

            s_level = shrunk_level(row, zone_means, overall_mean)

            feature_rows.append([
                zone_idx,
                hour,
                dow,
                month,
                is_weekend,
                is_holiday_se,
                wind_local,
                wind_north,
                wind_south,
                temp_local,
                temp_anomaly,
                hdd,
                solar_local,
                lag24,
                lag168,
                lag336,
                s_level,
            ])

        x_mat = np.array(feature_rows, dtype=np.float64)

        p10_preds = self._boosters["q10"].predict(x_mat)
        p50_preds = self._boosters["q50"].predict(x_mat)
        p90_preds = self._boosters["q90"].predict(x_mat)

        points: list[ForecastPoint] = []
        for i, row in enumerate(targets.itertuples(index=False)):
            p10, p50, p90 = order_quantiles(p10_preds[i], p50_preds[i], p90_preds[i])
            points.append(
                ForecastPoint(
                    ts=row.ts.to_pydatetime().astimezone(TZ),
                    zone=row.zone,
                    p10=p10,
                    p50=p50,
                    p90=p90,
                )
            )
        return points
