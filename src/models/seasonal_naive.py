"""seasonal_naive: last week's price for the same weekday and hour.

The reference every other model has to beat. It carries no weather information at
all, which is exactly what makes it a fair yardstick for skill.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from ..timeutil import TZ
from .base import ForecastPoint, order_quantiles, target_window

# Fallback band when there are too few same-hour samples: identical to the spec's
# p50 * 0.70 / p50 * 1.45 whenever p50 >= 10 EUR/MWh, but it also produces a
# sensible band around zero and negative prices, where a bare multiplier inverts.
FALLBACK_LOW = 0.30
FALLBACK_HIGH = 0.45
MIN_BAND_BASE = 10.0

QUANTILE_LOOKBACK_WEEKS = 8
MIN_QUANTILE_SAMPLES = 8
COLD_START_PRICE = 40.0


def baseline_stats(features: pd.DataFrame) -> tuple[pd.Series, float]:
    """Zone means over the last week and an overall mean, used as last resorts."""
    history = features[features["actual_price"].notna()]
    if history.empty:
        return pd.Series(dtype="float64"), COLD_START_PRICE
    zone_means = history.groupby("zone")["actual_price"].apply(
        lambda s: float(s.tail(24 * 7).mean()) if len(s) else np.nan
    )
    overall = float(history["actual_price"].tail(24 * 7 * 4).mean())
    return zone_means, overall


def naive_level(row, zone_means: pd.Series, overall_mean: float) -> float:
    """Same hour one week ago, else one day ago, else the zone's recent mean."""
    for candidate in (row.price_lag_168h, row.price_lag_24h):
        if candidate is not None and not pd.isna(candidate):
            return float(candidate)
    zone_mean = zone_means.get(row.zone, np.nan)
    if not pd.isna(zone_mean):
        return float(zone_mean)
    return overall_mean


def same_hour_percentiles(features: pd.DataFrame, issued_at: datetime) -> dict:
    """10th/90th percentile per (zone, hour) over the last eight weeks."""
    history = features[features["actual_price"].notna()]
    if history.empty:
        return {}
    cutoff = pd.Timestamp(issued_at) - pd.Timedelta(weeks=QUANTILE_LOOKBACK_WEEKS)
    window = history[history["ts"] >= cutoff]
    out: dict[tuple[str, int], tuple[float, float]] = {}
    for (zone, hour), group in window.groupby(["zone", "hour"]):
        values = group["actual_price"].dropna()
        if len(values) >= MIN_QUANTILE_SAMPLES:
            out[(zone, int(hour))] = (float(values.quantile(0.10)), float(values.quantile(0.90)))
    return out


def fallback_band(p50: float, observed: tuple[float, float] | None) -> tuple[float, float]:
    if observed is not None:
        low, high = observed
        # Re-centre the empirical spread on this hour's level.
        centre = (low + high) / 2.0
        return p50 + (low - centre), p50 + (high - centre)
    base = max(abs(p50), MIN_BAND_BASE)
    return p50 - FALLBACK_LOW * base, p50 + FALLBACK_HIGH * base


class SeasonalNaive:
    id = "seasonal_naive"
    name_sv = "Säsongsnaiv"
    description_sv = (
        "Priset samma veckodag och timme sju dygn tidigare. Ingen väderinformation "
        "alls — modellen finns som referens som de andra ska slå."
    )
    quantiles = True
    derived = False

    def predict(self, features: pd.DataFrame, issued_at: datetime) -> list[ForecastPoint]:
        start, end = target_window(issued_at)
        zone_means, overall_mean = baseline_stats(features)
        percentiles = same_hour_percentiles(features, issued_at)

        targets = features[
            (features["ts"] >= pd.Timestamp(start)) & (features["ts"] <= pd.Timestamp(end))
        ]
        points: list[ForecastPoint] = []
        for row in targets.itertuples(index=False):
            p50 = naive_level(row, zone_means, overall_mean)
            p10, p90 = fallback_band(p50, percentiles.get((row.zone, int(row.hour))))
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
