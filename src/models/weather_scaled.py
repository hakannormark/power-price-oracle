"""weather_scaled: the seasonal-naive level, scaled by the weather that moves it.

Deliberately a closed-form formula with no trained artifacts, so a fresh clone
produces a forecast on the first run. Documented as v1 on metod.html.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from ..timeutil import TZ, horizon_hours
from .band import band_for
from .base import ForecastPoint, order_quantiles, target_window
from .seasonal_naive import baseline_stats, naive_level

SCALE_MIN, SCALE_MAX = 0.45, 2.2
BASE_WIDTH = 0.30
WIDTH_PER_SCALE = 0.25
HIGH_WIDTH_FACTOR = 1.4

# Weight on the residual-load index when ENTSO-E fundamentals are available. It
# replaces the local wind term, since published MW beat a wind speed proxy.
RESIDUAL_WEIGHT = 0.60


@dataclass(frozen=True)
class ZoneWeights:
    wind_local: float
    wind_north: float
    wind_south: float
    temp: float
    solar: float


# SE1/SE2 lean harder on their own wind and less on temperature; the north term is
# zero there because local wind already is the northern wind. SE4 additionally
# tracks southern wind and solar, following DK/DE.
ZONE_WEIGHTS = {
    "SE1": ZoneWeights(wind_local=0.35, wind_north=0.0, wind_south=0.0, temp=0.08, solar=0.08),
    "SE2": ZoneWeights(wind_local=0.35, wind_north=0.0, wind_south=0.0, temp=0.08, solar=0.08),
    "SE3": ZoneWeights(wind_local=0.25, wind_north=0.10, wind_south=0.0, temp=0.15, solar=0.08),
    "SE4": ZoneWeights(wind_local=0.25, wind_north=0.10, wind_south=0.20, temp=0.15, solar=0.20),
}
DEFAULT_WEIGHTS = ZONE_WEIGHTS["SE3"]


def _value(raw, default: float) -> float:
    if raw is None or pd.isna(raw):
        return default
    return float(raw)


def compute_scale(
    zone: str,
    wind_index_local: float | None,
    wind_index_north: float | None,
    wind_index_south: float | None,
    temp_anomaly_local: float | None,
    solar_index_local: float | None,
    residual_index: float | None = None,
) -> float:
    """Multiplier applied to the naive level. 1.0 means "a normal week for this hour"."""
    weights = ZONE_WEIGHTS.get(zone, DEFAULT_WEIGHTS)
    temp_anomaly = _value(temp_anomaly_local, 0.0)
    solar = _value(solar_index_local, 0.0)

    scale = 1.0
    if residual_index is not None and not pd.isna(residual_index):
        scale += RESIDUAL_WEIGHT * (float(residual_index) - 1.0)
    else:
        scale -= weights.wind_local * (_value(wind_index_local, 1.0) - 1.0)

    scale -= weights.wind_north * (_value(wind_index_north, 1.0) - 1.0)
    scale -= weights.wind_south * (_value(wind_index_south, 1.0) - 1.0)
    scale += weights.temp * (temp_anomaly / 10.0)  # cold -> higher, 10 degrees ~ +15 %
    scale -= weights.solar * solar  # midday solar dip

    return min(max(scale, SCALE_MIN), SCALE_MAX)


def _residual_load_index(features: pd.DataFrame) -> pd.Series:
    """Residual load (load - wind - solar) relative to its own zone mean.

    Returns an all-NaN series when fundamentals are missing, which puts the model
    back on the wind-speed formula.
    """
    if "load_forecast_mw" not in features.columns or features["load_forecast_mw"].isna().all():
        return pd.Series(float("nan"), index=features.index, dtype="float64")

    residual = features["load_forecast_mw"].astype("float64")
    for column in ("wind_forecast_mw", "solar_forecast_mw"):
        if column in features.columns:
            residual = residual - features[column].astype("float64").fillna(0.0)

    means = residual.groupby(features["zone"]).transform("mean")
    index = residual / means.where(means.abs() > 1.0)
    return index.clip(0.5, 1.8)


class WeatherScaled:
    id = "weather_scaled"
    name_sv = "Väderskalad"
    description_sv = (
        "Utgår från den säsongsnaiva nivån och skalar den med vindindex, "
        "temperaturavvikelse och solinstrålning per elområde. Finns ENTSO-E:s "
        "prognoser för last och vindkraft används residuallasten i stället för vindhastighet."
    )
    quantiles = True
    derived = False

    def predict(self, features: pd.DataFrame, issued_at: datetime) -> list[ForecastPoint]:
        start, end = target_window(issued_at)
        zone_means, overall_mean = baseline_stats(features)

        targets = features[
            (features["ts"] >= pd.Timestamp(start)) & (features["ts"] <= pd.Timestamp(end))
        ].copy()
        targets["residual_index"] = _residual_load_index(targets)

        points: list[ForecastPoint] = []
        for row in targets.itertuples(index=False):
            level = naive_level(row, zone_means, overall_mean)
            scale = compute_scale(
                row.zone,
                row.wind_index_local,
                row.wind_index_north,
                row.wind_index_south,
                row.temp_anomaly_local,
                row.solar_index_local,
                row.residual_index,
            )
            p50 = level * scale

            # Band comes from measured residual quantiles per forecast day, not
            # from the weather scale: the old width claimed 80 % coverage and
            # delivered 39.7 %. See models/band.py.
            low, high = band_for(p50, horizon_hours(issued_at, row.ts.to_pydatetime()))
            p10, p50, p90 = order_quantiles(low, p50, high)
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


# FUTURE: replace scale formula with LightGBM quantile models
# trained on features.build output. Load booster from models/artifacts/.
