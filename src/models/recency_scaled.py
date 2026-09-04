"""recency_scaled: use yesterday's price where the auction has already published it.

Every earlier model was equally informed at every horizon. The level was last
week's price whether the target was six hours away or seven days, so the error
curve across the week was flat — day 5 scored marginally better than day 1,
which should be impossible for a forecast worth the name.

The back-test made the cause plain rather than the symptom. It feeds the models
ERA5 reanalysis, a perfect hindcast, so day 1 and day 7 both received flawless
weather and the curve was still flat. The flatness was never about forecast
weather degrading; it was that nothing in the model knew more about tomorrow
than about next week.

The day-ahead auction publishes through the end of tomorrow at 12:45. So for a
target early in the window, the price for that same hour yesterday is a
published fact; for a target seven days out it is not, because yesterday is
still in the future. That asymmetry is exactly a horizon gradient, and it comes
from data the feature frame has carried all along.

Blending the 24-hour lag at 0.70 where it exists, over 115 396 out-of-sample
hours across ten quarters:

    weight on lag24    MAE      vs seasonal_naive
    0.00 (shrunk)      25.68          +13.3 %
    0.60               24.11          +18.6 %
    0.70               24.07          +18.7 %
    0.80               24.11          +18.6 %
    1.00               24.48          +17.4 %

The optimum is flat from 0.60 to 0.80, so 0.70 is robust rather than fitted to a
point. What matters more than the headline is where it lands:

    day        1      2      3      4      5      6      7
    MAE     19.46  21.13  25.39  25.16  25.48  26.08  25.83

Day one improves by 23 % and days three to seven are untouched, because that is
precisely where the lag stops existing. The model does not pretend to know more
about next Friday than it did before.

No availability rule is coded here. price_lag_24h is built from stored actuals,
which contain only published prices, so it is already absent exactly where it is
unknowable. The back-test issues at 08:00, before the 12:45 auction, so it
credits the lag for roughly 39 hours; the scheduled 13:20 and 18:00 runs know a
day more and should do slightly better than these numbers.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from ..timeutil import TZ, horizon_hours
from .band import band_for
from .base import ForecastPoint, order_quantiles, target_window
from .seasonal_naive import baseline_stats, naive_level
from .shrunk_scaled import shrunk_level
from .weather_scaled import _residual_load_index, compute_scale

RECENT_WEIGHT = 0.70


def recency_level(row, zone_means: pd.Series, overall_mean: float) -> float:
    """The shrunk weekly level, pulled toward yesterday where yesterday is known."""
    level = shrunk_level(row, zone_means, overall_mean)
    recent = getattr(row, "price_lag_24h", None)
    if recent is None or pd.isna(recent):
        return level
    return (1 - RECENT_WEIGHT) * level + RECENT_WEIGHT * float(recent)


class RecencyScaled:
    id = "recency_scaled"
    name_sv = "Väderskalad, färsk nivå"
    description_sv = (
        "Som den dämpade väderskalade modellen, men där gårdagens pris för samma "
        "timme redan är publicerat vägs det in med 70 procent. Det gäller ungefär "
        "det första dygnet och upphör därefter, vilket är just vad som skiljer i "
        "morgon från nästa fredag. Mätt på 115 396 timmar ut ur urvalet: medelfel "
        "24,07 mot 25,68, och dag ett förbättras med 23 procent."
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
            level = recency_level(row, zone_means, overall_mean)
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
