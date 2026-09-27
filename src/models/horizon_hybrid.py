"""horizon_hybrid: Seamless horizon-dependent blending of short-term and long-term specialists.

Empirical measurement over real out-of-sample delivery hours demonstrated:
- On hours 0-36 (days 1-2): Gradient Boosting (LightGBM) outperforms all other
  models by 33-53 % with a correlation of 0.85+.
- On hours 72-168 (days 4-7): The damped / market-scaled level provides the most
  stable, robust long-term baseline.

horizon_hybrid bridges the two with a smooth transition:
- h <= 36: 100 % LightGBM
- 36 < h < 72: Linear ramp between LightGBM and ShrunkScaled / MarketScaled
- h >= 72: 100 % ShrunkScaled / MarketScaled
"""

from __future__ import annotations

from datetime import datetime

from ..timeutil import horizon_hours
from .base import ForecastPoint, order_quantiles

SHORT_MODEL = "lightgbm_v2"
SHORT_FALLBACK = "lightgbm_v1"
LONG_MODEL = "market_scaled"
LONG_FALLBACK = "shrunk_scaled"

RAMP_START_H = 36
RAMP_END_H = 72


class HorizonHybrid:
    id = "horizon_hybrid"
    name_sv = "Horisonthybrid"
    description_sv = (
        "Kombinerar LightGBM:s överlägsna precision på kort sikt (0–36 h) med den "
        "dämpade och marknadsjusterade basnivån på längre horisonter (48–168 h)."
    )
    quantiles = True
    derived = True

    def combine(
        self, predictions: dict[str, list[ForecastPoint]], issued_at: datetime
    ) -> list[ForecastPoint]:
        short_points = predictions.get(SHORT_MODEL) or predictions.get(SHORT_FALLBACK) or []
        long_points = predictions.get(LONG_MODEL) or predictions.get(LONG_FALLBACK) or []

        if not short_points and not long_points:
            return []
        if not short_points:
            return list(long_points)
        if not long_points:
            return list(short_points)

        short_map = {(p.zone, p.ts): p for p in short_points}
        long_map = {(p.zone, p.ts): p for p in long_points}

        all_keys = sorted(set(short_map.keys()) & set(long_map.keys()), key=lambda k: (k[0], k[1]))
        if not all_keys:
            all_keys = sorted(set(short_map.keys()) | set(long_map.keys()), key=lambda k: (k[0], k[1]))

        combined: list[ForecastPoint] = []
        for zone, ts in all_keys:
            p_short = short_map.get((zone, ts))
            p_long = long_map.get((zone, ts))

            if p_short is None:
                combined.append(p_long)
                continue
            if p_long is None:
                combined.append(p_short)
                continue

            h = max(0, horizon_hours(issued_at, ts))
            if h <= RAMP_START_H:
                w_short = 1.0
            elif h >= RAMP_END_H:
                w_short = 0.0
            else:
                w_short = 1.0 - (h - RAMP_START_H) / float(RAMP_END_H - RAMP_START_H)

            w_long = 1.0 - w_short

            p50 = w_short * p_short.p50 + w_long * p_long.p50
            p10 = w_short * p_short.p10 + w_long * p_long.p10
            p90 = w_short * p_short.p90 + w_long * p_long.p90

            p10, p50, p90 = order_quantiles(p10, p50, p90)
            combined.append(ForecastPoint(ts=ts, zone=zone, p10=p10, p50=p50, p90=p90))

        return combined

    def predict(self, features, issued_at: datetime) -> list[ForecastPoint]:  # pragma: no cover
        raise NotImplementedError("horizon_hybrid is derived: call combine().")
