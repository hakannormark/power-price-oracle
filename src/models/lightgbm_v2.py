"""lightgbm_v2: Residual Quantile Regression using LightGBM.

Predicts the residual price delta (actual_price - shrunk_level) using gradient
boosted trees with explicit horizon_h conditioning.
Keeps the forecast anchored to current-month market reality on longer horizons
while delivering state-of-the-art diurnal and weather tracking.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..config import ZONES
from ..timeutil import TZ, horizon_hours
from .base import ForecastPoint, order_quantiles, target_window
from .seasonal_naive import baseline_stats
from .shrunk_scaled import shrunk_level

log = logging.getLogger(__name__)

ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"


class LightGbmV2:
    id = "lightgbm_v2"
    name_sv = "Residual-LightGBM"
    description_sv = (
        "Kvantilregression tränad på prisavvikelsen från den dämpade basnivån. Halverar "
        "ungefär felet dygn 1–2 mot de väderskalade modellerna. Tränad en gång, den "
        "27 september 2026, på väderarkiv – alltså på vädret som det blev, inte på "
        "väderprognoser – och horisonten finns bara som två lägen i träningen. På dygn "
        "4–7 ligger den därför ofta fel i nivå och bandet är för smalt."
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
                path = ARTIFACTS_DIR / f"lightgbm_v2_{alpha_name}.txt"
                if not path.exists():
                    log.warning("LightGBM v2 artifact %s not found", path)
                    return False
                self._boosters[alpha_name] = lgb.Booster(model_file=str(path))

            self._loaded = True
            return True
        except Exception as exc:
            log.warning("Could not load LightGBM v2 boosters: %s", exc)
            return False

    def predict(self, features: pd.DataFrame, issued_at: datetime) -> list[ForecastPoint]:
        start, end = target_window(issued_at)
        zone_means, overall_mean = baseline_stats(features)

        targets = features[
            (features["ts"] >= pd.Timestamp(start)) & (features["ts"] <= pd.Timestamp(end))
        ].copy()

        if not self._ensure_loaded():
            log.warning("Falling back from lightgbm_v2 to shrunk_scaled levels due to missing boosters.")
            from .shrunk_scaled import ShrunkScaled

            return ShrunkScaled().predict(features, issued_at)

        feature_rows: list[list[float]] = []
        base_levels: list[float] = []

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
            base_levels.append(s_level)

            h_hours = float(max(0, horizon_hours(issued_at, row.ts.to_pydatetime())))

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
                h_hours,
            ])

        x_mat = np.array(feature_rows, dtype=np.float64)

        p10_res = self._boosters["q10"].predict(x_mat)
        p50_res = self._boosters["q50"].predict(x_mat)
        p90_res = self._boosters["q90"].predict(x_mat)

        points: list[ForecastPoint] = []
        for i, row in enumerate(targets.itertuples(index=False)):
            base = base_levels[i]
            p10 = base + p10_res[i]
            p50 = base + p50_res[i]
            p90 = base + p90_res[i]

            p10, p50, p90 = order_quantiles(p10, p50, p90)
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
