"""shrunk_scaled: weather scaling on a level that does not trust one week alone.

seasonal_naive takes its level from a single observation — the same hour seven
days ago — so one anomalous week is copied forward wholesale, in either
direction. A Saturday that collapsed to 2 EUR/MWh becomes next Saturday's
forecast; an evening that spiked to 175 becomes next week's evening.

The fix is not to replace that observation but to shrink it toward the median of
the same hour over the last four weeks. Backtested on 90 days of ENTSO-E prices
for all four zones (32 928 scored hours, issued daily at 08:00 over a full 168 h
window):

    weight on lag168   MAE EUR/MWh   vs seasonal_naive
    1.00 (naive)             30.33            0.0 %
    0.80                     29.10           +4.1 %
    0.70                     28.83           +5.0 %
    0.65                     28.78           +5.1 %
    0.50                     29.05           +4.2 %
    0.00 (median only)       32.92           -8.5 %

Neither ingredient works alone: the lag alone is noisy, and the median alone
lags real level shifts. The optimum is flat between 0.60 and 0.75, so 0.70 is a
robust choice rather than a knife-edge fit. Every zone improved (SE1 +7.3 %,
SE2 +6.5 %, SE4 +4.1 %, SE3 +3.7 %).

Two other candidates were tested and rejected: a pure four-week median (-8.5 %)
and scaling the lag by the recent week-over-week level trend (-39 %).

It shipped as a competitor and has since been promoted to the site default. Over
82 576 out-of-sample hours across ten quarters it scored MAE 25.63 against 28.04
for weather_scaled and 28.40 for the ensemble that used to be published. Blending
it with either of those made the result worse, so the default is this model alone.

Two caveats belong with that number. The back-test scores weather from ERA5
reanalysis, which is a perfect forecast; live weather is a forecast and degrades
with horizon, so the weather-driven part of the advantage is optimistic. The
shrinkage part, worth 7.9 % on its own, does not depend on weather at all.
"""

from __future__ import annotations

import statistics
from datetime import datetime

import pandas as pd

from ..timeutil import TZ, horizon_hours
from .band import band_for
from .base import ForecastPoint, order_quantiles, target_window
from .seasonal_naive import baseline_stats, naive_level
from .weather_scaled import _residual_load_index, compute_scale

LAG_WEIGHT = 0.70  # weight on the one-week lag; the rest goes to the four-week median
WEEKLY_LAG_COLUMNS = ["price_lag_168h", "price_lag_336h", "price_lag_504h", "price_lag_672h"]
MIN_LAGS_FOR_SHRINKAGE = 3


def shrunk_level(row, zone_means: pd.Series, overall_mean: float) -> float:
    """Weekly lag, pulled toward the median of the same hour in recent weeks.

    Falls back to the plain naive level until enough weekly history exists, so a
    fresh install still forecasts on its first run.
    """
    lag = naive_level(row, zone_means, overall_mean)
    values = [getattr(row, column, None) for column in WEEKLY_LAG_COLUMNS]
    values = [float(v) for v in values if v is not None and not pd.isna(v)]
    if len(values) < MIN_LAGS_FOR_SHRINKAGE:
        return lag
    return LAG_WEIGHT * lag + (1 - LAG_WEIGHT) * statistics.median(values)


class ShrunkScaled:
    id = "shrunk_scaled"
    name_sv = "Väderskalad, dämpad nivå"
    description_sv = (
        "Samma väderskalning som den väderskalade modellen, men grundnivån är inte "
        "bara priset för en vecka sedan. Den vägs 70/30 mot medianen för samma timme "
        "de senaste fyra veckorna, så att ett enskilt avvikande dygn inte kopieras "
        "rakt in i prognosen. Mätt på 82 576 timmar ut ur urvalet över tio kvartal: "
        "medelfel 25,63 EUR/MWh mot 29,51 för den säsongsnaiva referensen och 28,04 "
        "för den väderskalade. Detta är sajtens standardmodell."
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
            level = shrunk_level(row, zone_means, overall_mean)
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
